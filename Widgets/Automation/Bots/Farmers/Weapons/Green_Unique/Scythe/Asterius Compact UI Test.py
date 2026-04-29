"""Asterius Compact UI Test -- minimal UI with progress bar, start/stop, debug log.

Replaces the default bot window with a slim panel. All farm logic is
imported from v3 — this file only overrides the UI layer.
"""

import time
import PyImGui
from Py4GWCoreLib import Color, ConsoleLog, Console, ImGui, Map

# ---------------------------------------------------------------------------
# Import the ENTIRE v3 bot (farm logic, FSM, bot instance, etc.)
# Module name has spaces so we use spec_from_file_location.
# ---------------------------------------------------------------------------
import importlib.util, os, sys
import Py4GW as _Py4GW

_base_dir = os.path.join(_Py4GW.Console.get_projects_path(),
                         "Widgets", "Automation", "Bots", "Farmers",
                         "Weapons", "Green_Unique", "Scythe")
_v3_path = os.path.join(_base_dir, "Asterius Scythe v3.py")
if not os.path.exists(_v3_path):
    raise FileNotFoundError(f"Asterius v3 not found: {_v3_path}")
_spec = importlib.util.spec_from_file_location("asterius_v3", _v3_path)
_v3 = importlib.util.module_from_spec(_spec)
sys.modules["asterius_v3"] = _v3
_spec.loader.exec_module(_v3)

bot = _v3.bot
BOT_NAME = _v3.BOT_NAME
_get_debug_state = _v3._get_debug_state
boss_state = _v3.boss_state
run_timer = _v3.run_timer
active_strategy = _v3.active_strategy
TEXTURE = _v3.TEXTURE

# ---------------------------------------------------------------------------
# Log ring buffer
# ---------------------------------------------------------------------------
_MAX_LOG_LINES = 200
_log_lines: list[str] = []


def _capture_log(tag: str, msg, msg_type=Console.MessageType.Info):
    """Intercept ConsoleLog calls and store them in the ring buffer."""
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] [{tag}] {msg}"
    _log_lines.append(line)
    if len(_log_lines) > _MAX_LOG_LINES:
        del _log_lines[:len(_log_lines) - _MAX_LOG_LINES]


# Monkey-patch: hook into Py4GW.Console.Log to capture messages.
# The C++ binding only accepts (str, str, MessageType) -- no kwargs.
# Store original for cleanup.
import Py4GW
_orig_py4gw_log = Py4GW.Console.Log
_hook_installed = False


def _hooked_log(tag, msg, msg_type=Py4GW.Console.MessageType.Info, **_kwargs):
    _capture_log(tag, msg, msg_type)
    _orig_py4gw_log(tag, msg, msg_type)


def _install_hook():
    global _hook_installed
    if not _hook_installed:
        Py4GW.Console.Log = _hooked_log
        _hook_installed = True


def _remove_hook():
    global _hook_installed
    if _hook_installed:
        Py4GW.Console.Log = _orig_py4gw_log
        _hook_installed = False


_install_hook()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_show_log = True

# Colors
_COL_BG = Color(30, 30, 35, 240).to_tuple_normalized()
_COL_ACCENT = Color(255, 200, 100, 255).to_tuple_normalized()
_COL_GREEN = Color(100, 255, 100, 255).to_tuple_normalized()
_COL_RED = Color(255, 100, 100, 255).to_tuple_normalized()
_COL_DIM = Color(140, 140, 140, 255).to_tuple_normalized()
_COL_BAR = Color(80, 180, 255, 255).to_tuple_normalized()
_COL_BAR_BG = Color(50, 50, 60, 255).to_tuple_normalized()


def _get_progress() -> tuple[float, str]:
    """Return (fraction 0-1, label) for the progress bar."""
    try:
        s = _get_debug_state()

        # Phase 1: traveling to map
        if not Map.IsExplorable():
            return 0.05, "Traveling..."

        # Phase 2: patrol progress (waypoint N/total)
        wp_str = s.get("waypoint", "?/?")
        if "/" in str(wp_str):
            parts = str(wp_str).split("/")
            try:
                current = int(parts[0])
                total = int(parts[1])
                if total > 0:
                    # Boss spotted or killed? Jump progress
                    if s.get("boss_killed"):
                        return 0.95, "Boss KILLED -- looting"
                    if s.get("boss_spotted"):
                        dist = s.get("boss_dist", "?")
                        return 0.85, f"Boss SPOTTED @ {dist}u"
                    frac = 0.10 + 0.70 * (current / total)
                    return frac, f"Patrol {current}/{total}"
            except (ValueError, ZeroDivisionError):
                pass

        return 0.10, "Patrolling..."
    except Exception:
        return 0.0, "Starting..."


def _draw_compact_window():
    """Draw the compact Asterius UI -- single strip."""
    global _show_log

    try:
        PyImGui.set_next_window_size(340, 180)
        if not PyImGui.begin("Asterius##compact", True):
            PyImGui.end()
            return
    except Exception:
        return

    try:
        # --- Line 1: progress bar with label overlay ---
        frac, label = _get_progress()
        try:
            s = _get_debug_state()
            strat = s.get("strategy", "?")
            hm = "HM" if s.get("hard_mode", True) else "NM"
            run_t = s.get("run_time", "0s")
            overlay = f"{label}  |  {strat} {hm}  |  {run_t}"
        except Exception:
            overlay = label
        PyImGui.progress_bar(frac, -1, 18, overlay)

        # --- Line 2: pause/resume + log toggle + copy ---
        fsm = bot.config.FSM
        is_paused = fsm.is_paused() if fsm else False
        if is_paused:
            if PyImGui.button("Resume", 55, 20):
                fsm.resume()
        else:
            if PyImGui.button("Pause", 55, 20):
                fsm.pause()
        PyImGui.same_line(0.0, 8.0)
        _show_log = PyImGui.checkbox("Log", _show_log)
        if _show_log:
            PyImGui.same_line(0.0, 8.0)
            if PyImGui.button("Copy", 38, 20):
                try:
                    PyImGui.set_clipboard_text("\n".join(_log_lines))
                except Exception:
                    pass

        # --- Log area (only if toggled) ---
        if _show_log:
            PyImGui.begin_child("##log", 0, -1, True)
            for line in _log_lines[-50:]:
                if "[WARN" in line or "WARNING" in line or "ERROR" in line:
                    PyImGui.text_colored(line, _COL_RED)
                elif "[WIN]" in line or "KILLED" in line:
                    PyImGui.text_colored(line, _COL_GREEN)
                elif "[DBG]" in line:
                    PyImGui.text_colored(line, _COL_DIM)
                else:
                    PyImGui.text(line)
            PyImGui.set_scroll_here_y(1.0)
            PyImGui.end_child()

    except Exception as e:
        PyImGui.text(f"UI Error: {e}")
    finally:
        PyImGui.end()


def main():
    bot.Update()
    _draw_compact_window()


def tooltip():
    PyImGui.begin_tooltip()
    PyImGui.text("Asterius Compact UI Test")
    PyImGui.text("Slim progress bar + log view")
    PyImGui.end_tooltip()


if __name__ == "__main__":
    main()
