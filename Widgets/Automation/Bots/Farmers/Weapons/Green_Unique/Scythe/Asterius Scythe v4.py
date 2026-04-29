"""Asterius Scythe Farm v4 -- single-file version with all logic inlined.

All shared farm-bot logic (zone entry, boss scanning, patrol intercept,
danger check, wipe handling, farm reset) and the UCB1 ScenarioLearner
are inlined directly. No external imports beyond Py4GW framework.
"""

import json
import math
import os
import random
import time

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import (
    AgentArray, Agent, Botting, Color, ConsoleLog, Console, GLOBAL_CACHE, ImGui,
    Inventory, Map, Party, Player, Range, Routines, SharedCommandType, Timer, Utils,
)
from Py4GWCoreLib.Py4GWcorelib import ActionQueueManager
from Py4GWCoreLib.enums_src.Model_enums import ModelID

try:
    from Widgets.Automation.Helpers.FarmStats import FarmRunTracker
    from Widgets.Automation.Helpers import AutoLootManager as _ALM
    _loot_config = _ALM.config
except ImportError:
    FarmRunTracker = None
    _loot_config = None

try:
    import PyImGui
except ImportError:
    PyImGui = None


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  INLINED: ScenarioLearner (UCB1-based strategy selection)               ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

class _StrategyStats:
    """Mutable stats for a single strategy."""
    __slots__ = ("name", "params", "wins", "losses", "total_time_ms")

    def __init__(self, name, params):
        self.name = name
        self.params = dict(params)
        self.wins = 0
        self.losses = 0
        self.total_time_ms = 0

    @property
    def trials(self):
        return self.wins + self.losses

    @property
    def success_rate(self):
        return self.wins / self.trials if self.trials > 0 else 0.0

    @property
    def avg_time_ms(self):
        return self.total_time_ms / self.wins if self.wins > 0 else 0.0

    def to_dict(self):
        return {
            "name": self.name,
            "params": self.params,
            "wins": self.wins,
            "losses": self.losses,
            "total_time_ms": self.total_time_ms,
        }

    @classmethod
    def from_dict(cls, d):
        s = cls(d["name"], d.get("params", {}))
        s.wins = d.get("wins", 0)
        s.losses = d.get("losses", 0)
        s.total_time_ms = d.get("total_time_ms", 0)
        return s


class ScenarioLearner:
    """UCB1-based multi-armed bandit for farm strategy selection."""

    def __init__(self, bot_name, save_path=None, min_trials=3):
        self.bot_name = bot_name
        self.save_path = save_path
        self.min_trials = max(1, min_trials)
        self.strategies = {}  # name -> _StrategyStats
        self._order = []      # insertion order for deterministic round-robin

        if save_path and os.path.isfile(save_path):
            self._load()

    def add_strategy(self, name, params):
        """Register a strategy. If already loaded from disk, keeps existing stats."""
        if name not in self.strategies:
            self.strategies[name] = _StrategyStats(name, params)
            self._order.append(name)
        else:
            self.strategies[name].params.update(params)
            if name not in self._order:
                self._order.append(name)

    def select_strategy(self):
        """Pick the next strategy via UCB1. Returns dict with 'name' and 'params'."""
        if not self.strategies:
            raise ValueError("No strategies registered")

        # Phase 1: round-robin until every strategy has min_trials
        for name in self._order:
            s = self.strategies[name]
            if s.trials < self.min_trials:
                return {"name": s.name, "params": dict(s.params)}

        # Phase 2: UCB1 selection
        total = self.total_runs
        best_name = None
        best_score = -1.0

        for name in self._order:
            s = self.strategies[name]
            if s.trials == 0:
                return {"name": s.name, "params": dict(s.params)}
            exploit = s.success_rate
            explore = math.sqrt(2.0 * math.log(total) / s.trials)
            score = exploit + explore
            if score > best_score:
                best_score = score
                best_name = name

        s = self.strategies[best_name]
        return {"name": s.name, "params": dict(s.params)}

    def record_result(self, strategy_name, success, run_time_ms=0):
        """Record the outcome of a run."""
        s = self.strategies.get(strategy_name)
        if s is None:
            return
        if success:
            s.wins += 1
            s.total_time_ms += max(0, int(run_time_ms))
        else:
            s.losses += 1

    @property
    def total_runs(self):
        return sum(s.trials for s in self.strategies.values())

    def get_best_strategy(self):
        """Return the strategy with the highest success rate (or None)."""
        best = None
        best_rate = -1.0
        for s in self.strategies.values():
            if s.trials > 0 and s.success_rate > best_rate:
                best_rate = s.success_rate
                best = s
        if best is None:
            return None
        return {"name": best.name, "params": dict(best.params), "success_rate": best.success_rate}

    # -- Persistence --

    def save(self):
        if not self.save_path:
            return
        data = {
            "bot_name": self.bot_name,
            "strategies": {name: self.strategies[name].to_dict() for name in self._order},
        }
        try:
            os.makedirs(os.path.dirname(self.save_path) or ".", exist_ok=True)
            with open(self.save_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            ConsoleLog("ScenarioLearner", f"Save error: {e}", Console.MessageType.Warning)

    def _load(self):
        try:
            with open(self.save_path, "r") as f:
                data = json.load(f)
            for name, sd in data.get("strategies", {}).items():
                s = _StrategyStats.from_dict(sd)
                self.strategies[name] = s
                if name not in self._order:
                    self._order.append(name)
        except FileNotFoundError:
            pass  # first run — no file yet
        except Exception as e:
            ConsoleLog("ScenarioLearner", f"Load error: {e}", Console.MessageType.Warning)

    # -- UI helper --

    def draw_stats_table(self):
        """Draw an ImGui table with strategy stats."""
        if PyImGui is None:
            return
        if PyImGui.begin_table("##scenario_learner", 5):
            try:
                PyImGui.table_setup_column("Strategy")
                PyImGui.table_setup_column("Wins")
                PyImGui.table_setup_column("Losses")
                PyImGui.table_setup_column("Rate")
                PyImGui.table_setup_column("Avg Time")
                PyImGui.table_headers_row()

                for name in self._order:
                    s = self.strategies.get(name)
                    if s is None:
                        continue
                    PyImGui.table_next_row()
                    PyImGui.table_next_column()
                    PyImGui.text(name)
                    PyImGui.table_next_column()
                    PyImGui.text(str(s.wins))
                    PyImGui.table_next_column()
                    PyImGui.text(str(s.losses))
                    PyImGui.table_next_column()
                    PyImGui.text(f"{s.success_rate:.0%}")
                    PyImGui.table_next_column()
                    PyImGui.text(f"{s.avg_time_ms:.0f}ms" if s.wins > 0 else "-")
            except Exception as e:
                ConsoleLog("ScenarioLearner", f"Table draw error: {e}",
                           Console.MessageType.Warning)
            finally:
                PyImGui.end_table()


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  INLINED: FarmBotBase helper functions                                  ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

def count_nearby_enemies(radius=None):
    """Count enemies within *radius* of the player (default: compass)."""
    if radius is None:
        radius = Range.SafeCompass.value
    try:
        enemies = AgentArray.GetEnemyArray()
        px, py = Player.GetXY()
        return len(AgentArray.Filter.ByCondition(
            enemies,
            lambda aid: Utils.Distance((px, py), Agent.GetXY(aid)) <= radius,
        ))
    except Exception as e:
        ConsoleLog(BOT_NAME, f"count_nearby_enemies error: {e}", Console.MessageType.Warning)
        return 0


def create_danger_check(bot_name, strategy_ref):
    """Return a function() -> bool that reads thresholds from active strategy dynamically."""
    def _team_in_danger():
        try:
            if not Map.IsMapReady() or not Map.IsExplorable():
                return False
            params = strategy_ref.get("params", {})
            enemy_thr = params.get("enemy_threshold", 8)
            hp_thr = params.get("hp_threshold", 0.50)

            enemy_array = AgentArray.GetEnemyArray()
            px, py = Player.GetXY()
            nearby = AgentArray.Filter.ByCondition(
                enemy_array,
                lambda aid: Utils.Distance((px, py), Agent.GetXY(aid))
                <= Range.SafeCompass.value,
            )
            if len(nearby) <= enemy_thr:
                return False

            players = Party.GetPlayers()
            for player in players:
                try:
                    agent_id = Party.Players.GetAgentIDByLoginNumber(player.login_number)
                    if agent_id <= 0 or not Agent.IsValid(agent_id):
                        continue
                    hp_pct = Agent.GetHealth(agent_id)
                    if 0 < hp_pct < hp_thr:
                        return True
                except Exception:
                    continue

            return False
        except Exception as e:
            ConsoleLog(bot_name, f"Danger check error: {e}", Console.MessageType.Warning)
            return False

    return _team_in_danger


BOSS_SCAN_RANGE = Range.SafeCompass.value  # 4800 units — full compass range (detect boss 1-2 WP earlier)


def create_boss_scanner(bot_name, model_id, fallback_boss_glow=True,
                        scan_range=BOSS_SCAN_RANGE):
    """Return (scan_fn, check_dead_fn, background_coro_fn, state_dict)."""
    state = {"spotted": False, "killed": False, "agent_id": -1, "run_id": 0}

    def _scan():
        """Scan enemy array for the boss. Returns (found: bool, agent_id: int)."""
        if state["spotted"] and state["agent_id"] > 0:
            try:
                if Agent.IsValid(state["agent_id"]) and not Agent.IsDead(state["agent_id"]):
                    return True, state["agent_id"]
            except Exception as e:
                ConsoleLog(bot_name, f"Boss validation error: {e}", Console.MessageType.Warning)
            state["spotted"] = False
            state["agent_id"] = -1

        try:
            enemy_array = AgentArray.GetEnemyArray()
            px, py = Player.GetXY()
            enemy_array = AgentArray.Filter.ByCondition(
                enemy_array,
                lambda aid: Utils.Distance((px, py), Agent.GetXY(aid))
                <= scan_range,
            )

            # Primary: exact model ID match (skip dead agents — prevents
            # re-detecting boss corpse from previous run)
            current_run = state["run_id"]
            for enemy_id in enemy_array:
                try:
                    if Agent.IsDead(enemy_id):
                        continue
                    if Agent.GetModelID(enemy_id) == model_id:
                        state["spotted"] = True
                        state["agent_id"] = enemy_id
                        state["_spot_run_id"] = current_run
                        ConsoleLog(bot_name, f"Boss found (model {model_id}) agent {enemy_id} run={current_run}",
                                   Console.MessageType.Info)
                        return True, enemy_id
                except Exception:
                    continue

            # Fallback: boss-glow detection (only accept if model ID also matches,
            # to prevent false positives from other bosses in the area)
            if fallback_boss_glow:
                for enemy_id in enemy_array:
                    try:
                        if Agent.IsDead(enemy_id):
                            continue
                        if Agent.HasBossGlow(enemy_id):
                            try:
                                mid = Agent.GetModelID(enemy_id)
                            except Exception:
                                mid = -1
                            if mid == model_id:
                                state["spotted"] = True
                                state["agent_id"] = enemy_id
                                state["_spot_run_id"] = current_run
                                ConsoleLog(bot_name, f"Boss-glow confirmed (model {mid}) agent {enemy_id} run={current_run}",
                                           Console.MessageType.Info)
                                return True, enemy_id
                    except Exception:
                        continue

        except Exception as e:
            ConsoleLog(bot_name, f"Boss scan error: {e}", Console.MessageType.Warning)

        return False, -1

    def _check_dead():
        """Return True if boss is confirmed dead."""
        if state["killed"]:
            return True
        if state["spotted"] and state["agent_id"] > 0:
            try:
                if Agent.IsDead(state["agent_id"]):
                    state["killed"] = True
                    ConsoleLog(bot_name, "Boss confirmed dead", Console.MessageType.Info)
                    return True
            except Exception as e:
                ConsoleLog(bot_name, f"Boss death check error: {e}", Console.MessageType.Warning)
        return False

    def _background_scanner():
        """Managed coroutine: scan every second, detect boss + death.
        Uses run_id to prevent cross-run contamination: if the scanner's
        spotted data is from a previous run_id, it is stale and ignored."""
        while True:
            try:
                if not Map.IsExplorable() or not Map.IsMapReady():
                    yield from Routines.Yield.wait(1000)
                    continue

                current_run = state["run_id"]

                # If killed flag is set for THIS run, nothing to do
                if state["killed"]:
                    # Cross-run guard: if spotted data is from a stale run, clear it
                    if state.get("_spot_run_id", current_run) != current_run:
                        state["spotted"] = False
                        state["killed"] = False
                        state["agent_id"] = -1
                    yield from Routines.Yield.wait(1000)
                    continue

                if state["spotted"] and state["agent_id"] > 0:
                    # Cross-run guard: stale detection from previous run
                    if state.get("_spot_run_id", current_run) != current_run:
                        state["spotted"] = False
                        state["agent_id"] = -1
                        yield from Routines.Yield.wait(1000)
                        continue
                    try:
                        if Agent.IsDead(state["agent_id"]):
                            state["killed"] = True
                            ConsoleLog(bot_name,
                                       f"Boss died (scanner) agent={state['agent_id']} run={current_run}",
                                       Console.MessageType.Warning)
                    except Exception as e:
                        ConsoleLog(bot_name, f"Scanner death check error: {e}", Console.MessageType.Warning)
                    yield from Routines.Yield.wait(1000)
                    continue

                _scan()

            except Exception as e:
                ConsoleLog(bot_name, f"Scanner error: {e}", Console.MessageType.Warning)

            yield from Routines.Yield.wait(1000)

    return _scan, _check_dead, _background_scanner, state


def _count_dead_players() -> tuple:
    """Count dead vs total human party members. Returns (dead, total)."""
    dead = 0
    total = 0
    try:
        players = Party.GetPlayers()
        for player in players:
            try:
                agent_id = Party.Players.GetAgentIDByLoginNumber(player.login_number)
                if agent_id > 0 and Agent.IsValid(agent_id):
                    total += 1
                    if Agent.IsDead(agent_id):
                        dead += 1
            except Exception:
                continue
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Dead count error: {e}",
                   Console.MessageType.Warning)
    return dead, total


MASS_DEATH_THRESHOLD = 4  # resign when 4+ player characters are dead
MASS_DEATH_LOOT_WINDOW_S = 10  # seconds to loot before resigning


def create_zone_entry_handler(bot, bot_name, safe_point,
                              first_waypoint=None,
                              small_pack_threshold=4, max_wait_ms=30000):
    """Return (coro_fn, custom_state_fn)."""
    aggro_radius = Range.Earshot.value

    # Minimum distance from portal before allowing A* pathfinding.
    # A* can route through portals; micro-stepping cannot.
    _PORTAL_SAFE_DIST = 1500.0

    def _handle_zone_entry_mobs():
        """Coroutine: immediately run to safe point.
        Never fight at zone entry — just sprint past.
        Uses explicit waypoints to avoid A* pathing back through the zone portal.

        Phase 1: micro-step in 200u increments until BOTH conditions met:
          - At least 1500u from the portal entry point
          - OR within 500u of safe_point
        Phase 2: A* FollowPath through intermediate waypoints (away from portal)
                 to safe_point. Only starts when safely beyond portal range.
        """
        # Wait for map to be fully ready after zone
        for _ in range(20):
            if Map.IsMapReady() and Map.IsExplorable():
                break
            yield from Routines.Yield.wait(500)

        expected_map = Map.GetMapID()

        # Record portal entry position — used as reference for distance checks
        try:
            portal_x, portal_y = Player.GetXY()
        except Exception:
            portal_x, portal_y = 0.0, 0.0

        sx, sy = safe_point

        def _wrong_map():
            return not Map.IsMapReady() or Map.GetMapID() != expected_map

        def _dist_to_portal():
            try:
                px, py = Player.GetXY()
                dx, dy = px - portal_x, py - portal_y
                return (dx * dx + dy * dy) ** 0.5
            except Exception:
                return 0.0

        def _dist_to_safe():
            try:
                px, py = Player.GetXY()
                dx, dy = sx - px, sy - py
                return (dx * dx + dy * dy) ** 0.5
            except Exception:
                return 99999.0

        # Phase 1: micro-step away from portal.
        # Continue until far enough from portal OR close enough to safe point.
        # Max 15s to handle combat slowdowns (was 8s, too short when slowed).
        ConsoleLog(bot_name, "Zone entry: micro-stepping away from portal",
                   Console.MessageType.Info)

        _STEP_SIZE = 200.0
        _sprint_end = time.time() + 15  # generous timeout for slowed movement
        while time.time() < _sprint_end:
            if _wrong_map():
                ConsoleLog(bot_name, "Zoned back during micro-step -- aborting",
                           Console.MessageType.Error)
                return
            try:
                px, py = Player.GetXY()
                dx, dy = sx - px, sy - py
                dist_safe = (dx * dx + dy * dy) ** 0.5

                # Exit condition: close enough to safe point
                if dist_safe < 500:
                    break

                # Exit condition: far enough from portal to allow A*
                if _dist_to_portal() >= _PORTAL_SAFE_DIST:
                    break

                # Move only _STEP_SIZE toward safe_point
                ratio = _STEP_SIZE / dist_safe
                step_x = px + dx * ratio
                step_y = py + dy * ratio
                Player.Move(step_x, step_y)
            except Exception:
                pass
            yield from Routines.Yield.wait(300)

        # Safety check: if STILL too close to portal after timeout, keep micro-stepping
        # instead of falling through to A* (which would route through the portal)
        while _dist_to_portal() < _PORTAL_SAFE_DIST and _dist_to_safe() > 500:
            if _wrong_map():
                ConsoleLog(bot_name, "Zoned back during extended micro-step -- aborting",
                           Console.MessageType.Error)
                return
            try:
                px, py = Player.GetXY()
                dx, dy = sx - px, sy - py
                dist_safe = (dx * dx + dy * dy) ** 0.5
                if dist_safe < 1.0:
                    break
                ratio = _STEP_SIZE / dist_safe
                Player.Move(px + dx * ratio, py + dy * ratio)
            except Exception:
                pass
            yield from Routines.Yield.wait(300)

        # Phase 2: A* FollowPath with intermediate waypoints guaranteed
        # to be AWAY from the portal. Uses INTERCEPT_PATH[0] and [1] as
        # intermediate points before the final safe_point.
        if _dist_to_safe() > 500:
            ConsoleLog(bot_name, "Zone entry: A* pathfinding to safe point",
                       Console.MessageType.Info)

            # Build waypoint chain: current pos → INTERCEPT_PATH[0] → [1] → safe_point
            # These intermediate points pull the path AWAY from the portal.
            intermediate_points = []
            if first_waypoint is not None:
                intermediate_points.append(first_waypoint)
            intermediate_points.append((sx, sy))

            yield from Routines.Yield.Movement.FollowPath(
                path_points=intermediate_points,
                custom_exit_condition=_wrong_map,
                timeout=30000,
                tolerance=200,
            )

            if _wrong_map():
                ConsoleLog(bot_name, "Zoned back during A* path -- aborting",
                           Console.MessageType.Error)
                return

        # Brief wait for aggro to drop (max 15s) before starting patrol
        _ooc_start = time.time()
        while (time.time() - _ooc_start) < 15:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                break
            yield from Routines.Yield.wait(1000)

        ConsoleLog(bot_name, "Zone entry: safe -- starting patrol",
                   Console.MessageType.Info)

    def _custom_state_wrapper():
        """Non-generator wrapper for AddCustomState."""
        return _handle_zone_entry_mobs()

    return _handle_zone_entry_mobs, _custom_state_wrapper


def create_patrol_intercept(bot, bot_name, waypoints, boss_state,
                            scan_fn, check_dead_fn, danger_fn,
                            max_loops=3, danger_interval_ms=2000):
    """Return (intercept_coro_fn, launch_fn, done_fn)."""

    def _wait_for_kill():
        """Sub-coroutine: wait for boss to die, re-approach if out of range.
        Max 5 re-approach attempts — if pathfinding keeps failing, give up and
        let the kill timeout handle it."""
        KILL_TIMEOUT_S = 120
        MAX_REAPPROACH = 5
        start = time.time()
        reapproach_count = 0

        while (time.time() - start) < KILL_TIMEOUT_S:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if check_dead_fn():
                ConsoleLog(bot_name, "Boss killed!", Console.MessageType.Info)
                return

            # Re-approach if boss moved out of range (max 5 attempts)
            aid = boss_state["agent_id"]
            if aid > 0 and reapproach_count < MAX_REAPPROACH:
                try:
                    dist = Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
                    if dist > Range.SafeCompass.value:
                        reapproach_count += 1
                        ConsoleLog(bot_name, f"Boss out of range -- re-approaching ({reapproach_count}/{MAX_REAPPROACH})",
                                   Console.MessageType.Info)
                        ax, ay = Agent.GetXY(aid)
                        if ax != 0 or ay != 0:
                            yield from bot.Move._coro_get_path_to(ax, ay)
                            yield from bot.Move._coro_follow_path_to()
                            yield from Routines.Yield.wait(2000)
                            continue  # Check distance again immediately
                except Exception as e:
                    ConsoleLog(bot_name, f"Re-approach error: {e}", Console.MessageType.Warning)

            yield from Routines.Yield.wait(3000)

        _log_and_record(bot_name, "Kill timeout reached")

    _RUSH_TIMEOUT_S = 60  # generous timeout -- always engage, never give up

    def _rush_to_boss(aid):
        """Coroutine: target boss and move toward it, then wait for kill.
        Always engages regardless of distance -- if detected, we go."""
        try:
            dist = Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
        except Exception:
            dist = 0
        ConsoleLog(bot_name, f"Boss spotted! Engaging target ({dist:.0f}u away)",
                   Console.MessageType.Info)
        ActionQueueManager().AddAction("ACTION", Player.ChangeTarget, aid)
        yield from Routines.Yield.wait(400)

        # Rush with A* pathfinding
        _rush_start = time.time()
        while (time.time() - _rush_start) < _RUSH_TIMEOUT_S:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if Agent.IsDead(aid):
                boss_state["killed"] = True
                return
            dist = Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            if dist < Range.Earshot.value:
                break
            ax, ay = Agent.GetXY(aid)
            if ax != 0 or ay != 0:
                try:
                    yield from bot.Move._coro_get_path_to(ax, ay)
                    yield from bot.Move._coro_follow_path_to(forced_timeout=5000)
                except Exception:
                    ActionQueueManager().AddAction("ACTION", Player.Move, ax, ay)
                    yield from Routines.Yield.wait(500)
        # Only non-delegated dead check: gates entry to _wait_for_kill (not a state transition)
        if not Agent.IsDead(aid):
            yield from _wait_for_kill()


    def _intercept_patrol():
        """Walk waypoints, scanning for boss at each step."""
        # Start from the closest waypoint to avoid running backwards
        try:
            px, py = Player.GetXY()
            start_idx = 0
            best_dist = float('inf')
            for i, (wx, wy) in enumerate(waypoints):
                d = Utils.Distance((px, py), (wx, wy))
                if d < best_dist:
                    best_dist = d
                    start_idx = i
        except Exception:
            start_idx = 0  # explicit fallback — closest-waypoint calc failed

        # Build reordered waypoint list starting from closest
        n = len(waypoints)
        ordered_waypoints = [waypoints[(start_idx + i) % n] for i in range(n)]

        for loop_count in range(max_loops):
            if boss_state["killed"]:
                return

            ConsoleLog(bot_name, f"Patrol loop {loop_count + 1}/{max_loops}",
                       Console.MessageType.Info)

            wp_list = ordered_waypoints if loop_count == 0 else waypoints
            total_wp = len(wp_list)
            for _i, (wx, wy) in enumerate(wp_list):
                # Map guard
                if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
                    return
                if boss_state["killed"]:
                    return

                # Boss spotted? Rush to it
                if boss_state["spotted"] and boss_state["agent_id"] > 0:
                    aid = boss_state["agent_id"]
                    try:
                        if check_dead_fn():
                            return
                        yield from _rush_to_boss(aid)
                        return
                    except Exception as e:
                        _log_and_record(bot_name, "Rush error", e)
                        boss_state["spotted"] = False
                        boss_state["agent_id"] = -1

                boss_state["waypoint"] = _i
                boss_state["waypoint_total"] = total_wp

                if _i > 0 and _i % 10 == 0:
                    ConsoleLog(bot_name, f"Patrol: waypoint {_i}/{total_wp}",
                               Console.MessageType.Info)

                jx = wx + random.randint(-50, 50)
                jy = wy + random.randint(-50, 50)
                yield from bot.Move._coro_get_path_to(jx, jy)
                _path_gen = bot.Move._coro_follow_path_to()
                _boss_interrupted = False
                for _step in _path_gen:
                    yield _step
                    # Background scanner already sets boss_state["spotted"] every 1s
                    if boss_state["spotted"] and boss_state["agent_id"] > 0:
                        ConsoleLog(bot_name, "Boss detected mid-walk -- aborting waypoint",
                                   Console.MessageType.Info)
                        _boss_interrupted = True
                        break
                    # Earshot check: brief pause if ALIVE party members falling behind
                    # Dead members are ignored -- don't stall patrol for corpses
                    try:
                        all_in_range = True
                        px, py = Player.GetXY()
                        for p in Party.GetPlayers():
                            aid = Party.Players.GetAgentIDByLoginNumber(p.login_number)
                            if aid and aid > 0 and Agent.IsAlive(aid):
                                if Utils.Distance((px, py), Agent.GetXY(aid)) > Range.Earshot.value:
                                    all_in_range = False
                                    break
                        if not all_in_range:
                            yield from Routines.Yield.wait(400)
                    except Exception:
                        pass
                if _boss_interrupted:
                    break

            # After waypoint loop: if boss was spotted mid-walk, rush now
            if boss_state["spotted"] and boss_state["agent_id"] > 0:
                aid = boss_state["agent_id"]
                try:
                    if check_dead_fn():
                        return
                    yield from _rush_to_boss(aid)
                    return
                except Exception as e:
                    ConsoleLog(bot_name, f"Post-loop rush error: {e}",
                               Console.MessageType.Warning)

        ConsoleLog(bot_name, "Patrol loops exhausted -- boss not found",
                   Console.MessageType.Warning)
        # Keep coroutine alive to avoid FSM list.remove crash on StopIteration
        while True:
            yield from Routines.Yield.wait(5000)

    def _launch_as_custom_state():
        """Non-generator: schedule intercept as managed coroutine."""
        fsm = bot.config.FSM
        fsm.AddManagedCoroutine(_CORO_INTERCEPT, _intercept_patrol)
        boss_state["patrol_launched"] = True
        # Set start time NOW so the grace period in is_intercept_or_death_done
        # is active from the very first poll — prevents race conditions during
        # the map-loading gap between launch and first explorable tick.
        _intercept_start_time[0] = time.time()

    def _is_done():
        """Condition: True when patrol/kill is finished."""
        if not boss_state.get("patrol_launched", False):
            return False
        if boss_state["killed"]:
            return True
        if not bot.config.FSM.HasManagedCoroutine(_CORO_INTERCEPT):
            return True
        return False

    return _intercept_patrol, _launch_as_custom_state, _is_done


def _broadcast_resign(bot_name):
    """Send resign to all active accounts including self. Returns True on success."""
    if not Map.IsMapReady() or Map.IsMapLoading():
        return False
    try:
        sender = Player.GetAccountEmail()
        if not sender:
            return False
        # Resign self via AQM
        ActionQueueManager().AddAction("ACTION", Player.SendChatCommand, "resign")
        # Then tell all slaves to resign
        my_map = Map.GetMapID()
        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            if acc and acc.IsSlotActive and acc.AccountEmail and acc.AccountEmail != sender:
                try:
                    if acc.AgentData.Map.MapID != my_map:
                        continue
                except Exception:
                    continue
                GLOBAL_CACHE.ShMem.SendMessage(
                    sender, acc.AccountEmail,
                    SharedCommandType.Resign, (0, 0, 0, 0))
        return True
    except Exception as e:
        ConsoleLog(bot_name, f"Resign send error: {e}",
                   Console.MessageType.Warning)
        return False


def create_mass_death_monitor(bot, bot_name, boss_state,
                              on_failure_fn=None,
                              death_threshold=MASS_DEATH_THRESHOLD,
                              loot_window_s=MASS_DEATH_LOOT_WINDOW_S):
    """Background coroutine: when >=threshold players are dead, loot for 10s then resign.
    No resurrection -- always proceed. Returns (bg_coro_fn, state_dict)."""
    state = {"triggered": False}

    def _monitor():
        """Background coroutine: poll death count during explorable."""
        while True:
            if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
                yield from Routines.Yield.wait(2000)
                continue

            # Don't trigger if boss already killed (normal loot phase handles it)
            if boss_state.get("killed", False):
                yield from Routines.Yield.wait(2000)
                continue

            dead, total = _count_dead_players()
            if total > 0 and dead >= death_threshold:
                state["triggered"] = True
                ConsoleLog(bot_name,
                           f"Mass death: {dead}/{total} players dead -- "
                           f"{loot_window_s}s loot window then resign",
                           Console.MessageType.Warning)

                # Record failure via injected callback
                if on_failure_fn:
                    try:
                        on_failure_fn()
                    except Exception as e:
                        ConsoleLog(bot_name, f"Failure record error: {e}",
                                   Console.MessageType.Warning)

                # Loot window -- let surviving characters auto-loot
                yield from Routines.Yield.wait(loot_window_s * 1000)

                # Force resign (with map guard + retry)
                if Map.IsMapReady() and Map.IsExplorable():
                    ConsoleLog(bot_name, "Mass death: resigning now",
                               Console.MessageType.Warning)
                    _broadcast_resign(bot_name)
                    yield from Routines.Yield.wait(3000)
                    # Retry if still in explorable
                    if Map.IsMapReady() and Map.IsExplorable():
                        _broadcast_resign(bot_name)
                # Keep alive to avoid FSM list.remove crash
                while True:
                    yield from Routines.Yield.wait(5000)

            yield from Routines.Yield.wait(2000)

    def _monitor_custom_state():
        return _monitor()

    return _monitor_custom_state, state


def create_farm_reset(bot, boss_state, coroutine_names=None,
                      extra_state_dicts=None):
    """Return a plain function that resets all state for a fresh farm loop."""
    if coroutine_names is None:
        coroutine_names = [_CORO_INTERCEPT, _CORO_SCANNER]
    if extra_state_dicts is None:
        extra_state_dicts = []

    def _reset():
        # Increment run_id FIRST — this invalidates any stale scanner
        # detections from the previous run before we clear anything else.
        # The scanner checks run_id on every tick and discards mismatches.
        boss_state["run_id"] = boss_state.get("run_id", 0) + 1
        boss_state["spotted"] = False
        boss_state["killed"] = False
        boss_state["agent_id"] = -1
        boss_state["_spot_run_id"] = -1  # no valid spot for this run yet
        boss_state["patrol_launched"] = False
        boss_state["early_abort"] = False
        boss_state["waypoint"] = 0
        boss_state["waypoint_total"] = 0
        _intercept_start_time[0] = 0.0
        _stuck_slave_tracker.clear()
        for sd in extra_state_dicts:
            if "triggered" in sd:
                sd["triggered"] = False
            if "done" in sd:
                sd["done"] = False
        # CRITICAL: Remove old managed coroutines so AddManagedCoroutine
        # can register fresh generators on the next run. Without this,
        # AddManagedCoroutine is a no-op (name already exists) and the
        # stale run-1 scanner re-detects the dead boss body → killed=True.
        fsm = bot.config.FSM
        for coro_name in coroutine_names:
            try:
                if fsm.HasManagedCoroutine(coro_name):
                    fsm.RemoveManagedCoroutine(coro_name)
                    ConsoleLog(BOT_NAME, f"Removed stale coroutine: {coro_name}",
                               Console.MessageType.Info)
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Coroutine remove error ({coro_name}): {e}",
                           Console.MessageType.Warning)

    return _reset


# ===========================================================================
# Post-kill: loot sweep + OOC wait + resign
# ===========================================================================
LOOT_SWEEP_RADIUS = 200      # units — half auto-loot range for cardinal pattern
LOOT_LEG_TIMEOUT_S = 8       # max seconds per sweep leg before skipping
LOOT_OOC_TIMEOUT_S = 15      # max seconds waiting for out-of-combat
LOOT_FINAL_WINDOW_S = 1      # brief pause after OOC for last pickups
LOOT_LEG_PAUSE_MS = 800      # base pause between sweep legs (auto-loot pickup)


def create_loot_and_resign(bot, bot_name, sweep_radius=LOOT_SWEEP_RADIUS,
                           leg_timeout_s=LOOT_LEG_TIMEOUT_S,
                           ooc_timeout_s=LOOT_OOC_TIMEOUT_S,
                           final_window_s=LOOT_FINAL_WINDOW_S):
    """Factory: return (custom_state_fn, is_done_fn) for post-kill loot + resign."""
    state = {"done": False}

    def _is_player_dead():
        try:
            my_id = Player.GetAgentID()
            return my_id <= 0 or Agent.IsDead(my_id)
        except Exception:
            return True

    DEATH_OOC_WAIT_S = 5  # seconds to wait after OOC when a player died

    def _loot_sweep(cx, cy):
        """Coroutine: sweep cardinal points around kill center. Skip movement if dead."""
        if _is_player_dead():
            ConsoleLog(bot_name, "Player dead -- skipping loot sweep movement",
                       Console.MessageType.Warning)
            return
        angles = [0, 90, 180, 270]  # cardinal sweep
        total = len(angles)
        for i, angle_deg in enumerate(angles):
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if _is_player_dead():
                ConsoleLog(bot_name, "Player died mid-sweep -- stopping movement",
                           Console.MessageType.Warning)
                return
            rad = math.radians(angle_deg)
            sx = cx + sweep_radius * math.cos(rad)
            sy = cy + sweep_radius * math.sin(rad)
            ConsoleLog(bot_name, f"Loot sweep {i + 1}/{total}",
                       Console.MessageType.Info)
            move_start = time.time()
            try:
                yield from bot.Move._coro_get_path_to(sx, sy)
                if not Map.IsMapReady() or Map.IsMapLoading():
                    return
                # Guard: if A* pathfinding returned an empty path, skip this leg
                if not bot.Move._config.path:
                    ConsoleLog(bot_name, f"Sweep leg {i + 1}: no valid path -- skipping",
                               Console.MessageType.Warning)
                    continue
                yield from bot.Move._coro_follow_path_to(forced_timeout=leg_timeout_s * 1000)
            except Exception as e:
                ConsoleLog(bot_name, f"Sweep leg {i + 1} blocked: {e}",
                           Console.MessageType.Warning)
                continue
            if (time.time() - move_start) > leg_timeout_s:
                ConsoleLog(bot_name, f"Sweep leg {i + 1} took >{leg_timeout_s}s -- skipping rest",
                           Console.MessageType.Warning)
                break
            jitter = random.randint(0, 500)
            yield from Routines.Yield.wait(LOOT_LEG_PAUSE_MS + jitter)

        ConsoleLog(bot_name, "Loot sweep complete",
                   Console.MessageType.Info)

    def _wait_ooc():
        """Coroutine: wait until out of combat or timeout. Dead players wait 10s after OOC."""
        ConsoleLog(bot_name, "Waiting for out-of-combat before resign",
                   Console.MessageType.Info)
        ooc_start = time.time()
        timed_out = True
        _last_log = 0
        while (time.time() - ooc_start) < ooc_timeout_s:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            try:
                if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                    timed_out = False
                    break
            except Exception:
                timed_out = False
                break
            elapsed = int(time.time() - ooc_start)
            if elapsed > 0 and elapsed % 10 == 0 and elapsed != _last_log:
                _last_log = elapsed
                ConsoleLog(bot_name, f"OOC wait: {elapsed}/{ooc_timeout_s}s",
                           Console.MessageType.Info)
            yield from Routines.Yield.wait(1000)

        if timed_out:
            ConsoleLog(bot_name, f"OOC timeout ({ooc_timeout_s}s) -- resigning anyway",
                       Console.MessageType.Warning)
        else:
            # After leaving combat: extra 10s wait if any player died
            dead, total = _count_dead_players()
            if dead > 0:
                ConsoleLog(bot_name,
                           f"{dead} player(s) dead -- waiting {DEATH_OOC_WAIT_S}s then proceeding",
                           Console.MessageType.Warning)
                yield from Routines.Yield.wait(DEATH_OOC_WAIT_S * 1000)
            else:
                yield from Routines.Yield.wait(final_window_s * 1000)
            ConsoleLog(bot_name, "Out of combat -- resigning",
                       Console.MessageType.Info)

    def _loot_and_resign():
        """Top-level coroutine: sweep, OOC wait, done. Always proceeds -- deaths don't block."""
        state["done"] = False
        try:
            if not Map.IsMapReady() or not Map.IsExplorable():
                return
            ConsoleLog(bot_name, "Post-kill: looting during combat",
                       Console.MessageType.Info)
            px, py = Player.GetXY()
            yield from _loot_sweep(px, py)
            yield from _wait_ooc()
        except Exception as e:
            _log_and_record(bot_name, "Loot+resign error", e)
        finally:
            state["done"] = True

    def _custom_state():
        return _loot_and_resign()

    def _is_done():
        return state["done"]

    return _custom_state, _is_done


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  Asterius-specific configuration                                        ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

_CORO_INTERCEPT = "InterceptPatrol"
_CORO_SCANNER = "AsteriusScanner"
_CORO_DEATH_MONITOR = "DeathMonitor"
_CORO_RESUME_GUARD = "OutpostResumeGuard"

BOT_NAME = "Asterius Scythe Farm"
MODULE_ICON = "Textures\\Module_Icons\\Asterius' Scythe.png"
TEXTURE = os.path.join(
    Py4GW.Console.get_projects_path(), "Bots", "marks_coding_corner", "textures", "asterius_scythe.png"
)
OUTPOST_TO_TRAVEL = Map.GetMapIDByName('Olafstead')
VARAJAR_FELLS_MAP_ID = 553
ASTERIUS_MODEL_ID = 6509
MASTER_EMAIL = "jonasglawion@aol.com"

def _is_master():
    """True only on the master account. Never uses GetPartyLeaderID()."""
    try:
        return Player.GetAccountEmail() == MASTER_EMAIL
    except Exception:
        return False

# Patrol route: 22 points. Boss hot zone = WP 7-21 (all 10 observed spawns).
# WP 0-6 = travel from zone entry to hot zone.
# WP 22-27 removed (dead zone, 0 boss spawns in 25+ runs, saves 6357u / ~30s).
# Boss scan range doubled to full compass (4800u) to compensate for fewer waypoints.
INTERCEPT_PATH = [
    (-3259,  -1743),  (-3880,  -4110),  (-5355,  -4110),  (-6403,  -3761),
    (-7567,  -3256),  (-8421,  -2441),  (-9314,  -1820),  (-10167, -1316),
    (-11021, -967),   (-12030, -889),   (-12884, -1238),  (-13660, -1898),
    (-14126, -2830),  (-14747, -3528),  (-15290, -4304),  (-15135, -5042),
    (-14786, -5895),  (-14592, -6555),  (-15562, -6439),  (-16377, -6400),
    (-17192, -6555),  (-18279, -6439),
]

# ===========================================================================
# Scenario Learner -- adaptive strategy selection (UCB1)
# ===========================================================================
_learner_save_path = os.path.join(
    Py4GW.Console.get_projects_path(), "Bots", "marks_coding_corner", "asterius_learner.json"
)
learner = ScenarioLearner("Asterius", save_path=_learner_save_path, min_trials=3)
learner.add_strategy("conservative", {"enemy_threshold": 5, "hp_threshold": 0.65, "hard_mode": True})
learner.add_strategy("balanced", {"enemy_threshold": 7, "hp_threshold": 0.55, "hard_mode": True})
learner.add_strategy("cautious", {"enemy_threshold": 5, "hp_threshold": 0.75, "hard_mode": True})

run_timer = Timer()
active_strategy = {"name": "balanced", "params": {"enemy_threshold": 8, "hp_threshold": 0.50, "hard_mode": True}}
_kit_state = {"done": False}

# ===========================================================================
# Build reusable components
# ===========================================================================
bot = Botting(BOT_NAME)
_farm_tracker = FarmRunTracker(BOT_NAME) if FarmRunTracker else None

# Danger thresholds will be updated per-strategy before each run
danger_fn = create_danger_check(BOT_NAME, active_strategy)
scan_fn, check_dead_fn, bg_scanner_fn, boss_state = create_boss_scanner(
    BOT_NAME, ASTERIUS_MODEL_ID, fallback_boss_glow=True,
)
_ze_coro, zone_entry_custom_state = create_zone_entry_handler(
    bot, BOT_NAME, safe_point=INTERCEPT_PATH[2],
    first_waypoint=INTERCEPT_PATH[0],
)
_ip_coro, launch_intercept, is_intercept_done = create_patrol_intercept(
    bot, BOT_NAME, INTERCEPT_PATH, boss_state,
    scan_fn, check_dead_fn, danger_fn,
    max_loops=1,
)
loot_custom_state, is_loot_done = create_loot_and_resign(bot, BOT_NAME)
death_monitor_state, death_monitor_flag = create_mass_death_monitor(
    bot, BOT_NAME, boss_state,
    on_failure_fn=lambda: _record_failure(),
)

# Wrap done-checks to also exit on mass death
_orig_intercept_done = is_intercept_done
_orig_loot_done = is_loot_done

_intercept_start_time = [0.0]  # mutable for closure
_stuck_slave_tracker = {}  # login_number -> first_seen_time when alive but >5000u away
STUCK_SLAVE_DISTANCE = 5000.0  # units — normal party spread is 200-1200u
STUCK_SLAVE_TIMEOUT_S = 30.0   # seconds before treating as abort

def is_intercept_or_death_done():
    if death_monitor_flag.get("triggered", False):
        ConsoleLog(BOT_NAME, "EXIT: death_monitor triggered", Console.MessageType.Warning)
        return True

    # Grace period: don't evaluate ANY done-condition in the first 30s after
    # patrol_launched. This prevents race conditions during map loading where
    # HasManagedCoroutine returns False before the coroutine actually starts.
    if _intercept_start_time[0] > 0 and (time.time() - _intercept_start_time[0]) < 30:
        return False

    # Only treat as "done" if we're genuinely back in outpost AND the run
    # has been active for at least 30s (prevents false-fire during FSM jump-back)
    if Map.IsMapReady() and Map.IsOutpost() and not Map.IsMapLoading():
        if _intercept_start_time[0] > 0 and (time.time() - _intercept_start_time[0]) > 30:
            ConsoleLog(BOT_NAME, "EXIT: back in outpost", Console.MessageType.Warning)
            return True

    # Don't run dead-player checks during loading or non-explorable
    if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
        return False  # Never delegate to _orig_intercept_done during loading — too fragile

    dead, total = _count_dead_players()

    # If MOST players dead and boss NOT spotted after 60s, abort run (not strategy's fault).
    # This does NOT compete with mass_death_monitor — that handles full wipes (MASS_DEATH_THRESHOLD+).
    # This only handles the "patrolling with dead members, boss nowhere" scenario.
    if not boss_state.get("killed", False) and not boss_state.get("spotted", False):
        if total > 0 and dead >= max(3, total - 1) and (time.time() - _intercept_start_time[0]) > 60:
            ConsoleLog(BOT_NAME,
                       f"{dead}/{total} dead, boss not found after 60s -- aborting (not counted as loss)",
                       Console.MessageType.Warning)
            boss_state["early_abort"] = True
            return True

    # --- Stuck slave detection (Issue #8) ---
    # If any party member is alive but >5000u away for >30s, abort the run.
    try:
        now = time.time()
        px, py = Player.GetXY()
        players = Party.GetPlayers()
        active_logins = set()
        for player in players:
            try:
                ln = player.login_number
                aid = Party.Players.GetAgentIDByLoginNumber(ln)
                if aid <= 0 or not Agent.IsValid(aid):
                    continue
                active_logins.add(ln)
                if Agent.IsDead(aid):
                    # Dead players are handled above; don't flag as stuck
                    _stuck_slave_tracker.pop(ln, None)
                    continue
                dist = Utils.Distance((px, py), Agent.GetXY(aid))
                if dist > STUCK_SLAVE_DISTANCE:
                    if ln not in _stuck_slave_tracker:
                        _stuck_slave_tracker[ln] = now
                    elif (now - _stuck_slave_tracker[ln]) > STUCK_SLAVE_TIMEOUT_S:
                        ConsoleLog(BOT_NAME,
                                   f"Slave login#{ln} alive but {dist:.0f}u away "
                                   f"for >{STUCK_SLAVE_TIMEOUT_S:.0f}s -- aborting run",
                                   Console.MessageType.Warning)
                        boss_state["early_abort"] = True
                        _stuck_slave_tracker.clear()
                        return True
                else:
                    _stuck_slave_tracker.pop(ln, None)
            except Exception:
                continue
        # Prune logins no longer in party
        for ln in list(_stuck_slave_tracker):
            if ln not in active_logins:
                _stuck_slave_tracker.pop(ln, None)
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Stuck-slave check error: {e}",
                   Console.MessageType.Warning)

    result = _orig_intercept_done()
    if result:
        has_coro = bot.config.FSM.HasManagedCoroutine(_CORO_INTERCEPT)
        ConsoleLog(BOT_NAME,
                   f"EXIT: orig_intercept_done "
                   f"(killed={boss_state.get('killed')}, "
                   f"launched={boss_state.get('patrol_launched')}, "
                   f"has_coro={has_coro}, "
                   f"agent_id={boss_state.get('agent_id')}, "
                   f"run_id={boss_state.get('run_id')})",
                   Console.MessageType.Warning)
        _stuck_slave_tracker.clear()
    return result

def is_loot_or_death_done():
    if death_monitor_flag.get("triggered", False):
        return True
    if Map.IsMapReady() and Map.IsOutpost() and not Map.IsMapLoading():
        return True
    return _orig_loot_done()

reset_farm = create_farm_reset(
    bot, boss_state,
    coroutine_names=[_CORO_INTERCEPT, _CORO_SCANNER, _CORO_DEATH_MONITOR, _CORO_RESUME_GUARD],
    extra_state_dicts=[death_monitor_flag, _kit_state],
)


# ===========================================================================
# Main farm routine
# ===========================================================================
def _select_strategy():
    """Non-generator: pick next strategy via UCB1 and apply params."""
    # Mutate in-place to preserve closure references (danger_fn, FarmStats)
    new = learner.select_strategy()
    active_strategy.clear()
    active_strategy.update(new)
    params = active_strategy["params"]
    ConsoleLog(BOT_NAME,
               f"Strategy: {active_strategy['name']} | "
               f"threshold={params.get('enemy_threshold', 8)} "
               f"HP={params.get('hp_threshold', 0.50)} "
               f"HM={params.get('hard_mode', True)}",
               Console.MessageType.Info)
    run_timer.Reset()
    run_timer.Start()
    if _farm_tracker and _is_master():
        _farm_tracker.start_run(
            strategy=active_strategy["name"],
            hard_mode=active_strategy["params"].get("hard_mode", True),
            loot_config=_loot_config,
        )


def _record_success():
    """Non-generator: record successful run. Only master tracks stats to file."""
    elapsed = run_timer.GetElapsedTime()
    run_timer.Stop()
    learner.record_result(active_strategy["name"], success=True, run_time_ms=int(elapsed))
    learner.save()
    if _farm_tracker and _is_master():
        _farm_tracker.end_run(success=True, loot_config=_loot_config)
    ConsoleLog(BOT_NAME,
               f"[WIN] {active_strategy['name']} | {elapsed/1000:.0f}s | "
               f"Rate: {learner.strategies[active_strategy['name']].success_rate:.0%} | "
               f"Total: {learner.total_runs} runs",
               Console.MessageType.Info)


def _record_failure():
    """Non-generator: record failed run (wipe)."""
    run_timer.Stop()
    learner.record_result(active_strategy["name"], success=False)
    learner.save()
    if _farm_tracker and _is_master():
        _farm_tracker.end_run(success=False, loot_config=_loot_config)
    ConsoleLog(BOT_NAME,
               f"[FAIL] {active_strategy['name']} | "
               f"Rate: {learner.strategies[active_strategy['name']].success_rate:.0%} | "
               f"Total: {learner.total_runs} runs",
               Console.MessageType.Warning)


def _record_run_result():
    """Non-generator: record WIN if boss killed, FAIL otherwise.
    Skip if death monitor triggered, early abort, or not in explorable (outpost false-fire)."""
    # Guard: don't record if we're not in explorable (e.g. already back in outpost)
    try:
        if not Map.IsExplorable() or not Map.IsMapReady():
            ConsoleLog(BOT_NAME, "Record skipped -- not in explorable",
                       Console.MessageType.Warning)
            return
    except Exception:
        return
    if death_monitor_flag.get("triggered", False):
        return  # Already recorded by mass death monitor
    if boss_state.get("early_abort", False):
        ConsoleLog(BOT_NAME, "Early abort (player died) -- not counted as strategy loss",
                   Console.MessageType.Info)
        return  # Don't pollute learner stats with deaths unrelated to strategy
    # Guard: run_timer must have actually been running (>10s) to count
    elapsed = run_timer.GetElapsedTime()
    if elapsed < 10000:
        ConsoleLog(BOT_NAME, f"Record skipped -- run too short ({elapsed/1000:.1f}s)",
                   Console.MessageType.Warning)
        return
    if boss_state.get("killed", False):
        _record_success()
    else:
        ConsoleLog(BOT_NAME, "Patrol complete -- boss not found, recording FAIL",
                   Console.MessageType.Warning)
        _record_failure()


# ===========================================================================
# Kit restocking: buy ID + Salvage kits for master, send to slaves
# ===========================================================================
KIT_TARGET_ID = 2
KIT_TARGET_SALVAGE = 4
KIT_TARGET_EXPERT = 1
MERCHANT_SELL_THRESHOLD = 7  # sell junk when free slots <= this


def _buy_kits_in_outpost():
    """Coroutine: find merchant in outpost, buy kits for master, send to slaves."""
    if not Map.IsMapReady() or not Map.IsOutpost():
        return

    def _is_merchant_npc(agent_id):
        try:
            name = Agent.GetNameByID(agent_id) or ""
            return "[Merchant]" in name or "Merchant" in name
        except Exception:
            return False

    try:
        # Check if we actually need kits BEFORE walking to merchant
        id_kits_in_inv = int(GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Identification_Kit.value))
        sup_id_in_inv = int(GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Superior_Identification_Kit.value))
        salvage_in_inv = int(GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Salvage_Kit.value))
        free_slots = Inventory.GetFreeSlotCount()

        id_to_buy = max(0, KIT_TARGET_ID - (id_kits_in_inv + sup_id_in_inv))
        salvage_to_buy = max(0, KIT_TARGET_SALVAGE - salvage_in_inv)
        needs_sell = free_slots <= MERCHANT_SELL_THRESHOLD

        if id_to_buy == 0 and salvage_to_buy == 0 and not needs_sell:
            ConsoleLog(BOT_NAME, f"Kits OK (ID: {id_kits_in_inv}+{sup_id_in_inv}, Salvage: {salvage_in_inv}, Free: {free_slots}) -- skipping merchant",
                       Console.MessageType.Info)
            return
        px, py = Player.GetXY()
        npc_array = AgentArray.GetNPCMinipetArray()
        npc_array = AgentArray.Filter.ByCondition(npc_array, _is_merchant_npc)
        if not npc_array:
            ConsoleLog(BOT_NAME, "No merchant NPC found in outpost", Console.MessageType.Warning)
            return

        npc_array = AgentArray.Sort.ByDistance(npc_array, (px, py))
        merchant_id = npc_array[0]
        mx, my = Agent.GetXY(merchant_id)
        merchant_model = Agent.GetPlayerNumber(merchant_id)

        ConsoleLog(BOT_NAME, f"Walking to merchant (agent {merchant_id})",
                   Console.MessageType.Info)
        yield from Routines.Yield.Movement.FollowPath([(mx, my)])
        yield from Routines.Yield.wait(500)

        ActionQueueManager().AddAction("ACTION", Player.ChangeTarget, merchant_id)
        yield from Routines.Yield.Player.InteractTarget()
        yield from Routines.Yield.wait(1200)

        if id_to_buy > 0:
            yield from Routines.Yield.Merchant.BuyIDKits(id_to_buy, log=True)
        if salvage_to_buy > 0:
            yield from Routines.Yield.Merchant.BuySalvageKits(salvage_to_buy, log=True)

        ConsoleLog(BOT_NAME, f"Master kits restocked (ID: +{id_to_buy}, Salvage: +{salvage_to_buy})",
                   Console.MessageType.Info)

        # Sell junk if bags are low (merchant window already open from kit buy)
        free_slots = Inventory.GetFreeSlotCount()
        if free_slots <= MERCHANT_SELL_THRESHOLD:
            try:
                from Widgets.Automation.Helpers.MerchantManager import _get_junk_items_to_sell
                junk = _get_junk_items_to_sell(_loot_config) if _loot_config else []
                if junk:
                    ConsoleLog(BOT_NAME, f"Bags low ({free_slots} free) -- selling {len(junk)} junk items",
                               Console.MessageType.Info)
                    yield from Routines.Yield.Merchant.SellItems(junk, log=True)
                    yield from Routines.Yield.wait(1000)
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Merchant sell error: {e}", Console.MessageType.Warning)

        sender = Player.GetAccountEmail()
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
        for acc in all_accounts:
            try:
                if not acc or not acc.AccountEmail or acc.AccountEmail == sender:
                    continue
                if not acc.IsSlotActive:
                    continue
                if acc.AgentData.Map.MapID != Map.GetMapID():
                    continue
                GLOBAL_CACHE.ShMem.SendMessage(
                    sender, acc.AccountEmail, SharedCommandType.MerchantItems,
                    (mx, my, KIT_TARGET_ID, KIT_TARGET_SALVAGE),
                    ExtraData=(str(KIT_TARGET_EXPERT), str(merchant_model)))
                short = acc.AccountEmail.split("@")[0] if "@" in acc.AccountEmail else acc.AccountEmail
                ConsoleLog(BOT_NAME, f"Kit buy sent to {short}",
                           Console.MessageType.Info)
                yield from Routines.Yield.wait(500)
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Kit send error: {e}", Console.MessageType.Warning)
                continue

        yield from Routines.Yield.wait(2000)

    except Exception as e:
        _log_and_record(BOT_NAME, "Kit restock error", e)


def _run_kit_restock():
    _kit_state["done"] = False
    try:
        yield from _buy_kits_in_outpost()
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Kit restock wrapper error: {e}", Console.MessageType.Warning)
    finally:
        _kit_state["done"] = True


def _run_kit_restock_as_custom_state():
    return _run_kit_restock()


def _is_kits_done():
    # Outpost guard: if we zoned out of outpost (or back into it after a crash),
    # don't stall the FSM waiting for a kit coroutine that can never finish.
    if _kit_state["done"]:
        return True
    try:
        if Map.IsMapReady() and Map.IsExplorable():
            return True  # We left outpost -- kits are done (or irrelevant now)
    except Exception:
        pass
    return False


def farm_scythes(bot_ref: Botting) -> None:
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    bot_ref.States.AddHeader(BOT_NAME)
    bot_ref.Templates.Multibox_Aggressive()

    # Force slaves to re-follow master during combat (stay within Earshot)
    def _set_combat_follow_threshold():
        # Only master sets this — prevents cross-client write conflicts
        if not _is_master():
            return
        sender = Player.GetAccountEmail()
        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            try:
                if not acc or not acc.IsSlotActive or not acc.AccountEmail:
                    continue
                if acc.AccountEmail == sender:
                    continue
                acc.FollowMoveThresholdCombat = float(Range.Earshot.value)
            except Exception:
                continue
    bot_ref.States.AddCustomState(_set_combat_follow_threshold, "Set combat follow range")

    # PrepareForFarm without kick -- party is already formed
    bot_ref.States.AddHeader("Prepare For Farm")
    bot_ref.Map.Travel(target_map_id=OUTPOST_TO_TRAVEL)
    bot_ref.Multibox.InviteAllAccounts()

    # Initial kit restock before first farm run
    bot_ref.States.AddHeader("Initial Restock")
    bot_ref.States.AddCustomState(_run_kit_restock_as_custom_state, "Buy ID + Salvage kits")
    bot_ref.Wait.UntilCondition(_is_kits_done, duration=500, max_timeout_s=7)
    bot_ref.Wait.ForTime(2000)

    # Select strategy + set hard mode (loop re-entry point)
    bot_ref.States.AddHeader("Strategy")

    # Wait until we're actually in the outpost after resign
    def _is_in_outpost():
        try:
            return Map.IsMapReady() and Map.IsOutpost() and not Map.IsMapLoading()
        except Exception:
            return False
    bot_ref.Wait.UntilCondition(_is_in_outpost, duration=500, max_timeout_s=30)

    def _select_and_set_hm():
        """Non-generator: select strategy AND set hard mode in one step."""
        _select_strategy()
        try:
            hm = active_strategy.get("params", {}).get("hard_mode", True)
            if hm:
                ActionQueueManager().AddAction("ACTION", Party.SetHardMode)
            else:
                ActionQueueManager().AddAction("ACTION", Party.SetNormalMode)
            ConsoleLog(BOT_NAME, f"Mode set: {'Hard' if hm else 'Normal'}",
                       Console.MessageType.Info)
        except Exception as e:
            _log_and_record(BOT_NAME, "Mode set error", e)
    bot_ref.States.AddCustomState(_select_and_set_hm, "Select strategy + set HM")
    bot_ref.Wait.ForTime(500)

    # Background coroutines in explorable
    bot_ref.States.AddManagedCoroutine(_CORO_SCANNER, bg_scanner_fn)
    bot_ref.States.AddManagedCoroutine(_CORO_DEATH_MONITOR, death_monitor_state)

    # Safety coroutine: FollowPath party-wipe detection (MOVE_src.py:140)
    # pauses the FSM.  This coroutine runs even when paused (managed
    # coroutines execute before the pause check) and auto-resumes once
    # the team is safely back in outpost.
    def _outpost_fsm_resume_guard():
        while True:
            if (bot.config.FSM.paused
                    and Map.IsMapReady()
                    and Map.IsOutpost()
                    and not Map.IsMapLoading()):
                ConsoleLog(BOT_NAME, "FSM paused in outpost -- auto-resuming",
                           Console.MessageType.Warning)
                bot.config.FSM.resume()
            yield from Routines.Yield.wait(1000)
    bot_ref.States.AddManagedCoroutine(_CORO_RESUME_GUARD,
                                       _outpost_fsm_resume_guard)

    # Disable party-behind/dead-behind/wipe events -- HeroAI follow handles stragglers,
    # mass death monitor handles wipes, these events cause spinning and FSM pauses
    bot_ref.Events.OnPartyMemberBehindCallback(lambda: None)
    bot_ref.Events.OnPartyMemberDeadBehindCallback(lambda: None)
    bot_ref.Events.OnPartyWipeCallback(lambda: None)

    bot_ref.States.AddHeader('Exit To Farm')
    bot_ref.Properties.Disable('pause_on_danger')
    bot_ref.Properties.Disable('halt_on_death')  # Deaths don't stop movement — keep going
    bot_ref.Move.XYAndExitMap(-2166, 861, target_map_id=VARAJAR_FELLS_MAP_ID)
    bot_ref.Wait.ForTime(500)
    bot_ref.Properties.Enable('pause_on_danger')

    bot_ref.States.AddHeader('Zone Entry Safety')
    bot_ref.States.AddCustomState(zone_entry_custom_state, "Handle exit mobs")

    bot_ref.States.AddHeader("Start Combat")
    bot_ref.States.AddCustomState(launch_intercept, "Launch Intercept Patrol")
    bot_ref.Wait.UntilCondition(is_intercept_or_death_done, duration=1000, max_timeout_s=600)

    # Record result (WIN if boss killed, FAIL if not found)
    bot_ref.States.AddCustomState(_record_run_result, "Record result")

    # Wait 5s for loot pickup before resigning
    bot_ref.Wait.ForTime(5000)

    # Post-loot handler: resume FSM (party-wipe pauses it),
    # resign, reset, and loop back.
    def _post_kill_resign_and_restart():
        """Non-generator: handles entire post-kill → next-run transition.

        Key fix: FollowPath party-wipe detection (MOVE_src.py:140) pauses
        the FSM.  If we don't resume it here, the bot stays stuck forever
        at whatever step was active when the wipe was detected.
        """
        # 1. Resume FSM if paused by party-wipe detection
        if bot.config.FSM.paused:
            bot.config.FSM.resume()

        # 2. Reset BEFORE resign — this increments run_id, which prevents
        #    the background scanner from re-detecting the dead boss corpse
        #    that still exists in the agent array during map transition.
        #    If reset runs after resign, the scanner can tick between resign
        #    and reset, re-setting killed=True from the stale corpse.
        reset_farm()

        # 3. Send resign if still in explorable
        if Map.IsMapReady() and Map.IsExplorable():
            _broadcast_resign(BOT_NAME)

        # 4. Direct jump back to strategy selection — bypasses the
        #    JumpToStepName yield/pause/resume dance which deadlocks
        #    when the FSM is already at the last step.
        bot.config.FSM.jump_to_state_by_name("[H]Strategy_4")

    bot_ref.States.AddCustomState(_post_kill_resign_and_restart,
                                  "Resign + Reset + Restart")


bot.SetMainRoutine(farm_scythes)


# ===========================================================================
# Debug Log System
# ===========================================================================
_debug_enabled = False
_debug_console_interval = 30.0  # seconds between console dumps
_debug_last_console_dump = 0.0
_last_error = ""
_last_error_time = 0.0


def _record_debug_error(msg):
    """Record the last error for debug display."""
    global _last_error, _last_error_time
    _last_error = str(msg)[:200]
    _last_error_time = time.time()


def _log_and_record(source, msg, e=None):
    """Log a warning and record it for the debug panel."""
    full = f"{msg}: {e}" if e else msg
    ConsoleLog(source, full, Console.MessageType.Warning)
    _record_debug_error(full)


def _get_debug_state() -> dict:
    """Collect all bot state into a single dict for display."""
    state = {}

    # FSM state
    try:
        fsm = bot.config.FSM
        state["fsm_step"] = f"{fsm.get_current_step_index() + 1}/{fsm.get_total_steps()}"
        state["fsm_name"] = fsm.get_current_step_name()
        state["fsm_paused"] = fsm.is_paused()
    except Exception:
        state["fsm_step"] = "?"
        state["fsm_name"] = "?"
        state["fsm_paused"] = False

    # Player
    my_id = 0
    try:
        px, py = Player.GetXY()
        state["pos"] = f"({px:.0f}, {py:.0f})"
        my_id = Player.GetAgentID()
        state["hp"] = f"{Agent.GetHealth(my_id):.0%}" if my_id > 0 else "?"
        state["dead"] = Agent.IsDead(my_id) if my_id > 0 else False
    except Exception:
        state["pos"] = "?"
        state["hp"] = "?"
        state["dead"] = False

    # Casting state
    try:
        if my_id > 0:
            state["casting"] = Agent.IsCasting(my_id)
            cast_id = Agent.GetCastingSkillID(my_id) if state["casting"] else 0
            state["casting_skill"] = cast_id
        else:
            state["casting"] = False
            state["casting_skill"] = 0
    except Exception:
        state["casting"] = False
        state["casting_skill"] = 0

    # Adrenaline (first 3 skill slots)
    try:
        adr = []
        for slot in range(min(3, 8)):
            sd = GLOBAL_CACHE.SkillBar.GetSkillData(slot + 1)
            adr.append(str(sd.adrenaline_a) if sd else "0")
        state["adrenaline"] = "/".join(adr)
    except Exception:
        state["adrenaline"] = "?"

    # Map
    try:
        state["map_id"] = Map.GetMapID()
        state["map_ready"] = Map.IsMapReady()
        state["explorable"] = Map.IsExplorable()
        state["in_combat"] = Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot)
    except Exception:
        state["map_id"] = 0
        state["map_ready"] = False
        state["explorable"] = False
        state["in_combat"] = False

    # Nearby enemy count
    try:
        state["enemies"] = count_nearby_enemies(Range.Earshot.value)
    except Exception:
        state["enemies"] = 0

    # Boss scanner
    state["boss_spotted"] = boss_state.get("spotted", False)
    state["boss_killed"] = boss_state.get("killed", False)
    state["boss_agent"] = boss_state.get("agent_id", -1)

    # Distance to boss
    try:
        aid = boss_state.get("agent_id", -1)
        if aid > 0 and boss_state.get("spotted") and not boss_state.get("killed"):
            state["boss_dist"] = f"{Utils.Distance(Player.GetXY(), Agent.GetXY(aid)):.0f}"
        else:
            state["boss_dist"] = "-"
    except Exception:
        state["boss_dist"] = "?"

    # Patrol waypoint
    state["waypoint"] = f"{boss_state.get('waypoint', '?')}/{boss_state.get('waypoint_total', '?')}"

    # Death monitor
    dead, total = _count_dead_players()
    state["dead_players"] = f"{dead}/{total}"
    state["death_triggered"] = death_monitor_flag.get("triggered", False)

    # Slave status (per-account: alive/dead/map)
    slaves = []
    try:
        my_email = Player.GetAccountEmail()
        my_map = Map.GetMapID()
        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            try:
                if not acc or not acc.IsSlotActive or not acc.AccountEmail:
                    continue
                if acc.AccountEmail == my_email:
                    continue
                short = acc.AccountEmail.split("@")[0][:6]
                acc_map = acc.AgentData.Map.MapID
                same_map = acc_map == my_map
                # Check if slave agent is dead
                try:
                    agent_id = acc.AgentData.AgentID
                    is_dead = Agent.IsDead(agent_id) if agent_id > 0 and same_map else False
                except Exception:
                    is_dead = False
                # Distance from master
                try:
                    if same_map and agent_id > 0:
                        dist = Utils.Distance(Player.GetXY(), Agent.GetXY(agent_id))
                        dist_str = f"{dist:.0f}u"
                    else:
                        dist_str = "OOM"
                except Exception:
                    dist_str = "?"
                tag = "DEAD" if is_dead else ("OK" if same_map else "OOM")
                slaves.append(f"{short}:{tag}:{dist_str}")
            except Exception:
                continue
    except Exception:
        pass
    state["slaves"] = slaves

    # Strategy
    state["strategy"] = active_strategy.get("name", "?")
    state["hard_mode"] = active_strategy.get("params", {}).get("hard_mode", True)

    # Run timer
    try:
        state["run_time"] = f"{run_timer.GetElapsedTime() / 1000:.0f}s"
    except Exception:
        state["run_time"] = "0s"

    # Learner stats
    try:
        strat_name = active_strategy.get("name", "")
        s = learner.strategies.get(strat_name)
        if s:
            state["win_rate"] = f"{s.success_rate:.0%}"
            state["total_runs"] = learner.total_runs
        else:
            state["win_rate"] = "?"
            state["total_runs"] = learner.total_runs
    except Exception:
        state["win_rate"] = "?"
        state["total_runs"] = 0

    # Session stats from FarmStats
    if _farm_tracker:
        try:
            ss = _farm_tracker.get_session_summary()
            state["session_runs"] = ss.get("runs", 0)
            state["session_gold"] = f"{ss.get('gold', 0):,}"
            state["session_gph"] = f"{ss.get('gold_per_hour', 0):,.0f}"
            state["session_greens"] = len(ss.get("greens", []))
        except Exception:
            state["session_runs"] = 0
            state["session_gold"] = "0"
            state["session_gph"] = "0"
            state["session_greens"] = 0

    # Inventory
    try:
        state["free_slots"] = Inventory.GetFreeSlotCount()
        state["gold"] = f"{Inventory.GetGoldOnCharacter():,}"
    except Exception:
        state["free_slots"] = "?"
        state["gold"] = "?"

    # Kits
    state["kits_done"] = _kit_state.get("done", False)

    # Last error
    state["last_error"] = _last_error
    state["last_error_age"] = f"{time.time() - _last_error_time:.0f}s ago" if _last_error else ""

    return state


def _draw_debug_window():
    """Draw the debug overlay window with live bot state."""
    global _debug_enabled, _debug_console_interval

    if PyImGui is None:
        return

    try:
        if not PyImGui.begin("Asterius Debug##debug", True):
            PyImGui.end()
            return
    except Exception:
        return

    try:
        # Toggle + settings — check BEFORE calling expensive _get_debug_state()
        _debug_enabled = PyImGui.checkbox("Enable Debug Log", _debug_enabled)
        if not _debug_enabled:
            PyImGui.text("Debug disabled -- check box to enable")
            return

        # ONLY now call the expensive state collector
        s = _get_debug_state()

        PyImGui.separator()

        col_warn = Color(255, 100, 100, 255).to_tuple_normalized()
        col_good = Color(100, 255, 100, 255).to_tuple_normalized()
        col_info = Color(180, 200, 255, 255).to_tuple_normalized()
        col_gold = Color(255, 215, 0, 255).to_tuple_normalized()
        col_dim = Color(140, 140, 140, 255).to_tuple_normalized()

        # --- FSM ---
        PyImGui.text_colored("FSM", col_info)
        PyImGui.text(f"  Step: {s['fsm_step']}  {s['fsm_name']}")
        if s["fsm_paused"]:
            PyImGui.text_colored("  PAUSED", col_warn)

        PyImGui.spacing()

        # --- Player ---
        PyImGui.text_colored("Player", col_info)
        PyImGui.text(f"  Pos: {s['pos']}  HP: {s['hp']}")
        if s["dead"]:
            PyImGui.text_colored("  DEAD", col_warn)
        PyImGui.text(f"  Map: {s['map_id']}  Ready: {s['map_ready']}  Explorable: {s['explorable']}")
        cast_str = f"  Casting: skill {s['casting_skill']}" if s["casting"] else "  Not casting"
        PyImGui.text(cast_str)
        PyImGui.text(f"  Adrenaline (1-3): {s['adrenaline']}")
        if s["in_combat"]:
            PyImGui.text_colored(f"  IN COMBAT ({s['enemies']} enemies)", col_warn)
        else:
            PyImGui.text_colored(f"  Out of combat ({s['enemies']} nearby)", col_dim)

        PyImGui.spacing()

        # --- Boss ---
        PyImGui.text_colored("Boss", col_info)
        PyImGui.text(f"  Patrol: waypoint {s['waypoint']}")
        if s["boss_killed"]:
            PyImGui.text_colored("  KILLED", col_good)
        elif s["boss_spotted"]:
            PyImGui.text_colored(f"  SPOTTED (agent {s['boss_agent']}, dist {s['boss_dist']})", col_gold)
        else:
            PyImGui.text(f"  Scanning...")

        PyImGui.spacing()

        # --- Slaves ---
        PyImGui.text_colored("Party", col_info)
        PyImGui.text(f"  Dead: {s['dead_players']}")
        if s["death_triggered"]:
            PyImGui.text_colored("  MASS DEATH -- resigning", col_warn)
        for sl in s.get("slaves", []):
            parts = sl.split(":")
            if len(parts) >= 3:
                tag = parts[1]
                col = col_warn if tag == "DEAD" else (col_dim if tag == "OOM" else col_good)
                PyImGui.text_colored(f"  {sl}", col)

        PyImGui.spacing()

        # --- Strategy ---
        PyImGui.text_colored("Strategy", col_info)
        PyImGui.text(f"  {s['strategy']} | HM: {s['hard_mode']}")
        PyImGui.text(f"  Run: {s['run_time']} | Win: {s['win_rate']} | Total: {s['total_runs']}")

        PyImGui.spacing()

        # --- Session ---
        PyImGui.text_colored("Session", col_info)
        if _farm_tracker:
            PyImGui.text(f"  Runs: {s.get('session_runs', 0)} | Gold: {s.get('session_gold', '0')}")
            PyImGui.text(f"  Gold/hr: {s.get('session_gph', '0')} | Greens: {s.get('session_greens', 0)}")

        PyImGui.spacing()

        # --- Inventory ---
        PyImGui.text_colored("Inventory", col_info)
        PyImGui.text(f"  Free: {s['free_slots']} | Gold: {s['gold']} | Kits: {'ready' if s['kits_done'] else 'pending'}")

        # --- Last Error ---
        if s["last_error"]:
            PyImGui.spacing()
            PyImGui.text_colored("Last Error", col_warn)
            PyImGui.text(f"  {s['last_error']}")
            PyImGui.text_colored(f"  {s['last_error_age']}", col_dim)

        # --- Console dump ---
        global _debug_last_console_dump
        now = time.time()
        if now - _debug_last_console_dump >= _debug_console_interval:
            _debug_last_console_dump = now
            boss_short = "KILL" if s['boss_killed'] else ("SPOT" if s['boss_spotted'] else "scan")
            combat_short = "T" if s['in_combat'] else "F"
            strat_short = s['strategy'][:3] if s['strategy'] else "-"
            ConsoleLog(BOT_NAME,
                       f"[DBG] wp={s['waypoint']} boss={boss_short} "
                       f"dead={s['dead_players']} strat={strat_short} "
                       f"run={s['run_time']} combat={combat_short}({s['enemies']})",
                       Console.MessageType.Info)

    except Exception as e:
        PyImGui.text(f"Debug error: {e}")
    finally:
        PyImGui.end()


# ===========================================================================
# Tooltip
# ===========================================================================
def tooltip():
    if PyImGui is None:
        return
    PyImGui.begin_tooltip()
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Asterius Scythe Farmer v4", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("multi-account bot to farm Asterius Scythe")
    PyImGui.spacing()
    PyImGui.bullet_text("v4: single-file, all logic inlined")
    PyImGui.bullet_text("Adaptive HM, 22-point patrol, full-compass boss scan")
    PyImGui.bullet_text("UCB1 adaptive strategy selection")
    PyImGui.spacing()
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Mark")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window(icon_path=TEXTURE)
    _draw_debug_window()


if __name__ == "__main__":
    main()
