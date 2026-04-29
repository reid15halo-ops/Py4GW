import os
import importlib.util

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import (Botting, ConsoleLog, Routines, Agent, Player,
                          AgentArray, Range, Timer)

# Load ScenarioLearner from the same directory (widget loader doesn't add script dir to sys.path)
_SCRIPT_DIR = os.path.join(Py4GW.Console.get_projects_path(), "Bots", "Farmers", "Weapons", "Green_Unique", "Sword")
if not os.path.isdir(_SCRIPT_DIR):
    _SCRIPT_DIR = os.path.join(Py4GW.Console.get_projects_path(), "Widgets", "Automation", "Bots", "Farmers", "Weapons", "Green_Unique", "Sword")
_spec = importlib.util.spec_from_file_location(
    "ScenarioLearner", os.path.join(_SCRIPT_DIR, "ScenarioLearner.py"))
_sl_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sl_mod)
ScenarioLearner = _sl_mod.ScenarioLearner

BOT_NAME = "Rajazan's Fervor"
MODULE_ICON = "Textures\\Module_Icons\\Rajazan's Fervor.png"
bot = Botting(BOT_NAME)

# Map IDs
HARVEST_TEMPLE = 277  # Outpost
UNWAKING_WATERS = 227  # Explorable (Cultist Rajazan spawn)

# Kill route through Unwaking Waters (clockwise sweep)
KILLING_PATH = [
    (3573.0, 1788),
    (4670, 2949),
    (4731, 4260),
    (3939, 5265),
    (3116, 5570),
    (1409, 5723),
    (861, 2827),
    (2324, 725),
    (3878, 176),
    (5310, 450),
    (6926, 1304),
    (8602, 4138),
    (7779, 5905),
    (5006, 8008),
    (526, 8222),
    (-1485, 7155),
    (-3618, 6180),
    (-6605, 5235),
    (-8708, 3955),
    (-9104, 2126),
]

# =========================
# SCENARIO LEARNER SETUP
# =========================

learner = ScenarioLearner(
    "Rajazan",
    save_path=os.path.join(os.path.dirname(__file__), "rajazan_learner.json"),
    min_trials=3,
)

# Define strategies to evaluate
learner.add_strategy("conservative", {
    "pull_cap": 5, "hard_mode": True, "post_combat_wait": 6000,
})
learner.add_strategy("balanced", {
    "pull_cap": 8, "hard_mode": True, "post_combat_wait": 4000,
})
learner.add_strategy("aggressive", {
    "pull_cap": 12, "hard_mode": True, "post_combat_wait": 3000,
})
learner.add_strategy("safe_hm", {
    "pull_cap": 6, "hard_mode": True, "post_combat_wait": 5000,
})

# Active strategy params (set before each run)
active_params = {"pull_cap": 8, "hard_mode": True, "post_combat_wait": 4000}
active_strategy_name = "balanced"
run_timer = Timer()


# =========================
# PULL LOGIC
# =========================

def get_nearby_enemy_count():
    """Count alive enemies in aggro range."""
    px, py = Player.GetXY()
    enemies = AgentArray.GetEnemyArray()
    enemies = AgentArray.Filter.ByDistance(enemies, (px, py), Range.Spellcast.value)
    enemies = AgentArray.Filter.ByAttribute(enemies, 'IsAlive')
    return len(enemies)


def should_pause_movement():
    """Pause path movement when too many enemies are pulled.
    Returns True to pause (party fights current enemies before pulling more)."""
    count = get_nearby_enemy_count()
    return count >= active_params["pull_cap"]


# =========================
# WIPE HANDLING
# =========================

run_failed = False

def _on_party_wipe(bot: "Botting"):
    global run_failed
    run_failed = True
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            bot.config.FSM.resume()
            return
    # After resurrection at shrine, resume combat
    bot.States.JumpToStepName("[H]Combat_3")
    bot.config.FSM.resume()


def OnPartyWipe(bot: "Botting"):
    ConsoleLog("on_party_wipe", "party wipe detected")
    fsm = bot.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot))


# =========================
# STRATEGY SELECTION
# =========================

def select_next_strategy():
    """Pick the next strategy via UCB1 and apply its params."""
    global active_params, active_strategy_name, run_failed
    run_failed = False

    strategy = learner.select_strategy()
    active_strategy_name = strategy["name"]
    active_params.update(strategy["params"])

    ConsoleLog(BOT_NAME,
               f"Strategy: {active_strategy_name} | "
               f"pull_cap={active_params['pull_cap']} "
               f"HM={active_params['hard_mode']}",
               Py4GW.Console.MessageType.Info)
    run_timer.Reset()
    run_timer.Start()


def record_run_result(success: bool):
    """Record this run's result for the active strategy."""
    elapsed = run_timer.GetElapsedTime() if success else 0
    run_timer.Stop()

    learner.record_result(
        active_strategy_name,
        success=success,
        run_time_ms=int(elapsed),
    )
    learner.save()

    result = "WIN" if success else "FAIL"
    ConsoleLog(BOT_NAME,
               f"[{result}] {active_strategy_name} | "
               f"Rate: {learner.strategies[active_strategy_name].success_rate:.0%} | "
               f"Total: {learner.total_runs} runs",
               Py4GW.Console.MessageType.Info)


# =========================
# MAIN ROUTINE
# =========================

def Routine(bot: Botting) -> None:
    """Rajazan's Fervor — Adaptive multibox HM farm"""

    # ===== CONFIGURATION =====
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    bot.Templates.Multibox_Aggressive()
    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))
    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=HARVEST_TEMPLE)
    bot.Properties.Disable("auto_inventory_management")
    # =========================

    # Select strategy for this run
    select_next_strategy()
    bot.Party.SetHardMode(active_params["hard_mode"])

    bot.States.AddHeader("Exit Outpost")
    bot.Move.XYAndExitMap(3200, 2499, target_map_id=UNWAKING_WATERS)
    bot.Wait.ForMapLoad(UNWAKING_WATERS)
    bot.Wait.ForTime(2000)

    bot.States.AddHeader("Combat")
    bot.Move.FollowAutoPath(
        KILLING_PATH,
        "Rajazan's Fervor Kill Route",
        custom_pause_fn=should_pause_movement,
    )
    bot.Wait.UntilOutOfCombat()
    bot.Wait.ForTime(active_params["post_combat_wait"])

    # Record result (success = reached resign without wipe flag)
    record_run_result(success=(not run_failed))

    bot.States.AddHeader("Resign and Return to Outpost")
    bot.Multibox.ResignParty()
    bot.Wait.ForMapToChange(target_map_id=HARVEST_TEMPLE)
    bot.UI.PrintMessageToConsole(BOT_NAME, "Run complete - Restarting...")
    bot.States.JumpToStepName("[H]Exit Outpost_2")


bot.SetMainRoutine(Routine)


# =========================
# UI
# =========================

def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Rajazan's Fervor Farmer bot", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("Adaptive multibox farm with scenario learning")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.spacing()
    PyImGui.bullet_text(f"Active: {active_strategy_name} "
                        f"(cap={active_params['pull_cap']})")
    best = learner.get_best_strategy()
    if best:
        PyImGui.bullet_text(f"Best: {best['name']} ({best['success_rate']:.0%})")
    PyImGui.spacing()

    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Xan")
    PyImGui.bullet_text("Contributors: LeZgw")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window()

    # Draw learner stats in bot's debug section
    if hasattr(bot.UI, '_window_open') and bot.UI._window_open:
        learner.draw_stats_table()


if __name__ == "__main__":
    main()
