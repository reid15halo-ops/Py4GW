---
name: ActionSafetyLayer security review 2026-04-05
description: Security review of monkey-patch safety layer — fail-open exceptions, DRY violation, per-frame queue scan, burst-fire risk
type: project
---

ActionSafetyLayer.py patches ActionQueueNode.execute_next() with map/dialog/exception safety. Scored 4/10, revision required.

Key findings:
1. Fail-open exceptions on safety checks — except pass allows actions through when Map checks throw
2. Duplicated dialog logic — _is_any_blocking_dialog_open() duplicates UIManager.IsAnyBlockingDialogOpen()
3. Per-execution O(n) queue scan — _get_queue_name() iterates all 7 queues on every action
4. Unbounded error counter — _safety_error_count[0] never resets
5. Burst-fire risk — timer keeps elapsing while blocked; queued actions fire without throttle on unblock
6. Uninstall doesn't reset counter

**Why:** This is the last-resort 007 disconnect prevention for 6 clients. Fail-open under exceptions = silent 007.
**How to apply:** All 6 fixes required before approval. Recheck after revision.
