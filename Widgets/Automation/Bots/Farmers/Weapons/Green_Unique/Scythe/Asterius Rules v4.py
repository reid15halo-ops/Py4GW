"""Asterius v4 Rules -- drastische Vereinfachung fuer stabilen Betrieb.

Dieses File definiert die neuen Regeln. Wird von v3 importiert oder
ersetzt die entsprechenden Sections.

Design-Philosophie:
- KEIN Wait > 10s (ausser Patrol)
- KEIN Loot-Sweep (HeroAI Auto-Loot reicht)
- KEIN WaitUntilOnOutpost (Outpost = sofort weiter)
- KEIN RemoveManagedCoroutine (FSM list.remove crash)
- JEDER Wait hat max_timeout_s
- Tote Spieler = sofort resignen wenn Boss nicht spotted
- Zone-Back = Raw Move 5s, dann A*
- Coroutines enden NIE (while True am Ende)
"""

# ===========================================================================
# TIMING RULES -- keine Zeitverschwendung
# ===========================================================================

# Outpost
KIT_CHECK_TIMEOUT_S = 7        # max warten auf Kit-Restock
OUTPOST_SETTLE_MAX_S = 15      # max warten bis Outpost ready nach Resign
INVENTORY_WAIT_MS = 2000       # InventoryPlus ID/Salvage Zeit

# Combat
POST_KILL_WAIT_MS = 10000      # HeroAI Auto-Loot Zeit nach Boss-Kill (KEIN Sweep)
OOC_TIMEOUT_S = 10             # max warten auf Out-of-Combat
DEATH_OOC_WAIT_S = 3           # extra warten wenn jemand tot ist

# Resign
RESIGN_WAIT_MS = 1500          # zwischen Resign-Attempts
RESIGN_ATTEMPTS = 2            # 2x reicht

# Zone Entry
ZONE_SPRINT_S = 5              # Raw Move weg vom Portal
ZONE_ENTRY_AGGRO_WAIT_S = 10   # max warten auf aggro drop

# Patrol
DEAD_ABORT_S = 20              # Reid Tao tot + Boss nicht found = abort nach 20s
BOSS_SCAN_RANGE = 4800.0       # voller Compass
BOSS_RUSH_TIMEOUT_S = 60      # kein Distanz-Cap, immer engagen
PATROL_TIMEOUT_S = 600         # 10 Min max fuer gesamten Patrol

# Death
MASS_DEATH_THRESHOLD = 4       # erst bei 4+ Toten Mass-Resign
MASS_DEATH_LOOT_S = 10         # Loot-Fenster vor Mass-Resign


# ===========================================================================
# STABILITY RULES -- kein Crash, kein Stuck
# ===========================================================================

RULES = """
1. KEIN WaitUntilOnOutpost -- ersetze durch UntilCondition(is_outpost, max_timeout_s=15)
2. KEIN Loot-Sweep -- HeroAI Auto-Loot via headless_tree._handle_looting reicht
3. KEIN RemoveManagedCoroutine -- Coroutines bleiben alive (while True am Ende)
4. KEIN _coro_follow_path_to im Loot-Context -- triggert FSM list.remove crash
5. JEDER UntilCondition braucht max_timeout_s (default 300s ist zu lang)
6. JEDE Coroutine endet mit `while True: yield from wait(5000)` statt `return`
7. Zone-Entry: 5s Raw Player.Move(), dann erst A* FollowPath
8. Dead Player = sofort FAILURE in _handle_looting und _follow
9. Agent.IsValid() VOR jedem Player.Interact() (verhindert GW1 itemTable crash)
10. Player.Interact() ist korrekt -- NICHT Player.InteractAgent (existiert nicht)
11. Shouts/Chants/Stances sind INSTANT -- nie durch GetCasting blockieren
12. Adrenaline-Skills skippen recharge-Check in IsSkillReady
13. Flash Enchantments (Sand Shards) als Shout.value konfigurieren
14. Master-Only Tracking: nur Party Leader schreibt farm_stats.jsonl
15. Early-Abort (player dead) zaehlt NICHT als strategischer Loss im Learner
"""


# ===========================================================================
# FSM FLOW -- minimale Steps
# ===========================================================================

FSM_FLOW = """
INITIAL (einmalig):
  1. [H] Bot Name
  2. Set combat follow threshold
  3. [H] Prepare For Farm
  4. Travel to Olafstead
  5. Invite all
  6. [H] Initial Restock
  7. Kit check (7s timeout)
  8. Wait 2s (InventoryPlus)

LOOP (wiederholt ab hier):
  9.  [H] Strategy
  10. Select strategy + Set HM (1 Step, nicht 2)
  11. Wait 500ms

  12. Start managed coroutines (Scanner, DeathMonitor)
  13. Disable events

  14. [H] Exit To Farm
  15. Disable pause_on_danger
  16. XYAndExitMap
  17. Wait 500ms
  18. Enable pause_on_danger

  19. [H] Zone Entry
  20. Raw Move 5s + A* to safe point

  21. [H] Patrol
  22. Launch Intercept (max 600s)

  23. Record result (skip if early_abort)
  24. Wait 10s (HeroAI Auto-Loot)
  25. Resign x2 (1.5s between)
  26. Reset farm
  27. Wait for outpost (max 15s)
  28. Check kits
  29. Jump to Step 9

TOTAL: ~29 Steps (war 45+)
"""


# ===========================================================================
# PERFORMANCE TARGETS
# ===========================================================================

TARGETS = """
- Avg Run Time: < 180s (aktuell ~190s)
- Win Rate: > 90% (aktuell ~100% mit mutant_g2)
- Outpost Time: < 20s (aktuell ~30s)
- Zone Entry: < 40s (aktuell ~35s)
- Deaths per Run: < 0.5 (Reid Tao stirbt ~30% der Runs)
- Crashes per Hour: 0 (war ~2-3/h durch FSM list.remove)
- Gold per Hour: tracking nach Loot-Fix
"""


# ===========================================================================
# KNOWN ISSUES (solved)
# ===========================================================================

SOLVED = """
- Player.InteractAgent existiert nicht -> Player.Interact
- LootConfig filtert alles weg -> direktes AgentArray.GetItemArray
- GetCasting blockiert Shouts -> is_instant Check
- IsSkillReady blockiert Adrenaline -> adr_req Check
- FSM list.remove crash -> Coroutines enden nie
- Zone-Back -> Raw Move 5s statt A* bei Portal
- WaitUntilOnOutpost 300s -> UntilCondition 15s
- Loot-Sweep crash -> entfernt, HeroAI Auto-Loot
- 5x Stats-Duplikat -> Master-Only Tracking
- Death-Abort als Loss -> early_abort Flag
"""
