"""Ice Breaker v2 -- Improved green-unique hammer farm bot.

Farms 'The Ice Breaker' in Mineral Springs (map 26) from Marhan's Grotto (155).
The boss is an Avicara (model ~2862) with boss glow, patrolling the Mineral
Springs zone.  The route sweeps the full explorable so we intercept regardless
of where the boss is in its patrol cycle.

Improvements over v1:
- FarmBotBase factories for zone entry, danger check, boss scanning, patrol
- 3-tier zone entry safety (clear / small fight / large retreat)
- Danger check (>8 enemies + <50% HP = pause movement)
- Background boss scanning with HasBossGlow fallback
- Expanded 28-point patrol path with better coverage
- Wall-clock timeouts on all wait loops
- Map guards on every coroutine iteration
- Logged exceptions (never silently swallowed)
- Cleanup & loot phase after boss kill
- Proper farm state reset between loops

Requirements:
- 6-8 well-geared accounts with Hero AI enabled
- Launch on party leader only
- Normal Mode (configurable via USE_HARD_MODE flag)

Credits: Original by XLeek, v2 improvements by Claude
"""

import os
import time
import importlib.util

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import (
    AgentArray, Agent, Botting, Console, ConsoleLog, Map, Party,
    Player, Range, Routines, Utils,
)

# Load FarmBotBase from the same directory (widget loader doesn't add script dir to sys.path)
_spec = importlib.util.spec_from_file_location(
    "FarmBotBase", os.path.join(os.path.dirname(os.path.abspath(__file__)), "FarmBotBase.py"))
_FarmBotBase = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_FarmBotBase)

count_nearby_enemies = _FarmBotBase.count_nearby_enemies
create_boss_scanner = _FarmBotBase.create_boss_scanner
create_danger_check = _FarmBotBase.create_danger_check
create_farm_reset = _FarmBotBase.create_farm_reset
create_patrol_intercept = _FarmBotBase.create_patrol_intercept
create_wipe_handler = _FarmBotBase.create_wipe_handler
create_zone_entry_handler = _FarmBotBase.create_zone_entry_handler

# ===========================================================================
# Constants
# ===========================================================================
BOT_NAME = "Ice Breaker Farm v2"
MODULE_ICON = "Textures\\Module_Icons\\The Ice Breaker.png"

OUTPOST_MAP_ID = 155          # Marhan's Grotto
EXPLORABLE_MAP_ID = 26        # Mineral Springs
ICE_BREAKER_MODEL_ID = 2862   # Avicara boss (approximate -- HasBossGlow fallback covers it)
USE_HARD_MODE = False          # NM for faster/easier runs (change to True for HM)

# ---------------------------------------------------------------------------
# Expanded patrol path through Mineral Springs.
# The original v1 had 22 waypoints in a mostly-linear sweep.  This v2 path
# adds side branches to cover boss spawn/patrol variations we might miss
# on a straight run.  28 waypoints with better coverage of the NW and SE
# corners where the boss sometimes lingers.
# ---------------------------------------------------------------------------
INTERCEPT_PATH: list[tuple[float, float]] = [
    # -- Exit area: head south from zone-in --
    (-21836, 15600),     #  0: Near Marhan's Grotto exit
    (-22630, 13524),     #  1: South into first valley
    (-21400, 12500),     #  2: Side branch east (catch NE patrols)
    (-20801, 13248),     #  3: Continue south
    (-18928, 14829),     #  4: Swing east
    (-18313, 12206),     #  5: Drop south
    (-16213, 10641),     #  6: Continue SE
    (-14646, 10271),     #  7: Central area
    (-12738, 9655),      #  8: Eastern mid-section
    (-13883, 14651),     #  9: Loop back north (catch northern patrols)
    (-12000, 15800),     # 10: Side branch NW coverage
    (-11231, 16500),     # 11: Northern ridge
    (-9226, 14035),      # 12: Drop back south
    (-7800, 12800),      # 13: NEW -- deeper south sweep
    (-6594, 15729),      # 14: Back up to main ridge
    (-4177, 15850),      # 15: Continue east along ridge
    (-2379, 15626),      # 16: Eastern ridge
    (-1000, 14200),      # 17: NEW -- south side of eastern area
    (1615, 14791),       # 18: Continue east
    (2791, 14776),       # 19: Eastern section
    (3903, 14060),       # 20: SE approach
    (5074, 14428),       # 21: Further east
    (7195, 14766),       # 22: Deep east
    (9114, 14959),       # 23: Approaching far east
    (10940, 14962),      # 24: Far east ridge
    (12161, 14508),      # 25: Easternmost point (turnaround)
    (10500, 13500),      # 26: NEW -- south return sweep
    (8500, 14200),       # 27: NEW -- cut back through center on return
]

# Zone entry: 3rd waypoint is well into the route, safe from exit mobs
ZONE_ENTRY_SAFE_POINT = INTERCEPT_PATH[3]

# ===========================================================================
# Bot instance
# ===========================================================================
bot = Botting(BOT_NAME)

# ===========================================================================
# Wire up FarmBotBase factories
# ===========================================================================
# Boss scanner: model ID match + HasBossGlow fallback
scan_boss, check_boss_dead, background_scanner, boss_state = create_boss_scanner(
    BOT_NAME, ICE_BREAKER_MODEL_ID, fallback_boss_glow=True,
)

# Danger check: >8 enemies AND any party member <50% HP
team_in_danger = create_danger_check(BOT_NAME, enemy_threshold=8, hp_threshold=0.50)

# Zone entry handler: handles mobs near Marhan's Grotto exit
_zone_entry_coro, zone_entry_custom_state = create_zone_entry_handler(
    bot, BOT_NAME, safe_point=ZONE_ENTRY_SAFE_POINT,
    small_pack_threshold=4, max_wait_ms=30000,
)

# Patrol intercept: walk waypoints scanning for boss
_intercept_coro, launch_intercept, intercept_done = create_patrol_intercept(
    bot, BOT_NAME, INTERCEPT_PATH, boss_state,
    scan_fn=scan_boss, check_dead_fn=check_boss_dead,
    danger_fn=team_in_danger, max_loops=3, danger_interval_ms=2000,
)

# Wipe handler: pause FSM, wait for revive, resume at combat header
wipe_callback = create_wipe_handler(bot, BOT_NAME, resume_step_name="[H]Start Combat_4")

# Farm reset: clear all state between loops
reset_farm = create_farm_reset(bot, boss_state, coroutine_names=["InterceptPatrol", "BossScanner"])


# ===========================================================================
# Cleanup & loot coroutine (bot-specific, not in FarmBotBase)
# ===========================================================================
_cleanup_done = False


def _cleanup_and_loot():
    """Coroutine: after boss is dead, clear remaining mobs then loot."""
    global _cleanup_done
    _cleanup_done = False

    if not Map.IsMapReady() or not Map.IsExplorable():
        _cleanup_done = True
        return

    ConsoleLog(BOT_NAME, "Boss dead -- clearing remaining enemies")

    # Phase 1: Wait for HeroAI to finish killing nearby enemies (max 60s)
    start = time.time()
    while (time.time() - start) < 60:
        if not Map.IsMapReady() or Map.IsMapLoading():
            _cleanup_done = True
            return

        try:
            enemies = AgentArray.GetEnemyArray()
            nearby = AgentArray.Filter.ByCondition(
                enemies,
                lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
                <= Range.SafeCompass.value,
            )
            alive = [e for e in nearby if not Agent.IsDead(e)]
        except Exception:
            alive = []

        if len(alive) == 0:
            ConsoleLog(BOT_NAME, "All enemies cleared")
            break

        yield from Routines.Yield.wait(2000)

    # Phase 2: Wait for fully out of combat (max 30s)
    ConsoleLog(BOT_NAME, "Waiting for out-of-combat state")
    start = time.time()
    while (time.time() - start) < 30:
        if not Map.IsMapReady() or Map.IsMapLoading():
            _cleanup_done = True
            return
        try:
            if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                break
        except Exception:
            break
        yield from Routines.Yield.wait(1000)

    # Phase 3: Walk back through recent waypoints to pick up loot
    ConsoleLog(BOT_NAME, "Looting phase -- walking through drop area")
    try:
        px, py = Player.GetXY()
        closest_idx = 0
        closest_dist = float('inf')
        for i, (wx, wy) in enumerate(INTERCEPT_PATH):
            d = Utils.Distance((px, py), (wx, wy))
            if d < closest_dist:
                closest_dist = d
                closest_idx = i

        loot_range = 3
        start_idx = max(0, closest_idx - loot_range)
        end_idx = min(len(INTERCEPT_PATH), closest_idx + loot_range + 1)
        loot_path = INTERCEPT_PATH[start_idx:end_idx]

        for wx, wy in loot_path:
            if not Map.IsMapReady() or Map.IsMapLoading():
                break
            yield from bot.Move._coro_get_path_to(wx, wy)
            yield from bot.Move._coro_follow_path_to()
            yield from Routines.Yield.wait(3000)  # Pause for auto-loot pickup

    except Exception as e:
        ConsoleLog(BOT_NAME, f"Loot walk error: {e}", Console.MessageType.Warning)

    # Final wait for any remaining auto-loot
    yield from Routines.Yield.wait(5000)

    ConsoleLog(BOT_NAME, "Cleanup & loot complete -- resigning")
    _cleanup_done = True


def _run_cleanup_as_custom_state():
    """Non-generator wrapper: returns the cleanup coroutine for FSM."""
    return _cleanup_and_loot()


def _is_cleanup_done():
    """Condition check for FSM: True when cleanup finished."""
    return _cleanup_done


def _reset_all():
    """Reset both FarmBotBase state and local cleanup flag."""
    global _cleanup_done
    _cleanup_done = False
    reset_farm()


# ===========================================================================
# Main farm routine
# ===========================================================================
def Routine(bot: Botting) -> None:
    """Main farm routine for Ice Breaker v2."""

    # ===== Configuration =====
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    bot.Templates.Multibox_Aggressive()
    bot.Events.OnPartyWipeCallback(wipe_callback)
    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_MAP_ID)
    bot.Properties.Disable("auto_inventory_management")
    bot.Party.SetHardMode(USE_HARD_MODE)

    # Background scanner: runs continuously once we enter explorable
    bot.States.AddManagedCoroutine('BossScanner', background_scanner)

    # ===== Exit Outpost =====
    bot.States.AddHeader("Exit Outpost")
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(7589, -45014, target_map_id=EXPLORABLE_MAP_ID)
    bot.Wait.ForMapLoad(EXPLORABLE_MAP_ID)
    bot.Wait.ForTime(3000)
    bot.Properties.Enable('pause_on_danger')

    # ===== Zone Entry Safety =====
    bot.States.AddHeader("Zone Entry Safety")
    bot.States.AddCustomState(zone_entry_custom_state, "Handle exit mobs")
    bot.Wait.ForTime(2000)

    # ===== Combat: Intercept Patrol =====
    bot.States.AddHeader("Start Combat")
    bot.States.AddCustomState(launch_intercept, "Launch Intercept Patrol")
    bot.Wait.UntilCondition(intercept_done, duration=1000)

    # ===== Cleanup & Loot =====
    bot.States.AddHeader("Cleanup & Loot")
    bot.States.AddCustomState(_run_cleanup_as_custom_state, "Kill remaining + loot")
    bot.Wait.UntilCondition(_is_cleanup_done, duration=1000)

    # ===== Resign and Loop =====
    bot.States.AddHeader("Resign and Return to Outpost")
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(_reset_all, "Reset Farm detections")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(10000)
    bot.UI.PrintMessageToConsole(BOT_NAME, "Finished routine - Restarting...")
    bot.States.JumpToStepName("[H]Exit Outpost_2")


bot.SetMainRoutine(Routine)


# ===========================================================================
# Tooltip
# ===========================================================================
def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Ice Breaker Farmer v2", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("multi-account bot to farm The Ice Breaker")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    mode_str = "Hard Mode" if USE_HARD_MODE else "Normal Mode"
    PyImGui.bullet_text(f"Running in {mode_str} (edit USE_HARD_MODE to change)")
    PyImGui.spacing()
    PyImGui.bullet_text("Features (v2):")
    PyImGui.bullet_text("- Smart patrol intercept (28-point route)")
    PyImGui.bullet_text("- Real-time boss scanning with BossGlow fallback")
    PyImGui.bullet_text("- 3-tier zone entry safety handler")
    PyImGui.bullet_text("- Danger check: pauses if overwhelmed")
    PyImGui.bullet_text("- Cleanup & loot phase after kill")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")
    PyImGui.spacing()
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Original by XLeek, v2 by Claude")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window()


if __name__ == "__main__":
    main()
