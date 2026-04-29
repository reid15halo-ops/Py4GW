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

BOT_NAME = "Darkroot's Daggers Farm"
MODULE_NAME = "Darkroot's Daggers"
MODULE_ICON = "Textures\\Module_Icons\\Darkroot's Daggers.png"

# ---------------------------------------------------------------------------
# Map constants
# Brauer Academy (outpost 230) exits to Ferndale (explorable 209).
# Darkroot is a Warden of Earth boss that spawns in the western area of
# Ferndale.  He drops Darkroot's Daggers (unique daggers).
# ---------------------------------------------------------------------------
OUTPOST_TO_TRAVEL = 230
COORD_TO_EXIT_MAP = (-4494.75, 4760.00)
EXPLORABLE_MAP_ID = 209

# ---------------------------------------------------------------------------
# Darkroot model ID.  Warden of Earth boss in Ferndale, Echovald Forest.
# If this ID is incorrect for your GW build, the bot falls back to generic
# boss detection (any boss-level enemy near the patrol endpoint).
# ---------------------------------------------------------------------------
DARKROOT_MODEL_ID = 5138

# ---------------------------------------------------------------------------
# Patrol path: expanded route from Brauer Academy exit through western
# Ferndale toward Darkroot's known spawn locations.
#
# The original bot only had 2 waypoints.  This expanded path adds
# intermediate points so the party doesn't skip over aggro bubbles, and
# covers a wider area to intercept Darkroot's patrol circuit.
# ---------------------------------------------------------------------------
PATROL_PATH: list[tuple[float, float]] = [
    # Exit area -- move away from the outpost portal
    (-5500.0,   4200.0),
    (-7000.0,   3500.0),
    (-8500.0,   2800.0),
    # Head southwest toward the Warden patrol area
    (-10000.0,  1800.0),
    (-11500.0,  800.0),
    (-13000.0, -200.0),
    # Approach the western spawn zone
    (-14500.0, -1500.0),
    (-16000.0, -3000.0),
    (-17500.0, -4500.0),
    # Near original waypoint 1 -- the core farming area
    (-19123.88, -6413.41),
    # Swing north toward the second original waypoint
    (-20000.0, -5500.0),
    (-20367.02, -3802.24),
    # Extend north for wider coverage
    (-20000.0, -2500.0),
    (-19500.0, -1500.0),
    # Loop back south for a second pass
    (-19800.0, -3000.0),
    (-19500.0, -5000.0),
    (-19123.88, -6413.41),
]

# ---------------------------------------------------------------------------
# Danger thresholds.  Ferndale has Wardens, Stone Scale Kirins, and
# various Echovald mobs.  We pause movement if the team is overwhelmed.
# ---------------------------------------------------------------------------
DANGER_ENEMY_THRESHOLD = 8
DANGER_HP_THRESHOLD = 0.50
DANGER_CHECK_INTERVAL_MS = 2000

# ---------------------------------------------------------------------------
# Zone entry constants.  Mobs may be near the Brauer Academy exit.
# ---------------------------------------------------------------------------
ZONE_ENTRY_SAFE_POINT = PATROL_PATH[2]  # 3rd waypoint -- clear of portal
ZONE_ENTRY_AGGRO_RADIUS = Range.Earshot.value
ZONE_ENTRY_SMALL_PACK = 4
ZONE_ENTRY_WAIT_MAX_MS = 30000

# ---------------------------------------------------------------------------
# State shared between coroutines.  All access is single-threaded within
# one client, so no locking is needed.
# ---------------------------------------------------------------------------
is_boss_spotted = False
is_boss_killed = False
boss_agent_id = -1


bot = Botting(BOT_NAME)


# ===========================================================================
# Helper: count nearby enemies within a given radius
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
# Helper: scan compass for Darkroot
# ===========================================================================
def _scan_for_boss():
    """Scan enemy array within compass range for Darkroot (model ID 5138).
    Returns (found: bool, agent_id: int).  Safe to call when no enemies exist."""
    global is_boss_spotted, boss_agent_id

    if is_boss_spotted and boss_agent_id > 0:
        # Already tracking -- verify still valid
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
                if Agent.GetModelID(enemy_id) == DARKROOT_MODEL_ID:
                    is_boss_spotted = True
                    boss_agent_id = enemy_id
                    ConsoleLog(BOT_NAME, f"Darkroot spotted! Agent ID: {enemy_id}")
                    return True, enemy_id
            except Exception:
                continue  # Stale agent IDs are expected
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Boss scan error: {e}", Console.MessageType.Warning)

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
    except Exception:
        return False


# ===========================================================================
# Helper: check if boss is dead
# ===========================================================================
def _check_boss_dead():
    """Returns True if we have spotted and confirmed Darkroot is dead."""
    global is_boss_killed
    if is_boss_killed:
        return True
    if is_boss_spotted and boss_agent_id > 0:
        try:
            if Agent.IsDead(boss_agent_id):
                is_boss_killed = True
                ConsoleLog(BOT_NAME, "Darkroot confirmed dead")
                return True
        except Exception:
            pass
    return False


# ===========================================================================
# Zone entry handler: handle mobs near Brauer Academy exit
# ===========================================================================
def _handle_zone_entry_mobs():
    """Coroutine: after zoning into Ferndale, handle any mob pack near the
    outpost exit before starting the patrol.

    Logic:
    1. Wait for map to be fully ready
    2. Scan for enemies within earshot (aggro range)
    3. If NO enemies: proceed immediately
    4. If SMALL pack (<=4): stand and let HeroAI fight
    5. If LARGE pack (>4): retreat toward safe point and let mobs deaggro
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

    # Large pack -- retreat toward safe point along the farm route
    ConsoleLog(BOT_NAME, f"Zone entry: large pack ({nearby}), retreating to safe point")
    sx, sy = ZONE_ENTRY_SAFE_POINT
    try:
        yield from bot.Move._coro_get_path_to(sx, sy)
        yield from bot.Move._coro_follow_path_to()
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Zone entry retreat error: {e}", Console.MessageType.Warning)

    # Wait for combat to resolve after dragging mobs
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
# Background scanner: continuously scan for Darkroot + death detection
# Runs alongside the FSM to detect the boss at any point during movement.
# ===========================================================================
def _background_boss_scanner():
    """Managed coroutine that runs every second, scanning for Darkroot
    and checking if he has died (e.g. killed by other mobs en route)."""
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
                        ConsoleLog(BOT_NAME, "Darkroot died (detected by scanner)")
                except Exception:
                    pass
                yield from Routines.Yield.wait(1000)
                continue

            # Scan for the boss
            _scan_for_boss()

        except Exception as e:
            ConsoleLog(BOT_NAME, f"Scanner error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)


# ===========================================================================
# Intercept patrol: walk the path scanning for Darkroot at each step
# ===========================================================================
def _intercept_patrol():
    """Coroutine: walk patrol path, scanning for Darkroot at each step.
    When the boss is spotted, break from patrol and move toward him.
    When the team is overwhelmed, pause until combat resolves."""
    global is_boss_spotted, is_boss_killed, boss_agent_id

    MAX_PATROL_LOOPS = 3  # Maximum full loops before giving up

    for loop_count in range(MAX_PATROL_LOOPS):
        if is_boss_killed:
            return

        ConsoleLog(BOT_NAME, f"Patrol loop {loop_count + 1}/{MAX_PATROL_LOOPS}")

        for i, (wx, wy) in enumerate(PATROL_PATH):
            # --- Guard: map still valid? ---
            if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
                return
            if is_boss_killed:
                return

            # --- Check: boss spotted? Rush to him ---
            if is_boss_spotted and boss_agent_id > 0:
                try:
                    if not Agent.IsDead(boss_agent_id):
                        ax, ay = Agent.GetXY(boss_agent_id)
                        if ax != 0 or ay != 0:
                            ConsoleLog(BOT_NAME, f"Darkroot spotted! Rushing to ({ax:.0f}, {ay:.0f})")
                            yield from bot.Move._coro_get_path_to(ax, ay)
                            yield from bot.Move._coro_follow_path_to()
                            # After reaching him, wait for the kill
                            if not Agent.IsDead(boss_agent_id):
                                yield from _wait_for_boss_kill()
                            return
                    else:
                        is_boss_killed = True
                        return
                except Exception as e:
                    ConsoleLog(BOT_NAME, f"Rush error: {e}", Console.MessageType.Warning)
                    # Reset tracking to avoid error loop on stale agent
                    is_boss_spotted = False
                    boss_agent_id = -1

            # --- Danger check: are we overwhelmed? ---
            if _team_in_danger():
                ConsoleLog(BOT_NAME, "Team overwhelmed -- pausing to fight")
                while _team_in_danger():
                    if not Map.IsMapReady() or Map.IsMapLoading():
                        return
                    yield from Routines.Yield.wait(DANGER_CHECK_INTERVAL_MS)
                # Wait until fully out of combat before resuming
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

    # Exhausted all loops without finding Darkroot
    ConsoleLog(BOT_NAME, "Patrol loops exhausted -- Darkroot not found", Console.MessageType.Warning)


def _wait_for_boss_kill():
    """Wait for Darkroot to die after engagement.  Includes wall-clock
    safety timeout so we don't wait forever if something goes wrong."""
    global is_boss_killed

    KILL_TIMEOUT_S = 120  # 2 minutes max
    start_time = time.time()

    while (time.time() - start_time) < KILL_TIMEOUT_S:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        if _check_boss_dead():
            ConsoleLog(BOT_NAME, "Darkroot killed!")
            return

        # If boss moved out of range, re-approach
        if boss_agent_id > 0:
            try:
                dist = Utils.Distance(Player.GetXY(), Agent.GetXY(boss_agent_id))
                if dist > Range.SafeCompass.value:
                    ConsoleLog(BOT_NAME, "Darkroot moved out of range -- re-approaching")
                    ax, ay = Agent.GetXY(boss_agent_id)
                    if ax != 0 or ay != 0:
                        yield from bot.Move._coro_get_path_to(ax, ay)
                        yield from bot.Move._coro_follow_path_to()
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Re-approach error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)

    ConsoleLog(BOT_NAME, "Kill timeout reached", Console.MessageType.Warning)


# ===========================================================================
# Custom state wrappers for FSM integration
# ===========================================================================
def _run_intercept_as_custom_state():
    """Non-generator wrapper for AddCustomState.  Schedules the intercept
    patrol as a managed coroutine and returns immediately."""
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
        bot.States.RemoveManagedCoroutine("DarkrootScanner")
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

    # Player revived on same map -- jump to recovery step
    bot_ref.States.JumpToStepName("[H]Start Combat_4")
    bot_ref.config.FSM.resume()


def OnPartyWipe(bot_ref: "Botting"):
    ConsoleLog(BOT_NAME, "Party wipe detected")
    fsm = bot_ref.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot_ref))


# ===========================================================================
# Main farm routine
# ===========================================================================
def farm_daggers(bot: Botting) -> None:
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
    bot.States.AddManagedCoroutine('DarkrootScanner', _background_boss_scanner)

    bot.States.AddHeader('Exit To Farm')
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(*COORD_TO_EXIT_MAP, target_map_id=EXPLORABLE_MAP_ID)
    bot.Wait.ForTime(3000)
    bot.Properties.Enable('pause_on_danger')

    # Handle mobs near zone-in before starting patrol
    bot.States.AddHeader('Zone Entry Safety')
    bot.States.AddCustomState(_run_zone_entry_as_custom_state, "Handle exit mobs")
    bot.Wait.ForTime(2000)

    bot.States.AddHeader("Start Combat")
    bot.States.AddCustomState(_run_intercept_as_custom_state, "Launch Intercept Patrol")
    bot.Wait.UntilCondition(_wait_for_intercept_done, duration=1000)

    # Loot phase -- give HeroAI/AutoLoot time to pick up drops
    bot.Wait.ForTime(10000)

    # Resign and loop
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(reset_farm_flags, "Reset Farm detections")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(10000)
    bot.States.JumpToStepName("[H]Exit To Farm_3")


bot.SetMainRoutine(farm_daggers)


# ===========================================================================
# Tooltip for widget manager
# ===========================================================================
def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Darkroot's Daggers Farmer v2", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("Multi-account bot to farm Darkroot's Daggers")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- Brauer Academy outpost (Echovald)")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.spacing()
    PyImGui.bullet_text("Features (v2):")
    PyImGui.bullet_text("- 17-point expanded patrol path")
    PyImGui.bullet_text("- Real-time Darkroot scanning on compass")
    PyImGui.bullet_text("- Zone entry mob handler")
    PyImGui.bullet_text("- Danger check: stops if overwhelmed")
    PyImGui.bullet_text("- Wipe handler with auto-recovery")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")

    PyImGui.spacing()
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Original by Aura")
    PyImGui.bullet_text("Contributors: Wick-Divinus, XLeek")
    PyImGui.bullet_text("v2 improvements by Claude")
    PyImGui.end_tooltip()


# ===========================================================================
# Main entry point (called every frame by widget manager)
# ===========================================================================
def main():
    bot.Update()

    texture_path = MODULE_ICON
    if os.path.exists(texture_path):
        bot.UI.draw_window(icon_path=texture_path)
    else:
        bot.UI.draw_window()


if __name__ == "__main__":
    main()
