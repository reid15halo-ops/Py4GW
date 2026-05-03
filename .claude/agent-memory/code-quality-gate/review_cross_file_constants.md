---
name: Cross-file constant duplication pattern
description: INI paths and config constants duplicated between HeroAI.py and MultiboxCommander.py -- track for future drift
type: feedback
---

Duplicated constants found across Widgets/Automation/Multiboxing/:

- `_OUTPOST_PREP_INI` path defined identically in HeroAI.py (line 58) and MultiboxCommander.py (line 59)
- `_BLESSED_INI` path defined identically in HeroAI.py (line 61) and MultiboxCommander.py (line 24)
- `_HEALER_PROFESSIONS = {3, 8}` and `_INTERRUPT_PRIORITY_PROFESSIONS = {3, 8}` both in targeting.py (lines 26 and 508)

**Why:** If one file's path changes, the other silently diverges. The INI reader (MultiboxCommander) and writer (HeroAI) would stop communicating.

**How to apply:** When reviewing new features that read/write shared INI files, check that the path constant is defined in exactly one place. Flag any new cross-file constant duplication.
