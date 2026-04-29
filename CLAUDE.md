# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Py4GW is a Python automation framework for Guild Wars 1 that injects via DLL into the game client. It supports multiboxing (6 simultaneous clients), combat AI, inventory management, and scripting via hot-loadable Python widgets. Built on GWCA (C++ memory API) with a Python bridge.

**Requirements:** Python 3.13.0 32-bit ONLY. Other versions crash the GW client.

## Architecture

```
Py4GW-main/
├── Py4GW.dll              # C++ DLL injected into GW client (bridges GWCA → Python)
├── Py4GW_Launcher.py      # Multi-client launcher (INI config → inject DLL → spawn)
├── bridge_daemon.py       # IPC socket server for 6-client coordination
├── accounts.json          # Account roster (master + 5 slaves)
│
├── Py4GWCoreLib/          # Framework library — DO NOT MODIFY unless fixing bugs
│   ├── Agent.py           # Agent API (NPC/player/enemy queries)
│   ├── Inventory.py       # Inventory + salvage + dialog handling
│   ├── Skill.py           # Skill data and casting
│   ├── Map.py             # Map state, loading, travel
│   ├── Player.py          # Player state
│   ├── Party.py           # Party management
│   ├── Routines.py        # High-level routines (Helpers, Checks, Targeting, Yield)
│   ├── GlobalCache/       # Shared memory + caching (GLOBAL_CACHE singleton)
│   ├── py4gwcorelib_src/
│   │   ├── BehaviorTree.py    # BT framework (Node, Sequence, Selector, Parallel)
│   │   ├── ActionQueue.py     # Serialized game action execution
│   │   ├── WidgetManager.py   # Widget lifecycle management
│   │   └── Profiling.py       # SimpleProfiler for timing
│   ├── Builds/            # Per-profession build templates
│   └── enums_src/         # Game enums and constants
│
├── HeroAI/                # Combat AI system (the main automation brain)
│   ├── combat.py          # Core combat engine — skill execution, target selection
│   ├── targeting.py       # Weighted multi-factor enemy/ally scoring
│   ├── following.py       # Formation/follow logic for party coordination
│   ├── commands.py        # Command dispatch
│   ├── cache_data.py      # Per-frame combat state cache
│   ├── custom_skill_src/  # Per-profession skill databases (10 professions + PVE)
│   └── types.py           # SkillNature, Skilltarget, SkillType enums
│
├── Widgets/               # Hot-loadable Python scripts (drop .py file, it runs)
│   └── Automation/
│       ├── Multiboxing/   # HeroAI.py, MultiboxCommander.py, FollowingModule.py
│       └── Helpers/       # AutoLootManager.py, AutoStore/, Dashboard/
│
├── Settings/              # Per-account INI configs (keyed by email)
├── Sources/               # Community-contributed bot scripts
└── docs/                  # Architecture docs, build guides
```

### Key Patterns

- **Shared Memory:** 6 clients coordinate via `ctypes.Structure` mapped to raw shared memory. NO locks, NO atomics. New multi-byte fields require sequence counter + double buffer protocol.
- **ActionQueueManager:** ALL game API calls go through `ActionQueueManager().AddAction()`. Never call game functions directly.
- **Coroutines:** Multi-step operations use `GLOBAL_CACHE.Coroutines.append()` with `yield from Routines.Yield.wait()`. State machines with timers are insufficient.
- **BehaviorTree:** Nodes have `tick_interval_ms` (throttle) and `per_frame_exempt` (safety override). Dialog/healing nodes MUST set `per_frame_exempt=True`.
- **Widget System:** ImGui-based. Widgets load from `Widgets/` subdirs. Per-widget INI config. All overlay visuals default to OFF.
- **Routines.Helpers.Multibox:** Widgets access multibox functions via this path. It MUST stay exposed — removing it causes silent button failures.

## CRITICAL: Game Action Safety (007 Disconnect Prevention)

This project automates Guild Wars 1 with 6 simultaneous clients. **Any unhandled game dialog causes error 007 (connection lost) and crashes the client.**

### Rules for ANY code that calls game actions:

1. **NEVER call `Inventory.SalvageItem()` without handling ALL possible dialogs:**
   - Materials confirmation ("are you sure?") — call `AcceptSalvageMaterialsWindow()` 3x
   - Salvage Choice Dialog (upgrade selection) — use `Inventory.HandleSalvageChoiceDialog()` coroutine
   - The choice dialog MUST scan option text to pick upgrades vs materials — never blindly click

2. **ALWAYS use coroutines for multi-step game interactions:**
   - Append to `GLOBAL_CACHE.Coroutines` — this is the framework's coroutine runner
   - Use `yield from Routines.Yield.wait()` for delays
   - Use `yield from Inventory.HandleSalvageChoiceDialog()` for salvage choice handling
   - State machines with timers are NOT sufficient — they can't call coroutine-based framework handlers

3. **ALWAYS use `ActionQueueManager` for game API calls:**
   - `ActionQueueManager().AddAction("ACTION", function, args...)` — never call game functions directly
   - This serializes actions and prevents race conditions
   - NEVER call `Inventory.IdentifyItem`, `Inventory.PickUpItem`, or `Trading.Merchant.SellItem` directly. Always wrap in `ActionQueueManager().AddAction('ACTION', ...)`

4. **Throttle ALL automated actions:**
   - Minimum 400ms between server-facing actions (deposit, salvage, identify, sell)
   - Add random jitter (0-0.5s) to desync the 6 accounts
   - Never fire actions while a dialog is pending

5. **Handle map changes:**
   - Check `Map.IsMapLoading()` and `Map.IsMapReady()` in loops
   - Abort operations if map changes mid-action

### Reference implementations:
- Canonical salvage pattern: `Py4GWCoreLib/py4gwcorelib_src/AutoInventoryHandler.py` `SalvageItems()`
- Choice dialog handler: `Py4GWCoreLib/Inventory.py` `HandleSalvageChoiceDialog()`
- Auto identify+salvage+deposit: Use `InventoryPlus` widget (wraps `AutoInventoryHandler`). Do NOT reimplement.

## CRITICAL: Do Not Reimplement Framework Features

16. **NEVER reimplement identify/salvage/deposit logic:**
    - `AutoInventoryHandler` already handles all dialogs, retries, blacklists, and edge cases
    - `InventoryPlus` wraps it with auto-trigger on outpost entry and periodic explorable checks
    - Both are battle-tested by VaettirBot, YAVB, and all working bots
    - If you need custom item evaluation, add it as a filter/callback — don't rewrite the salvage loop
    - Reference: `bot.Items.AutoIDAndSalvageItems()` → `AutoInventoryHandler.IDAndSalvageItems()`

17. **NEVER put `yield from` in a function that also has a non-generator code path:**
    - Python makes the ENTIRE function a generator if ANY code path contains `yield`/`yield from`
    - This silently breaks callers that expect a regular function return
    - The `yield_step` decorator was broken this way — split into two separate functions instead
    - Pattern: dispatch to `_generator_path()` or `_regular_path()` from a yield-free wrapper
    - Reference: `Py4GWCoreLib/botting_src/helpers_src/decorators.py`

18. **Widgets do NOT hot-reload on file changes:**
    - Modifying a `.py` file does NOT take effect until full client restart
    - Disable/enable in Widget Manager does NOT reload from disk (module stays cached)
    - The only way to load new code is: restart the GW client
    - A crashing widget crashes EVERY FRAME, stealing CPU from HeroAI healing — fix crashes immediately

19. **Shared memory field access: use full path, never assume top-level attributes:**
    - `AccountStruct.AgentData.Map.MapID` — correct
    - `AccountStruct.MapID` — WRONG, crashes with AttributeError
    - Always wrap shared memory field access in try/except when iterating accounts
    - Reference: `AutoLootManager.py` `_request_kit_from_team()`

20. **AutoMirror runs ONLY on master (jonasglawion@aol.com):**
    - Guard by email, NOT by `Party.IsPartyLeader()` — leader can change if master crashes
    - If a slave becomes leader, it must NOT start issuing travel/summon commands

## CRITICAL: HeroAI / BehaviorTree Safety Rules

These rules apply to ANY changes in `HeroAI/`, `Py4GWCoreLib/py4gwcorelib_src/BehaviorTree.py`, or shared memory structs:

6. **NEVER throttle dialog detection or healing below per-frame tick rate:**
   - `Map.IsMapLoading()`, `Map.IsMapReady()`, dialog window checks → MUST run every frame
   - Healing skill evaluation → MUST run every frame
   - If implementing tiered tick rates in BehaviorTree.Node, these node types are EXEMPT from throttling
   - Reason: A dialog open for >1 frame while another client fires an action = 007 disconnect

7. **Shared Memory writes for new fields require torn-read protection:**
   - `HeroAIOptionStruct` uses `ctypes.Structure` in raw shared memory — NO locks, NO atomics
   - Any new multi-byte field (target IDs, condition arrays) MUST use sequence counter + double buffer
   - Pattern: Writer increments `seq` before AND after write. Reader checks both reads match.
   - Without this: slave reads half-written target_id → passes garbage to `Player.ChangeTarget()` → 007
   - Reference: See `Py4GWCoreLib/GlobalCache/shared_memory_src/HeroAIOptionStruct.py`

8. **Cross-client coordination must be read-only observation, not write-sync:**
   - Each client tracks what IT applied (conditions, hexes, buffs)
   - NEVER have multiple clients writing to the same shared memory field concurrently
   - Use pessimistic model: assume another client MAY have applied the debuff, don't require it
   - Reason: Concurrent writes + stale reads → wrong decisions → rapid-fire actions → 007

9. **Profiling before optimizing:**
   - NEVER change BehaviorTree tick rates without first measuring baseline via SimpleProfiler
   - The actual bottleneck is likely in targeting/scoring loops, NOT BT traversal overhead
   - Measure: ms per client per frame, identify top-5 hotspots, then optimize those specifically

10. **IsReadyToCast() must NEVER set `self.in_casting_routine`** — it is a pure readiness check. `InCastingRoutine()` is the single source of truth for casting state.

## CRITICAL: Inter-Account Messaging Safety (Kit Sharing, Commands)

These rules apply to ANY shared memory message that triggers inventory actions (drop, pickup, sell, deposit):

10. **NEVER broadcast to ALL accounts — send to ONE target only:**
    - Broadcasting causes ALL eligible accounts to act simultaneously (e.g., all drop a kit)
    - Pattern: iterate accounts, send to the FIRST eligible one, then `return`
    - Reference: `AutoLootManager.py` `_request_kit_from_team()`

11. **ALWAYS validate same-map on BOTH sender AND receiver:**
    - Sender: check `acc.MapID != Map.GetMapID()` before sending
    - Receiver: pass map_id in `Params[0]`, receiver checks `int(message.Params[0]) != Map.GetMapID()`
    - Without this: account changes map between send and receive → item dropped in wrong instance → lost

12. **ALWAYS enforce receiver-side cooldown for item drops:**
    - A global `_last_kit_drop_time` with 30s cooldown prevents burst drain from multiple senders
    - Reference: `Messaging.py` `_ShareKit()` — `_KIT_DROP_COOLDOWN_S = 30.0`

13. **DRY for message handlers — use parameterized functions:**
    - Kit sharing, item dropping, restocking → use ONE generic handler with a filter function
    - Reference: `Messaging.py` `_ShareKit(index, message, kit_filter, kit_name)`

14. **Per-type cooldowns for message senders:**
    - NEVER use a single shared cooldown across different request types
    - Requesting a lesser kit must not block requesting an expert kit or ID kit
    - Reference: `AutoLootManager.py` `_kit_cooldowns` dict keyed by SharedCommandType

15. **NEVER silently swallow exceptions in coroutines:**
    - `except Exception: pass` makes 007 crashes impossible to diagnose
    - Always log: `Py4GW.Console.Log(MODULE, f"error: {e}", Console.MessageType.Warning)`

## AutoLootManager Module Structure

AutoLootManager was split into focused modules. When modifying loot/inventory logic:
- Item modifier constants & value tables → Widgets/Automation/Helpers/ItemModTables.py
- Salvage evaluation (keep/extract/salvage) → Widgets/Automation/Helpers/SalvageDecision.py
- Ground loot pickup → Widgets/Automation/Helpers/PickupManager.py
- Merchant selling & inventory-full → Widgets/Automation/Helpers/MerchantManager.py
- Orchestration, config, UI, restock → Widgets/Automation/Helpers/AutoLootManager.py

SalvageDecision.py requires config wiring: `SalvageDecision.config = config` + `SalvageDecision.install_tables(...)` called from AutoLootManager after LootConfig creation.

## CRITICAL: Performance Rules (6-Client Multiplication)

Every per-frame cost is multiplied by 6 clients. A 1ms overhead becomes 6ms system-wide.

21. **NEVER call GetFilteredEnemyArray/GetFilteredAllyArray/GetFilteredSpiritArray per-skill:**
    - `AreCastConditionsMet()` evaluates up to 8 skills per frame. If each calls `GetFilteredEnemyArray()`, that's 8 scans × 6 clients = 48 scans/frame.
    - ALWAYS use `self._get_cached_enemy_array(range_area)` which caches per-frame in `CombatClass._cached_enemy_arrays` (dict keyed by range value, cleared in `Update()`).
    - Same rule applies to ally arrays, spirit arrays, and any expensive game API call inside skill evaluation loops.
    - Reference: `HeroAI/combat.py` `_get_cached_enemy_array()`

22. **NEVER check Player.GetAgentID() == GetPartyLeaderID() before Map.IsMapReady():**
    - During character select / login, BOTH return 0. `0 == 0` evaluates True on ALL 6 clients.
    - ANY party leader check MUST first verify: `Map.IsMapReady() and Map.IsExplorable()`
    - Reference: `HeroAI.py` `EnsureFollowModuleIni()` — was broken by this exact bug.

23. **ALWAYS use try/finally for SnapshotHeroAI/RestoreHeroAI pairs:**
    - `DisableHeroAIOptions()` → skill casting → `RestoreHeroAISnapshot()` MUST use `finally:`
    - If the coroutine throws, is cancelled, or the map loads mid-cast, HeroAI stays permanently disabled on that client.
    - Pattern:
      ```python
      SnapshotHeroAIOptions(email)
      DisableHeroAIOptions(email)
      try:
          yield from cast_skills(...)
      except Exception as e:
          log_error(e)
      finally:
          RestoreHeroAISnapshot(email)
      ```

24. **ALWAYS add Map.IsMapLoading() guard in coroutine casting loops:**
    - Any loop that calls `CastSkillID` must check at the top of each iteration:
      ```python
      if Map.IsMapLoading() or not Map.IsMapReady():
          break
      ```
    - Without this: skills fire into a loading screen → undefined game state → 007

25. **IsReadyToCast() returns tuple (bool, int) — never use truthiness on the result:**
    - `not (False, 0)` is always `False` (non-empty tuple is truthy). This was a bug in all CombatPrep handlers.
    - Use `is_ready, target = self.IsReadyToCast(slot)` then check `if not is_ready:`
    - Or use direct recharge check: `GLOBAL_CACHE.SkillBar.GetSkillBySlot(slot).recharge != 0`

## Pathing & Movement Safety

- **NEVER use `FollowPath([(x, y)])` for distances >100 units.** Use `AutoPathing().get_path_to(x, y)` for A* pathfinding, fall back to direct line only if A* returns empty.
- FollowPath has built-in A* re-route after 10 stuck retries — do not disable or bypass this.
- Formation positions MUST be validated against navmesh before publishing to shared memory (`AutoPathing().get_navmesh().find_nearest_reachable()`).
- Stuck detection is unified via `Py4GWCoreLib/stuck_state.py` — use `update_followpath_state()` / `is_followpath_handling_stuck()` to coordinate. Do NOT add independent stuck detection without checking this.
- Key files: `Py4GWCoreLib/Pathing.py` (A* + NavMesh), `Py4GWCoreLib/routines_src/Yield.py` (FollowPath), `HeroAI/following.py` (formations), `Py4GWCoreLib/stuck_state.py` (shared state).

## CRITICAL: Master Identity (Non-Py4GW Party Members)

26. **NEVER use `GetPartyLeaderID()` or `IsPartyLeader()` to identify the master account:**
    - When a non-Py4GW friend joins the party, party leader can change
    - This breaks follow logic (slaves follow stale positions → circles), AutoMirror, and combat targeting
    - ALWAYS use `Player.GetAccountEmail() == "jonasglawion@aol.com"` to identify master
    - Reference: `HeroAI.py` Follow function, `AutoMirror.py` master guard

27. **ALL party members must be treated equally for heals/prots regardless of Py4GW status:**
    - `Party.GetPlayers()` returns ALL human players including non-Py4GW friends
    - `SortAlliesByPartyPosition` correctly includes them in `player_order`
    - Non-Py4GW players must NOT fall to a lower heal priority
    - Shared memory operations (kit sharing, travel) correctly skip non-Py4GW players (no AccountEmail)

## CRITICAL: Testing Before Every Restart

**Run BOTH tests before every client restart. Fix all failures before allowing restart.**

1. **Deep Smoke Test** (66 checks): `python Tests/test_smoke.py`
   - Syntax check all 25 critical files
   - Decorator generator trap verification (inspect.isgeneratorfunction)
   - MASTER_EMAIL constant in all files (no hardcoded emails)
   - No GetPartyLeaderID for identity checks
   - Skill config validation (no SkillNature.Hex, StripDervishEnchantments, etc.)
   - AutoLootManager config (identify/salvage disabled, pipeline safety)
   - accounts.json valid JSON with all 6 accounts
   - Widget enable/disable state (InventoryPlus ON, FollowingModule OFF)
   - Debug flags OFF (_combat_debug, _follow_debug)
   - Shared memory field path safety

2. **Custom Changes Verification** (49 checks): `python verify_custom_changes.py`
   - All custom markers present in critical files
   - Anti-pattern detection (bare except, reversed combos, etc.)
   - .gitignore safety

**Both must pass with zero failures. Any failure = do not restart.**

## Enum Safety

- Always verify enum values exist before using them (e.g., `SkillNature.Hex` does NOT exist)
- Check `Py4GWCoreLib/enums.py` or `enums_src/` for valid values

## Combat AI Quick Reference

- **Target scoring** (`targeting.py`): Distance(20) + HP(20) + PartyCall(40) + Healer(35) + Caster(12) + Casting(8) + DiscordReady(25) + AntiOverkill(-15 at <10%HP)
- **Skill priority** (`combat.py`): CustomA > Interrupt > Healing > Resurrection > HexRemoval > CondiCleanse > SelfTargeted > EnergyBuff > Buff > Offensive
- **Energy reserve** by profession: Monk=25%, Ritualist=20%, Mesmer/Ele=15%, Paragon/Derv=10%, others=5%
- **Tick-rate gating** (`BehaviorTree.py`): Set `per_frame_exempt=True` on dialog/healing/map-check nodes
