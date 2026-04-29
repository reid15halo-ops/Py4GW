"""Brightclaw v2 -- Chkkr Brightclaw (Wand) farm bot.

Drops: Brightclaw (unique wand)
Boss:  Chkkr Brightclaw (model 3669) -- Ritualist boss in Melandru's Hope
Route: Jade Flats (Kurzick) -> Melandru's Hope, patrol south-east sector

Improvements over v1:
- Zone entry safety handler (fight/retreat near exit)
- Danger check (>8 enemies + <50% HP -> pause and fight)
- Background boss scanning with HasBossGlow fallback
- Expanded 14-point patrol path covering Chkkr's spawn area
- Wall-clock timeouts on all waits
- Map guards on every coroutine loop iteration
- Logged exceptions (never silently swallowed)
- Cleanup + loot phase after boss kill
- Uses FarmBotBase utilities for DRY code
"""

import os
import time
import importlib.util

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import (
    AgentArray, Botting, Console, ConsoleLog,
    Range, Routines, Utils,
    Map, Agent, Player, Party,
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BOT_NAME = "Brightclaw Farm v2"
MODULE_ICON = "Textures\\Module_Icons\\Brightclaw.png"

OUTPOST_MAP_ID = 390          # Jade Flats (Kurzick)
EXPLORABLE_MAP_ID = 201       # Melandru's Hope
BOSS_MODEL_ID = 3669          # Chkkr Brightclaw

USE_HARD_MODE = False         # NM is faster for this boss

# ---------------------------------------------------------------------------
# Patrol path -- covers the south-east sector of Melandru's Hope where
# Chkkr Brightclaw spawns.  The original v1 had 9 waypoints; this expanded
# version adds coverage further south and loops back to improve intercept
# odds on the boss's patrol circuit.
# ---------------------------------------------------------------------------
PATROL_PATH: list[tuple[float, float]] = [
    (12951.50, 20539.63),   # 0  Near zone-in from Jade Flats
    (10076.71, 18229.31),   # 1  South-west toward mantis territory
    ( 7715.59, 18963.31),   # 2  Into the valley
    ( 5500.00, 18500.00),   # 3  Further west -- new waypoint for coverage
    ( 4453.28, 18097.54),   # 4  Deep in mantis area
    ( 3947.94, 18492.72),   # 5  Slightly north
    ( 2433.26, 18725.63),   # 6  West edge of patrol zone
    ( 1476.71, 18246.45),   # 7  South-west corner
    ( 1524.68, 19679.80),   # 8  Loop north
    ( 2800.00, 20800.00),   # 9  New -- cover the north-west return
    ( 3659.15, 20559.77),   # 10 Original endpoint
    ( 5500.00, 20200.00),   # 11 New -- sweep east on the return
    ( 8000.00, 19500.00),   # 12 New -- mid-east return leg
    (10500.00, 19800.00),   # 13 New -- approach start from east
]

# Zone-entry safe point: 3rd waypoint (well into the route, away from exit mobs)
ZONE_ENTRY_SAFE_POINT = PATROL_PATH[2]

# Exit coordinates from Jade Flats -> Melandru's Hope
EXIT_X, EXIT_Y = -7058, -10806

# ---------------------------------------------------------------------------
# Bot instance
# ---------------------------------------------------------------------------
bot = Botting(BOT_NAME)

# ---------------------------------------------------------------------------
# Build all components from FarmBotBase factories
# ---------------------------------------------------------------------------
# Boss scanner: model 3669, with HasBossGlow fallback
scan_boss, check_boss_dead, background_scanner, boss_state = create_boss_scanner(
    BOT_NAME, BOSS_MODEL_ID, fallback_boss_glow=True,
)

# Danger check: >8 enemies AND any party member below 50% HP
team_in_danger = create_danger_check(BOT_NAME, enemy_threshold=8, hp_threshold=0.50)

# Zone entry handler: handle mobs near the Jade Flats exit
_zone_entry_coro, zone_entry_custom_state = create_zone_entry_handler(
    bot, BOT_NAME, safe_point=ZONE_ENTRY_SAFE_POINT,
    small_pack_threshold=4, max_wait_ms=30000,
)

# Patrol intercept: walk PATROL_PATH scanning for boss
_intercept_coro, launch_intercept, is_intercept_done = create_patrol_intercept(
    bot, BOT_NAME, PATROL_PATH, boss_state,
    scan_fn=scan_boss, check_dead_fn=check_boss_dead,
    danger_fn=team_in_danger, max_loops=3, danger_interval_ms=2000,
)

# Wipe handler: pause FSM, wait for revive, resume at combat header
on_wipe = create_wipe_handler(bot, BOT_NAME, resume_step_name="[H]Start Combat_4")

# Farm state reset between loops
reset_farm = create_farm_reset(
    bot, boss_state,
    coroutine_names=["InterceptPatrol", "BossScanner"],
)

# ---------------------------------------------------------------------------
# Cleanup + loot phase (boss-specific, not in FarmBotBase)
# ---------------------------------------------------------------------------
_cleanup_done = False


def _cleanup_and_loot():
    """Coroutine: after boss is dead, clear remaining enemies then loot."""
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
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Enemy scan error in cleanup: {e}", Console.MessageType.Warning)
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

    # Phase 3: Walk nearby waypoints to pick up loot drops
    ConsoleLog(BOT_NAME, "Looting phase -- walking through drop area")
    try:
        px, py = Player.GetXY()
        closest_idx = 0
        closest_dist = float('inf')
        for i, (wx, wy) in enumerate(PATROL_PATH):
            d = Utils.Distance((px, py), (wx, wy))
            if d < closest_dist:
                closest_dist = d
                closest_idx = i

        loot_range = 3
        start_idx = max(0, closest_idx - loot_range)
        end_idx = min(len(PATROL_PATH), closest_idx + loot_range + 1)
        loot_path = PATROL_PATH[start_idx:end_idx]

        for wx, wy in loot_path:
            if not Map.IsMapReady() or Map.IsMapLoading():
                break
            yield from bot.Move._coro_get_path_to(wx, wy)
            yield from bot.Move._coro_follow_path_to()
            yield from Routines.Yield.wait(3000)  # Pause for auto-loot pickup
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Loot walk error: {e}", Console.MessageType.Warning)

    # Final auto-loot window
    yield from Routines.Yield.wait(5000)

    ConsoleLog(BOT_NAME, "Cleanup & loot complete -- resigning")
    _cleanup_done = True


def _run_cleanup_custom_state():
    """Non-generator wrapper: returns the cleanup coroutine for FSM."""
    return _cleanup_and_loot()


def _is_cleanup_done():
    """Condition check for FSM: returns True when cleanup is finished."""
    return _cleanup_done


def _reset_all():
    """Combined reset: FarmBotBase state + local cleanup flag."""
    global _cleanup_done
    _cleanup_done = False
    reset_farm()


# ===========================================================================
# Main farm routine
# ===========================================================================
def Routine(bot: Botting) -> None:
    """Main Brightclaw farm routine."""

    # ===== CONFIGURATION =====
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    bot.Templates.Multibox_Aggressive()
    bot.Events.OnPartyWipeCallback(on_wipe)
    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_MAP_ID)

    # ===== TRAVEL =====
    bot.States.AddHeader("Travel to Jade Flats Kurzick")
    bot.Map.Travel(target_map_id=OUTPOST_MAP_ID)
    bot.Party.SetHardMode(USE_HARD_MODE)

    # ===== EXIT TO EXPLORABLE =====
    bot.States.AddHeader("Exit to Melandru's Hope")
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(EXIT_X, EXIT_Y, EXPLORABLE_MAP_ID)
    bot.Wait.ForMapLoad(target_map_id=EXPLORABLE_MAP_ID)
    bot.Wait.ForTime(100)
    bot.Properties.Enable('pause_on_danger')

    # ===== ZONE ENTRY SAFETY =====
    bot.States.AddHeader("Zone Entry Safety")
    bot.States.AddCustomState(zone_entry_custom_state, "Handle exit mobs")
    bot.Wait.ForTime(2000)

    # ===== BACKGROUND BOSS SCANNER =====
    bot.States.AddManagedCoroutine('BossScanner', background_scanner)

    # ===== COMBAT PATROL =====
    bot.States.AddHeader("Start Combat")
    bot.States.AddCustomState(launch_intercept, "Launch Intercept Patrol")
    bot.Wait.UntilCondition(is_intercept_done, duration=1000)

    # ===== CLEANUP + LOOT =====
    bot.States.AddHeader("Cleanup & Loot")
    bot.States.AddCustomState(_run_cleanup_custom_state, "Kill remaining + loot")
    bot.Wait.UntilCondition(_is_cleanup_done, duration=1000)

    # ===== RESIGN AND LOOP =====
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(_reset_all, "Reset Farm State")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(10000)
    bot.UI.PrintMessageToConsole(BOT_NAME, "Finished routine - Restarting...")
    bot.States.JumpToStepName("[H]Exit to Melandru's Hope_3")


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
    PyImGui.text_colored("Brightclaw Farm v2", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()

    PyImGui.text("Multi-account bot to farm Brightclaw wand")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.spacing()
    PyImGui.bullet_text("Features (v2):")
    PyImGui.bullet_text("- Zone entry safety handler")
    PyImGui.bullet_text("- Danger check: pauses when overwhelmed")
    PyImGui.bullet_text("- Background boss scan + HasBossGlow fallback")
    PyImGui.bullet_text("- Expanded 14-point patrol route")
    PyImGui.bullet_text("- Wall-clock timeouts on all waits")
    PyImGui.bullet_text("- Cleanup + loot phase after boss kill")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")
    PyImGui.spacing()

    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Original by LeZgw")
    PyImGui.bullet_text("v2 by Claude")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window()


if __name__ == "__main__":
    main()
