"""
Gemini Debugger — Sends Py4GW error logs and code context to Gemini for diagnosis.

Usage:
    from tools.gemini_commander.debugger import GeminiDebugger

    dbg = GeminiDebugger(api_key="...", py4gw_root="C:/Users/reid1/Documents/Py4GW-main")

    # Analyze a crash log
    result = dbg.diagnose_error("AttributeError: 'AccountStruct' object has no attribute 'MapID'")
    print(result.explanation)
    print(result.fix_suggestion)

    # Analyze an injection log
    result = dbg.analyze_log("Py4GW_injection_log.txt")

    # Watch for errors in real-time
    dbg.watch_logs(callback=lambda r: print(r.fix_suggestion))
"""

import os
import time
import json
from dataclasses import dataclass, field
from pathlib import Path

try:
    import google.generativeai as genai
except ImportError:
    genai = None


DEBUG_SYSTEM_PROMPT = """You are a Python debugging expert specializing in the Py4GW framework — a Guild Wars 1 automation/multiboxing tool.

## Critical Context

Py4GW injects a Python 3.13 (32-bit) DLL into the GW1 game client. 6 clients run simultaneously.
The #1 bug class is "error 007" (connection lost) caused by:
- Unhandled game dialogs (salvage choice, materials confirmation)
- Rapid-fire server actions (<400ms apart)
- Invalid agent IDs passed to Player.ChangeTarget()
- Silent exception swallowing (except: pass) masking the real cause

## Key Architecture

- Py4GWCoreLib/ — Framework library (Agent, Inventory, Map, Player, Skill APIs)
- HeroAI/ — Combat AI with BehaviorTree, weighted target scoring, skill prioritization
- Widgets/ — Hot-loaded Python scripts (AutoLootManager, MultiboxCommander, HeroAI widget)
- SharedMemory — ctypes.Structure for 6-client coordination (NO locks, NO atomics)
- ActionQueueManager — ALL game API calls must go through this (serializes actions)

## Common Error Patterns

1. `AttributeError: 'X' object has no attribute 'Y'` — Usually means shared memory struct layout changed or wrong field path (e.g., acc.MapID should be acc.AgentData.Map.MapID)
2. `error 007` — Client disconnected from server. Check for unhandled dialogs, missing ActionQueueManager usage, or rapid actions
3. `TypeError: cannot unpack` — Skill data or agent data returned None/0 during map transition
4. `IndexError` — Party array or enemy array was empty (checked during loading screen)
5. Widget silently doing nothing — Usually except:pass swallowing the real error

## Rules for Your Responses

Respond with JSON:
{
    "severity": "critical|high|medium|low",
    "category": "007_risk|crash|logic_bug|performance|silent_failure",
    "root_cause": "One sentence explanation",
    "explanation": "Detailed explanation of what went wrong and why",
    "fix_suggestion": "Specific code change to fix the issue",
    "file_hint": "Which file(s) to look at",
    "prevention": "How to prevent this class of bug in the future",
    "related_rules": ["CLAUDE.md rule numbers that apply"]
}
"""


@dataclass
class DiagnosisResult:
    severity: str = "unknown"
    category: str = "unknown"
    root_cause: str = ""
    explanation: str = ""
    fix_suggestion: str = ""
    file_hint: str = ""
    prevention: str = ""
    related_rules: list[str] = field(default_factory=list)
    raw_response: str = ""
    error: str = ""

    @staticmethod
    def from_json(text: str) -> "DiagnosisResult":
        """Parse Gemini's JSON response into a DiagnosisResult."""
        try:
            # Strip markdown code fences if present
            clean = text.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(clean)
            return DiagnosisResult(
                severity=data.get("severity", "unknown"),
                category=data.get("category", "unknown"),
                root_cause=data.get("root_cause", ""),
                explanation=data.get("explanation", ""),
                fix_suggestion=data.get("fix_suggestion", ""),
                file_hint=data.get("file_hint", ""),
                prevention=data.get("prevention", ""),
                related_rules=data.get("related_rules", []),
                raw_response=text,
            )
        except (json.JSONDecodeError, KeyError) as e:
            return DiagnosisResult(
                root_cause="Failed to parse Gemini response",
                explanation=text[:500],
                raw_response=text,
                error=str(e),
            )

    def summary(self) -> str:
        """One-line summary for console output."""
        return f"[{self.severity.upper()}] {self.category}: {self.root_cause}"

    def full_report(self) -> str:
        """Multi-line report for detailed debugging."""
        lines = [
            f"{'='*60}",
            f"Severity: {self.severity.upper()}",
            f"Category: {self.category}",
            f"Root Cause: {self.root_cause}",
            f"{'='*60}",
            f"\nExplanation:\n{self.explanation}",
            f"\nFix:\n{self.fix_suggestion}",
            f"\nFile(s): {self.file_hint}",
            f"\nPrevention: {self.prevention}",
        ]
        if self.related_rules:
            lines.append(f"\nCLAUDE.md Rules: {', '.join(self.related_rules)}")
        return "\n".join(lines)


class GeminiDebugger:
    """Sends Py4GW errors to Gemini for diagnosis and fix suggestions."""

    def __init__(self, api_key: str, py4gw_root: str, model: str = "gemini-2.5-flash"):
        if not genai:
            raise ImportError("pip install google-generativeai")
        if not api_key:
            raise ValueError("GEMINI_API_KEY required")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model, system_instruction=DEBUG_SYSTEM_PROMPT)
        self.py4gw_root = Path(py4gw_root)
        self._call_count = 0

    def diagnose_error(self, error_text: str, context: str = "") -> DiagnosisResult:
        """Send an error message/traceback to Gemini for diagnosis."""
        prompt = f"## Error\n```\n{error_text}\n```"
        if context:
            prompt += f"\n\n## Context\n{context}"
        return self._query(prompt)

    def diagnose_traceback(self, traceback_text: str) -> DiagnosisResult:
        """Send a full Python traceback for diagnosis. Optionally reads the referenced file."""
        # Try to extract the file path from the traceback to provide code context
        code_context = ""
        for line in traceback_text.split("\n"):
            line = line.strip()
            if line.startswith("File \"") and ", line " in line:
                try:
                    file_path = line.split('"')[1]
                    line_no = int(line.split("line ")[1].split(",")[0])
                    # Security: only read files within the Py4GW project root
                    real_path = os.path.realpath(file_path)
                    real_root = os.path.realpath(str(self.py4gw_root))
                    if not real_path.startswith(real_root):
                        continue  # path traversal attempt or system file — skip
                    if os.path.exists(real_path):
                        with open(real_path, "r", encoding="utf-8") as f:
                            all_lines = f.readlines()
                        start = max(0, line_no - 10)
                        end = min(len(all_lines), line_no + 5)
                        snippet = "".join(all_lines[start:end])
                        code_context += f"\n## Code ({os.path.basename(file_path)} lines {start+1}-{end})\n```python\n{snippet}```\n"
                except Exception:
                    pass

        prompt = f"## Traceback\n```\n{traceback_text}\n```"
        if code_context:
            prompt += f"\n{code_context}"
        return self._query(prompt)

    def analyze_log(self, log_path: str = "", last_n_lines: int = 50) -> list[DiagnosisResult]:
        """Read a log file and diagnose any errors found."""
        if not log_path:
            log_path = str(self.py4gw_root / "Py4GW_injection_log.txt")

        if not os.path.exists(log_path):
            return [DiagnosisResult(error=f"Log file not found: {log_path}")]

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Extract error lines
        error_blocks = []
        current_block = []
        for line in lines[-last_n_lines:]:
            if "error" in line.lower() or "exception" in line.lower() or "traceback" in line.lower():
                current_block.append(line)
            elif current_block:
                if line.strip().startswith("File ") or line.strip().startswith("at ") or line.strip():
                    current_block.append(line)
                else:
                    error_blocks.append("".join(current_block))
                    current_block = []
        if current_block:
            error_blocks.append("".join(current_block))

        if not error_blocks:
            return [DiagnosisResult(root_cause="No errors found in log", severity="low")]

        results = []
        for block in error_blocks[:5]:  # Max 5 errors per analysis
            results.append(self.diagnose_error(block))
        return results

    def check_verification(self) -> DiagnosisResult:
        """Run verify_custom_changes.py and diagnose any failures."""
        import subprocess
        try:
            result = subprocess.run(
                ["python", str(self.py4gw_root / "verify_custom_changes.py")],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout + result.stderr

            if "MISSING" in output or "FAIL" in output:
                return self.diagnose_error(
                    output,
                    context="This is the output of verify_custom_changes.py which checks that critical safety fixes are still present."
                )
            return DiagnosisResult(
                severity="low",
                root_cause="All verification checks passed",
                explanation=output.split("\n")[-3] if output.strip() else "OK",
            )
        except Exception as e:
            return DiagnosisResult(error=f"Failed to run verification: {e}")

    def watch_logs(self, log_path: str = "", interval: float = 10.0, callback=None):
        """Continuously watch a log file for new errors. Blocking."""
        if not log_path:
            log_path = str(self.py4gw_root / "Py4GW_injection_log.txt")

        print(f"[Debugger] Watching {log_path} (Ctrl+C to stop)")
        last_size = 0
        if os.path.exists(log_path):
            last_size = os.path.getsize(log_path)

        while True:
            try:
                if os.path.exists(log_path):
                    current_size = os.path.getsize(log_path)
                    if current_size > last_size:
                        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_size)
                            new_content = f.read()
                        last_size = current_size

                        if "error" in new_content.lower() or "exception" in new_content.lower():
                            result = self.diagnose_error(new_content[-2000:])  # Last 2KB of new content
                            print(f"\n{result.summary()}")
                            if callback:
                                callback(result)
                            else:
                                print(result.fix_suggestion)

                time.sleep(interval)
            except KeyboardInterrupt:
                print("\n[Debugger] Stopped watching.")
                break
            except Exception as e:
                print(f"[Debugger] Watch error: {e}")
                time.sleep(interval)

    def _query(self, prompt: str) -> DiagnosisResult:
        """Send prompt to Gemini and parse response."""
        try:
            response = self.model.generate_content(prompt)
            self._call_count += 1
            return DiagnosisResult.from_json(response.text)
        except Exception as e:
            return DiagnosisResult(error=f"Gemini API error: {e}")
