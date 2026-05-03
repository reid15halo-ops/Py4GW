---
name: HeroAI main() function density
description: HeroAI.py main() mixes watchdog, BT tick, map housekeeping, outpost prep, and UI -- needs extraction for readability
type: feedback
---

HeroAI.py main() (lines 916-992) is becoming a density hotspot. As of 2026-03-28 it contains:
- Outpost prep reporter call
- FollowingModule INI bootstrap
- Floating window update
- UI handler
- initialize() gate
- Map signature computation (3x duplication)
- Watchdog stall detection + recovery
- BT tick + profiler
- Non-explorable cleanup
- 4 separate except clauses

Pattern: each new feature (watchdog, outpost prep) inlines into main() rather than being extracted to a helper. The map signature tuple is now computed 3 times identically.

**Why:** A dense main() makes it hard to scan for "what changed" in diffs, and impossible to unit-test subsystems independently.

**How to apply:** When reviewing new features that add logic to main(), push back unless the logic is extracted to a named function. Suggest _get_map_signature(), _check_watchdog(), etc.
