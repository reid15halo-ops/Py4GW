"""Multi-client broadcast scan for expensive targeting computations.

One designated scan master (party leader's client) runs cluster scans once per
interval and writes results to a named shared memory mmap. All other clients read
the cached result, falling back to local scans when the master is absent or stale.
"""
from __future__ import annotations

import struct
import time

from Py4GWCoreLib import GLOBAL_CACHE, Player

# Named mmap tag — shared across all Py4GW clients on this machine.
_MMAP_NAME = "HeroAI_BroadcastScan"
_MMAP_SIZE = 32
_STRUCT_FMT = "Iiiii3i"  # unsigned version + 4 signed ints + 3 reserved

_SCAN_INTERVAL_MS = 100
_STALE_MASTER_MS = 500


class _MmapHandle:
    """Persistent mmap handle to avoid open/close kernel calls per access."""

    _writer: object | None = None
    _reader: object | None = None

    @classmethod
    def writer(cls):
        import mmap

        if cls._writer is None or cls._writer.closed:
            cls._writer = mmap.mmap(-1, _MMAP_SIZE, tagname=_MMAP_NAME, access=mmap.ACCESS_WRITE)
        return cls._writer

    @classmethod
    def reader(cls):
        import mmap

        if cls._reader is None or cls._reader.closed:
            cls._reader = mmap.mmap(-1, _MMAP_SIZE, tagname=_MMAP_NAME, access=mmap.ACCESS_READ)
        return cls._reader


# Reader cache
_cached_version: int = -1
_cached_result: tuple[int, int, int, int] = (0, 0, 0, 0)

# Writer state
_prev_result: tuple[int, int, int, int] = (0, 0, 0, 0)
_last_scan_ms: float = 0.0
_version: int = 0

# Safe-reader staleness tracking
_last_version_change_ms: float = 0.0
_last_seen_version: int = -1


def _is_scan_master() -> bool:
    """True when this client controls the party leader."""
    try:
        return Player.GetAgentID() == GLOBAL_CACHE.Party.GetPartyLeaderID()
    except Exception:
        return False


def _write_scan(adj: int, nearby: int, focused_ally: int, hp_spike_ally: int, version: int) -> None:
    """Write scan results to shared memory; skip if unchanged."""
    global _prev_result
    result = (adj, nearby, focused_ally, hp_spike_ally)
    if result == _prev_result:
        return
    _prev_result = result
    mm = _MmapHandle.writer()
    mm.seek(0)
    mm.write(struct.pack(_STRUCT_FMT, version, *result, 0, 0, 0))


def _read_scan() -> tuple[int, int, int, int]:
    """Read scan results from shared memory; return cached tuple on version hit."""
    global _cached_version, _cached_result
    mm = _MmapHandle.reader()
    mm.seek(0)
    raw = mm.read(_MMAP_SIZE)
    version = struct.unpack_from("I", raw, 0)[0]
    if version == _cached_version:
        return _cached_result
    data = struct.unpack(_STRUCT_FMT, raw)
    _cached_version = version
    _cached_result = data[1:5]  # (adj, nearby, focused_ally, hp_spike)
    return _cached_result


def read_scan_safe(fallback_fn) -> int:
    """Return the broadcast scan value for the caller's field, or fallback_fn() if stale/absent.

    The *caller* is responsible for indexing the returned 4-tuple.  This helper
    only tracks version staleness and handles exceptions.
    """
    global _last_version_change_ms, _last_seen_version
    try:
        result = _read_scan()
        if _cached_version != _last_seen_version:
            _last_seen_version = _cached_version
            _last_version_change_ms = time.perf_counter() * 1000
        if time.perf_counter() * 1000 - _last_version_change_ms > _STALE_MASTER_MS:
            return fallback_fn()
        return result
    except Exception:
        return fallback_fn()


def maybe_broadcast_scan(
    target_enemy_clustered_adjacent_fn,
    target_enemy_clustered_nearby_fn,
    target_ally_most_focused_fn=None,
    detect_ally_hp_spike_fn=None,
) -> None:
    """If this client is scan master, run expensive scans and broadcast results.

    Call once per combat tick *before* skill evaluation.
    """
    global _last_scan_ms, _version

    if not _is_scan_master():
        return

    now = time.perf_counter() * 1000
    if now - _last_scan_ms < _SCAN_INTERVAL_MS:
        return
    _last_scan_ms = now
    _version += 1

    adj = target_enemy_clustered_adjacent_fn()
    nearby = target_enemy_clustered_nearby_fn()
    focused_ally = target_ally_most_focused_fn() if target_ally_most_focused_fn else 0
    hp_spike = detect_ally_hp_spike_fn() if detect_ally_hp_spike_fn else 0

    _write_scan(adj, nearby, focused_ally, hp_spike, _version)
