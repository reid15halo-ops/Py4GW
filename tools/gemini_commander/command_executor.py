"""
Gemini Commander - Command Executor

Translates GeminiCommand objects into a shared INI file that HeroAI can
optionally poll. This is the safest integration approach: no direct shared
memory writes, no DLL injection, no socket commands. Just a file on disk.

Output: Widgets/Config/GeminiCommander/commands.ini

INI layout:
  [Commander]  - timestamp + active flag
  [Strategy]   - stance, focus_target, formation, prebuff, flag_position
  [Weights]    - scoring weight overrides (0 = use HeroAI defaults)
  [History]    - last 5 commands as JSON for debugging

HeroAI reads this file at its own pace. If the file is missing or
active=false, HeroAI falls back to its default behavior tree.

Atomic writes (temp + rename via os.replace) prevent torn reads across
all 6 clients.

No Py4GW imports - this module is standalone.
"""

import configparser
import json
import os
import time


class CommandExecutor:
    """Writes Gemini tactical commands to a shared INI file for HeroAI."""

    def __init__(self, py4gw_root: str):
        self.ini_path = os.path.join(
            py4gw_root, "Widgets", "Config", "GeminiCommander", "commands.ini"
        )
        os.makedirs(os.path.dirname(self.ini_path), exist_ok=True)

        self._command_history: list[dict] = []

        self._current_strategy = {
            "stance": "balanced",
            "focus_target": "",
            "formation": "tight",
            "prebuff": "false",
            "flag_position": "follow",
        }

        self._weight_overrides = {
            "healer": 0,
            "caster": 0,
            "casting": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, commands: list) -> None:
        """Process a list of GeminiCommand objects and update the shared INI.

        Each command object must expose:
            cmd    - str   (e.g. "focus", "stance", "formation", ...)
            params - dict  (command-specific key/value pairs)
        """
        for cmd in commands:
            self._apply_command(cmd)
            self._command_history.insert(0, {
                "cmd": cmd.cmd,
                **cmd.params,
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

        # Keep only the last 5 commands for the history section.
        self._command_history = self._command_history[:5]

        self._write_ini()

    def deactivate(self) -> None:
        """Write active=false so HeroAI falls back to default behavior."""
        cp = configparser.ConfigParser()
        cp["Commander"] = {
            "timestamp": str(time.time()),
            "active": "false",
        }
        self._atomic_write(cp)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _apply_command(self, cmd) -> None:
        """Map a single GeminiCommand into internal strategy state."""
        handler = self._COMMAND_MAP.get(cmd.cmd)
        if handler is not None:
            handler(self, cmd.params)

    def _handle_focus(self, params: dict) -> None:
        self._current_strategy["focus_target"] = params.get("target", "")

    def _handle_stance(self, params: dict) -> None:
        self._current_strategy["stance"] = params.get("mode", "balanced")

    def _handle_formation(self, params: dict) -> None:
        self._current_strategy["formation"] = params.get("type", "tight")

    def _handle_prebuff(self, params: dict) -> None:
        self._current_strategy["prebuff"] = str(params.get("enabled", False)).lower()

    def _handle_flag(self, params: dict) -> None:
        self._current_strategy["flag_position"] = params.get("position", "follow")

    def _handle_priority(self, params: dict) -> None:
        weights = params.get("weights", {})
        for key, value in weights.items():
            if key in self._weight_overrides:
                self._weight_overrides[key] = int(value)

    # Dispatch table - avoids long if/elif chains.
    _COMMAND_MAP = {
        "focus": _handle_focus,
        "stance": _handle_stance,
        "formation": _handle_formation,
        "prebuff": _handle_prebuff,
        "flag": _handle_flag,
        "priority": _handle_priority,
    }

    # ------------------------------------------------------------------
    # INI serialization
    # ------------------------------------------------------------------

    def _write_ini(self) -> None:
        """Build the full INI from current state and write atomically."""
        cp = configparser.ConfigParser()

        cp["Commander"] = {
            "timestamp": str(time.time()),
            "active": "true",
            "heartbeat": str(time.time()),  # HeroAI should ignore commands if heartbeat >30s stale
        }

        cp["Strategy"] = self._current_strategy

        cp["Weights"] = {k: str(v) for k, v in self._weight_overrides.items()}

        cp["History"] = {
            f"cmd_{i}": json.dumps(c)
            for i, c in enumerate(self._command_history)
        }

        self._atomic_write(cp)

    def _atomic_write(self, cp: configparser.ConfigParser) -> None:
        """Write via temp file + os.replace to prevent torn reads from 6 clients."""
        tmp_path = self.ini_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                cp.write(fh)
            os.replace(tmp_path, self.ini_path)
        except OSError as e:
            print(f"[CommandExecutor] Write error: {e}")
            # Clean up temp file on failure
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
