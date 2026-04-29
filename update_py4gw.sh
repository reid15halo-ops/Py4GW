#!/bin/bash
# Update Py4GW from upstream while keeping Jonas's custom fixes.
#
# Usage: bash update_py4gw.sh
#
# What it does:
# 1. Fetches latest upstream (apoguita/Py4GW)
# 2. Stashes any uncommitted changes
# 3. Switches to master, merges upstream
# 4. Switches back to jonas-custom, rebases on master
# 5. If conflicts: runs apply_custom_fixes.py as fallback
# 6. Verifies all fixes are applied

set -e
cd "$(dirname "$0")"

echo "=== Py4GW Update Script ==="
echo ""

# Step 1: Fetch upstream
echo "Fetching upstream..."
git fetch upstream 2>/dev/null || echo "WARN: Could not fetch upstream (offline?)"

# Step 2: Stash uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
    echo "Stashing uncommitted changes..."
    git stash
fi

# Step 3: Update master from upstream
echo "Updating master branch..."
git checkout master
git merge upstream/main --no-edit 2>/dev/null || git merge upstream/master --no-edit 2>/dev/null || echo "WARN: No upstream changes or merge failed"

# Step 4: Rebase jonas-custom on top of master
echo "Rebasing jonas-custom on master..."
git checkout jonas-custom
if ! git rebase master; then
    echo ""
    echo "CONFLICT during rebase! Aborting rebase and applying fixes manually..."
    git rebase --abort
    git merge master --no-edit || true

    # Fallback: run the fix script
    echo "Running apply_custom_fixes.py..."
    python apply_custom_fixes.py

    git add -A
    git commit -m "Re-apply Jonas custom fixes after upstream update" || true
fi

# Step 5: Verify skill fixes
echo ""
echo "Verifying skill fixes..."
python apply_custom_fixes.py

# Step 5b: Verify session changes (salvage safety, kit sharing, etc.)
echo ""
echo "Verifying session changes..."
python verify_custom_changes.py
if [ $? -ne 0 ]; then
    echo ""
    echo "!!! WARNING: Some custom changes were lost during update !!!"
    echo "!!! Check output above and restore from jonas-custom branch !!!"
    echo ""
fi

# Step 6: Restore stash if needed
if git stash list | grep -q "stash@{0}"; then
    echo "Restoring stashed changes..."
    git stash pop || echo "WARN: Could not pop stash (conflicts?)"
fi

echo ""
echo "=== Update complete! ==="
echo "Restart all GW clients to load the new configs."
