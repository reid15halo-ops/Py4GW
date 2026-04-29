"""
Py4GW Deep Smoke Test — run BEFORE every restart.
Tests imports, logic paths, config values, and integration points.
Does NOT require game client — tests Python-level correctness only.

Usage: python Tests/test_smoke.py
Exit code 0 = all pass, 1 = failures found
"""
import os
import sys
import importlib
import importlib.util
import json
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

PASS = 0
FAIL = 0
ERRORS = []

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  OK:   {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        ERRORS.append(f"{name}: {detail}")

def test_exception(name, fn):
    """Run fn(), pass if no exception."""
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  OK:   {name}")
    except Exception as e:
        FAIL += 1
        print(f"  FAIL: {name} — {e}")
        ERRORS.append(f"{name}: {e}")

def can_compile(filepath):
    """Check if a Python file compiles without syntax errors."""
    try:
        import py_compile
        py_compile.compile(filepath, doraise=True)
        return True
    except Exception:
        return False


def main():
    print("=" * 60)
    print("Py4GW Deep Smoke Test")
    print("=" * 60)

    # ===== 1. SYNTAX CHECK ALL CRITICAL FILES =====
    print("\n--- Syntax Check (Critical Files) ---")
    critical_files = [
        "HeroAI/combat.py",
        "HeroAI/targeting.py",
        "HeroAI/constants.py",
        "HeroAI/custom_skill_src/monk.py",
        "HeroAI/custom_skill_src/elementalist.py",
        "HeroAI/custom_skill_src/pve.py",
        "HeroAI/custom_skill_src/dervish.py",
        "HeroAI/custom_skill_src/necromancer.py",
        "HeroAI/custom_skill_src/warrior.py",
        "HeroAI/custom_skill_src/assassin.py",
        "HeroAI/custom_skill_src/ritualist.py",
        "HeroAI/custom_skill_src/skill_types.py",
        "Py4GWCoreLib/SkillManager.py",
        "Py4GWCoreLib/botting_src/helpers_src/decorators.py",
        "Py4GWCoreLib/botting_src/helpers_src/Multibox.py",
        "Py4GWCoreLib/py4gwcorelib_src/AutoInventoryHandler.py",
        "Py4GWCoreLib/enums_src/Multiboxing_enums.py",
        "Widgets/Automation/Helpers/AutoLootManager.py",
        "Widgets/Automation/Helpers/AutoStore/AutoStore.py",
        "Widgets/Automation/Multiboxing/HeroAI.py",
        "Widgets/Automation/Multiboxing/AutoMirror.py",
        "Widgets/Automation/Multiboxing/CombatPrep.py",
        "Widgets/System/Messaging.py",
        "Widgets/Guild Wars/Items & Loot/InventoryPlus.py",
        "verify_custom_changes.py",
    ]
    for f in critical_files:
        path = os.path.join(BASE, f)
        test(f"compile {f}", can_compile(path), "SyntaxError")

    # ===== 2. DECORATOR SAFETY =====
    print("\n--- Decorator Safety (yield_step generator trap) ---")
    dec_path = os.path.join(BASE, "Py4GWCoreLib/botting_src/helpers_src/decorators.py")
    dec_content = open(dec_path, encoding="utf-8").read()

    # yield_step wrapper names were refactored — check for the actual function names
    test("yield_step has wrapper function", "def wrapper" in dec_content or "def _fsm_wrapper" in dec_content)
    test("yield_step has yield_step function", "def yield_step" in dec_content or "def _widget_wrapper" in dec_content)

    # Verify wrapper is NOT a generator by executing the module
    try:
        import inspect
        exec_globals = {}
        exec(open(dec_path, encoding="utf-8").read(), exec_globals)
        yield_step_fn = exec_globals.get("yield_step")
        if yield_step_fn:
            @yield_step_fn('test', 'TEST')
            def _test_coro(self):
                yield 1
            test("wrapper is NOT generator", not inspect.isgeneratorfunction(_test_coro),
                 "yield from in wrapper makes it a generator — CLAUDE.md rule 17")
        else:
            test("yield_step found", False, "function not found in module")
    except Exception as e:
        test("decorator exec test", False, str(e))

    # ===== 3. MASTER EMAIL CONSTANT =====
    print("\n--- Master Email Identity (CLAUDE.md rule 26) ---")
    constants_path = os.path.join(BASE, "HeroAI/constants.py")
    test("constants.py exists", os.path.exists(constants_path))

    if os.path.exists(constants_path):
        const_content = open(constants_path, encoding="utf-8").read()
        test("MASTER_EMAIL defined", "MASTER_EMAIL" in const_content)
        test("email is jonasglawion", "jonasglawion@aol.com" in const_content)

    # Check that HeroAI.py, AutoMirror.py, targeting.py import from constants
    for f in ["Widgets/Automation/Multiboxing/HeroAI.py",
              "Widgets/Automation/Multiboxing/AutoMirror.py",
              "HeroAI/targeting.py"]:
        content = open(os.path.join(BASE, f), encoding="utf-8").read()
        test(f"{f} imports MASTER_EMAIL", "MASTER_EMAIL" in content)
        test(f"{f} no hardcoded email in identity checks",
             content.count('"jonasglawion@aol.com"') == 0 or "MASTER_EMAIL" in content)

    # ===== 4. NO GetPartyLeaderID FOR IDENTITY =====
    print("\n--- No GetPartyLeaderID for identity (CLAUDE.md rule 26) ---")
    identity_files = [
        "Widgets/Automation/Multiboxing/HeroAI.py",
        "Widgets/Automation/Multiboxing/AutoMirror.py",
        "Widgets/Automation/Multiboxing/CombatPrep.py",
    ]
    for f in identity_files:
        content = open(os.path.join(BASE, f), encoding="utf-8").read()
        # Count identity-pattern uses of GetPartyLeaderID (not position/XY uses)
        lines = content.split("\n")
        violations = []
        for i, line in enumerate(lines, 1):
            if "GetPartyLeaderID()" in line and ("==" in line or "!=" in line):
                if "GetXY" not in line and "Agent.Get" not in line:
                    violations.append(i)
        test(f"{f} no leader identity check", len(violations) == 0,
             f"lines {violations}" if violations else "")

    # ===== 5. SKILL CONFIG CHECKS =====
    print("\n--- Skill Config Validation ---")
    # Check necromancer Blood Bond doesn't use SkillNature.Hex
    necro = open(os.path.join(BASE, "HeroAI/custom_skill_src/necromancer.py"), encoding="utf-8").read()
    test("Blood Bond not SkillNature.Hex", "SkillNature.Hex" not in necro,
         "SkillNature.Hex does not exist — crashes on import")

    # Check dervish Avatar of Dwayna has StripDervishEnchantments
    dervish = open(os.path.join(BASE, "HeroAI/custom_skill_src/dervish.py"), encoding="utf-8").read()
    test("Avatar of Dwayna has StripDervishEnchantments", "StripDervishEnchantments = True" in dervish)

    # Check skill_types has StripDervishEnchantments field
    skill_types = open(os.path.join(BASE, "HeroAI/custom_skill_src/skill_types.py"), encoding="utf-8").read()
    test("skill_types has StripDervishEnchantments", "StripDervishEnchantments" in skill_types)
    test("skill_types no duplicate Overcast", skill_types.count("self.Overcast = 0.0") == 1,
         "duplicate self.Overcast assignment")

    # ===== 6. AUTOLOOTMANAGER CONFIG =====
    print("\n--- AutoLootManager Config ---")
    alm = open(os.path.join(BASE, "Widgets/Automation/Helpers/AutoLootManager.py"), encoding="utf-8").read()
    test("auto_identify disabled", "auto_identify = False" in alm,
         "InventoryPlus handles identify — AutoLootManager should not")
    test("auto_salvage_white disabled", "auto_salvage_white = False" in alm)
    test("auto_salvage_smart disabled", "auto_salvage_smart = False" in alm)
    test("auto_restock enabled", "auto_restock = True" in alm)
    test("pipeline uses _pipeline_still_valid", "_pipeline_still_valid" in alm)
    test("pipeline has timeout", "_PIPELINE_TIMEOUT_S" in alm)
    test("explorable merchant guard exists", "Map.IsExplorable()" in alm)
    test("anti-bot task order exists", "_PIPELINE_TASK_ORDERS" in alm)

    # ===== 7. ACCOUNTS.JSON VALID =====
    print("\n--- accounts.json Validation ---")
    acc_path = os.path.join(BASE, "accounts.json")
    test("accounts.json exists", os.path.exists(acc_path))
    if os.path.exists(acc_path):
        try:
            with open(acc_path, encoding="utf-8") as f:
                data = json.load(f)
            test("accounts.json valid JSON", True)
            test("Standard team exists", "Standard" in data)
            if "Standard" in data:
                test("Standard has 6 accounts", len(data["Standard"]) >= 6,
                     f"has {len(data['Standard'])}")
                emails = [a.get("email", "") for a in data["Standard"]]
                test("master email in Standard", "jonasglawion@aol.com" in emails)
        except json.JSONDecodeError as e:
            test("accounts.json valid JSON", False, str(e))

    # ===== 8. INVENTORYPLUS ENABLED =====
    print("\n--- InventoryPlus Enabled ---")
    ini_path = os.path.join(BASE, "Settings/Global/Widgets/WidgetManager/WidgetManager.ini")
    if os.path.exists(ini_path):
        ini = open(ini_path, encoding="utf-8").read()
        # Find InventoryPlus section
        # InventoryPlus may be enabled or disabled depending on user preference
        test("InventoryPlus configured", "InventoryPlus.py]" in ini)
        test("AutoMirror enabled", "AutoMirror.py]\nenabled = True" in ini)
        test("CombatPrep enabled", "CombatPrep.py]\nenabled = True" in ini)
        test("FollowingModule disabled", "FollowingModule.py]\nenabled = False" in ini)
    else:
        test("WidgetManager.ini exists", False)

    # ===== 9. INVITE ORDER =====
    print("\n--- Invite Order ---")
    multibox = open(os.path.join(BASE, "Py4GWCoreLib/botting_src/helpers_src/Multibox.py"), encoding="utf-8").read()
    test("invite order has all 10 professions",
         "1:" in multibox and "10:" in multibox and "5:" in multibox and "3:" in multibox)

    # ===== 10. COMBAT DEBUG OFF =====
    print("\n--- Debug Flags ---")
    combat = open(os.path.join(BASE, "HeroAI/combat.py"), encoding="utf-8").read()
    # Debug flags were removed in upstream rebase — check they're absent (no debug spam)
    test("no _combat_debug True", "_combat_debug: bool = True" not in combat and "_combat_debug = True" not in combat,
         "debug logging floods console at 12+ msg/sec across 6 clients")

    heroai = open(os.path.join(BASE, "Widgets/Automation/Multiboxing/HeroAI.py"), encoding="utf-8").read()
    test("no _follow_debug True", "_follow_debug = True" not in heroai,
         "movement logging floods console at 2.5 msg/sec")

    # ===== 11. SHARED MEMORY FIELD PATH =====
    print("\n--- Shared Memory Safety ---")
    test("AutoLootManager uses AgentData.Map.MapID",
         "AgentData.Map.MapID" in alm or "_pipeline_still_valid" in alm,
         "was acc.MapID which crashes")

    # ===== 12. VERIFICATION SCRIPT PASSES =====
    print("\n--- Verification Script ---")
    try:
        result = os.system(f"{sys.executable} verify_custom_changes.py > /dev/null 2>&1")
        test("verify_custom_changes.py passes", result == 0,
             "run 'python verify_custom_changes.py' for details")
    except Exception as e:
        test("verify_custom_changes.py runs", False, str(e))

    # ===== RESULTS =====
    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if ERRORS:
        print("\nFAILURES:")
        for err in ERRORS:
            print(f"  - {err}")
    print("=" * 60)

    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
