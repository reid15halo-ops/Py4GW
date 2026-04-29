"""ScenarioLearner.py -- UCB1-based strategy selection for adaptive farm bots.

Tracks multiple named strategies, records success/failure and run times,
and selects the next strategy using UCB1 (Upper Confidence Bound) to
balance exploration vs exploitation.

Persists results to a JSON file so learning carries across sessions.
"""

import json
import math
import os
import time

try:
    import PyImGui
except ImportError:
    PyImGui = None


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
    """UCB1-based multi-armed bandit for farm strategy selection.

    Usage::

        learner = ScenarioLearner("MyBot", save_path="learner.json", min_trials=3)
        learner.add_strategy("aggressive", {"pull_cap": 12, "hard_mode": True})
        learner.add_strategy("safe", {"pull_cap": 5, "hard_mode": False})

        strat = learner.select_strategy()   # returns {"name": ..., "params": ...}
        # ... run the farm ...
        learner.record_result("aggressive", success=True, run_time_ms=45000)
        learner.save()
    """

    def __init__(self, bot_name, save_path=None, min_trials=3):
        self.bot_name = bot_name
        self.save_path = save_path
        self.min_trials = max(1, min_trials)
        self.strategies = {}  # name -> _StrategyStats
        self._order = []      # insertion order for deterministic round-robin

        if save_path and os.path.isfile(save_path):
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_strategy(self, name, params):
        """Register a strategy. If already loaded from disk, keeps existing stats."""
        if name not in self.strategies:
            self.strategies[name] = _StrategyStats(name, params)
            self._order.append(name)
        else:
            # Update params in case they changed, keep stats
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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

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
        except Exception:
            pass  # non-critical

    def _load(self):
        try:
            with open(self.save_path, "r") as f:
                data = json.load(f)
            for name, sd in data.get("strategies", {}).items():
                s = _StrategyStats.from_dict(sd)
                self.strategies[name] = s
                if name not in self._order:
                    self._order.append(name)
        except Exception:
            pass  # start fresh

    # ------------------------------------------------------------------
    # UI helper
    # ------------------------------------------------------------------

    def draw_stats_table(self):
        """Draw an ImGui table with strategy stats. Safe to call if PyImGui unavailable."""
        if PyImGui is None:
            return
        try:
            if PyImGui.begin_table("##scenario_learner", 5):
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
                PyImGui.end_table()
        except Exception:
            pass
