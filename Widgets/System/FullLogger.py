"""FullLogger.py — 8-hour comprehensive logger for all 6 clients.

Writes to Settings/Global/full_log.tsv in a compact TSV format.
Polls every 500ms. Logs state changes only (delta compression).
Skill casts, deaths, map changes, combat state, positions logged as events.

Format: epoch_ms<TAB>acct<TAB>evt<TAB>data
  acct = 0-5 index or * for global
  evt  = single char event type
  data = compact payload

Event types:
  S = skill cast (slot,skill_id,target_id)
  D = death (agent_id)
  R = resurrect (agent_id)
  H = health change >20% (agent_id,hp%)
  M = map change (map_id,type)  type: O=outpost E=explorable L=loading
  C = combat state change (0/1,enemy_count)
  P = position snapshot (x,y)  only every 10s or on large move
  K = kill (target_agent_id,model_id)
  W = wipe (dead_count/total)
  I = item pickup (item_id,model_id)
  X = error/exception (short msg)
  L = loot/gold (gold_amount)
  B = boss event (spotted/killed/lost,agent_id,dist)
  Z = resign sent
  F = FSM state change (step_index,step_name)
  T = strategy change (name,threshold,hp_thresh)
"""

import os
import time
import Py4GW
from Py4GWCoreLib import (
    Agent, GLOBAL_CACHE, Map, Party, Player, Utils, ThrottledTimer,
)

MODULE = "FullLogger"
_LOG_DIR = os.path.join(Py4GW.Console.get_projects_path(), "Settings", "Global")
_LOG_PATH = os.path.join(_LOG_DIR, "full_log.tsv")
# Poll interval for state-change detection. Set at author's documented intent
# (500ms). Note: instant GW1 skills (shouts, stances, chants — CLAUDE.md rule
# 30) may fire and end between polls and will NOT be captured in the log. If
# instant-skill telemetry becomes required, lower this to ~100ms or hook the
# skill event system directly instead of polling.
_POLL_MS = 500
_POS_INTERVAL_S = 10.0
_HEALTH_DELTA = 0.20  # log health changes > 20%
_MOVE_DELTA = 300.0    # log position if moved > 300u
_MAX_HOURS = 8

# State tracking per account (index 0-5)
_prev = {}
_start_time = 0.0
_file = None
_initialized = False
_last_pos_time = {}
_tick_count = 0
# Throttle gate: reuses the project's ThrottledTimer primitive (established
# convention — see HeroAI\windows.py:41). Without this gate main() runs per
# frame (~60×/sec) and its per-account state-scan work causes <1 FPS on slaves.
_poll_timer = ThrottledTimer(_POLL_MS)


def _ms():
    """Epoch ms since logger start."""
    return int((time.time() - _start_time) * 1000)


def _w(acct, evt, data=""):
    """Write one log line. acct=0-5 or '*'."""
    global _file
    if _file:
        try:
            _file.write(f"{_ms()}\t{acct}\t{evt}\t{data}\n")
        except Exception:
            pass


def _init():
    global _file, _start_time, _initialized, _prev
    _start_time = time.time()
    try:
        _file = open(_LOG_PATH, "a", buffering=8192, encoding="utf-8")
        _w("*", "#", f"START {time.strftime('%Y-%m-%dT%H:%M:%S')}")
        _initialized = True
        Py4GW.Console.Log(MODULE, f"Logging to {_LOG_PATH}", Py4GW.Console.MessageType.Info)
    except Exception as e:
        Py4GW.Console.Log(MODULE, f"Failed to open log: {e}", Py4GW.Console.MessageType.Error)
        _initialized = False


def _get_account_agents():
    """Return list of (index, email_prefix, agent_id, is_active) for all 6 accounts."""
    result = []
    try:
        all_acc = GLOBAL_CACHE.ShMem.GetAllAccountData()
        idx = 0
        for acc in all_acc:
            if not acc:
                continue
            try:
                if not acc.IsAccount:
                    continue
                email = acc.AccountEmail or ""
                prefix = email[:5] if email else f"unk{idx}"
                aid = 0
                try:
                    aid = acc.AgentData.AgentID
                except Exception:
                    pass
                active = bool(acc.IsSlotActive)
                result.append((idx, prefix, aid, active))
                idx += 1
            except Exception:
                idx += 1
                continue
    except Exception:
        pass
    return result


def _check_account(idx, prefix, aid):
    """Check one account for state changes and log events."""
    global _prev, _last_pos_time
    now = time.time()
    key = idx
    if key not in _prev:
        _prev[key] = {
            "dead": None, "hp": -1.0, "casting": 0, "map": 0,
            "combat": None, "x": 0.0, "y": 0.0,
        }
        _last_pos_time[key] = 0.0
    p = _prev[key]

    if aid <= 0:
        return

    try:
        # Death / Resurrect
        is_dead = Agent.IsDead(aid) if Agent.IsValid(aid) else None
        if is_dead is not None and is_dead != p["dead"]:
            if is_dead:
                _w(idx, "D", str(aid))
            else:
                _w(idx, "R", str(aid))
            p["dead"] = is_dead

        # Health (>20% change)
        hp = Agent.GetHealth(aid) if Agent.IsValid(aid) else -1.0
        if hp >= 0 and abs(hp - p["hp"]) > _HEALTH_DELTA:
            _w(idx, "H", f"{aid},{int(hp*100)}")
            p["hp"] = hp

        # Casting (skill start)
        casting_skill = Agent.GetCastingSkillID(aid) if Agent.IsValid(aid) else 0
        if casting_skill > 0 and casting_skill != p["casting"]:
            target = 0
            try:
                # Get target from shared memory
                all_acc = GLOBAL_CACHE.ShMem.GetAllAccountData()
                for acc in all_acc:
                    if acc and acc.IsAccount and acc.AgentData.AgentID == aid:
                        target = acc.AgentData.TargetID
                        break
            except Exception:
                pass
            _w(idx, "S", f"{casting_skill},{target}")
        p["casting"] = casting_skill

    except Exception as e:
        _w(idx, "X", str(e)[:40])


def _check_global():
    """Check global state: map, combat, wipe, positions."""
    global _prev, _last_pos_time
    now = time.time()

    # Map change (master only)
    try:
        map_id = Map.GetMapID()
        mtype = "L" if Map.IsMapLoading() else ("E" if Map.IsExplorable() else "O")
        prev_map = _prev.get("_map", 0)
        if map_id != prev_map:
            _w("*", "M", f"{map_id},{mtype}")
            _prev["_map"] = map_id
    except Exception:
        pass

    # Combat state (master)
    try:
        from Py4GWCoreLib import Routines, Range
        in_combat = Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot)
        prev_combat = _prev.get("_combat", None)
        if in_combat != prev_combat:
            enemies = 0
            try:
                from Py4GWCoreLib import AgentArray
                enemies = len(AgentArray.GetEnemyArray())
            except Exception:
                pass
            _w("*", "C", f"{1 if in_combat else 0},{enemies}")
            _prev["_combat"] = in_combat
    except Exception:
        pass

    # Position snapshots (all accounts, every 10s or on big move)
    accounts = _get_account_agents()
    for idx, prefix, aid, active in accounts:
        if aid <= 0 or not active:
            continue
        try:
            x, y = Agent.GetXY(aid)
            key = idx
            if key not in _prev:
                continue
            px, py = _prev[key].get("x", 0.0), _prev[key].get("y", 0.0)
            dist = ((x - px)**2 + (y - py)**2)**0.5
            last_t = _last_pos_time.get(key, 0.0)
            if dist > _MOVE_DELTA or (now - last_t) > _POS_INTERVAL_S:
                _w(idx, "P", f"{int(x)},{int(y)}")
                _prev[key]["x"] = x
                _prev[key]["y"] = y
                _last_pos_time[key] = now
        except Exception:
            pass

    # Wipe check
    try:
        dead = 0
        total = 0
        players = Party.GetPlayers()
        for pl in players:
            total += 1
            try:
                a = Party.Players.GetAgentIDByLoginNumber(pl.login_number)
                if a > 0 and Agent.IsDead(a):
                    dead += 1
            except Exception:
                pass
        prev_dead = _prev.get("_dead_count", 0)
        if dead != prev_dead and dead > 0:
            _w("*", "W", f"{dead}/{total}")
            _prev["_dead_count"] = dead
        elif dead == 0 and prev_dead > 0:
            _prev["_dead_count"] = 0
    except Exception:
        pass


def _flush():
    global _file
    if _file:
        try:
            _file.flush()
        except Exception:
            pass


def main():
    global _initialized, _tick_count, _file

    if not _initialized:
        if not Map.IsMapReady():
            return
        _init()
        if not _initialized:
            return

    if not _poll_timer.IsExpired():
        return
    _poll_timer.Reset()

    # Check 8h limit
    if time.time() - _start_time > _MAX_HOURS * 3600:
        if _file:
            _w("*", "#", "END 8h limit")
            _file.close()
            _file = None
            _initialized = False
        return

    _tick_count += 1

    try:
        # Per-account checks
        accounts = _get_account_agents()
        for idx, prefix, aid, active in accounts:
            if active and aid > 0:
                _check_account(idx, prefix, aid)

        # Global checks (every other tick = ~1s)
        if _tick_count % 2 == 0:
            _check_global()

        # Flush every 10 ticks (~5s)
        if _tick_count % 10 == 0:
            _flush()

    except Exception as e:
        _w("*", "X", str(e)[:60])


if __name__ == "__main__":
    main()
