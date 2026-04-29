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

BOT_NAME = "The Mindsquall Farm v2"
MODULE_ICON = "Textures\\Module_Icons\\The Mindsquall.png"

OUTPOST_TO_TRAVEL = 640       # Rata Sum
EXPLORABLE_MAP_ID = 569       # Magus Stones
BOUNTY_NPC_XY = (14810.47, 13164.24)
BOUNTY_DIALOG = 0x84

# ---------------------------------------------------------------------------
# The Mindsquall is a Kirin boss (Mesmer) in Magus Stones.
# Kirin model IDs in EotN areas range ~5647-5652.
# If the exact model ID is wrong, HasBossGlow fallback will still detect it.
# To find the correct ID: run the bot, check console output from the scanner
# which logs every boss model ID it encounters.
# ---------------------------------------------------------------------------
MINDSQUALL_MODEL_ID = 5647  # Kirin (Mesmer) -- verify in-game, update if needed

# ---------------------------------------------------------------------------
# Patrol path: expanded coverage of Magus Stones from Rata Sum exit.
# The original path followed a mostly-linear route west.  This expanded
# version adds coverage of the northern and southern branches where the
# Kirin patrol wanders, plus a return leg to catch spawns we may have
# missed on the first pass.
# ---------------------------------------------------------------------------
INTERCEPT_PATH: list[tuple[float, float]] = [
    # Phase 1: Exit area, head toward bounty NPC area
    (14810.0, 13164.0),    # Near bounty NPC
    (12500.0, 12500.0),    # East corridor
    (10933.0, 11521.0),    # Original path point 1
    (9000.0,  11000.0),    # Bridge area
    (7221.0,  10923.0),    # Original path point 2
    # Phase 2: Continue west into Kirin territory
    (5173.0,  10854.0),    # Original path point 3
    (4000.0,  12000.0),    # Northern branch
    (3845.0,  13876.0),    # Original path point 4 (north)
    (2000.0,  13000.0),    # Deep north
    (500.0,   12600.0),    # Central north
    # Phase 3: West toward Kirin spawns
    (-1146.0, 12302.0),    # Original path point 5
    (-2500.0, 11500.0),    # South branch split
    (-4257.0, 12403.0),    # Original path point 6
    (-5500.0, 13500.0),    # North west branch
    (-6287.0, 13025.0),    # Original path point 7
    # Phase 4: Deep west -- Kirin patrol terminus
    (-8000.0, 13500.0),    # Extended west
    (-8988.0, 12881.0),    # Original path point 8
    (-10000.0, 12500.0),   # Far west coverage
    # Phase 5: Return leg (south route for extra coverage)
    (-8500.0, 11500.0),    # South return
    (-6000.0, 11000.0),    # Mid south
    (-3500.0, 11500.0),    # East return
    (-1000.0, 11000.0),    # Near center
    (2000.0,  11500.0),    # East approach
    (5000.0,  11000.0),    # Back toward start
]

# ---------------------------------------------------------------------------
# Danger thresholds
# Magus Stones has Angorodon, Kirin, Azure Shadows, and Chromatic Drakes.
# Groups of >8 enemies with low party HP means we pulled too much.
# ---------------------------------------------------------------------------
DANGER_ENEMY_THRESHOLD = 8
DANGER_HP_THRESHOLD = 0.50
DANGER_CHECK_INTERVAL_MS = 2000

# ---------------------------------------------------------------------------
# Zone entry config
# ---------------------------------------------------------------------------
ZONE_ENTRY_AGGRO_RADIUS = Range.Earshot.value
ZONE_ENTRY_SMALL_PACK = 4
ZONE_ENTRY_WAIT_MAX_MS = 30000
ZONE_ENTRY_SAFE_POINT = INTERCEPT_PATH[2]  # Move into the route

# ---------------------------------------------------------------------------
# State shared between coroutines (single-threaded per client, no lock needed)
# ---------------------------------------------------------------------------
is_boss_spotted = False
is_boss_killed = False
boss_agent_id = -1


bot = Botting(BOT_NAME)


# ===========================================================================
# Helper: scan compass for The Mindsquall
# ===========================================================================
def _scan_for_boss():
    """Scan enemy array within compass range for The Mindsquall.
    Primary detection: model ID match.
    Fallback: HasBossGlow for any boss in the area (logs model ID for tuning).
    Returns (found: bool, agent_id: int)."""
    global is_boss_spotted, boss_agent_id

    # If already tracking, verify the agent is still valid
    if is_boss_spotted and boss_agent_id > 0:
        try:
            if Agent.IsValid(boss_agent_id):
                return True, boss_agent_id
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Agent validation error: {e}", Console.MessageType.Warning)
        # Lost reference, re-scan
        is_boss_spotted = False
        boss_agent_id = -1

    try:
        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            <= Range.SafeCompass.value,
        )
        for enemy_id in enemy_array:
            try:
                model_id = Agent.GetModelID(enemy_id)
                # Primary: exact model ID match
                if model_id == MINDSQUALL_MODEL_ID:
                    is_boss_spotted = True
                    boss_agent_id = enemy_id
                    ConsoleLog(BOT_NAME, f"Mindsquall found (model {model_id}) agent {enemy_id}")
                    return True, enemy_id
                # Fallback: any boss glow in the area (log its model for tuning)
                if Agent.HasBossGlow(enemy_id):
                    ConsoleLog(BOT_NAME, f"Boss detected: model={model_id} agent={enemy_id} (glow)")
                    is_boss_spotted = True
                    boss_agent_id = enemy_id
                    return True, enemy_id
            except Exception:
                continue  # Stale agent IDs are expected
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Enemy scan error: {e}", Console.MessageType.Warning)

    return False, -1


# ===========================================================================
# Helper: check if team is in danger (overwhelmed)
# ===========================================================================
def _team_in_danger():
    """Returns True if there are many enemies AND any party member is low HP."""
    try:
        if not Map.IsMapReady() or not Map.IsExplorable():
            return False

        enemy_array = AgentArray.GetEnemyArray()
        nearby_enemies = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            <= Range.SafeCompass.value,
        )
        if len(nearby_enemies) <= DANGER_ENEMY_THRESHOLD:
            return False

        # Any party member below HP threshold triggers danger
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
    except Exception:
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
# Zone entry: handle mobs near the Rata Sum exit
# ===========================================================================
def _handle_zone_entry_mobs():
    """Coroutine: after zoning into Magus Stones, handle mobs near the exit.

    Logic:
    1. Wait for map to be ready
    2. Scan for enemies within earshot (aggro range)
    3. If NO enemies nearby: proceed immediately
    4. If SMALL pack (<=4): stand and let HeroAI fight
    5. If LARGE pack (>4): run to safe point and wait for combat to resolve
    """
    # Wait for map to be fully ready after zone
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
        yield from Routines.Yield.wait(2000)  # Brief loot window
        return

    # Large pack -- retreat to safe point
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
                return True
        except Exception:
            pass
    return False


# ===========================================================================
# Background scanner: runs continuously to detect boss at any point
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
                        ConsoleLog(BOT_NAME, "Boss died (detected by scanner)")
                except Exception:
                    pass
                yield from Routines.Yield.wait(1000)
                continue

            # Scan for boss
            _scan_for_boss()

        except Exception as e:
            ConsoleLog(BOT_NAME, f"Scanner error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)


# ===========================================================================
# Intercept patrol: walk the path, scanning for boss at each waypoint
# ===========================================================================
def _intercept_patrol():
    """Coroutine: walk intercept path, scanning for the boss at each step.
    When boss is spotted, break from patrol and move toward it.
    When team is overwhelmed, pause movement until combat resolves."""
    global is_boss_spotted, is_boss_killed, boss_agent_id

    MAX_PATROL_LOOPS = 3

    for loop_count in range(MAX_PATROL_LOOPS):
        if is_boss_killed:
            return

        ConsoleLog(BOT_NAME, f"Patrol loop {loop_count + 1}/{MAX_PATROL_LOOPS}")

        for i, (wx, wy) in enumerate(INTERCEPT_PATH):
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
                            ConsoleLog(BOT_NAME, f"Boss spotted! Rushing to ({ax:.0f}, {ay:.0f})")
                            yield from bot.Move._coro_get_path_to(ax, ay)
                            yield from bot.Move._coro_follow_path_to()
                            # We're close, let HeroAI engage.  Wait for kill.
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

        # End of one full loop
        _scan_for_boss()

    ConsoleLog(BOT_NAME, "Patrol loops exhausted -- boss not found", Console.MessageType.Warning)


def _wait_for_boss_kill():
    """Wait for the boss to die after engagement.
    Includes a wall-clock safety timeout."""
    global is_boss_killed

    KILL_TIMEOUT_S = 120  # 2 minutes max
    start_time = time.time()

    while (time.time() - start_time) < KILL_TIMEOUT_S:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        if _check_boss_dead():
            ConsoleLog(BOT_NAME, "Boss killed!")
            return

        # If boss moved out of range, re-approach
        if boss_agent_id > 0:
            try:
                dist = Utils.Distance(Player.GetXY(), Agent.GetXY(boss_agent_id))
                if dist > Range.SafeCompass.value:
                    ConsoleLog(BOT_NAME, "Boss moved out of range -- re-approaching")
                    ax, ay = Agent.GetXY(boss_agent_id)
                    if ax != 0 or ay != 0:
                        yield from bot.Move._coro_get_path_to(ax, ay)
                        yield from bot.Move._coro_follow_path_to()
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Re-approach error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)

    ConsoleLog(BOT_NAME, "Kill timeout reached", Console.MessageType.Warning)


# ===========================================================================
# Custom state wrappers for FSM
# ===========================================================================
def _run_intercept_as_custom_state():
    """Schedules the intercept patrol as a managed coroutine."""
    fsm = bot.config.FSM
    fsm.AddManagedCoroutine("InterceptPatrol", _intercept_patrol)


def _wait_for_intercept_done():
    """Condition function: returns True when the patrol/kill phase is over."""
    if is_boss_killed:
        return True
    if not bot.config.FSM.HasManagedCoroutine("InterceptPatrol"):
        return True
    return False


# ===========================================================================
# State reset between farm loops
# ===========================================================================
def reset_farm_flags():
    global is_boss_killed, is_boss_spotted, boss_agent_id
    is_boss_spotted = False
    is_boss_killed = False
    boss_agent_id = -1
    # Clean up managed coroutines from the previous loop
    try:
        bot.States.RemoveManagedCoroutine("InterceptPatrol")
    except Exception:
        pass
    try:
        bot.States.RemoveManagedCoroutine("BossScanner")
    except Exception:
        pass


# ===========================================================================
# Wipe handling
# ===========================================================================
def _on_party_wipe(bot_ref: "Botting"):
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot_ref.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            bot_ref.config.FSM.resume()
            return

    # Player revived on same map -- jump to combat (6th AddHeader call = _6)
    bot_ref.States.JumpToStepName("[H]Start Combat_6")
    bot_ref.config.FSM.resume()


def OnPartyWipe(bot_ref: "Botting"):
    ConsoleLog(BOT_NAME, "Party wipe detected")
    fsm = bot_ref.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot_ref))


# ===========================================================================
# Main farm routine
# ===========================================================================
def farm_mindsquall(bot: Botting) -> None:
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    # Events
    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))

    bot.States.AddHeader(BOT_NAME)
    bot.Templates.Multibox_Aggressive()
    bot.Properties.Disable("auto_inventory_management")

    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_TO_TRAVEL)
    bot.Party.SetHardMode(False)

    # Background scanner: runs continuously once we enter the explorable
    bot.States.AddManagedCoroutine('BossScanner', _background_boss_scanner)

    bot.States.AddHeader(f'{BOT_NAME}_loop')
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(16429.57, 13491.29, target_map_id=EXPLORABLE_MAP_ID)
    bot.Properties.Enable('pause_on_danger')  # Re-enable ASAP after zone exit
    bot.Wait.ForTime(4000)

    # Get bounty from NPC (in Magus Stones, near zone-in point)
    bot.States.AddHeader('Get Bounty')
    bot.Move.XYAndInteractNPC(BOUNTY_NPC_XY[0], BOUNTY_NPC_XY[1])
    bot.Multibox.SendDialogToTarget(BOUNTY_DIALOG)
    bot.Wait.ForTime(1000)

    # Handle mobs near zone-in before starting patrol
    bot.States.AddHeader('Zone Entry Safety')
    bot.States.AddCustomState(_run_zone_entry_as_custom_state, "Handle exit mobs")
    bot.Wait.ForTime(2000)

    bot.States.AddHeader("Start Combat")
    # Schedule the intercept patrol, then wait for completion
    bot.States.AddCustomState(_run_intercept_as_custom_state, "Launch Intercept Patrol")
    bot.Wait.UntilCondition(_wait_for_intercept_done, duration=1000)

    # Loot phase
    bot.Wait.ForTime(10000)

    # Resign and loop — retry resign to ensure all slaves accept
    bot.Multibox.ResignParty()
    bot.Wait.ForTime(5000)
    bot.Multibox.ResignParty()  # Retry in case any slave missed first resign
    bot.States.AddCustomState(reset_farm_flags, "Reset Farm detections")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(10000)
    bot.States.JumpToStepName(f"[H]{BOT_NAME}_loop_3")


bot.SetMainRoutine(farm_mindsquall)


def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("The Mindsquall Farmer v2", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("multi-account bot to farm The Mindsquall (Kirin boss)")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.bullet_text("Normal Mode for faster runs.")
    PyImGui.spacing()
    PyImGui.bullet_text("Features (v2):")
    PyImGui.bullet_text("- Zone entry safety handler")
    PyImGui.bullet_text("- Real-time boss scanning (model ID + HasBossGlow)")
    PyImGui.bullet_text("- 24-point expanded patrol path")
    PyImGui.bullet_text("- Danger check: stops if overwhelmed")
    PyImGui.bullet_text("- Wipe handler with resume")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")
    PyImGui.bullet_text("- Bounty NPC interaction preserved")
    PyImGui.spacing()
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Original by Icefox & LeZgw")
    PyImGui.bullet_text("v2 by Claude")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window()


if __name__ == "__main__":
    main()
