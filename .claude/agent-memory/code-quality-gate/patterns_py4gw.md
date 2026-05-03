---
name: Py4GW codebase patterns
description: Recurring patterns in Py4GW code -- coroutine contract, error counter idiom, DRY opportunities in HeroAI and Messaging
type: project
---

Key patterns observed in architecture review (2026-03-28):

1. **Coroutine contract in Messaging.py**: All message handlers follow MarkAsRunning -> SnapshotHeroAI -> try/finally(Restore + MarkFinished). SalvageItems, IdentifyItems, MerchantItems all conform. MerchantItems has `_merchant_busy` serialization; salvage does not (correct -- no NPC interaction).

2. **Error counter idiom in targeting.py**: `_scoring_errors` counter with `% 100 == 1` log throttle. Effective for frame-rate code across 6 processes. The boilerplate is repeated 10+ times -- a `_log_scoring_error(context, e)` helper would reduce this.

3. **DRY opportunities**: commands.py broadcast-to-all pattern repeated in 4+ methods. Messaging.py `CreateBagList + GetItemArray` repeated 3x in SalvageItems.

4. **UIManager.FrameExists linear scan**: Calls `GetFrameArray()` then `not in` on a list. Called from rendering paths. Could be O(1) with a set.

**Why:** These patterns are stable and validated. Future reviews should check new code conforms to the coroutine contract and does not introduce new DRY violations in these areas.

**How to apply:** When reviewing new Messaging.py handlers, verify they follow the snapshot/restore/finally pattern. When reviewing targeting changes, check for error-log boilerplate creep.
