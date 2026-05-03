---
name: HeroAI codebase review patterns
description: Recurring code quality patterns found during security/007-disconnect audit of HeroAI and Messaging changes
type: feedback
---

Patterns observed in the Py4GW codebase during 007-disconnect safety review:

**Positive patterns (keep doing):**
- SalvageItems coroutine in Messaging.py follows the canonical pattern from CLAUDE.md: uses coroutines, HandleSalvageChoiceDialog, AcceptSalvageMaterialsWindow, 400ms throttle, map loading checks
- All HeroAI/commands.py wrappers log exceptions via ConsoleLog with severity 1 (Warning), never bare except
- targeting.py uses modulo-100 throttled logging for high-frequency errors (scoring runs 6x per frame), preventing log floods
- UIManager.py exception handlers all log before pass; no truly silent swallowing

**Minor issues found:**
- SalvageItems lacks random jitter between the 400ms inter-item waits (CLAUDE.md says 0-0.5s jitter to desync 6 accounts)
- UIManager.py has redundant `pass` after ConsoleLog in except blocks (cosmetic, not harmful)
- _local_has_effect() and _inventory_ready() in Messaging.py swallow exceptions without logging (borderline; these are high-frequency guard checks)
- targeting.py _scoring_errors counter grows unboundedly (no reset mechanism), though the modulo-100 logging keeps it from being a practical issue

**Minor issues found (continued):**
- GetBestInterruptTarget() does NOT call _check_map_change() at the top, unlike GetBestScoredEnemy(). If called before GetBestScoredEnemy in a frame, stale _last_interrupt_target_id from a previous map can match reused agent IDs and apply false deconfliction penalty.

**Combat pipeline bypasses (2026-03-29):**
- Death's Charge emergency Endure Pain at L1269 fires UseSkill directly, bypassing HandleCombat's final IsValid, Map.IsMapLoading(), and aftercast lock. Self-targeted so low 007 risk, but violates casting pipeline.
- Library SafeChangeTarget/SafeInteract (Routines.Targeting/Agents) lack the try/except + IsAlive checks that combat.py's versions have. SkillManager callers remain vulnerable to TOCTOU.
- `_get_emergency_peel_target` L1891: Agent.GetXY(critical_ally) outside inner try/except can throw on despawned ally.

**How to apply:** When reviewing future changes, verify: (1) no bare except, (2) salvage uses coroutines + HandleSalvageChoiceDialog, (3) inter-action waits include jitter for multibox desync, (4) map loading checked in all loops, (5) any new targeting.py public function calls _check_map_change() if it uses per-map cached state, (6) any direct UseSkill call outside HandleCombat must have its own IsValid + Map.IsMapLoading guard, (7) library Safe* wrappers should match combat.py's protection level.
