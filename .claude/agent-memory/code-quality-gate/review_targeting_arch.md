---
name: Targeting scoring architecture
description: Module-level constants in targeting.py validated as correct scope; global error counter safe across processes
type: project
---

Reviewed 2026-03-28. Key architectural decisions validated:

1. **25 named constants at module level**: Correct scope. They are tightly coupled to scoring functions in the same file, not shared or user-configurable. Moving to INI/config would add parsing overhead on a hot path for no benefit.

2. **`global _scoring_errors` thread safety**: Safe. 6 clients run as separate OS processes with independent Python interpreters. No shared memory write to this counter.

3. **EnemyBlacklist private API access**: `targeting.py` calls `bl._read()` and `bl._read_names()` (underscore-prefixed). Works but couples to internal API. Consider public `get_ids()`/`get_names()` methods on EnemyBlacklist.

**Why:** These questions will recur when scoring weights or the blacklist system are modified. Recording the rationale avoids re-analysis.

**How to apply:** If scoring weights need per-user tuning in the future, that is when they should move to config -- not preemptively.
