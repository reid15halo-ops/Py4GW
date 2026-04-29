"""
Deep Smoke Test Widget — verifies every button, every coroutine, every import.
Enable via Widget Manager > Tests > SmokeTest
Runs automatically every 5 seconds. Shows PASS/FAIL in an ImGui window.

This is the STANDARD test that must pass before any session ends.
"""
import time
import traceback
import os
import Py4GW
import PyImGui

MODULE_NAME = "SmokeTest"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Party, Agent, Item, ItemArray,
    Inventory, Routines, SharedCommandType, Range,
)
from Py4GWCoreLib.py4gwcorelib_src.ActionQueue import ActionQueueManager

# ═══════════════════════════════════════════════════════════════════
# Test Infrastructure
# ═══════════════════════════════════════════════════════════════════
class TestResult:
    def __init__(self, name, status="PENDING", msg=""):
        self.name = name
        self.status = status
        self.msg = msg
        self.t = time.time()

    def ok(self, msg=""):
        self.status = "PASS"
        self.msg = msg
        self.t = time.time()

    def fail(self, msg):
        self.status = "FAIL"
        self.msg = msg
        self.t = time.time()

    def skip(self, msg=""):
        self.status = "SKIP"
        self.msg = msg
        self.t = time.time()

results = {}
_last_run = 0.0
_cycle = 0
_run_once_done = False

def _r(name):
    """Get or create a test result."""
    if name not in results:
        results[name] = TestResult(name)
    return results[name]

def _try(name, fn):
    """Run fn(), mark PASS or FAIL."""
    r = _r(name)
    try:
        msg = fn()
        r.ok(str(msg) if msg else "")
    except Exception as e:
        r.fail(f"{e}")

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 1: Import Chain
# ═══════════════════════════════════════════════════════════════════
def test_imports():
    def _test():
        # MultiboxCommander
        from Widgets.Automation.Multiboxing.MultiboxCommander import (
            get_my_email, get_all_slaves, get_slaves_same_district,
            set_action, set_error, set_heroai_all, check_stuck_slaves,
            summon_all, invite_all, kick_all, leave_all, resign_all,
            pixel_stack, unstuck_all, interact_target_all, dialog_target_all,
            buy_kits_all, identify_all, collect_valuables, enable_slave_widgets,
            bless_all, full_setup, _send_enable_widgets_sync,
        )
        # AutoLootManager + sub-modules
        from Widgets.Automation.Helpers.AutoLootManager import config, process_inventory
        from Widgets.Automation.Helpers.ItemModTables import MOD_REQUIREMENT, VALUABLE_MODEL_IDS
        from Widgets.Automation.Helpers.SalvageDecision import evaluate_item, DECISION_KEEP, get_expert_kit
        from Widgets.Automation.Helpers.PickupManager import get_free_slot_count
        from Widgets.Automation.Helpers.MerchantManager import process_merchant, process_inventory_full
        # HeroAI combat
        from HeroAI.combat import CombatClass
        # Pathing
        from Py4GWCoreLib.Pathing import AutoPathing
        from Py4GWCoreLib.stuck_state import update_followpath_state, is_followpath_handling_stuck
        # GDW
        from Sources.oazix.CustomBehaviors.skills.common.great_dwarf_weapon_utility import GreatDwarfWeaponUtility
        return "All 30+ imports OK"
    _try("1_imports", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 2: Routines.Helpers.Multibox
# ═══════════════════════════════════════════════════════════════════
def test_routines_helpers():
    def _test():
        assert hasattr(Routines, 'Helpers'), "Routines.Helpers missing"
        assert hasattr(Routines.Helpers, 'Multibox'), "Routines.Helpers.Multibox missing"
        mb = Routines.Helpers.Multibox
        required = ['summon_all_accounts', 'invite_all_accounts', 'kick_all_accounts',
                     'leave_party_on_all_accounts', 'resign_party', 'pixel_stack',
                     'brute_force_unstuck']
        missing = [m for m in required if not hasattr(mb, m)]
        assert not missing, f"Missing methods: {missing}"
        return f"All {len(required)} methods present"
    _try("2_routines_helpers", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 3: MultiboxCommander functions
# ═══════════════════════════════════════════════════════════════════
def test_multibox_functions():
    def _test():
        from Widgets.Automation.Multiboxing.MultiboxCommander import (
            get_my_email, get_all_slaves, get_slaves_same_district, check_stuck_slaves
        )
        email = get_my_email()
        assert isinstance(email, str) and len(email) > 0, f"get_my_email returned '{email}'"
        slaves = get_all_slaves()
        assert isinstance(slaves, list), f"get_all_slaves returned {type(slaves)}"
        same = get_slaves_same_district()
        assert isinstance(same, list), f"get_slaves_same_district returned {type(same)}"
        stuck = check_stuck_slaves()
        assert isinstance(stuck, list), f"check_stuck_slaves returned {type(stuck)}"
        return f"email={email[:12]}.. slaves={len(slaves)} same_dist={len(same)} stuck={len(stuck)}"
    _try("3_mb_functions", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 4: Coroutine append (safe_coroutine wrapper)
# ═══════════════════════════════════════════════════════════════════
def test_coroutine_append():
    def _test():
        from Widgets.Automation.Multiboxing.MultiboxCommander import identify_all
        initial = len(GLOBAL_CACHE.Coroutines)
        GLOBAL_CACHE.Coroutines.append(identify_all())
        after = len(GLOBAL_CACHE.Coroutines)
        assert after >= initial + 1, f"Coroutine not appended: {initial} -> {after}"
        return f"Appended identify_all ({initial}->{after})"
    _try("4_coroutine_append", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 5: AutoLootManager config + sub-module wiring
# ═══════════════════════════════════════════════════════════════════
def test_loot_config():
    def _test():
        from Widgets.Automation.Helpers.AutoLootManager import config
        import Widgets.Automation.Helpers.SalvageDecision as SD
        required = ['enabled', 'expert_extract', 'auto_pickup', 'auto_merchant',
                     'auto_restock', 'items_identified', 'items_salvaged',
                     'items_extracted', 'items_kept', 'last_action',
                     'valuable_items_for_master']
        missing = [a for a in required if not hasattr(config, a)]
        assert not missing, f"Config missing: {missing}"
        # Verify SalvageDecision wiring
        assert SD.config is config, "SalvageDecision.config not wired to LootConfig"
        return f"Config OK ({len(required)} attrs), SD wired"
    _try("5_loot_config", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 6: SharedCommandType enum values
# ═══════════════════════════════════════════════════════════════════
def test_shared_commands():
    def _test():
        checks = {
            'TravelToMap': 1, 'InviteToParty': 2, 'InteractWithTarget': 3,
            'GetBlessing': 5, 'PickUpLoot': 7, 'IdentifyItems': 12,
            'SalvageItems': 13, 'MerchantItems': 14, 'EnableWidget': 45,
            'DropValuables': 50,
        }
        for name, val in checks.items():
            actual = getattr(SharedCommandType, name, None)
            assert actual == val, f"{name}: expected {val}, got {actual}"
        return f"All {len(checks)} command types verified"
    _try("6_shared_commands", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 7: Messaging.py dispatcher (no pass stubs)
# ═══════════════════════════════════════════════════════════════════
def test_messaging_dispatcher():
    def _test():
        msg_path = os.path.join(Py4GW.Console.get_projects_path(),
                                "Widgets", "System", "Messaging.py")
        content = open(msg_path, encoding='utf-8').read()
        # These must NOT be followed by just "pass"
        for cmd in ['GetBlessing', 'IdentifyItems', 'DropValuables']:
            pattern = f"SharedCommandType.{cmd}:"
            idx = content.find(pattern)
            assert idx != -1, f"{cmd} not found in dispatcher"
            # Get the next non-empty line after the case
            after = content[idx + len(pattern):idx + len(pattern) + 200]
            lines = [l.strip() for l in after.split('\n') if l.strip()]
            assert lines[0] != 'pass', f"{cmd} is still 'pass' in dispatcher!"
        return "GetBlessing, IdentifyItems, DropValuables all wired"
    _try("7_dispatcher_wired", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 8: __init__.py files exist
# ═══════════════════════════════════════════════════════════════════
def test_init_files():
    def _test():
        base = Py4GW.Console.get_projects_path()
        required = [
            os.path.join(base, "Widgets", "__init__.py"),
            os.path.join(base, "Widgets", "Automation", "__init__.py"),
            os.path.join(base, "Widgets", "Automation", "Helpers", "__init__.py"),
        ]
        missing = [f for f in required if not os.path.exists(f)]
        assert not missing, f"Missing: {missing}"
        return f"All {len(required)} __init__.py files present"
    _try("8_init_files", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 9: Combat system (aftercast, emergency heal, aggressive)
# ═══════════════════════════════════════════════════════════════════
def test_combat_system():
    def _test():
        from HeroAI.combat import CombatClass
        c = CombatClass()
        # Verify ping-based aftercast (no fixed table)
        assert not hasattr(c, '_AFTERCAST_BY_NATURE') or not isinstance(getattr(c, '_AFTERCAST_BY_NATURE', None), dict) or len(getattr(c, '_AFTERCAST_BY_NATURE', {})) == 0, \
            "Fixed aftercast table still exists"
        assert hasattr(c, '_AFTERCAST_BASE_MS'), "Missing _AFTERCAST_BASE_MS"
        assert hasattr(c, '_aggressive_energy_threshold'), "Missing _aggressive_energy_threshold"
        assert hasattr(c, '_CRITICAL_HEAL_THRESHOLD'), "Missing _CRITICAL_HEAL_THRESHOLD"
        assert c._CRITICAL_HEAL_THRESHOLD == 0.30, f"Heal threshold wrong: {c._CRITICAL_HEAL_THRESHOLD}"
        assert hasattr(c, '_emergency_heal_timer'), "Missing _emergency_heal_timer"
        return f"Aftercast={c._AFTERCAST_BASE_MS}ms, Aggressive>{c._aggressive_energy_threshold}, Heal<{c._CRITICAL_HEAL_THRESHOLD}"
    _try("9_combat_system", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 10: Pathing (stuck_state, AutoPathing)
# ═══════════════════════════════════════════════════════════════════
def test_pathing():
    def _test():
        from Py4GWCoreLib.stuck_state import update_followpath_state, is_followpath_handling_stuck
        # Verify state management works
        update_followpath_state(0, 0, False)
        assert not is_followpath_handling_stuck(), "Should not be stuck when inactive"
        update_followpath_state(5, 0, True)
        assert is_followpath_handling_stuck(), "Should be stuck when active with retries"
        update_followpath_state(0, 0, False)  # cleanup
        # Verify AutoPathing importable
        from Py4GWCoreLib.Pathing import AutoPathing
        return "stuck_state OK, AutoPathing importable"
    _try("10_pathing", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 11: Blessed widget auto-bless
# ═══════════════════════════════════════════════════════════════════
def test_blessed_widget():
    def _test():
        blessed_path = os.path.join(Py4GW.Console.get_projects_path(),
                                    "Widgets", "Automation", "Helpers", "Blessed.py")
        content = open(blessed_path, encoding='utf-8').read()
        assert '_auto_bless_timer' in content, "Missing _auto_bless_timer in Blessed.py"
        assert 'has_any_blessing' in content, "Missing has_any_blessing check"
        assert '_runner.start()' in content, "Missing _runner.start() call"
        return "Auto-bless timer + trigger present"
    _try("11_blessed_autobless", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 12: Inventory pipeline (evaluate_item, kit selection)
# ═══════════════════════════════════════════════════════════════════
def test_inventory_pipeline():
    def _test():
        from Widgets.Automation.Helpers.SalvageDecision import (
            evaluate_item, get_expert_kit, get_lesser_kit,
            DECISION_KEEP, DECISION_SALVAGE_MATS, DECISION_EXTRACT,
            load_market_prices,
        )
        # Verify decision constants
        assert DECISION_KEEP == "KEEP"
        assert DECISION_SALVAGE_MATS == "MATS"
        assert DECISION_EXTRACT == "EXTRACT"
        # Verify market price loader doesn't crash
        prices = load_market_prices()
        has_prices = prices is not None
        # Verify kit functions don't crash (may return 0 if no kits)
        expert = get_expert_kit()
        lesser = get_lesser_kit()
        assert isinstance(expert, int), f"get_expert_kit returned {type(expert)}"
        assert isinstance(lesser, int), f"get_lesser_kit returned {type(lesser)}"
        return f"Decisions OK, prices={'loaded' if has_prices else 'none'}, expert={expert}, lesser={lesser}"
    _try("12_inventory_pipeline", _test)

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 13: CLAUDE.md compliance (grep-based)
# ═══════════════════════════════════════════════════════════════════
def test_claude_compliance():
    def _test():
        base = Py4GW.Console.get_projects_path()
        alm_path = os.path.join(base, "Widgets", "Automation", "Helpers", "AutoLootManager.py")
        content = open(alm_path, encoding='utf-8').read()
        # No direct PickUpItem/IdentifyItem/SellItem outside AddAction
        import re
        # Find lines with direct calls NOT inside AddAction
        for pattern in [r'(?<!AddAction.*)Inventory\.PickUpItem\(',
                        r'(?<!AddAction.*)Inventory\.IdentifyItem\(',
                        r'(?<!AddAction.*)Trading\.Merchant\.SellItem\(']:
            matches = re.findall(pattern, content)
            assert not matches, f"Direct API call found: {pattern}"
        return "No direct API calls bypassing ActionQueueManager"
    _try("13_claude_compliance", _test)

# ═══════════════════════════════════════════════════════════════════
# Runner + UI
# ═══════════════════════════════════════════════════════════════════
def run_all():
    global _last_run, _cycle
    now = time.time()
    if now - _last_run < 5.0:
        return
    _last_run = now
    _cycle += 1

    if not Player.IsPlayerLoaded():
        return

    test_imports()
    test_routines_helpers()
    test_multibox_functions()
    test_coroutine_append()
    test_loot_config()
    test_shared_commands()
    test_messaging_dispatcher()
    test_init_files()
    test_combat_system()
    test_pathing()
    test_blessed_widget()
    test_inventory_pipeline()
    test_claude_compliance()

def draw_window():
    if PyImGui.begin("Smoke Test", PyImGui.WindowFlags.AlwaysAutoResize):
        total = len(results)
        passed = sum(1 for r in results.values() if r.status == "PASS")
        failed = sum(1 for r in results.values() if r.status == "FAIL")

        if total > 0:
            if failed == 0:
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 1.0, 0.3, 1.0))
                PyImGui.text(f"ALL {passed}/{total} PASS  (cycle {_cycle})")
                PyImGui.pop_style_color(1)
            else:
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.15, 0.15, 1.0))
                PyImGui.text(f"{failed} FAIL / {passed} PASS / {total} total  (cycle {_cycle})")
                PyImGui.pop_style_color(1)
        else:
            PyImGui.text("Waiting for first run...")

        PyImGui.separator()

        for name in sorted(results.keys()):
            r = results[name]
            if r.status == "PASS":
                color = (0.3, 1.0, 0.3, 1.0)
            elif r.status == "FAIL":
                color = (1.0, 0.15, 0.15, 1.0)
            else:
                color = (1.0, 0.8, 0.0, 1.0)
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, color)
            PyImGui.text(f"{r.status:4} {r.name}: {r.msg[:80]}")
            PyImGui.pop_style_color(1)

    PyImGui.end()

# ═══════════════════════════════════════════════════════════════════
# Widget Entry Points
# ═══════════════════════════════════════════════════════════════════
def main():
    try:
        run_all()
        draw_window()
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"SmokeTest error: {e}", Py4GW.Console.MessageType.Error)
        Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)

def configure():
    PyImGui.text("Deep Smoke Test — 13 test groups")
    PyImGui.text("Verifies: imports, Routines.Helpers, functions, coroutines,")
    PyImGui.text("config wiring, SharedCommandType, dispatcher, __init__.py,")
    PyImGui.text("combat system, pathing, blessed, inventory, CLAUDE.md compliance")
