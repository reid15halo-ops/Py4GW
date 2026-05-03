---
name: Outpost autonomy audit 2026-03-29
description: Two surviving autonomous NPC interaction paths found after outpost automation disable — Blessed auto-bless and Pycons vault restock
type: project
---

Audit of "zero autonomous outpost NPC interaction" goal found two surviving paths:

1. **Blessed.py auto-bless** (line 151-165): Every client autonomously walks to and interacts with blessing NPCs on outpost entry. Not gated by master. MultiboxCommander's `auto_outpost_prep` triggers this chain via `bless_all()` INI flag.

2. **Pycons.py vault restock** (line 6602-6604, 3842-3880): When `auto_vault_restock` INI setting is True, autonomously calls `OpenXunlaiWindow()` in outpost. Defaults to False but is per-account configurable.

**Why:** User's stated goal is zero autonomous outpost NPC interaction to prevent 007 disconnects.

**How to apply:** When reviewing future outpost-safety changes, check Blessed.py and Pycons.py in addition to HeroAI/AutoLootManager/AutoStore. The Blessed auto-bless is the higher risk since it involves NPC walking + dialog.
