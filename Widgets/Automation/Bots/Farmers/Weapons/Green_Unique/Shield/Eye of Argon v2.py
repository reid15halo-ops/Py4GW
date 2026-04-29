import os
import time

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib import AgentArray
from Py4GWCoreLib import Botting
from Py4GWCoreLib import ConsoleLog, Console
from Py4GWCoreLib import Range
from Py4GWCoreLib import Routines
from Py4GWCoreLib import Utils
from Py4GWCoreLib import Map, Agent, Player, Party

BOT_NAME = "Eye of Argon shield Farm"
MODULE_NAME = "Eye of Argon shield"
MODULE_ICON = "Textures\\Module_Icons\\Eye_of_Argon.png"
TEXTURE = MODULE_ICON

# ---------------------------------------------------------------------------
# Map IDs — Sunspear Sanctuary → Command Post (transit) → Jahai Bluffs
# ---------------------------------------------------------------------------
OUTPOST_TO_TRAVEL = 387        # Sunspear Sanctuary
TRANSIT_EXPLORABLE = 436       # Command Post
EXPLORABLE_TO_TRAVEL = 369     # Jahai Bluffs

# Overseer Boktek — Kournan Paragon boss who drops Eye of Argon (shield).
# Model ID 5245.  He patrols the NW area of Jahai Bluffs.
BOSS_MODEL_ID = 5245
BOSS_NAME = "Overseer Boktek"

# ---------------------------------------------------------------------------
# Path from Sunspear Sanctuary to Command Post exit
# ---------------------------------------------------------------------------
COORD_TO_EXIT_MAP = [
    (-419, 4024),
]

# Transit path through Command Post to reach Jahai Bluffs
TRANSIT_PATH = [
    (5316, 7722),
]

# ---------------------------------------------------------------------------
# Expanded patrol/killing path through Jahai Bluffs.
# Original had 5 points; this adds intermediate waypoints for better coverage
# of Boktek's patrol route in the NW area.
# ---------------------------------------------------------------------------
KILLING_PATH: list[tuple[float, float]] = [
    (-6988, 8797),     # Original point 1 — entry area
    (-6400, 9100),     # Swing north for wider coverage
    (-5883, 9168),     # Original point 2
    (-5200, 8900),     # Intermediate — sweep east
    (-4752, 8737),     # Original point 3
    (-4600, 7200),     # Intermediate — head south
    (-4599, 5727),     # Original point 4
    (-5500, 4500),     # Swing west into patrol zone
    (-6500, 3200),     # Continue SW
    (-7400, 1800),     # Further into boss territory
    (-7800, 500),      # Deep patrol push
    (-8331, -1268),    # Original point 5
    (-7500, -500),     # Return leg — northeast
    (-6800, 800),      # Continue NE back toward start
    (-6000, 2500),     # Midpoint return
    (-5500, 4000),     # Closing the loop
]

# ---------------------------------------------------------------------------
# Danger thresholds
# ---------------------------------------------------------------------------
DANGER_ENEMY_THRESHOLD = 8        # More than this many enemies = large pull
DANGER_HP_THRESHOLD = 0.50        # Any party member below 50% HP = stressed
DANGER_CHECK_INTERVAL_MS = 2000   # Check every 2 seconds

# ---------------------------------------------------------------------------
# Zone entry safety thresholds
# ---------------------------------------------------------------------------
ZONE_ENTRY_SAFE_POINT = KILLING_PATH[2]
ZONE_ENTRY_AGGRO_RADIUS = Range.Earshot.value
ZONE_ENTRY_SMALL_PACK = 4
ZONE_ENTRY_WAIT_MAX_MS = 30000

# ---------------------------------------------------------------------------
# Shared state (single-threaded per-client, no locking needed)
# ---------------------------------------------------------------------------
is_boss_spotted = False
is_boss_killed = False
boss_agent_id = -1


bot = Botting(BOT_NAME)


# ===========================================================================
# Helper: scan compass for the boss
# ===========================================================================
def _scan_for_boss():
    """Scan enemy array within compass range for Overseer Boktek (model 5245).
    Falls back to scanning for any boss-glow enemy if model ID not found.
    Returns (found: bool, agent_id: int).  Safe when no enemies exist."""
    global is_boss_spotted, boss_agent_id

    if is_boss_spotted and boss_agent_id > 0:
        try:
            if Agent.IsValid(boss_agent_id) and not Agent.IsDead(boss_agent_id):
                return True, boss_agent_id
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Boss validation error: {e}", Console.MessageType.Warning)
        is_boss_spotted = False
        boss_agent_id = -1

    try:
        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            <= Range.SafeCompass.value,
        )

        # First pass: look for exact model ID
        for enemy_id in enemy_array:
            try:
                if Agent.GetModelID(enemy_id) == BOSS_MODEL_ID:
                    is_boss_spotted = True
                    boss_agent_id = enemy_id
                    ConsoleLog(BOT_NAME, f"{BOSS_NAME} found! Agent ID: {enemy_id}")
                    return True, enemy_id
            except Exception:
                continue

        # Second pass fallback: look for any boss-glow enemy (in case model
        # ID is wrong or wiki data is stale)
        for enemy_id in enemy_array:
            try:
                if Agent.HasBossGlow(enemy_id) and not Agent.IsDead(enemy_id):
                    is_boss_spotted = True
                    boss_agent_id = enemy_id
                    try:
                        mid = Agent.GetModelID(enemy_id)
                    except Exception:
                        mid = -1
                    ConsoleLog(BOT_NAME, f"Boss-glow enemy found (model {mid}), treating as target")
                    return True, enemy_id
            except Exception:
                continue

    except Exception as e:
        ConsoleLog(BOT_NAME, f"Enemy scan error: {e}", Console.MessageType.Warning)

    return False, -1


# ===========================================================================
# Helper: check if team is in danger (overwhelmed)
# ===========================================================================
def _team_in_danger():
    """Returns True if many enemies AND any party member is low HP."""
    try:
        if not Map.IsMapReady() or not Map.IsExplorable():
            return False

        enemy_array = AgentArray.GetEnemyArray()
        nearby_enemies = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            <= Range.SafeCompass.value,
        )
        enemy_count = len(nearby_enemies)

        if enemy_count <= DANGER_ENEMY_THRESHOLD:
            return False

        players = Party.GetPlayers()
        for player in players:
            try:
                agent_id = Party.Players.GetAgentIDByLoginNumber(player.login_number)
                if agent_id <= 0 or not Agent.IsValid(agent_id):
                    continue
                hp_pct = Agent.GetHealth(agent_id)
                if 0 < hp_pct < DANGER_HP_THRESHOLD:
                    return True
            except Exception:
                continue

        return False
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Danger check error: {e}", Console.MessageType.Warning)
        return False


# ===========================================================================
# Helper: count nearby enemies
# ===========================================================================
def _count_nearby_enemies(radius=None):
    """Count enemies within given radius (default compass)."""
    if radius is None:
        radius = Range.SafeCompass.value
    try:
        enemies = AgentArray.GetEnemyArray()
        return len(AgentArray.Filter.ByCondition(
            enemies,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid)) <= radius,
        ))
    except Exception:
        return 0


# ===========================================================================
# Helper: check if boss is dead
# ===========================================================================
def _check_boss_dead():
    """Returns True if we have spotted and confirmed the boss is dead."""
    global is_boss_killed
    if is_boss_killed:
        return True
    if is_boss_spotted and boss_agent_id > 0:
        try:
            if Agent.IsDead(boss_agent_id):
                is_boss_killed = True
                ConsoleLog(BOT_NAME, f"{BOSS_NAME} confirmed dead!")
                return True
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Boss death check error: {e}", Console.MessageType.Warning)
    return False


# ===========================================================================
# Helper: check if entire party is dead (wipe detection)
# ===========================================================================
def _is_party_wiped():
    """Returns True if ALL party members are dead."""
    try:
        if not Map.IsMapReady() or not Map.IsExplorable():
            return False
        players = Party.GetPlayers()
        if not players:
            return False
        for player in players:
            try:
                agent_id = Party.Players.GetAgentIDByLoginNumber(player.login_number)
                if agent_id <= 0 or not Agent.IsValid(agent_id):
                    continue
                if not Agent.IsDead(agent_id):
                    return False  # At least one alive
            except Exception:
                continue
        return True  # All dead
    except Exception:
        return False


# ===========================================================================
# Zone entry: handle mobs near the zone-in point
# ===========================================================================
def _handle_zone_entry_mobs():
    """Coroutine: after zoning into Jahai Bluffs, handle mobs near exit.

    Logic:
    1. Wait for map to be ready
    2. Scan for enemies within earshot (aggro range)
    3. If NO enemies nearby: proceed immediately
    4. If SMALL pack (<=4): stand and let HeroAI fight
    5. If LARGE pack (>4): run to safe point and let mobs deaggro
    """
    # Wait for map ready
    for _ in range(20):
        if Map.IsMapReady() and Map.IsExplorable():
            break
        yield from Routines.Yield.wait(500)

    yield from Routines.Yield.wait(1500)  # Brief settle after zone-in

    nearby = _count_nearby_enemies(ZONE_ENTRY_AGGRO_RADIUS)
    ConsoleLog(BOT_NAME, f"Zone entry: {nearby} enemies within earshot")

    if nearby == 0:
        ConsoleLog(BOT_NAME, "Zone entry: clear, proceeding to patrol")
        return

    if nearby <= ZONE_ENTRY_SMALL_PACK:
        ConsoleLog(BOT_NAME, f"Zone entry: small pack ({nearby}), fighting")
        elapsed = 0
        while elapsed < ZONE_ENTRY_WAIT_MAX_MS:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if _count_nearby_enemies(ZONE_ENTRY_AGGRO_RADIUS) == 0:
                ConsoleLog(BOT_NAME, "Zone entry: pack cleared")
                break
            yield from Routines.Yield.wait(1000)
            elapsed += 1000
        yield from Routines.Yield.wait(2000)  # Loot window
        return

    # Large pack — retreat to safe point
    ConsoleLog(BOT_NAME, f"Zone entry: large pack ({nearby}), retreating to safe point")
    sx, sy = ZONE_ENTRY_SAFE_POINT
    try:
        yield from bot.Move._coro_get_path_to(sx, sy)
        yield from bot.Move._coro_follow_path_to()
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Zone entry retreat error: {e}", Console.MessageType.Warning)

    # Wait for combat to resolve
    elapsed = 0
    while elapsed < ZONE_ENTRY_WAIT_MAX_MS:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
            break
        yield from Routines.Yield.wait(1000)
        elapsed += 1000

    ConsoleLog(BOT_NAME, "Zone entry: resolved, starting patrol")


def _run_zone_entry_as_custom_state():
    """Returns the generator so the FSM waits for it to complete."""
    return _handle_zone_entry_mobs()


# ===========================================================================
# Background boss scanner coroutine
# ===========================================================================
def _background_boss_scanner():
    """Managed coroutine that runs every second, scanning for the boss
    and checking if it has died (e.g. killed by other mobs en route)."""
    global is_boss_killed, is_boss_spotted, boss_agent_id

    while True:
        try:
            if not Map.IsExplorable() or not Map.IsMapReady():
                yield from Routines.Yield.wait(1000)
                continue

            if is_boss_killed:
                yield from Routines.Yield.wait(1000)
                continue

            # Check if already-spotted boss has died
            if is_boss_spotted and boss_agent_id > 0:
                try:
                    if Agent.IsDead(boss_agent_id):
                        is_boss_killed = True
                        ConsoleLog(BOT_NAME, f"{BOSS_NAME} died (detected by scanner)")
                except Exception as e:
                    ConsoleLog(BOT_NAME, f"Scanner death check error: {e}", Console.MessageType.Warning)
                yield from Routines.Yield.wait(1000)
                continue

            # Scan for boss
            _scan_for_boss()

        except Exception as e:
            ConsoleLog(BOT_NAME, f"Scanner error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)


# ===========================================================================
# Intercept patrol — walk the killing path while scanning for boss
# ===========================================================================
def _intercept_patrol():
    """Coroutine: walk KILLING_PATH, scanning for boss at each step.
    When boss is spotted, break from patrol and move toward it.
    When team is overwhelmed, pause until combat resolves."""
    global is_boss_spotted, is_boss_killed, boss_agent_id

    MAX_PATROL_LOOPS = 3

    for loop_count in range(MAX_PATROL_LOOPS):
        if is_boss_killed:
            return

        ConsoleLog(BOT_NAME, f"Patrol loop {loop_count + 1}/{MAX_PATROL_LOOPS}")

        for i, (wx, wy) in enumerate(KILLING_PATH):
            # --- Guard: map still valid? ---
            if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
                return
            if is_boss_killed:
                return

            # --- Check: boss spotted? Rush to it ---
            if is_boss_spotted and boss_agent_id > 0:
                try:
                    if not Agent.IsDead(boss_agent_id):
                        ax, ay = Agent.GetXY(boss_agent_id)
                        if ax != 0 or ay != 0:
                            ConsoleLog(BOT_NAME, f"{BOSS_NAME} spotted! Rushing to ({ax:.0f}, {ay:.0f})")
                            yield from bot.Move._coro_get_path_to(ax, ay)
                            yield from bot.Move._coro_follow_path_to()
                            if not Agent.IsDead(boss_agent_id):
                                yield from _wait_for_boss_kill()
                            return
                    else:
                        is_boss_killed = True
                        return
                except Exception as e:
                    ConsoleLog(BOT_NAME, f"Rush error: {e}", Console.MessageType.Warning)
                    is_boss_spotted = False
                    boss_agent_id = -1

            # --- Wipe check: if entire party dead, wait for revive ---
            if _is_party_wiped():
                ConsoleLog(BOT_NAME, "Party wipe detected during patrol -- waiting for revive")
                yield from _wait_for_revive()
                if not Map.IsMapReady() or not Map.IsExplorable():
                    return
                ConsoleLog(BOT_NAME, "Revived -- resuming patrol")

            # --- Danger check: are we overwhelmed? ---
            if _team_in_danger():
                ConsoleLog(BOT_NAME, "Team overwhelmed -- pausing to fight")
                while _team_in_danger():
                    if not Map.IsMapReady() or Map.IsMapLoading():
                        return
                    yield from Routines.Yield.wait(DANGER_CHECK_INTERVAL_MS)
                # Wait until fully out of combat
                while Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                    if not Map.IsMapReady() or Map.IsMapLoading():
                        return
                    yield from Routines.Yield.wait(1000)
                ConsoleLog(BOT_NAME, "Combat resolved -- resuming patrol")

            # --- Move to next waypoint via A* pathing ---
            yield from bot.Move._coro_get_path_to(wx, wy)
            yield from bot.Move._coro_follow_path_to()

            # --- Post-move scan ---
            _scan_for_boss()

        _scan_for_boss()

    ConsoleLog(BOT_NAME, "Patrol loops exhausted -- boss not found", Console.MessageType.Warning)


# ===========================================================================
# Wait for boss kill after engaging
# ===========================================================================
def _wait_for_boss_kill():
    """Wait for the boss to die after engaging.  Includes timeout and
    re-approach logic if boss moves out of range."""
    global is_boss_killed

    KILL_TIMEOUT_S = 120
    start_time = time.time()

    while (time.time() - start_time) < KILL_TIMEOUT_S:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        if _check_boss_dead():
            return

        # Wipe check inside combat
        if _is_party_wiped():
            ConsoleLog(BOT_NAME, "Party wipe during boss fight -- waiting for revive")
            yield from _wait_for_revive()
            if not Map.IsMapReady() or not Map.IsExplorable():
                return
            # After revive, try to re-engage if boss still alive
            if not _check_boss_dead() and boss_agent_id > 0:
                try:
                    ax, ay = Agent.GetXY(boss_agent_id)
                    if ax != 0 or ay != 0:
                        yield from bot.Move._coro_get_path_to(ax, ay)
                        yield from bot.Move._coro_follow_path_to()
                except Exception as e:
                    ConsoleLog(BOT_NAME, f"Re-engage after wipe error: {e}", Console.MessageType.Warning)

        # Re-approach if boss moved out of range
        if boss_agent_id > 0:
            try:
                dist = Utils.Distance(Player.GetXY(), Agent.GetXY(boss_agent_id))
                if dist > Range.SafeCompass.value:
                    ConsoleLog(BOT_NAME, f"{BOSS_NAME} moved out of range -- re-approaching")
                    ax, ay = Agent.GetXY(boss_agent_id)
                    if ax != 0 or ay != 0:
                        yield from bot.Move._coro_get_path_to(ax, ay)
                        yield from bot.Move._coro_follow_path_to()
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Re-approach error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)

    ConsoleLog(BOT_NAME, "Kill timeout reached", Console.MessageType.Warning)


# ===========================================================================
# Wait for revive after party wipe
# ===========================================================================
def _wait_for_revive():
    """Coroutine: wait until the player is no longer dead (revived by
    'Return to outpost on defeat' widget or manual res).
    Includes map-change guard and wall-clock timeout."""
    REVIVE_TIMEOUT_S = 120
    start_time = time.time()

    while (time.time() - start_time) < REVIVE_TIMEOUT_S:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        try:
            my_id = Player.GetAgentID()
            if my_id > 0 and not Agent.IsDead(my_id):
                ConsoleLog(BOT_NAME, "Player revived!")
                yield from Routines.Yield.wait(2000)  # Settle time after res
                return
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Revive check error: {e}", Console.MessageType.Warning)
        yield from Routines.Yield.wait(1000)

    ConsoleLog(BOT_NAME, "Revive timeout reached", Console.MessageType.Warning)


# ===========================================================================
# Custom state wrappers (non-generator)
# ===========================================================================
def _run_intercept_as_custom_state():
    """Non-generator wrapper for AddCustomState.  Schedules the intercept
    patrol as a managed coroutine."""
    fsm = bot.config.FSM
    fsm.AddManagedCoroutine("InterceptPatrol", _intercept_patrol)


def _wait_for_intercept_done():
    """Condition function: returns True when patrol/kill phase is over."""
    if is_boss_killed:
        return True
    if not bot.config.FSM.HasManagedCoroutine("InterceptPatrol"):
        return True
    return False


# ===========================================================================
# State reset between farm loops
# ===========================================================================
def reset_farm_flags():
    """Reset all tracking state for a fresh farm loop."""
    global is_boss_killed, is_boss_spotted, boss_agent_id
    is_boss_spotted = False
    is_boss_killed = False
    boss_agent_id = -1
    try:
        bot.States.RemoveManagedCoroutine("InterceptPatrol")
    except Exception:
        pass
    try:
        bot.States.RemoveManagedCoroutine("BossScanner")
    except Exception:
        pass


# ===========================================================================
# Wipe handler (event-driven)
# ===========================================================================
def _on_party_wipe(bot_ref: "Botting"):
    """Coroutine: pause FSM, wait for revive, then resume."""
    while True:
        if not Map.IsMapReady() or Map.IsMapLoading():
            bot_ref.config.FSM.resume()
            return
        try:
            my_id = Player.GetAgentID()
            if my_id > 0 and not Agent.IsDead(my_id):
                break
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Wipe handler check error: {e}", Console.MessageType.Warning)
        yield from bot_ref.Wait._coro_for_time(1000)

    # Player revived — resume the FSM
    bot_ref.config.FSM.resume()


def OnPartyWipe(bot_ref: "Botting"):
    """Event callback: triggered on party wipe."""
    ConsoleLog(BOT_NAME, "Party wipe event triggered")
    fsm = bot_ref.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot_ref))


# ===========================================================================
# Main farm routine
# ===========================================================================
def farm_routine(bot: Botting) -> None:
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    # Register wipe handler
    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))

    bot.States.AddHeader(BOT_NAME)
    bot.Templates.Multibox_Aggressive()
    bot.Properties.Disable("auto_inventory_management")

    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_TO_TRAVEL)
    bot.Party.SetHardMode(False)

    # Background boss scanner — runs continuously in explorable
    bot.States.AddManagedCoroutine('BossScanner', _background_boss_scanner)

    bot.States.AddHeader('Exit To Farm')
    bot.Properties.Disable('pause_on_danger')
    bot.Move.FollowPathAndExitMap(COORD_TO_EXIT_MAP, target_map_id=TRANSIT_EXPLORABLE)
    bot.Move.FollowAutoPath(TRANSIT_PATH)
    bot.Wait.ForMapToChange(EXPLORABLE_TO_TRAVEL)
    bot.Wait.ForTime(3000)
    bot.Properties.Enable('pause_on_danger')

    # Handle mobs near zone-in before starting patrol
    bot.States.AddHeader('Zone Entry Safety')
    bot.States.AddCustomState(_run_zone_entry_as_custom_state, "Handle exit mobs")
    bot.Wait.ForTime(2000)

    bot.States.AddHeader("Start Combat")
    bot.States.AddCustomState(_run_intercept_as_custom_state, "Launch Intercept Patrol")
    bot.Wait.UntilCondition(_wait_for_intercept_done, duration=1000)

    # Loot phase — wait for looting
    bot.Wait.ForTime(10000)

    # Resign and loop
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(reset_farm_flags, "Reset Farm detections")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(10000)
    bot.States.JumpToStepName("[H]Exit To Farm_3")


bot.SetMainRoutine(farm_routine)


# ===========================================================================
# Tooltip
# ===========================================================================
def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored(BOT_NAME + " bot", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("Multi-account bot to farm Eye of Argon shield")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- Sunspear Sanctuary outpost")
    PyImGui.bullet_text("- 6 well-geared accounts with Hero AI")
    PyImGui.spacing()
    PyImGui.bullet_text("Features:")
    PyImGui.bullet_text("- Boss scanning (model ID + boss-glow fallback)")
    PyImGui.bullet_text("- 16-point expanded patrol route")
    PyImGui.bullet_text("- Zone entry safety handler")
    PyImGui.bullet_text("- Danger check (stop & fight if overwhelmed)")
    PyImGui.bullet_text("- Wipe handler with revive wait")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")

    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Original by Aura, v2 improvements by Claude")
    PyImGui.end_tooltip()


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    bot.Update()
    bot.UI.draw_window(icon_path=TEXTURE)


if __name__ == "__main__":
    main()
