"""
Gemini Commander strategy reader for HeroAI.

Reads the Gemini Commander's ``commands.ini`` file and exposes the current
strategy as a :class:`GeminiStrategy` dataclass.  HeroAI calls
:func:`get_gemini_strategy` during combat to check for overrides.
If the Gemini Commander is not running, the file is missing, or the
heartbeat is stale (>30 s), returns ``None`` -- HeroAI uses its defaults.

Design principles (per CLAUDE.md):
  - **Read-only**: never writes to the INI or any game state.
  - **Throttled**: re-reads the file at most every 5 seconds.
  - **Fail-silent**: any exception returns ``None`` (zero crashes > speed).
  - **No game API calls**: pure file I/O + parsing, safe for all 6 clients.

INI layout written by ``tools/gemini_commander/command_executor.py``:
  [Commander]  timestamp, active, heartbeat
  [Strategy]   stance, focus_target, formation, prebuff, flag_position
  [Weights]    healer, caster, casting (0 = use HeroAI defaults)

Integration points (Phase 2b, NOT wired yet):
  - targeting.py ``_score_enemy()``  -- apply weight overrides and focus bonus
  - combat.py   ``FindCastableSkill()``  -- honour retreat stance

See bottom of this file for integration comments showing exact hook locations.
"""

from __future__ import annotations

import configparser
import os
import time
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Strategy data
# ---------------------------------------------------------------------------

@dataclass
class GeminiStrategy:
    """Snapshot of the Gemini Commander's current tactical commands.

    All fields have safe defaults that match HeroAI's normal behaviour,
    so partially-parsed files never produce unexpected overrides.
    """
    stance: str = "balanced"    # aggressive | balanced | defensive | retreat
    focus_target: str = ""      # monk | caster | lowest_hp | boss | nearest | ""
    formation: str = "tight"    # tight | spread | line | behind_master
    prebuff: bool = False
    flag_position: str = "follow"  # hold | follow | master
    weight_healer: int = 0      # 0 = use HeroAI default (WEIGHT_HEALER)
    weight_caster: int = 0      # 0 = use HeroAI default (WEIGHT_CASTER)
    weight_casting: int = 0     # 0 = use HeroAI default (WEIGHT_CASTING)
    is_active: bool = False
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Allowed values -- anything outside these sets is ignored (Gemini output can
# be unpredictable; we whitelist, not blacklist).
# ---------------------------------------------------------------------------

_VALID_STANCES = frozenset({"aggressive", "balanced", "defensive", "retreat"})
_VALID_FOCUS = frozenset({"monk", "caster", "lowest_hp", "boss", "nearest", ""})
_VALID_FORMATIONS = frozenset({"tight", "spread", "line", "behind_master"})
_VALID_FLAGS = frozenset({"hold", "follow", "master"})


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_INI_PATH: str = ""                          # Resolved on first call
_POLL_INTERVAL: float = 5.0                  # seconds between disk reads
_HEARTBEAT_TIMEOUT: float = 30.0             # seconds before commander is "dead"
_last_read_time: float = 0.0
_cached_strategy: Optional[GeminiStrategy] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_gemini_strategy(py4gw_root: str = "") -> Optional[GeminiStrategy]:
    """Return the current Gemini strategy, or ``None`` if inactive / stale.

    Safe to call every frame -- internally throttled to one disk read per
    :data:`_POLL_INTERVAL` seconds.  All exceptions are swallowed and
    logged to ``_parse_errors`` (not to game console, since this module
    has no Py4GW dependency).

    Parameters
    ----------
    py4gw_root:
        Absolute path to the Py4GW-main directory.  Only required on the
        first call; subsequent calls reuse the cached path.  If omitted on
        the first call, falls back to ``os.getcwd()``.
    """
    global _INI_PATH, _last_read_time, _cached_strategy

    # -- Resolve path once --------------------------------------------------
    if not _INI_PATH:
        root = py4gw_root or os.getcwd()
        _INI_PATH = os.path.join(
            root, "Widgets", "Config", "GeminiCommander", "commands.ini"
        )

    # -- Throttle: return cache if still fresh ------------------------------
    now = time.time()
    if now - _last_read_time < _POLL_INTERVAL:
        return _cached_strategy

    _last_read_time = now

    # -- Read + parse -------------------------------------------------------
    try:
        _cached_strategy = _read_ini(_INI_PATH, now)
    except Exception:
        # Fail silent.  File might not exist, be mid-write, or malformed.
        # Returning None tells HeroAI "no overrides, use defaults".
        _cached_strategy = None

    return _cached_strategy


def reset() -> None:
    """Clear cached state.  Useful for tests or manual reloads."""
    global _INI_PATH, _last_read_time, _cached_strategy
    _INI_PATH = ""
    _last_read_time = 0.0
    _cached_strategy = None


# ---------------------------------------------------------------------------
# Internal parsing
# ---------------------------------------------------------------------------

def _read_ini(path: str, now: float) -> Optional[GeminiStrategy]:
    """Parse the INI file and return a strategy, or None if unusable."""

    if not os.path.isfile(path):
        return None

    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")

    # -- [Commander] section: must exist and be active ----------------------
    if not cp.has_section("Commander"):
        return None

    active = cp.get("Commander", "active", fallback="false").strip().lower()
    if active != "true":
        return None

    # -- Heartbeat freshness check ------------------------------------------
    heartbeat_str = cp.get("Commander", "heartbeat", fallback="0")
    try:
        heartbeat = float(heartbeat_str)
    except (ValueError, TypeError):
        return None  # unparseable heartbeat = treat as dead

    if now - heartbeat > _HEARTBEAT_TIMEOUT:
        return None  # commander stopped updating, ignore stale commands

    # -- Timestamp ----------------------------------------------------------
    timestamp_str = cp.get("Commander", "timestamp", fallback="0")
    try:
        timestamp = float(timestamp_str)
    except (ValueError, TypeError):
        timestamp = 0.0

    # -- [Strategy] section -------------------------------------------------
    stance = _validated(cp, "Strategy", "stance", _VALID_STANCES, "balanced")
    focus_target = _validated(cp, "Strategy", "focus_target", _VALID_FOCUS, "")
    formation = _validated(cp, "Strategy", "formation", _VALID_FORMATIONS, "tight")
    flag_position = _validated(cp, "Strategy", "flag_position", _VALID_FLAGS, "follow")

    prebuff_str = cp.get("Strategy", "prebuff", fallback="false").strip().lower()
    prebuff = prebuff_str == "true"

    # -- [Weights] section (0 = "no override, keep HeroAI default") ---------
    weight_healer = _safe_int(cp, "Weights", "healer", 0)
    weight_caster = _safe_int(cp, "Weights", "caster", 0)
    weight_casting = _safe_int(cp, "Weights", "casting", 0)

    return GeminiStrategy(
        stance=stance,
        focus_target=focus_target,
        formation=formation,
        prebuff=prebuff,
        flag_position=flag_position,
        weight_healer=weight_healer,
        weight_caster=weight_caster,
        weight_casting=weight_casting,
        is_active=True,
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validated(
    cp: configparser.ConfigParser,
    section: str,
    key: str,
    allowed: frozenset,
    default: str,
) -> str:
    """Read a string value from *cp* and return it only if it is in *allowed*.

    Returns *default* when the key is missing or its value is not whitelisted.
    This guards against arbitrary strings from Gemini output reaching HeroAI.
    """
    raw = cp.get(section, key, fallback=default).strip().lower()
    return raw if raw in allowed else default


def _safe_int(
    cp: configparser.ConfigParser,
    section: str,
    key: str,
    default: int,
) -> int:
    """Read an integer value, clamped to [0, 100].  Non-numeric = *default*."""
    raw = cp.get(section, key, fallback=str(default)).strip()
    try:
        value = int(raw)
        return max(0, min(100, value))
    except (ValueError, TypeError):
        return default


# ===========================================================================
# INTEGRATION COMMENTS
# ===========================================================================
#
# These show WHERE and HOW get_gemini_strategy() plugs into HeroAI.
# Actual wiring is Phase 2b -- these are comments only, no files are modified.
#
# ---------------------------------------------------------------------------
# 1. In targeting.py :: _score_enemy()  (line ~210)
# ---------------------------------------------------------------------------
#
#   After the existing weight calculations (healer, caster, casting bonuses),
#   add a Gemini override block:
#
#       # --- Gemini Commander weight overrides ---
#       # from HeroAI.gemini_reader import get_gemini_strategy
#       #
#       # strategy = get_gemini_strategy()
#       # if strategy is not None:
#       #     # Override healer weight if commander has an opinion
#       #     if strategy.weight_healer > 0 and _is_enemy_healer(agent_id):
#       #         score += strategy.weight_healer  # replaces WEIGHT_HEALER
#       #
#       #     # Override caster weight
#       #     if strategy.weight_caster > 0 and _is_enemy_caster(agent_id):
#       #         score += strategy.weight_caster  # replaces WEIGHT_CASTER
#       #
#       #     # Override casting weight
#       #     if strategy.weight_casting > 0:
#       #         try:
#       #             if Agent.IsCasting(agent_id):
#       #                 score += strategy.weight_casting
#       #         except Exception:
#       #             pass
#       #
#       #     # Focus target bonus: large score bump for the commanded target type
#       #     if strategy.focus_target == "monk" and _is_enemy_healer(agent_id):
#       #         score += 50.0  # Gemini says focus monks
#       #     elif strategy.focus_target == "caster" and _is_enemy_caster(agent_id):
#       #         score += 40.0  # Gemini says focus casters
#       #     elif strategy.focus_target == "lowest_hp":
#       #         score += (1.0 - hp) * 30.0  # amplify low-HP targeting
#
# ---------------------------------------------------------------------------
# 2. In combat.py :: FindCastableSkill()  (line ~2001)
# ---------------------------------------------------------------------------
#
#   At the top of the method, before the skill loop, check retreat stance:
#
#       # --- Gemini Commander retreat check ---
#       # from HeroAI.gemini_reader import get_gemini_strategy
#       #
#       # strategy = get_gemini_strategy()
#       # if strategy is not None and strategy.stance == "retreat":
#       #     return (-1, 0)  # Gemini says disengage -- cast nothing
#       #
#       # # Defensive stance: skip offensive skills
#       # if strategy is not None and strategy.stance == "defensive":
#       #     # Could filter out offensive skill natures in the loop below,
#       #     # allowing only heals/prots/cleanses.  Example:
#       #     # defensive_only = True
#       #     pass
#
# ---------------------------------------------------------------------------
# 3. In following.py :: formation logic
# ---------------------------------------------------------------------------
#
#   The formation field ("tight", "spread", "line", "behind_master") maps
#   directly to existing HeroAI formation presets.  Integration would read:
#
#       # strategy = get_gemini_strategy()
#       # if strategy is not None and strategy.formation != "tight":
#       #     formation_override = strategy.formation
#       #     # Apply to formation spacing / positioning logic
#
# ---------------------------------------------------------------------------
# 4. Initialisation: call with py4gw_root once at startup
# ---------------------------------------------------------------------------
#
#   In the HeroAI entry point (e.g. HeroAI.py widget or combat.py init):
#
#       # from HeroAI.gemini_reader import get_gemini_strategy
#       # get_gemini_strategy(py4gw_root=os.path.dirname(os.path.dirname(__file__)))
#       #
#       # After first call, py4gw_root is cached -- subsequent calls need no arg.
