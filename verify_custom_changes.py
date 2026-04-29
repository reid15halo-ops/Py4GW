"""
Post-update verification: checks that Jonas's session changes survived a rebase/merge.
Run after every `update_py4gw.sh` to detect if upstream overwrote critical fixes.

Usage: python verify_custom_changes.py
Exit code 0 = all OK, 1 = missing changes (manual merge needed)
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ERRORS = []
WARNINGS = []


def check_file_contains(filepath, marker, description):
    """Check that a file contains a specific string marker."""
    path = os.path.join(BASE, filepath)
    if not os.path.exists(path):
        ERRORS.append(f"MISSING FILE: {filepath} — {description}")
        return False
    content = open(path, encoding="utf-8").read()
    if marker not in content:
        ERRORS.append(f"MISSING CHANGE: {filepath} — {description}")
        return False
    return True


def check_file_exists(filepath, description):
    """Check that a file exists."""
    path = os.path.join(BASE, filepath)
    if not os.path.exists(path):
        ERRORS.append(f"MISSING FILE: {filepath} — {description}")
        return False
    return True


def main():
    print("=" * 60)
    print("Py4GW Custom Changes Verification")
    print("=" * 60)
    print()

    checks = 0
    passed = 0

    # === CRITICAL: Salvage dialog safety (007 prevention) ===
    tests = [
        # (file, marker, description)

        # Salvage coroutine uses HandleSalvageChoiceDialog
        ("Widgets/Automation/Helpers/AutoLootManager.py",
         "HandleSalvageChoiceDialog",
         "Salvage coroutine must use HandleSalvageChoiceDialog (007 crash fix)"),

        # Salvage uses coroutine pattern, not state machine
        ("Widgets/Automation/Helpers/AutoLootManager.py",
         "_co_safe_salvage",
         "Salvage must use coroutine (_co_safe_salvage), not state machine"),

        # Kit sharing: single-target messaging
        ("Widgets/Automation/Helpers/AutoLootManager.py",
         "_request_kit_from_team",
         "Kit sharing request function must exist"),

        # Kit sharing: per-type cooldowns (may be in AutoLootManager or SalvageDecision)
        ("Widgets/Automation/Helpers/SalvageDecision.py",
         "_kit_cooldowns",
         "Kit sharing must use per-type cooldowns (not shared)"),

        # Auto-restock on outpost entry
        ("Widgets/Automation/Helpers/AutoLootManager.py",
         "_co_outpost_pipeline",
         "Auto-restock/deposit outpost pipeline must exist"),

        # Kit sharing enum values
        ("Py4GWCoreLib/enums_src/Multiboxing_enums.py",
         "ShareSalvageKit = 47",
         "SharedCommandType.ShareSalvageKit enum must exist"),

        ("Py4GWCoreLib/enums_src/Multiboxing_enums.py",
         "ShareExpertKit = 48",
         "SharedCommandType.ShareExpertKit enum must exist"),

        ("Py4GWCoreLib/enums_src/Multiboxing_enums.py",
         "ShareIDKit = 49",
         "SharedCommandType.ShareIDKit enum must exist"),

        # Kit sharing handler in Messaging.py (DRY version)
        ("Widgets/System/Messaging.py",
         "_ShareKit",
         "Generic _ShareKit handler must exist (DRY pattern)"),

        # Receiver-side map validation
        ("Widgets/System/Messaging.py",
         "sender on different map",
         "Kit sharing receiver must validate same-map"),

        # Receiver-side cooldown
        ("Widgets/System/Messaging.py",
         "_KIT_DROP_COOLDOWN_S",
         "Kit sharing receiver must have cooldown to prevent drain"),

        # Dervish enchantment tracker
        ("Py4GWCoreLib/SkillManager.py",
         "_DervishEnchantTracker",
         "Dervish enchant tracker for Avatar of Dwayna pre-cast stripping"),

        ("Py4GWCoreLib/SkillManager.py",
         "strip_active_enchantments",
         "Strip dervish enchantments function must exist"),

        # StripDervishEnchantments condition
        ("HeroAI/custom_skill_src/skill_types.py",
         "StripDervishEnchantments",
         "CastConditions.StripDervishEnchantments field must exist"),

        ("HeroAI/custom_skill_src/dervish.py",
         "StripDervishEnchantments = True",
         "Avatar of Dwayna must have StripDervishEnchantments = True"),

        # SkillNature.Hex crash fix
        ("HeroAI/custom_skill_src/necromancer.py",
         "SkillNature.Offensive",
         "Blood Bond must use SkillNature.Offensive (not .Hex which doesn't exist)"),

        # HeroAI FollowModule bootstrap fix
        ("Widgets/Automation/Multiboxing/HeroAI.py",
         "EnsureFollowModuleIni()",
         "HeroAI must call EnsureFollowModuleIni() (not direct enable_widget)"),

        # AutoStore widget exists
        ("Widgets/Automation/Helpers/AutoStore/AutoStore.py",
         "Auto-deposit all materials",
         "AutoStore widget must exist"),

        # AutoStore uses FollowPath (not direct Move)
        ("Widgets/Automation/Helpers/AutoStore/AutoStore.py",
         "FollowPath",
         "AutoStore must use FollowPath for obstacle-safe pathfinding"),

        # CLAUDE.md safety rules
        ("CLAUDE.md",
         "NEVER call `Inventory.SalvageItem()` without handling ALL possible dialogs",
         "CLAUDE.md rule 1: salvage dialog handling"),

        ("CLAUDE.md",
         "NEVER broadcast to ALL accounts",
         "CLAUDE.md rule 10: single-target messaging"),

        ("CLAUDE.md",
         "ALWAYS validate same-map on BOTH sender AND receiver",
         "CLAUDE.md rule 11: map validation"),

        ("CLAUDE.md",
         "ALWAYS enforce receiver-side cooldown",
         "CLAUDE.md rule 12: receiver cooldown"),

        # === Session 2 fixes: decorator, InventoryPlus, AutoMirror ===

        # Decorator must NOT have yield in the wrapper function
        ("Py4GWCoreLib/botting_src/helpers_src/decorators.py",
         "_fsm_wrapper",
         "yield_step must use separate _fsm_wrapper (non-generator) for bot context"),

        ("Py4GWCoreLib/botting_src/helpers_src/decorators.py",
         "_widget_wrapper",
         "yield_step must use separate _widget_wrapper (generator) for widget context"),

        # InventoryPlus must be enabled
        ("CLAUDE.md",
         "NEVER reimplement identify/salvage/deposit logic",
         "CLAUDE.md rule 16: use framework, don't reimplement"),

        # AutoMirror must check email, not party leader
        ("Widgets/Automation/Multiboxing/AutoMirror.py",
         "MASTER_EMAIL",
         "AutoMirror must guard by master email (not IsPartyLeader)"),

        # Shared memory field access rule
        ("CLAUDE.md",
         "AccountStruct.AgentData.Map.MapID",
         "CLAUDE.md rule 19: correct shared memory field path"),

        # yield from safety rule
        ("CLAUDE.md",
         "NEVER put `yield from` in a function that also has a non-generator code path",
         "CLAUDE.md rule 17: yield from generator safety"),

        # Widget hot-reload warning
        ("CLAUDE.md",
         "Widgets do NOT hot-reload on file changes",
         "CLAUDE.md rule 18: no hot-reload, must restart"),

        # Master identity by email, not party leader
        ("Widgets/Automation/Multiboxing/HeroAI.py",
         'MASTER_EMAIL',
         "HeroAI Follow must identify master by email (not GetPartyLeaderID)"),

        ("Widgets/Automation/Multiboxing/AutoMirror.py",
         'MASTER_EMAIL_UI = MASTER_EMAIL',
         "AutoMirror must identify master by email constant"),

        ("CLAUDE.md",
         "NEVER use `GetPartyLeaderID()` or `IsPartyLeader()` to identify the master",
         "CLAUDE.md rule 26: master identity by email"),
    ]

    for filepath, marker, description in tests:
        checks += 1
        if check_file_contains(filepath, marker, description):
            passed += 1
            print(f"  OK:    {description}")
        else:
            print(f"  FAIL:  {description}")

    # === ANTI-PATTERN DETECTION (catches regressions) ===
    print()
    print("--- Anti-Pattern Detection ---")

    antipattern_checks = [
        # (file_glob, forbidden_pattern, description)

        # No bare except: in UIManager (007 crash vector)
        ("Py4GWCoreLib/UIManager.py",
         "\n    except:\n",
         "UIManager has bare except: (masks dialog detection failures → 007)"),

        # No silent except in targeting hot path
        ("HeroAI/targeting.py",
         "except Exception:\n            pass",
         "targeting.py has silent except:pass (enemies permanently ignored)"),

        # No reversed combo order
        ("HeroAI/combat.py",
         "combos = [3, 2, 1]",
         "Assassin combo order reversed (Dual before Lead breaks chain)"),

        # Energy reserve must NOT return False on failure
        ("HeroAI/combat.py",
         "_would_breach_energy_reserve",  # check exists, then verify
         None),  # special handling below

        # No flat salvage delay without jitter
        ("Widgets/System/Messaging.py",
         "yield from Routines.Yield.wait(400)\n\n        ConsoleLog(MODULE_NAME, f\"SalvageItems",
         "SalvageItems has flat 400ms delay without jitter (6 clients in lockstep → 007)"),

        # No overlay defaults set to True
        ("HeroAI/settings.py",
         "ShowPartyOverlay = True",
         "Overlay defaults are True (must be False per CLAUDE.md)"),

        # No dead commented-out code
        ("HeroAI/combat.py",
         "#for i in range(ptr,MAX_SKILLS)",
         "Dead commented code still present in combat.py"),

        # No print() in EVENTS_src
        ("Py4GWCoreLib/botting_src/subclases_src/EVENTS_src.py",
         "print(",
         "EVENTS_src.py uses print() instead of ConsoleLog()"),

        # No duplicate _HEALER_PROFS
        ("HeroAI/targeting.py",
         "_HEALER_PROFS",
         "Duplicate _HEALER_PROFS constant (should use _HEALER_PROFESSIONS)"),

        # No .bak files tracked
        ("Py4GWCoreLib/skill_descriptions.json.bak",
         None,  # file existence check
         "skill_descriptions.json.bak should be deleted"),
    ]

    for filepath, pattern, description in antipattern_checks:
        if pattern is None and description is not None:
            # File should NOT exist
            checks += 1
            path = os.path.join(BASE, filepath)
            if os.path.exists(path):
                ERRORS.append(f"ANTI-PATTERN: {filepath} — {description}")
                print(f"  FAIL:  {description}")
            else:
                passed += 1
                print(f"  OK:    {filepath} correctly absent")
            continue
        if description is None:
            continue  # special handling marker
        checks += 1
        path = os.path.join(BASE, filepath)
        if not os.path.exists(path):
            passed += 1  # file doesn't exist, can't have the pattern
            print(f"  OK:    {filepath} — no anti-pattern (file absent)")
            continue
        content = open(path, encoding="utf-8").read()
        if pattern in content:
            ERRORS.append(f"ANTI-PATTERN: {filepath} — {description}")
            print(f"  FAIL:  {description}")
        else:
            passed += 1
            print(f"  OK:    No {description.split('(')[0].strip().lower()}")

    # Special check: _would_breach_energy_reserve must return True on failure
    checks += 1
    combat_path = os.path.join(BASE, "HeroAI/combat.py")
    if os.path.exists(combat_path):
        content = open(combat_path, encoding="utf-8").read()
        # Find the except block in _would_breach_energy_reserve
        idx = content.find("_would_breach_energy_reserve")
        if idx >= 0:
            section = content[idx:idx+500]
            if "return False  # fail-safe" in section or ("except Exception" in section and "return False" in section.split("except")[1][:100]):
                ERRORS.append("ANTI-PATTERN: combat.py — _would_breach_energy_reserve returns False on error (must return True to block offensive skills)")
                print("  FAIL:  Energy reserve returns False on error (must return True)")
            else:
                passed += 1
                print("  OK:    Energy reserve fail-safe returns True")
        else:
            passed += 1
            print("  OK:    Energy reserve check (function not found, skip)")
    else:
        passed += 1

    # === .gitignore checks ===
    checks += 1
    gitignore_path = os.path.join(BASE, ".gitignore")
    if os.path.exists(gitignore_path):
        gi = open(gitignore_path, encoding="utf-8").read()
        missing_patterns = []
        for pat in ["Py4GW_injection_log.txt", "*.bak", "accounts.json"]:
            if pat not in gi:
                missing_patterns.append(pat)
        if missing_patterns:
            ERRORS.append(f".gitignore missing patterns: {', '.join(missing_patterns)}")
            print(f"  FAIL:  .gitignore missing: {', '.join(missing_patterns)}")
        else:
            passed += 1
            print("  OK:    .gitignore has all required patterns")
    else:
        ERRORS.append(".gitignore file missing entirely")

    # === File existence checks ===
    print()
    print("--- File Existence Checks ---")
    file_checks = [
        ("Widgets/Automation/Helpers/AutoStore/AutoStore.py", "AutoStore widget"),
        ("Widgets/Automation/Helpers/AutoStore/.widget", "AutoStore widget marker"),
        ("Widgets/Automation/Helpers/Dashboard/Dashboard.py", "Dashboard widget"),
        ("Widgets/Automation/Helpers/Dashboard/.widget", "Dashboard widget marker"),
        ("CLAUDE.md", "Development safety rules"),
    ]

    for filepath, description in file_checks:
        checks += 1
        if check_file_exists(filepath, description):
            passed += 1
            print(f"  OK:    {description} exists")
        else:
            print(f"  FAIL:  {description} missing")

    # === Results ===
    print()
    print(f"Results: {passed}/{checks} checks passed")

    if ERRORS:
        print()
        print("!" * 60)
        print("CRITICAL: The following changes are MISSING after update!")
        print("These must be restored or the bot will crash (007 disconnects).")
        print("!" * 60)
        for err in ERRORS:
            print(f"  - {err}")
        print()
        print("To restore: git checkout jonas-custom -- <file>")
        print("Or re-run the session fixes manually.")
        return 1

    print()
    print("All custom changes verified. Safe to run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
