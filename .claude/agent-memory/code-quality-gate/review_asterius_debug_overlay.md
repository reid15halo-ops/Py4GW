---
name: Asterius v3 debug overlay review 2026-04-05
description: 6/10 - _get_debug_state() runs every frame on all 6 clients even when debug disabled; likely c0000005 contributor
type: project
---

Asterius Scythe v3.py debug overlay scored 6/10, revision required.

Key findings:
1. **_get_debug_state() runs unconditionally every frame** -- enemy array scan, party iteration, shared memory iteration on ALL 6 clients even with debug off. This is the most likely contributor to the 2h c0000005 crash.
2. Console dump fires every 30s per client (1 log/5s systemwide) -- cumulative with 78 other ConsoleLog calls
3. tooltip() has zero error handling -- crash every frame if PyImGui throws
4. Patrol waypoint logging not gated by _is_master() -- 6x redundant logs

**Why:** The per-frame overhead of _get_debug_state() on 6 clients adds ~6 unnecessary enemy array scans + shared memory iterations per frame. Over 2 hours, the cumulative GW1 error buffer pressure from console logs + API overhead likely causes the c0000005.

**How to apply:** Gate _get_debug_state() after _debug_enabled checkbox. Increase console interval to 120s + master-only. Wrap tooltip in try/except. Gate patrol logs behind _is_master().
