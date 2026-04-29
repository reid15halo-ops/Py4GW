"""
HeroAI-specific lightweight profiler for identifying combat system hotspots.
Enable via INI or runtime toggle. Zero overhead when disabled (_NullCtx pattern).

Usage:
    from HeroAI.profiler import HeroAIProfiler
    profiler = HeroAIProfiler()

    # In main loop:
    profiler.begin_frame()

    # Around hot functions:
    with profiler.measure("targeting.score_enemy"):
        score = _score_enemy(enemy_id)

    # Get results:
    for name, mean, p50, p95, calls_per_frame in profiler.get_top_n(5):
        print(f"{name}: mean={mean:.2f}ms p50={p50:.2f}ms p95={p95:.2f}ms calls={calls_per_frame:.1f}/f")
"""
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _FrameSample:
    """Timing data for one frame."""
    sections: dict = field(default_factory=dict)   # section_name -> total_ms
    counts: dict = field(default_factory=dict)      # section_name -> call_count


class _SectionTimer:
    """Context manager that measures elapsed time for a named section."""
    __slots__ = ('_sample', '_section', '_t0')

    def __init__(self, sample: _FrameSample, section: str):
        self._sample = sample
        self._section = section

    def __enter__(self):
        self._t0 = time.perf_counter_ns()
        return self

    def __exit__(self, *_):
        elapsed_ms = (time.perf_counter_ns() - self._t0) / 1_000_000
        s = self._section
        self._sample.sections[s] = self._sample.sections.get(s, 0.0) + elapsed_ms
        self._sample.counts[s] = self._sample.counts.get(s, 0) + 1


class _NullCtx:
    """Zero-cost no-op context manager when profiling is disabled."""
    __slots__ = ()
    def __enter__(self): return self
    def __exit__(self, *_): pass


_NULL_CTX = _NullCtx()


class HeroAIProfiler:
    """Singleton profiler for HeroAI combat system internals."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.enabled: bool = False
        self._current: _FrameSample = _FrameSample()
        self._history: deque = deque(maxlen=2000)
        self._initialized = True

    def begin_frame(self):
        """Call once at the start of each frame. Saves previous frame's data."""
        if not self.enabled:
            return
        if self._current.sections:
            self._history.append(self._current)
        self._current = _FrameSample()

    def measure(self, section: str):
        """Returns a context manager that times the enclosed block.
        Zero cost when disabled."""
        if self.enabled:
            return _SectionTimer(self._current, section)
        return _NULL_CTX

    def get_top_n(self, n: int = 5) -> list:
        """Returns top-N sections by mean ms: [(name, mean_ms, p50_ms, p95_ms, avg_calls_per_frame)]"""
        if not self._history:
            return []
        from collections import defaultdict
        all_times = defaultdict(list)
        all_counts = defaultdict(list)
        for sample in self._history:
            seen = set()
            for name, ms in sample.sections.items():
                all_times[name].append(ms)
                all_counts[name].append(sample.counts.get(name, 1))
                seen.add(name)
            # Sections not present in this frame get 0
            for name in all_times:
                if name not in seen:
                    all_times[name].append(0.0)
                    all_counts[name].append(0)

        results = []
        for name, times in all_times.items():
            times_sorted = sorted(times)
            n_t = len(times_sorted)
            mean = sum(times_sorted) / n_t
            p50 = times_sorted[n_t // 2]
            p95 = times_sorted[min(int(n_t * 0.95), n_t - 1)]
            avg_calls = sum(all_counts[name]) / len(all_counts[name])
            results.append((name, mean, p50, p95, avg_calls))

        results.sort(key=lambda r: r[1], reverse=True)
        return results[:n]

    def clear(self):
        """Reset all collected data."""
        self._history.clear()
        self._current = _FrameSample()

    def format_report(self, n: int = 10) -> str:
        """Format a human-readable report string."""
        top = self.get_top_n(n)
        if not top:
            return "[HeroAI Profiler] No data collected yet."
        total_mean = sum(t[1] for t in top)
        lines = [f"[HeroAI Profiler] Total measured: {total_mean:.2f}ms/frame ({len(self._history)} samples)"]
        for i, (name, mean, p50, p95, calls) in enumerate(top, 1):
            lines.append(f"  #{i:<2} {name:<40} mean={mean:.2f}ms  p50={p50:.2f}ms  p95={p95:.2f}ms  calls={calls:.1f}/f")
        return "\n".join(lines)
