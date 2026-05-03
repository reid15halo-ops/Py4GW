---
name: Code quality patterns from 2026-03-28 review
description: Recurring patterns found in HeroAI/targeting.py, commands.py, Messaging.py, UIManager.py review - DRY violations, error counter boilerplate, print-vs-ConsoleLog
type: project
---

Patterns identified in the 2026-03-28 code quality review across 9 files:

1. **Error counter boilerplate in targeting.py** -- The `_scoring_errors += 1; if ... % 100 == 1: log(...)` pattern is repeated 13 times. A helper function would cut ~40 lines. This is the most widespread DRY violation in the HeroAI subsystem.

2. **Salvage dialog handling duplication** -- The inner salvage-wait-and-dialog loop (~50 lines) is near-identical in `Messaging.py SalvageItems()` and `AutoInventoryHandler.py SalvageItems()`. Both are coroutines with the same yield pattern. A shared utility coroutine could serve both.

3. **Broadcast command pattern in commands.py** -- 4+ command methods (leave_party, resign, donate_faction, pick_up_loot) follow identical structure. A `_send_to_all()` helper would deduplicate.

4. **Remaining print() in UIManager.py line 185** -- Should be ConsoleLog(). print() is invisible in the DLL-injected context.

5. **Redundant pass after ConsoleLog in except blocks** -- UIManager.py lines 935, 1004, 1142. Cosmetic only.

**Why:** These are the recurring anti-patterns to watch for when generating or reviewing code in this project. The error counter pattern is likely to spread to new modules, and the salvage duplication will drift over time.

**How to apply:** When reviewing future changes, check for these specific patterns. When generating new salvage-related code, reference AutoInventoryHandler.py as canonical. When adding error counters, suggest the helper function pattern.
