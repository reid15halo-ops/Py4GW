---
name: Combat batch review 2026-03-29
description: 9/10 review of stick-until-dead, SY! stagger, Death's Charge guard, AvSelect TOCTOU fix, BiP priority, emergency heal override
type: project
---

Reviewed combat.py, targeting.py, necromancer.py, monk.py, pve.py combat changes.

**Score: 9/10 -- APPROVED**

Key findings:
- Stick-until-dead works correctly: invulnerable targets stay committed (game engine rejects casts harmlessly), spirits with non-Enemy allegiance get dropped
- _BIP_PROF_PRIORITY dict duplicated at targeting.py lines ~480 and ~701 -- recurring DRY pattern from previous reviews
- Dead variable: `_target_commit_duration` (combat.py ~280) left over from timer-based reconsideration removal
- Per-call tuple/list rebuilds: `_VOW_SPELL_TYPES` in IsReadyToCast, `pet_attack_list` in AreCastConditionsMet

**Why:** Documents combat AI quality baseline and recurring minor DRY issues that accumulate across sessions.
**How to apply:** Flag BiP dict duplication and dead vars in future reviews; these are the persistent minor issues in HeroAI targeting/combat.
