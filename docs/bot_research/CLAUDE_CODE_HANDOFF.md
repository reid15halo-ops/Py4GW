# Py4GW Bot Optimization Project -- Claude Code Handoff

**Date:** 2026-03-30
**Author:** Jonas Glawion (with Claude Code Opus 4.6)
**Framework:** Py4GW (apoguita/Py4GW on GitHub) -- Python automation for Guild Wars 1

---

## Project Overview

This archive contains Guild Wars 1 bot scripts for the Py4GW framework. The main work was optimizing the **LDoA REID15** bot (Pre-Searing Ascalon leveler) and creating a full architectural rewrite (**v2**).

## Files in This Archive

| File | Description | Status |
|------|-------------|--------|
| `LDoA REID15` | Original bot, optimized in-place (v1) | Production-ready, tested patterns |
| `LDoA REID15 v2` | Full rewrite using Yield/Coroutine architecture | Complete, needs in-game testing |
| `Vaettir_Bot_v3_Optimized.py` | Vaettir farm bot -- REFERENCE implementation | Used as architectural template for v2 |
| `Vaettir bot combined for optimization` | Old Vaettir bot versions (multiple iterations) | Reference only |
| `Automation` | Asterius Scythe Farm bot (6-account multibox) | Reference only |
| `Rajazan` | Cultist Rajazan farm bot (original) | Not optimized |
| `Rajazan_Optimized.py` | Cultist Rajazan farm bot (optimized) | Optimized in separate session |
| `py4gw_research.html` | Comprehensive Py4GW API documentation | Reference |
| `Cultist_Rajazan_map.jpg` | Map reference for Rajazan farming route | Reference |
| `CLAUDE_CODE_HANDOFF.md` | This file | -- |

---

## What Was Done: LDoA REID15 v1 (In-Place Optimization)

### Bug Fixes
1. **Coordinate typo**: `70448` -> `7044` in unnatural seeds path (line 128 original) -- bot would walk off the map
2. **`Survivor_Hamnet()`** was identical copy of `Survivor()` -- deduplicated
3. **`quantityitem()`** missing `return` statement -- function was broken
4. **`equipitem()`** always overrode the `agent_id` parameter -- ignored caller's value
5. **`IsSkillReady()` was rejecting ALL skills with cooldowns** -- `Skill.Data.GetRecharge()` returns BASE recharge time, not current cooldown. Removed the check.
6. **Dialog hex values wrong for Necromancer and Mesmer** in v2 DIALOG dict:
   - Necro: `0x80DA01/07`, `0x805201/07` (v2 initially had `0x80DF01/07`, `0x805701/07`)
   - Mesmer: `0x80D901/07`, `0x805101/07` (v2 initially had `0x80E001/07`, `0x805801`)
   - Elementalist dialogs were entirely missing

### Performance
- Removed ALL blocking `time.sleep()` calls (6 instances in baked husk, gadget, loot sweeps)
- Fixed `get_farmed_items()` O(n^2) -> O(n) (was calling `get_count_items()` inside per-item loop)
- Replaced manual distance calculations with `Utils.Distance()`
- Stuck detection changed from exact float equality to distance threshold (<20 units)

### Combat System
- New `use_best_skill()` with energy checking, casting guard (`Agent.IsCasting`/`IsKnockedDown`)
- Self-heal priority when HP < 60% (profession-aware heal slot mapping)
- Target prioritization: wounded (<50% HP) > casters > nearest
- Skill interval reduced 2.0s -> 1.5s with jitter

### Code Quality (Phase 1 Refactor)
- `Config` class centralizes all magic numbers
- `combat_and_move()` -- single unified handler replaces 9 duplicate functions
- `pick_up_loot()` -- single unified handler replaces 3 duplicate functions
- Backwards-compatible wrappers so all FSM lambdas still work
- 3563 -> 3025 lines (-15%)

### Timing
- 43 instances of `transition_delay_ms=5000` -> `3000`
- 38 instances of NPC interaction delay `2000` -> `1500`

### Human-Like Behavior
- `jitter()` function: +/-30% variance on all timings
- `jitter_coord()`: +/-25 game units on waypoints
- Post-combat micro-pauses (8% chance, 0.5-2s)
- Session pauses every ~40 runs (8-20s break)

### Stats
- XP gained, XP/hour, time-to-next-level in UI
- Loot collection states added after Rurik and Hamnet kills

---

## What Was Done: LDoA REID15 v2 (Full Rewrite)

### Architecture Change
- **v1**: Raw `FSM` class with `AddState()` lambdas, per-frame `update()` calls
- **v2**: `GLOBAL_CACHE.Coroutines` with Python generators, `yield from Routines.Yield.*`
- Based on Vaettir Bot v3 Optimized architecture (proven pattern)

### Key Design Decisions
1. **No `Botting` class** -- v2 uses `GLOBAL_CACHE.Coroutines` directly (same as Vaettir v3). The `Botting` class adds overhead not needed for Pre-Searing.
2. **Generic profession handler** -- `_run_profession_level1()` takes parameters for all 6 professions instead of 6 separate functions
3. **Generic nick farm handler** -- `RunNickItemFarm()` takes path lists and outpost params
4. **`combat_loop()` generator** -- single combat+loot+movement function used by all routes
5. **Inventory management** via `Routines.Yield.Items.IdentifyItems()` and `SalvageItems()` -- framework handles throttling

### Inventory Safety
- `NICK_ITEM_MODELS` frozenset -- collectible items are NEVER salvaged
- `KIT_MODELS` frozenset -- salvage/ID kits are never salvaged
- Default: only salvage whites, blues OFF (safe for Pre-Searing)
- Uses `Routines.Yield.Items.SalvageItems()` which handles timing internally (no crash risk)

### What's in v2
- 2152 lines, 49 functions, 4 classes
- All 6 Level 1 profession quests (via generic handler)
- Charr at the Gate (lvl 2-10 loop)
- Farmer Hamnet (lvl 11-20 loop)
- 12 Nick item farm routes
- Charr Gate Opener, Tame Pet, Grand Tour
- 4 Travel routines, 4 Skill unlock quests
- Nicholas Sandford turn-in
- Full UI with all tabs (Leveling, Nick Items, Misc, Travel, Skills, Stats, Inventory)

---

## Known Issues / Open Problems

### Critical (Must Fix Before Production)
1. **`Routines.Yield.Movement.FollowPath()` API not verified** -- v2 assumes it takes `(path_points, exit_condition)` based on Vaettir v3 usage. The actual Pre-Searing bot may need different parameters. Test this first.
2. **`GLOBAL_CACHE` availability** -- v1 uses `from Py4GWCoreLib import*` which may or may not export `GLOBAL_CACHE`. If not available, v2 won't work. Check import.
3. **Coroutine stepping** -- v2's `main()` calls `next(coro)` on each frame. If Py4GW calls `main()` differently than expected, coroutines won't advance.

### Medium Priority
4. **`Agent.IsCaster()` may not exist** in Pre-Searing context -- `pick_best_target()` has a try/except fallback but should be verified
5. **`Item.Usage.IsSalvageable()` / `Item.Rarity.IsWhite()` etc** -- used in salvage filters but not verified against actual Pre-Searing item data
6. **`Inventory.EquipItem()` signature** -- v2 uses `Inventory.EquipItem(Item.GetItemIdFromModelID(model))` but v1 used `Inventory.EquipItem(item_id, agent_id)`. Check if agent_id param is needed.
7. **Nick item ModelID enum names** -- v2 uses `ModelID.Spider_Leg`, `ModelID.Baked_Husk` etc with `hasattr()` guards. Some may not exist in the enum.

### Low Priority / Future Work
8. **Vanguard quest automation** not implemented (daily quests for lvl 10-20, biggest XP source)
9. **Death leveling** not implemented (needed for lvl 14-20)
10. **Auto-sell to merchant** not implemented (only ID + salvage in field)
11. **Path optimization** -- many routes have excessive waypoints (enchanted lodestones: 45 points)
12. **Per-tick caching** -- `Player.GetAgentID()` called multiple times per frame
13. **Inventory full detection** -- should check before starting farm runs

---

## Py4GW API Quick Reference

### Movement (Yield pattern)
```python
yield from Routines.Yield.Movement.FollowPath(path_points, exit_condition)
yield from Routines.Yield.Map.TravelToOutpost(map_id, log)
yield from Routines.Yield.Map.WaitforMapLoad(map_id, log)
yield from Routines.Yield.wait(milliseconds)
```

### Inventory (Yield pattern -- SAFE, throttled internally)
```python
yield from Routines.Yield.Items.IdentifyItems(item_array, log)
yield from Routines.Yield.Items.SalvageItems(item_array, log)
yield from Routines.Yield.Items.DepositItems(item_array, log)
yield from Routines.Yield.Items.DepositGold(keep_amount, log)
yield from Routines.Yield.Merchant.BuyIDKits(count, log)
yield from Routines.Yield.Merchant.BuySalvageKits(count, log)
yield from Routines.Yield.Merchant.SellItems(items, log)
```

### Inventory (Direct API -- use with caution, needs manual throttling)
```python
Inventory.GetFreeSlotCount()
Inventory.GetModelCount(model_id)
Inventory.GetFirstIDKit()
Inventory.GetFirstSalvageKit()
Inventory.IdentifyItem(item_id, kit_id)    # CAN CRASH if spammed
Inventory.SalvageItem(item_id, kit_id)     # CAN CRASH if spammed, 350ms minimum between calls
```

### Combat
```python
Agent.GetHealth(id)         # Returns 0.0-1.0 ratio (NOT absolute HP)
Agent.GetEnergy(id)         # Returns 0.0-1.0 ratio (only for players/heroes)
Agent.IsDead(id)            # Checks is_dead OR hp < 0.01
Agent.IsCasting(id)         # Currently casting a skill
Agent.IsKnockedDown(id)     # Knocked down state
Skill.Data.GetRecharge(id)  # BASE recharge time, NOT current cooldown!
Skill.Data.GetEnergyCost(id)# Energy cost of skill
```

### Important Gotchas
- `Skill.Data.GetRecharge()` returns the skill's BASE recharge time (e.g., 45s for Shadow Form). It does NOT tell you if the skill is currently on cooldown. There is no direct API for current cooldown state -- you must track it yourself or just attempt to cast and let the game reject it.
- `Agent.GetHealth()` returns a 0.0-1.0 float ratio, not absolute HP. Multiply by `Agent.GetMaxHealth()` for absolute value.
- `time.sleep()` BLOCKS the entire Py4GW frame loop. NEVER use it in per-frame code. Use `yield from Routines.Yield.wait()` instead.
- Pre-Searing has NO heroes, NO Hero AI, limited skills, no inscriptions.

---

## Architecture Decision Log

| Decision | Reason |
|----------|--------|
| Yield/Coroutine over raw FSM | Cleaner code, built-in Yield.Items throttling, easier to read/maintain |
| `GLOBAL_CACHE.Coroutines` over `Botting` class | Simpler, less overhead, matches Vaettir v3 pattern |
| Generic `_run_profession_level1()` over 6 functions | 6 professions differ by ~5 parameters, not logic |
| `combat_loop()` as generator over FSM states | Natural flow control, no state machine boilerplate |
| Nick items in frozenset blacklist | O(1) lookup, prevents accidental salvage |
| Jitter built into all timings | ~8-10% speed overhead, much harder to detect |
| `ActionQueueNode(350)` rejected for v2 | Yield system handles throttling internally |
