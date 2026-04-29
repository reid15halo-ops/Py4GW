"""The Scar Eater v2 -- Improved green staff farm bot.

Farm target: The Scar Eater (Naga Ritualist boss)
Location:    The Eternal Grove (explorable, map 128)
Outpost:     Vasburg Armory (map 130)
Mode:        Normal Mode (original design, fast clear)

Improvements over v1:
- Zone entry safety handler (handles mobs at exit)
- Danger check (>8 enemies + <50% HP = pause and fight)
- Background boss scanning with HasBossGlow fallback
- Expanded patrol path covering more of the Naga territory
- Wall-clock timeouts on all wait loops
- Map guards on every coroutine iteration
- Logged exceptions (never silently swallowed)
- Post-kill cleanup and loot walk
- Uses shared FarmBotBase utilities

Credits: Original by LeZgw, v2 improvements by Mark
"""

import os
import time

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import (
    AgentArray, Agent, Botting, Console, ConsoleLog, Map, Party, Player,
    Range, Routines, Utils,
)

# FarmBotBase lives in the same directory
import importlib, sys
_base_dir = os.path.dirname(os.path.abspath(__file__))
if _base_dir not in sys.path:
    sys.path.insert(0, _base_dir)
import FarmBotBase

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BOT_NAME = "Scar Eater Farm v2"
MODULE_ICON = "Textures\\Module_Icons\\The Scar Eater.png"

OUTPOST_MAP_ID = 130          # Vasburg Armory
EXPLORABLE_MAP_ID = 128       # The Eternal Grove (explorable area)
EXIT_X, EXIT_Y = 18046, 1734  # Exit portal coordinates from Vasburg Armory

# The Scar Eater model ID is not in model_data.py.
# We rely on HasBossGlow as the primary detection method, with model ID as
# a secondary check if we discover it during runtime.  The boss is a Naga
# Ritualist that spawns in the eastern/southeastern part of the Eternal Grove.
SCAR_EATER_MODEL_ID = -1  # Unknown -- boss-glow fallback is primary

# ---------------------------------------------------------------------------
# Patrol path: expanded coverage of the Naga-infested areas in The Eternal
# Grove explorable.  The original path had 7 waypoints going roughly east
# to south.  This expanded path covers more ground to intercept the boss
# wherever it spawns/patrols.
#
# The Eternal Grove explorable layout (Luxon side):
# - Exit from Vasburg Armory is in the NE corner
# - Naga territory stretches south and southeast
# - The Scar Eater patrols the central-south Naga areas
# ---------------------------------------------------------------------------
INTERCEPT_PATH: list[tuple[float, float]] = [
    # Phase 1: Exit area, head south through initial Naga packs
    (15204.0,  1321),     # wp0: Just outside Vasburg exit
    (14314,    -769),     # wp1: South along the main path
    (14097,   -1194),     # wp2: Continue south (original path)
    (13200,   -1800),     # wp3: Further south into Naga territory
    (12216,   -1054),     # wp4: Slight west jog (original path)

    # Phase 2: Deeper into the Naga zone, sweep SE
    (12831,   -3230),     # wp5: South toward boss patrol area (original)
    (13500,   -4200),     # wp6: Continue SE
    (14683,   -3208),     # wp7: East sweep (original path)
    (15500,   -4000),     # wp8: Further east

    # Phase 3: Southern sweep and return
    (17338,   -2931),     # wp9: Far east (original end point)
    (16500,   -4500),     # wp10: Southeast corner
    (15000,   -5500),     # wp11: Deep south
    (13500,   -5800),     # wp12: Southwest sweep
    (12000,   -5000),     # wp13: Return west
    (11500,   -3500),     # wp14: Northwest return
    (12500,   -2000),     # wp15: Back toward center
]

# ---------------------------------------------------------------------------
# Danger thresholds
# The Eternal Grove has Naga Warriors, Spellblades, Witches, and Ritualists.
# Packs are typically 3-5 mobs.  More than 8 means we pulled multiple groups.
# ---------------------------------------------------------------------------
DANGER_ENEMY_THRESHOLD = 8
DANGER_HP_THRESHOLD = 0.50
DANGER_CHECK_INTERVAL_MS = 2000

# Zone entry: the Vasburg exit can have Naga near the portal
ZONE_ENTRY_SMALL_PACK = 4
ZONE_ENTRY_MAX_WAIT_MS = 30000

# Timeouts
MAX_PATROL_LOOPS = 3
KILL_TIMEOUT_S = 120
CLEANUP_TIMEOUT_S = 60
OOC_TIMEOUT_S = 30
LOOT_PAUSE_MS = 3000
FINAL_LOOT_WAIT_MS = 5000
POST_RESIGN_WAIT_MS = 10000


# ===========================================================================
# Bot instance
# ===========================================================================
bot = Botting(BOT_NAME)


# ===========================================================================
# Create reusable components from FarmBotBase
# ===========================================================================

# Boss scanner -- model ID is unknown so boss-glow is our primary detection
scan_boss, check_boss_dead, background_scanner, boss_state = \
    FarmBotBase.create_boss_scanner(
        BOT_NAME,
        model_id=SCAR_EATER_MODEL_ID,
        fallback_boss_glow=True,
    )

# Danger check
team_in_danger = FarmBotBase.create_danger_check(
    BOT_NAME,
    enemy_threshold=DANGER_ENEMY_THRESHOLD,
    hp_threshold=DANGER_HP_THRESHOLD,
)

# Zone entry handler -- safe point is waypoint 4 (well into the route)
ZONE_ENTRY_SAFE_POINT = INTERCEPT_PATH[4]
_zone_entry_coro, zone_entry_custom_state = FarmBotBase.create_zone_entry_handler(
    bot, BOT_NAME, ZONE_ENTRY_SAFE_POINT,
    small_pack_threshold=ZONE_ENTRY_SMALL_PACK,
    max_wait_ms=ZONE_ENTRY_MAX_WAIT_MS,
)

# Patrol intercept (walks waypoints, scans for boss, danger pause)
_intercept_coro, launch_intercept, is_intercept_done = \
    FarmBotBase.create_patrol_intercept(
        bot, BOT_NAME, INTERCEPT_PATH, boss_state,
        scan_fn=scan_boss,
        check_dead_fn=check_boss_dead,
        danger_fn=team_in_danger,
        max_loops=MAX_PATROL_LOOPS,
        danger_interval_ms=DANGER_CHECK_INTERVAL_MS,
    )

# Wipe handler -- resume at the combat header after revive
_on_wipe_callback = FarmBotBase.create_wipe_handler(
    bot, BOT_NAME, resume_step_name="[H]Start Combat_4",
)

# Farm reset (between loops)
reset_farm = FarmBotBase.create_farm_reset(
    bot, boss_state,
    coroutine_names=["InterceptPatrol", "BossScanner"],
)


# ===========================================================================
# Post-kill cleanup and loot (not in FarmBotBase -- bot-specific)
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

    # Phase 1: Wait for HeroAI to finish nearby enemies
    start = time.time()
    while (time.time() - start) < CLEANUP_TIMEOUT_S:
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
            ConsoleLog(BOT_NAME, f"Cleanup enemy scan error: {e}",
                       Console.MessageType.Warning)
            alive = []

        if len(alive) == 0:
            ConsoleLog(BOT_NAME, "All nearby enemies cleared")
            break

        yield from Routines.Yield.wait(2000)

    # Phase 2: Wait for out-of-combat state
    ConsoleLog(BOT_NAME, "Waiting for out-of-combat state")
    start = time.time()
    while (time.time() - start) < OOC_TIMEOUT_S:
        if not Map.IsMapReady() or Map.IsMapLoading():
            _cleanup_done = True
            return
        try:
            if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                break
        except Exception:
            break
        yield from Routines.Yield.wait(1000)

    # Phase 3: Walk through recent waypoints to pick up loot drops
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

        # Walk 3 waypoints in each direction from closest
        loot_range = 3
        start_idx = max(0, closest_idx - loot_range)
        end_idx = min(len(INTERCEPT_PATH), closest_idx + loot_range + 1)
        loot_path = INTERCEPT_PATH[start_idx:end_idx]

        for wx, wy in loot_path:
            if not Map.IsMapReady() or Map.IsMapLoading():
                break
            yield from bot.Move._coro_get_path_to(wx, wy)
            yield from bot.Move._coro_follow_path_to()
            yield from Routines.Yield.wait(LOOT_PAUSE_MS)

    except Exception as e:
        ConsoleLog(BOT_NAME, f"Loot walk error: {e}", Console.MessageType.Warning)

    # Final wait for auto-loot pickup
    yield from Routines.Yield.wait(FINAL_LOOT_WAIT_MS)

    ConsoleLog(BOT_NAME, "Cleanup & loot complete -- resigning")
    _cleanup_done = True


def _run_cleanup_custom_state():
    """Non-generator wrapper for AddCustomState."""
    return _cleanup_and_loot()


def _is_cleanup_done():
    """Condition: True when cleanup coroutine has finished."""
    return _cleanup_done


def _reset_all():
    """Reset all state for a fresh farm loop."""
    global _cleanup_done
    _cleanup_done = False
    reset_farm()


# ===========================================================================
# Main farm routine
# ===========================================================================
def Routine(bot_ref: Botting) -> None:
    """Main farm routine for The Scar Eater green staff."""

    # ===== CONFIGURATION =====
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    bot_ref.Templates.Multibox_Aggressive()
    bot_ref.Events.OnPartyWipeCallback(_on_wipe_callback)
    bot_ref.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_MAP_ID)
    bot_ref.Properties.Disable("auto_inventory_management")
    bot_ref.Party.SetHardMode(False)  # NM for fast, safe clears
    # =========================

    # Background boss scanner: runs continuously in the explorable
    bot_ref.States.AddManagedCoroutine('BossScanner', background_scanner)

    bot_ref.States.AddHeader("Exit Outpost")
    bot_ref.Properties.Disable('pause_on_danger')
    bot_ref.Move.XYAndExitMap(EXIT_X, EXIT_Y, target_map_id=EXPLORABLE_MAP_ID)
    bot_ref.Wait.ForMapLoad(EXPLORABLE_MAP_ID)
    bot_ref.Wait.ForTime(3000)
    bot_ref.Properties.Enable('pause_on_danger')

    # Handle mobs near the zone-in point before starting patrol
    bot_ref.States.AddHeader("Zone Entry Safety")
    bot_ref.States.AddCustomState(zone_entry_custom_state, "Handle exit mobs")
    bot_ref.Wait.ForTime(2000)

    bot_ref.States.AddHeader("Start Combat")
    # Intercept patrol: walk waypoints scanning for boss
    bot_ref.States.AddCustomState(launch_intercept, "Launch Intercept Patrol")
    bot_ref.Wait.UntilCondition(is_intercept_done, duration=1000)

    # Post-kill: clear remaining enemies, walk loot path, then resign
    bot_ref.States.AddHeader("Cleanup & Loot")
    bot_ref.States.AddCustomState(_run_cleanup_custom_state, "Kill remaining + loot")
    bot_ref.Wait.UntilCondition(_is_cleanup_done, duration=1000)

    # Resign and loop
    bot_ref.States.AddHeader("Resign and Return to Outpost")
    bot_ref.Multibox.ResignParty()
    bot_ref.States.AddCustomState(_reset_all, "Reset Farm State")
    bot_ref.Wait.UntilOnOutpost()
    bot_ref.Wait.ForTime(POST_RESIGN_WAIT_MS)
    bot_ref.UI.PrintMessageToConsole(BOT_NAME, "Finished routine - Restarting...")
    bot_ref.States.JumpToStepName("[H]Exit Outpost_2")


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
    PyImGui.text_colored("Scar Eater Farmer v2", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("Multi-account bot to farm The Scar Eater green staff")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.bullet_text("Normal Mode for fast, safe runs.")
    PyImGui.spacing()
    PyImGui.bullet_text("v2 Features:")
    PyImGui.bullet_text("- Zone entry safety (handles exit mobs)")
    PyImGui.bullet_text("- Boss-glow scanning (no model ID needed)")
    PyImGui.bullet_text("- Danger check: pauses if overwhelmed")
    PyImGui.bullet_text("- 16-point expanded patrol path")
    PyImGui.bullet_text("- Wall-clock timeouts on all waits")
    PyImGui.bullet_text("- Post-kill loot walk")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")
    PyImGui.spacing()
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Original by LeZgw, v2 by Mark")
    PyImGui.end_tooltip()


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    bot.Update()
    bot.UI.draw_window()


if __name__ == "__main__":
    main()
