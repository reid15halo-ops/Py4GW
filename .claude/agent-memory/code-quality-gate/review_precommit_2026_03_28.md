---
name: Pre-commit sweep 2026-03-28
description: Final pre-commit review of 55-file changeset on jonas-custom branch; 9/10 approved; key fixes: BT reset propagation, aggro outside throttle, merchant model ID, 15 command wrappers
type: project
---

Pre-commit review of 55 files (1026 insertions, 46707 deletions). Score: 9/10 APPROVED.

**Key improvements verified:**
- BT Sequence/Selector reset() now propagates to children (was previously flagged as a bug)
- Aggro detection moved outside 75ms throttle for immediate combat engagement
- Merchant model ID forwarded via ExtraData to prevent NPC misidentification
- 15 command methods in commands.py wrapped with try/except + ConsoleLog
- All print() in EVENTS_src.py converted to ConsoleLog()
- Overlay defaults flipped to False (3 toggles + 3 in FollowingModule)
- Map-loading guards added to FollowingModule main() and _draw_3d_overlay()
- Previously bare except:pass in following.py upgraded to log with ConsoleLog

**Remaining minor items (not blocking):**
- commands.py try/except wrappers are copy-paste; a decorator would DRY them (-0.5)
- Two `except Exception:` (no `as e`) in cache_data.py aggro hot-path; acceptable for perf but throttled error counter would be ideal (-0.5)
- A* re-pathfind inner loop (retries==10) could theoretically stall; bounded by outer limits so safe

**Why:** This was the 5th review pass on this changeset. All previously flagged critical issues (BT reset non-propagation, silent exceptions, print() logging, overlay defaults) have been addressed.

**How to apply:** These patterns are now baseline quality expectations for future HeroAI/BT/Messaging changes.
