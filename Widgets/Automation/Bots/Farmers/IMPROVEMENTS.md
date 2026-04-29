# Farm Bot Improvements — 2026-03-30

## Overview

Analyzed 80+ farming bots in the Py4GW project. Created improved v2/v3 versions of the 4 worst bots and extracted a reusable framework (`FarmBotBase.py`) that any future bot can use.

## Key Problems Found (across all 80+ bots)

- **21 Nicholas Traveler bots**: Zero safety features (no wipe handler, no danger check, no zone entry handling)
- **8 Simple Green Farmers**: Same — static paths, no recovery, get stuck on zone entry mobs
- **Only 1 bot** (Auspicious Beginnings 3.0) used real A* pathing
- **Massive code duplication**: 21 Nicholas bots are copy-paste with only waypoints changed

## New Files Created

### FarmBotBase.py (Framework)

**Location:** `Widgets/Automation/Bots/Farmers/FarmBotBase.py`
**Lines:** 450 | **Functions:** 20

7 reusable components any farm bot can import:

| Component | What it does |
|-----------|-------------|
| `create_zone_entry_handler()` | After zoning in: no mobs = proceed, small pack = fight, large pack = retreat |
| `create_boss_scanner()` | Background coroutine scanning compass for boss model ID every second |
| `create_danger_check()` | Returns True if >N enemies AND any party member <X% HP |
| `create_patrol_intercept()` | Walk waypoints, scan for boss, rush when spotted, pause if overwhelmed |
| `create_kill_wait()` | Wait for boss to die with wall-clock timeout and re-approach |
| `create_wipe_handler()` | Pause FSM on wipe, wait for revive, resume at correct step |
| `create_farm_reset()` | Clear all state between farm loops |

### Asterius Scythe v2 & v3

**Location:** `Weapons/Green_Unique/Scythe/`

| Version | Lines | Features |
|---------|-------|----------|
| **Original** (by Mark) | 214 | 7 static waypoints, basic boss scan, 3-min blind timeout |
| **v2** | 575 | 19 waypoints, zone entry handler, danger check, background scanner, wall-clock timeout, wipe handler |
| **v3** | 143 | Same features as v2 but uses FarmBotBase — 75% less code |

**Boss:** Asterius the Mighty (Model ID 6509), Dervish Minotaur, Varajar Fells
**Drop:** Asterius' Scythe (unique green)
**Patrol:** Counter-clockwise SW quarter, ~2 min cycle in HM

### Eye of Argon v2

**Location:** `Weapons/Green_Unique/Shield/Eye of Argon v2.py`

| Version | Lines | Features |
|---------|-------|----------|
| **Original** | 76 | 5 waypoints, zero safety, zero wipe handling |
| **v2** | 674 | 16 waypoints, zone entry, boss scan (Model ID 5245), danger check, wipe handler, background scanner |

**Boss:** Overseer Boktek, Jahai Bluffs (via Sunspear Sanctuary)
**Drop:** Eye of Argon (unique shield)

### Darkroot v2

**Location:** `Weapons/Green_Unique/Dagger/Darkroot v2.py`

| Version | Lines | Features |
|---------|-------|----------|
| **Original** | 99 | 2 waypoints (!), zero safety |
| **v2** | 590 | 17 waypoints, zone entry, boss scan (Model ID 5138), danger check, wipe handler, background scanner |

**Boss:** Darkroot, Warden of Earth, Ferndale (via Brauer Academy)
**Drop:** Darkroot's Daggers (unique daggers)

### The Mindsquall v2

**Location:** `Weapons/Green_Unique/Focus/The Mindsquall v2.py`

| Version | Lines | Features |
|---------|-------|----------|
| **Original** | 110 | 8 waypoints, bounty NPC, zero safety |
| **v2** | 588 | 24 waypoints, zone entry, boss scan (Model ID 5647 + boss glow fallback), danger check, wipe handler, bounty NPC preserved |

**Boss:** The Mindsquall, Kirin, Magus Stones (via Rata Sum)
**Drop:** The Mindsquall (unique focus)

## What Each v2 Bot Adds

Every v2 bot follows the same pattern:

### 1. Zone Entry Safety
After zoning into the explorable:
- **0 enemies nearby** → proceed without aggroing
- **Small pack (<=4)** → stand and let HeroAI fight
- **Large pack (>4)** → retreat toward farm route, fight along the way

### 2. Boss Scanning
- Background coroutine runs every 1 second
- Scans compass range for boss model ID
- Fallback: `Agent.HasBossGlow()` detects any boss if model ID is wrong
- Logs every boss model ID found (for tuning)

### 3. Danger Check
- Triggers when **>8 enemies on compass** AND **any party member <50% HP**
- Bot pauses movement and waits for HeroAI to resolve combat
- Resumes patrol once team is out of danger

### 4. Dynamic Intercept
- When boss spotted on compass: break from patrol, A* path directly to boss
- Mobs aggro naturally as team runs through them (natural balling)
- If boss moves out of range during fight: re-approach

### 5. Wipe Handler
- `OnPartyWipe` callback pauses FSM
- Waits for resurrection
- Resumes at the combat step (not from the beginning)

### 6. Wall-Clock Timeouts
- Kill wait uses `time.time()` (not iteration counting)
- Prevents infinite waits if pathing takes unexpectedly long

### 7. CLAUDE.md Compliance
- Map guards in every coroutine loop
- Zero silent exception swallows
- Proper generator/non-generator separation (Rule 17)
- All exceptions logged with `Console.MessageType.Warning`

## Model ID Notes

Some boss model IDs are estimated. The scanner logs every boss it finds, so after the first run you can check the console output and update the constant if needed:

| Bot | Model ID | Confidence |
|-----|----------|-----------|
| Asterius | 6509 | Confirmed (from original bot) |
| Eye of Argon (Boktek) | 5245 | Estimated — has boss glow fallback |
| Darkroot | 5138 | Estimated — has boss glow fallback |
| The Mindsquall | 5647 | Estimated — has boss glow fallback |

## How to Build a New Bot Using FarmBotBase

```python
import os
from Py4GWCoreLib import Botting, Map
from Widgets.Automation.Bots.Farmers.FarmBotBase import (
    create_boss_scanner, create_zone_entry_handler,
    create_danger_check, create_patrol_intercept,
    create_wipe_handler, create_farm_reset
)

BOT_NAME = "My Boss Farm"
BOSS_MODEL_ID = 1234
OUTPOST = 123
EXPLORABLE = 456
EXIT_COORDS = (-100, 200)

WAYPOINTS = [
    (-1000, -2000),
    (-3000, -4000),
    # ... more waypoints
]

bot = Botting(BOT_NAME)

# Create components
boss = create_boss_scanner(BOT_NAME, BOSS_MODEL_ID)
scan_fn, check_dead_fn, scanner_coro, boss_state = boss

danger_fn = create_danger_check(BOT_NAME)
zone_coro, zone_state_fn = create_zone_entry_handler(bot, BOT_NAME, WAYPOINTS[2])
patrol_coro, patrol_launch, patrol_done = create_patrol_intercept(
    bot, BOT_NAME, WAYPOINTS, boss_state, danger_fn
)
wipe_cb = create_wipe_handler(bot, BOT_NAME, "[H]Start Combat")
reset_fn = create_farm_reset(bot, boss_state, ["BossScanner", "Patrol"])

# Wire FSM
def farm(bot):
    bot.States.AddHeader(BOT_NAME)
    bot.Templates.Multibox_Aggressive()
    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST)
    bot.Party.SetHardMode(True)
    bot.Events.OnPartyWipeCallback(wipe_cb)
    bot.States.AddManagedCoroutine("BossScanner", scanner_coro)

    bot.States.AddHeader("Exit To Farm")
    bot.Move.XYAndExitMap(*EXIT_COORDS, target_map_id=EXPLORABLE)
    bot.Wait.ForTime(3000)

    bot.States.AddHeader("Zone Entry Safety")
    bot.States.AddCustomState(zone_state_fn, "Handle exit mobs")

    bot.States.AddHeader("Start Combat")
    bot.States.AddCustomState(patrol_launch, "Patrol")
    bot.Wait.UntilCondition(patrol_done, duration=1000)

    bot.Wait.ForTime(10000)
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(reset_fn, "Reset")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(10000)
    bot.States.JumpToStepName("[H]Exit To Farm")

bot.SetMainRoutine(farm)
```

~50 lines for a fully-featured farm bot with all safety features.

## Morpheus Review Scores

| Bot | Score | Syntax | Map Guards | Silent Swallows |
|-----|-------|--------|-----------|-----------------|
| Asterius v2 | 9/10 | OK | 15 | 0 |
| Eye of Argon v2 | 9/10 | OK | 22 | 0 |
| Darkroot v2 | 9/10 | OK | 15 | 0 |
| Mindsquall v2 | 9/10 | OK | 15 | 0 |
| FarmBotBase | 10/10 | OK | 17 | 0 |
| Asterius v3 | 10/10 | OK | 0 (in base) | 0 |

## Next Steps

1. **Test in-game** — load each v2 bot and verify pathing, boss detection, zone entry handling
2. **Update model IDs** — check console logs after first run, update constants
3. **Migrate Nicholas Traveler bots** — could all use FarmBotBase with a JSON config per trophy
4. **Add A* pathing** to FarmBotBase patrol (currently uses `bot.Move._coro_get_path_to` for boss rush only)

---

## Day 2 Improvements — 2026-03-30

### Phase 1: Nicholas Traveler Consolidation
- **20 copy-paste bots → 1 universal bot** with ImGui trophy selector
- 20 JSON configs in `nicholas_configs/` with all paths, outpost IDs, special cases
- Special cases preserved: challenge missions, double-zone, bounty NPCs, intermediate paths
- FarmBotBase safety features added: zone entry, danger check, wipe handler

### Phase 2: Keiran Farm Consolidation
- **3 clone bots → 1 universal farm** with quest selection dropdown
- QuestConfig data class encodes per-quest differences (dialog, waypoints, build, combat style)
- Death handler + bow management preserved from originals
- FarmBotBase zone entry + wipe handler added

### Phase 3: 5 Green Farmers Upgraded
All 5 now have zone entry, danger check, boss scanning, wipe handler, expanded paths, cleanup & loot:

| Bot | Original Lines | v2 Lines | Waypoints (old→new) |
|-----|---------------|----------|---------------------|
| Ice Breaker | 125 | 340 | 22→28 |
| Kepkhet's Refuge | 112 | 393 | 7→19 |
| The Scar Eater | 112 | 378 | 7→16 |
| Rajazan's Fervor | 126 | 741 | 20→27 |
| Brightclaw | 120 | 325 | 9→14 |

### Phase 4: Morpheus Review
- All 12 bots reviewed: 10 scored 10/10, 2 universal bots fixed from 6-7/10 → pass
- Zero silent exception swallows across all files

### Phase 5: AutoLootManager Syntax Fix
- Pre-existing IndentationError fixed (missing `pass` in comment-only `if` block)
- **Smoke test: 66/66 (was 65/66)**
- verify_custom_changes: 49/49

### Phase 6: Bot Integration from Download
- LDoA REID15 v1 (3,078L) + v2 (2,151L) — Pre-Searing leveler
- Vaettir Bot v3 Optimized (1,118L) — reference implementation
- Rajazan Optimized (252L) — alternate optimized version
- API research docs + Rajazan map + handoff notes

### Safety Coverage
- **Before (Day 1 start):** ~10% of bots had safety features
- **After Day 1:** ~40% coverage (4 v2 bots + FarmBotBase)
- **After Day 2:** **68% coverage** (50 of 73 bots have safety features)

### Total New Files: 54
- 30 Python scripts (all syntax OK)
- 20 JSON configs (all valid)
- 4 documentation files
