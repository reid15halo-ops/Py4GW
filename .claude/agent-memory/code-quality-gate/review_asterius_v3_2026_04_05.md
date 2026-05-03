---
name: Asterius Scythe v3 review 2026-04-05
description: 7/10 review of 2078-line farm bot; PII leak, untyped dicts, positional tuples, DRY map guards
type: project
---

Asterius Scythe v3.py scored 7/10 on 2026-04-05 after 25 successful cycles.

**Key findings:**
- MASTER_EMAIL PII hardcoded (Critical, recurring pattern)
- boss_state untyped dict with 8+ implicit keys -- typo risk
- Factory functions return positional tuples (4-tuple, 3-tuple) -- fragile
- _intercept_start_time uses mutable-list-in-closure instead of boss_state dict
- Map readiness guard duplicated ~20 times
- Boss rush logic duplicated in patrol body + post-loop

**Strengths:**
- Run-id cross-run contamination prevention is solid
- Zone-back micro-step + A* phased approach works
- Infinite-yield at coroutine ends prevents FSM crash
- Grace period on patrol launch prevents race conditions
- Stuck-slave detection with 30s timeout
- UCB1 learner implementation is textbook correct

**Why:** Context for future reviews of this file and tracking recurring PII pattern.
**How to apply:** Check if PII and untyped dict issues are resolved in future reviews.
