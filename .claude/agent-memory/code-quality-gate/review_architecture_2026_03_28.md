---
name: Architecture review findings 2026-03-28
description: Architecture review of 3 features — interrupt deconfliction, BT watchdog, outpost prep reporter. INI contention, dead variables, DRY gaps.
type: project
---

Architecture review of three new HeroAI features (2026-03-28):

**1. Interrupt deconfliction (targeting.py)** — 9.5/10
- Two-layer approach (IsCasting + local penalty) is architecturally sound
- Constants at module scope: correct, matches existing WEIGHT_*/INTERRUPT_* pattern
- Old _scored_best_interrupt/_scored_interrupt_time fully replaced, zero dangling references
- Cache-clear-on-map-change correctly resets interrupt tracking state

**2. BT watchdog (HeroAI.py)** — 9.0/10
- Module-scope globals follow existing pattern (build_contract_map_signature, following_flag, etc.)
- Positioned correctly: after initialize() True, before BT tick
- map_sig tuple duplicated between watchdog (line 934) and initialize() (line 696)
- Missing documentation of known BT stall failure modes

**3. Auto-outpost reporter (HeroAI.py + MultiboxCommander.py)** — 8.0/10
- File-based INI is correct choice over shared memory for low-frequency display data
- No circular dependency from Inventory/Item/ItemArray imports (widget -> framework direction)
- Bag iteration (80 items x 6 clients every 10s) acceptable for outpost-only context
- INI write contention: 6 clients read-modify-write same file with no locking (rare but possible)
- _outpost_prep_last_map is dead code: set but never read as gate condition
- _BLESSED_INI path duplicated in HeroAI.py and MultiboxCommander.py (should import from Blessed.py)

**Why:** These findings inform future reviews of HeroAI features and establish baseline expectations for module-scope state, INI communication patterns, and BT safety patterns.

**How to apply:** When reviewing new features: (1) verify INI communication uses per-client files or has contention mitigation, (2) check for dead variables in debounce patterns, (3) ensure shared paths are imported not duplicated.
