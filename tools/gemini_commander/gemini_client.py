"""
Gemini Commander -- API client for Py4GW multibox tactical AI.

Sends game-state snapshots to Gemini 2.5 Flash and returns structured
commands that the bridge layer translates into Py4GW actions.

Requires:  pip install google-generativeai
"""

import json
import time
from dataclasses import dataclass, field

try:
    import google.generativeai as genai
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False

try:
    from tools.gemini_commander.config import GEMINI_API_KEY
except ImportError:
    try:
        from config import GEMINI_API_KEY  # type: ignore[import]
    except ImportError:
        import os
        GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ---------------------------------------------------------------------------
# System prompt -- GW1-specialised tactical commander
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a Guild Wars 1 party commander controlling a 6-player multibox team.
You receive game state snapshots every 5-10 seconds and respond with tactical commands.

Your team composition:
- Janine Nazgul (Mesmer): Energy Surge DPS + Interrupts (Master, you don't control directly)
- Reid Infuse (Elementalist): Ether Renewal Prot Infuser
- Necro Reid (Necromancer): BiP Energy Battery + Resto Healer
- Reids Dwayna (Monk): Primary Healer (Healing Burst)
- Reid Seed (Paragon): Heroic Refrain + Save Yourselves
- Reid Tao (Ranger): Tao Dagger Spam Physical DPS

GW1 Knowledge:
- Monks are the highest priority interrupt/kill targets
- Discord combo: target needs BOTH hex AND condition to take bonus damage
- Save Yourselves (+100 armor) is the strongest defensive skill
- Protective Spirit caps damage at 10% max HP -- essential vs spike damage
- BiP (Blood is Power) gives energy to allies at cost of own HP
- Heroic Refrain gives +4 all attributes to entire party

Available commands (respond with JSON array):
- {"cmd": "focus", "target": "monk|caster|lowest_hp|boss|nearest"}
- {"cmd": "stance", "mode": "aggressive|balanced|defensive|retreat"}
- {"cmd": "formation", "type": "tight|spread|line|behind_master"}
- {"cmd": "prebuff", "enabled": true|false}
- {"cmd": "flag", "position": "hold|follow|master"}
- {"cmd": "priority", "weights": {"healer": 35, "caster": 12, "casting": 8}}

Rules:
- Respond ONLY with a JSON array of commands. No explanation.
- Maximum 3 commands per response.
- If the situation is fine (party HP >70%, winning the fight), respond with []
- NEVER command retreat unless party average HP is below 30%
- Priority order: survival > kill healers > DPS
"""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GeminiCommand:
    """Single tactical command returned by the model."""
    cmd: str
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Commander
# ---------------------------------------------------------------------------

class GeminiCommander:
    """Thin wrapper around the Gemini chat API with rate-limiting and
    structured-command parsing.

    Usage::

        commander = GeminiCommander(api_key="...")
        cmds = commander.get_commands(state_text)
        for c in cmds:
            print(c.cmd, c.params)
    """

    # Valid command names the model is allowed to emit.
    _VALID_CMDS = {"focus", "stance", "formation", "prebuff", "flag", "priority"}

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        min_interval: float = 5.0,
        max_retries: int = 2,
    ):
        if not _HAS_GENAI:
            raise ImportError(
                "google-generativeai is not installed. "
                "Run:  pip install google-generativeai"
            )

        resolved_key = api_key or GEMINI_API_KEY
        if not resolved_key:
            raise ValueError(
                "No Gemini API key provided. Either pass api_key= or set the "
                "GEMINI_API_KEY environment variable."
            )

        genai.configure(api_key=resolved_key)
        self._model = genai.GenerativeModel(
            model, system_instruction=SYSTEM_PROMPT
        )
        self._chat = self._model.start_chat()

        # Rate-limiting / stats
        self._last_call_time: float = 0.0
        self._min_interval: float = max(min_interval, 1.0)
        self._max_retries: int = max_retries
        self._call_count: int = 0
        self._error_count: int = 0
        self._consecutive_errors: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_commands(self, game_state_text: str) -> list[GeminiCommand]:
        """Send a game-state snapshot to Gemini and return parsed commands.

        Returns an empty list when rate-limited, when the model says
        "do nothing", or on any error (stability > speed).
        """
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < self._min_interval:
            return []

        # Back off after repeated failures (exponential, capped at 60 s).
        if self._consecutive_errors > 0:
            backoff = min(2 ** self._consecutive_errors, 60)
            if elapsed < backoff:
                return []

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._chat.send_message(game_state_text)
                self._last_call_time = time.time()
                self._call_count += 1
                self._consecutive_errors = 0
                return self._parse_response(response.text)

            except json.JSONDecodeError:
                self._error_count += 1
                self._consecutive_errors += 1
                return []  # bad JSON -- don't retry, just skip this tick

            except Exception as exc:  # noqa: BLE001
                self._error_count += 1
                self._consecutive_errors += 1
                print(
                    f"[GeminiCommander] attempt {attempt}/{self._max_retries} "
                    f"failed: {exc}"
                )
                if attempt < self._max_retries:
                    time.sleep(1.0)  # short pause before retry

        return []

    def get_stats(self) -> dict:
        """Return diagnostic counters (calls, errors, rough cost)."""
        return {
            "calls": self._call_count,
            "errors": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            # Gemini 2.5 Flash pricing: ~$0.15/1M input + $0.60/1M output.
            # Rough per-call estimate assuming ~800 input + 80 output tokens.
            "cost_estimate_usd": round(self._call_count * 0.00017, 5),
        }

    def reset_chat(self) -> None:
        """Start a fresh chat session (clears context window)."""
        self._chat = self._model.start_chat()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _parse_response(self, text: str) -> list[GeminiCommand]:
        """Extract a JSON command array from the model's raw text."""
        text = text.strip()

        # Strip markdown code fences if the model wraps them.
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        commands_raw = json.loads(text)
        if not isinstance(commands_raw, list):
            commands_raw = [commands_raw]

        # Cap at 3 and validate.
        result: list[GeminiCommand] = []
        for entry in commands_raw[:3]:
            if not isinstance(entry, dict):
                continue
            cmd_name = entry.get("cmd", "")
            if cmd_name not in self._VALID_CMDS:
                continue
            result.append(GeminiCommand(cmd=cmd_name, params=entry))

        return result
