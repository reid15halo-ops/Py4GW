"""
Py4GW Custom Skill Fixes — Apply after every Py4GW update.
Run: python apply_custom_fixes.py

Fixes Jonas's specific build issues that get overwritten by upstream updates.
Safe: only changes TargetAllegiance, Conditions, and Nature where proven necessary.
NEVER changes Nature unless the original was provably wrong (e.g. Together_as_One was Interrupt instead of Buff).
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(BASE, "HeroAI", "custom_skill_src")

FIXES = [
    # (file, skill_name, field_to_find, old_value, new_value, reason)

    # === NECRO REID: Blood is Power kills himself ===
    ("necromancer.py", "Blood_is_Power", "SacrificeHealth", "0.3", "0.6",
     "Necro was casting BiP down to 30% HP and dying. 60% threshold keeps him alive."),

    # === NECRO REID: Blood Bond on single target instead of cluster ===
    ("necromancer.py", "Blood_Bond", "Skilltarget.Enemy.value", "Skilltarget.Enemy.value", "Skilltarget.EnemyClustered.value",
     "Blood Bond is AoE hex — should target clusters, not single enemies."),

    # === NECRO REID: Mending Grip only heals out of combat ===
    ("ritualist.py", "Mending_Grip", "IsOutOfCombat = True", "IsOutOfCombat = True", "IsOutOfCombat = False",
     "Mending Grip was set to OOC only — useless. Must heal IN combat."),

    # === NECRO REID: Wielder's Boon only heals out of combat ===
    ("ritualist.py", "Wielders_Boon", "IsOutOfCombat = True", "IsOutOfCombat = True", "IsOutOfCombat = False",
     "Wielder's Boon was set to OOC only — useless. Must heal IN combat."),

    # === REID SEED: Seed of Life on random ally instead of tank ===
    ("pve.py", "Seed_of_Life", "Skilltarget.OtherAlly.value", "Skilltarget.OtherAlly.value", "Skilltarget.AllyMartialMelee.value",
     "Seed of Life must go on the melee tank (most aggro), not random ally."),

    # === REID SEED: Healing Burst triggers too early ===
    ("monk.py", "Healing_Burst", "LessLife = 0.8", "LessLife = 0.8", "LessLife = 0.6",
     "0.8 wastes Healing Burst on minor damage. 0.6 saves it for real emergencies."),

    # === JANINE: Endure Pain triggers too early ===
    ("warrior.py", "Endure_Pain", "LessLife = 0.4", "LessLife = 0.4", "LessLife = 0.3",
     "Emergency HP buff should only trigger at 30%, not 40%."),

    # === ALL: Great Dwarf Armor on random ally instead of frontline ===
    ("pve.py", "Great_Dwarf_Armor", "Skilltarget.Ally.value", "Skilltarget.Ally.value", "Skilltarget.AllyMartialMelee.value",
     "Armor buff on caster is wasted. Must go on melee frontline."),

    # === REID TAO: Together as One classified as Interrupt instead of Buff ===
    ("pve.py", "Together_as_one", "SkillNature.Interrupt.value", "SkillNature.Interrupt.value", "SkillNature.Buff.value",
     "Together as One is a party damage buff, not an interrupt. Wrong Nature caused bad priority."),

    # === REID TAO: Technobabble on any enemy instead of casters ===
    ("pve.py", "Technobabble", "Skilltarget.Enemy.value", "Skilltarget.Enemy.value", "Skilltarget.EnemyCaster.value",
     "Technobabble applies Dazed — only useful on casters, wasted on melee."),

    # === REID TAO: EBSoH placed for 1 enemy ===
    ("pve.py", "Ebon_Battle_Standard_of_Honor", "EnemiesInRange = 1", "EnemiesInRange = 1", "EnemiesInRange = 2",
     "Ward for 1 enemy is wasteful. Need at least 2 enemies to justify placement."),
]


def apply_fix(filename, skill_name, search_field, old_val, new_val, reason):
    path = os.path.join(SKILL_DIR, filename)
    if not os.path.exists(path):
        print(f"  SKIP: {filename} not found")
        return False

    content = open(path, encoding="utf-8").read()

    # Find the skill block (within 300 chars after the skill name)
    marker = f'"{skill_name}"'
    idx = content.find(marker)
    if idx == -1:
        print(f"  SKIP: {skill_name} not found in {filename}")
        return False

    block_start = idx
    block_end = min(idx + 400, len(content))
    block = content[block_start:block_end]

    if old_val in block:
        new_block = block.replace(old_val, new_val, 1)
        content = content[:block_start] + new_block + content[block_end:]
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  FIXED: {skill_name} in {filename}")
        print(f"         {old_val} -> {new_val}")
        return True
    elif new_val in block:
        print(f"  OK:    {skill_name} already has correct value")
        return False
    else:
        print(f"  WARN:  {skill_name} — expected '{old_val}' not found near marker")
        return False


def main():
    print("=" * 60)
    print("Py4GW Custom Skill Fixes — Jonas's Build Optimizations")
    print("=" * 60)
    print()

    applied = 0
    skipped = 0
    already_ok = 0

    for filename, skill_name, search_field, old_val, new_val, reason in FIXES:
        result = apply_fix(filename, skill_name, search_field, old_val, new_val, reason)
        if result:
            applied += 1
        else:
            # Check if it was already correct or skipped
            path = os.path.join(SKILL_DIR, filename)
            if os.path.exists(path):
                content = open(path, encoding="utf-8").read()
                marker = f'"{skill_name}"'
                idx = content.find(marker)
                if idx != -1 and new_val in content[idx:idx+400]:
                    already_ok += 1
                else:
                    skipped += 1
            else:
                skipped += 1

    print()
    print(f"Results: {applied} fixed, {already_ok} already OK, {skipped} skipped")
    print()

    if applied > 0:
        print("IMPORTANT: Restart all GW clients to load the new configs!")
    else:
        print("All fixes already applied. No restart needed.")


def fix_patcher_buffer():
    """Ensure Patcher buffer is 0xA00000 (10MB), not the upstream 0x48D000 (4.6MB)."""
    print("\n--- Patcher Buffer Check ---")
    fixed = 0
    for fname in ["Patcher.py", "Py4GW_Launcher.py"]:
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            continue
        content = open(path, encoding="utf-8").read()
        if "0x48D000" in content:
            content = content.replace("0x48D000", "0xA00000")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  FIXED: {fname} buffer 0x48D000 -> 0xA00000")
            fixed += 1
        elif "0xA00000" in content:
            print(f"  OK:    {fname} already 0xA00000")
        else:
            print(f"  WARN:  {fname} has neither buffer value")
    return fixed


def fix_python_dlls():
    """Ensure 32-bit Python DLLs exist in all GW directories."""
    print("\n--- Python 32-bit DLL Check ---")
    python_src = r"C:\Python313-32"
    dlls = ["python313.dll", "python3.dll", "vcruntime140.dll"]
    extra_files = ["python313.zip"]

    gw_dirs = [
        r"C:\Program Files\GUILD WARS Main",
        r"C:\Program Files\GUILD WARS Secondaccountintwokseventeen",
        r"C:\Program Files\GUILD WARS BIP",
        r"C:\Program Files\GUILD WARS HealDerv",
        r"C:\Program Files\GUILD WARS Seeder",
        r"C:\Program Files\GUILD WARS ESurge",
    ]

    if not os.path.exists(python_src):
        print(f"  SKIP: {python_src} not found (Python 3.13.0 32-bit not installed)")
        return 0

    import shutil
    fixed = 0
    for gw_dir in gw_dirs:
        if not os.path.exists(gw_dir):
            continue
        for dll in dlls + extra_files:
            src = os.path.join(python_src, dll)
            dst = os.path.join(gw_dir, dll)
            if not os.path.exists(src):
                continue
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                print(f"  FIXED: Copied {dll} to {os.path.basename(gw_dir)}")
                fixed += 1
            else:
                # Check size match
                if os.path.getsize(src) != os.path.getsize(dst):
                    shutil.copy2(src, dst)
                    print(f"  FIXED: Updated {dll} in {os.path.basename(gw_dir)} (size mismatch)")
                    fixed += 1

        # DLLs/ directory
        src_dlls = os.path.join(python_src, "DLLs")
        dst_dlls = os.path.join(gw_dir, "DLLs")
        if os.path.exists(src_dlls) and not os.path.exists(dst_dlls):
            shutil.copytree(src_dlls, dst_dlls)
            print(f"  FIXED: Copied DLLs/ to {os.path.basename(gw_dir)}")
            fixed += 1

    if fixed == 0:
        print("  OK:    All GW dirs have correct Python DLLs")
    return fixed


def fix_formation_config():
    """Ensure FollowModule has valid formation data (not empty)."""
    print("\n--- Formation Config Check ---")
    formations_path = os.path.join(BASE, "Settings", "Global", "HeroAI", "FollowModule_Formations.ini")
    defaults_path = os.path.join(BASE, "Settings", "Defaults", "HeroAI", "FollowModule_Formations.ini")

    if not os.path.exists(formations_path):
        print("  SKIP: Formations INI not found")
        return 0

    content = open(formations_path, encoding="utf-8").read()
    if "[FormationId:" not in content:
        # Empty formations — copy from defaults
        if os.path.exists(defaults_path):
            import shutil
            shutil.copy2(defaults_path, formations_path)
            print("  FIXED: Copied default formations (was empty)")
            return 1
        else:
            print("  WARN: Empty formations and no defaults found!")
            return 0

    print("  OK:    Formations have data")
    return 0


def check_gw_paths():
    """Warn if any account uses Program Files (x86) — causes crashes."""
    print("\n--- GW Path Safety Check ---")
    accounts_path = os.path.join(BASE, "accounts.json")
    if not os.path.exists(accounts_path):
        return

    import json
    with open(accounts_path, encoding="utf-8") as f:
        data = json.load(f)

    for team_name, accounts in data.items():
        for acc in accounts:
            gw_path = acc.get("gw_path", "")
            if "(x86)" in gw_path:
                print(f"  DANGER: {acc['character_name']} uses Program Files (x86)!")
                print(f"          Path: {gw_path}")
                print(f"          THIS WILL CRASH! Move to C:\\Program Files\\")
            else:
                name = acc.get("character_name", "?")
                # Just verify the exe exists
                if gw_path and not os.path.exists(gw_path):
                    print(f"  WARN:  {name} — Gw.exe not found at {gw_path}")

    print("  OK:    No (x86) paths found") if not any("(x86)" in acc.get("gw_path", "") for accs in data.values() for acc in accs) else None


if __name__ == "__main__":
    main()
    fix_patcher_buffer()
    fix_python_dlls()
    fix_formation_config()
    check_gw_paths()
    print("\n" + "=" * 60)
    print("All checks complete!")
    print("=" * 60)
