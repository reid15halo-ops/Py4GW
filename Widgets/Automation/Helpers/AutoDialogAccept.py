"""Auto Dialog Accept — automatically clicks the first dialog button on all clients.

When enabled, monitors for active NPC dialogs and clicks the first available
button after a short delay. Useful for quest chains, NPC interactions, and
leveler bots that need dialogs accepted without manual intervention.

Runs per-client (each GW instance runs its own copy). Enable on each client
where you want auto-accept behavior.
"""

import random
import time
import traceback

import Py4GW
import PyDialog
import PyImGui
from Py4GWCoreLib import Player, ConsoleLog, Console, Map

MODULE_NAME = "Auto Dialog Accept"
MODULE_ICON = "Textures\\Module_Icons\\Script Runner.png"

# State
# _enabled defaults True so the widget auto-accepts dialogs on every client
# without requiring a manual checkbox tick on each one. Toggle off in the
# widget's ImGui window if you need to disable it on a specific client.
_enabled = True
_delay_ms = 1500          # wait before clicking (ms) — lets dialog fully render
_last_click_time = 0.0    # prevents rapid double-clicks
_cooldown_s = 2.0         # min seconds between clicks
# _log_clicks defaults False to avoid console spam during long dialog chains
# (per feedback_console_log_spam_crashes.md — rapid ConsoleLog can crash
# the GW1 client). Re-enable in the widget UI when debugging.
_log_clicks = False       # log each auto-click to console
_skip_in_explorable = False  # if True, only auto-click in outposts
_click_count = 0          # consecutive clicks in current dialog chain
_max_clicks = 4           # after this many clicks, close the dialog instead
_dialog_was_active = False # track dialog state transitions


def configure():
    pass


def tooltip():
    PyImGui.begin_tooltip()
    PyImGui.text(MODULE_NAME)
    PyImGui.separator()
    PyImGui.text_wrapped(
        "Automatically clicks the first dialog button when an NPC dialog is open. "
        "Enable per-client. Configurable delay and cooldown."
    )
    PyImGui.end_tooltip()


def main():
    global _enabled, _delay_ms, _cooldown_s, _log_clicks, _skip_in_explorable
    global _last_click_time, _click_count, _max_clicks, _dialog_was_active

    try:
        if PyImGui.begin(f"{MODULE_NAME}##auto_dialog", PyImGui.WindowFlags.AlwaysAutoResize):
            _enabled = PyImGui.checkbox("Enable Auto-Accept", _enabled)

            if _enabled:
                PyImGui.separator()
                _delay_ms = int(PyImGui.slider_float("Delay (ms)", float(_delay_ms), 500.0, 5000.0))
                _cooldown_s = PyImGui.slider_float("Cooldown (s)", _cooldown_s, 0.5, 10.0)
                _max_clicks = int(PyImGui.slider_float("Max clicks before close", float(_max_clicks), 1.0, 20.0))
                _log_clicks = PyImGui.checkbox("Log clicks to console", _log_clicks)
                _skip_in_explorable = PyImGui.checkbox("Outpost only (skip explorable)", _skip_in_explorable)
                PyImGui.text(f"Click count: {_click_count} / {_max_clicks}")

                PyImGui.separator()
                # Show current dialog status
                try:
                    is_active = PyDialog.PyDialog.is_dialog_active()
                    PyImGui.text(f"Dialog active: {is_active}")
                    if is_active:
                        active = PyDialog.PyDialog.get_active_dialog()
                        PyImGui.text(f"Agent: {active.agent_id}")
                        buttons = list(PyDialog.PyDialog.get_active_dialog_buttons())
                        visible = [b for b in buttons if getattr(b, "dialog_id", 0) != 0]
                        PyImGui.text(f"Buttons: {len(visible)}")
                        for i, btn in enumerate(visible):
                            label = btn.message_decoded or btn.message or f"Button {i}"
                            PyImGui.text(f"  [{i}] 0x{btn.dialog_id:X}: {label[:40]}")
                except Exception:
                    PyImGui.text("(dialog check error)")
            else:
                PyImGui.text("Disabled — dialogs will not be auto-accepted")
        PyImGui.end()

        # Auto-accept logic
        if not _enabled:
            return

        # Map guard
        if not Map.IsMapReady() or Map.IsMapLoading():
            return

        # Explorable guard
        if _skip_in_explorable and Map.IsExplorable():
            return

        # Cooldown guard — add random jitter (0–0.5s) per CLAUDE.md rule 4
        # to desync dialog clicks across 6 simultaneous clients
        now = time.time()
        if (now - _last_click_time) < (_cooldown_s + random.uniform(0, 0.5)):
            return

        # Check if dialog is active
        dialog_active = PyDialog.PyDialog.is_dialog_active()

        # Reset click counter when dialog closes and reopens
        if not dialog_active:
            if _dialog_was_active:
                _click_count = 0
            _dialog_was_active = False
            return

        # Only accept dialogs triggered by an NPC interaction (agent_id > 0).
        # System popups, quest triggers, and non-NPC dialogs have agent_id == 0.
        try:
            active = PyDialog.PyDialog.get_active_dialog()
            if not active or getattr(active, "agent_id", 0) == 0:
                return
        except Exception:
            return

        _dialog_was_active = True

        # Get visible buttons
        try:
            buttons = list(PyDialog.PyDialog.get_active_dialog_buttons())
            visible = [b for b in buttons if getattr(b, "dialog_id", 0) != 0]
        except Exception:
            return

        if not visible:
            return

        # After max_clicks, close the dialog instead of clicking more
        if _click_count >= _max_clicks:
            try:
                Player.SendChatCommand("close")
            except Exception:
                pass
            # Also try pressing Escape via sending dialog 0 (common close pattern)
            try:
                Player.player_instance().SendDialog(0)
            except Exception:
                pass
            _last_click_time = now
            _click_count = 0
            if _log_clicks:
                ConsoleLog(MODULE_NAME,
                           f"Max clicks ({_max_clicks}) reached — closing dialog",
                           Console.MessageType.Info)
            return

        # Click the first button
        dialog_id = visible[0].dialog_id
        try:
            Player.SendDialog(dialog_id)
            _last_click_time = now
            _click_count += 1
            if _log_clicks:
                label = visible[0].message_decoded or visible[0].message or "?"
                ConsoleLog(MODULE_NAME,
                           f"Click {_click_count}/{_max_clicks}: 0x{dialog_id:X} ({label[:50]})",
                           Console.MessageType.Info)
        except Exception as e:
            ConsoleLog(MODULE_NAME, f"SendDialog error: {e}",
                       Console.MessageType.Warning)

    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error)


if __name__ == "__main__":
    main()
