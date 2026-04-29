"""
Gemini Commander Health Check -- verifies all components.

Usage:
    python -m tools.gemini_commander.healthcheck

Returns exit code 0 if everything is healthy, 1 if any check fails.
Designed to be called by cron jobs or monitoring systems.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Resolve project root (three levels up from this file).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure the project root is on sys.path so our sibling modules import cleanly.
_root_str = str(_PROJECT_ROOT)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: str  # "OK", "FAIL", "SKIP", "WARN"
    detail: str = ""

    def line(self) -> str:
        tag = {
            "OK": "[OK]  ",
            "FAIL": "[FAIL]",
            "SKIP": "[SKIP]",
            "WARN": "[WARN]",
        }.get(self.status, "[????]")
        return f"{tag} {self.name} ({self.detail})"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

_MODULE_NAMES = [
    "tools.gemini_commander.config",
    "tools.gemini_commander.game_state",
    "tools.gemini_commander.gemini_client",
    "tools.gemini_commander.command_executor",
    "tools.gemini_commander.debugger",
    "tools.gemini_commander.main",
    "tools.gemini_commander.watch",
]


def check_module_imports() -> CheckResult:
    """Verify all 7 Gemini Commander modules import without error."""
    imported = 0
    failures: list[str] = []
    for mod_name in _MODULE_NAMES:
        try:
            __import__(mod_name)
            imported += 1
        except Exception as exc:
            short = mod_name.rsplit(".", 1)[-1]
            failures.append(f"{short}: {exc}")

    if failures:
        return CheckResult(
            "Module imports",
            "FAIL",
            f"{imported}/{len(_MODULE_NAMES)} -- {'; '.join(failures[:3])}",
        )
    return CheckResult("Module imports", "OK", f"{imported}/{len(_MODULE_NAMES)}")


def check_config() -> CheckResult:
    """Verify config values are sane: API key, master email, ports."""
    try:
        from tools.gemini_commander.config import (
            BRIDGE_HOST,
            BRIDGE_PORT,
            GEMINI_API_KEY,
            MASTER_EMAIL,
            POLL_INTERVAL_S,
        )
    except Exception as exc:
        return CheckResult("Config valid", "FAIL", str(exc))

    issues: list[str] = []

    api_status = "SET" if GEMINI_API_KEY else "NOT SET"
    master_status = "SET" if MASTER_EMAIL else "NOT SET"

    if not MASTER_EMAIL:
        issues.append("MASTER_EMAIL empty")

    if not isinstance(BRIDGE_PORT, int) or not (1 <= BRIDGE_PORT <= 65535):
        issues.append(f"BRIDGE_PORT invalid ({BRIDGE_PORT})")

    if POLL_INTERVAL_S < 1.0:
        issues.append(f"POLL_INTERVAL_S too low ({POLL_INTERVAL_S})")

    if issues:
        return CheckResult(
            "Config valid",
            "FAIL",
            f"API key: {api_status}, master: {master_status} -- {'; '.join(issues)}",
        )
    return CheckResult(
        "Config valid",
        "OK",
        f"API key: {api_status}, master: {master_status}",
    )


def check_commands_ini() -> CheckResult:
    """Check commands.ini existence, active flag, and heartbeat freshness."""
    ini_path = _PROJECT_ROOT / "Widgets" / "Config" / "GeminiCommander" / "commands.ini"

    if not ini_path.is_file():
        return CheckResult("Commands INI", "SKIP", "file does not exist (commander not yet run)")

    cp = configparser.ConfigParser()
    try:
        cp.read(str(ini_path), encoding="utf-8")
    except Exception as exc:
        return CheckResult("Commands INI", "FAIL", f"parse error: {exc}")

    active = cp.getboolean("Commander", "active", fallback=False)
    heartbeat = cp.getfloat("Commander", "heartbeat", fallback=0.0)

    if heartbeat > 0:
        age = time.time() - heartbeat
        age_str = f"heartbeat {age:.0f}s ago"
    else:
        age = float("inf")
        age_str = "no heartbeat"

    if not active:
        return CheckResult("Commands INI", "WARN", f"inactive, {age_str}")
    if age > 30:
        return CheckResult("Commands INI", "WARN", f"active but stale, {age_str}")

    return CheckResult("Commands INI", "OK", f"active, {age_str}")


def check_outpost_ini() -> CheckResult:
    """Check OutpostPrep_Status.ini: exists, account count, staleness."""
    ini_path = _PROJECT_ROOT / "Widgets" / "Config" / "OutpostPrep_Status.ini"

    if not ini_path.is_file():
        return CheckResult("Outpost INI", "SKIP", "file does not exist")

    cp = configparser.ConfigParser()
    try:
        cp.read(str(ini_path), encoding="utf-8")
    except Exception as exc:
        return CheckResult("Outpost INI", "FAIL", f"parse error: {exc}")

    sections = cp.sections()
    if not sections:
        return CheckResult("Outpost INI", "WARN", "file exists but has 0 accounts")

    # Check freshness of the most recent timestamp
    now = time.time()
    stale_count = 0
    newest_age = float("inf")
    for section in sections:
        ts = cp.getfloat(section, "timestamp", fallback=0.0)
        if ts > 0:
            age = now - ts
            newest_age = min(newest_age, age)
            if age > 300:  # >5 min = stale
                stale_count += 1

    age_str = f"newest {newest_age:.0f}s ago" if newest_age < float("inf") else "no timestamps"

    if stale_count == len(sections):
        return CheckResult("Outpost INI", "WARN", f"{len(sections)} accounts, all stale ({age_str})")

    return CheckResult("Outpost INI", "OK", f"{len(sections)} accounts reporting, {age_str}")


def check_game_state() -> CheckResult:
    """Verify GameStateSnapshot factories run without crashing."""
    try:
        from tools.gemini_commander.game_state import GameStateSnapshot
    except Exception as exc:
        return CheckResult("Game state", "FAIL", f"import error: {exc}")

    # Try mock first (always works)
    try:
        snap = GameStateSnapshot.from_mock(num_enemies=3, combat=True)
        mock_ok = True
    except Exception as exc:
        return CheckResult("Game state", "FAIL", f"from_mock() crashed: {exc}")

    # Try INI (may produce empty party if INI missing, but should not crash)
    ini_detail = ""
    try:
        snap_ini = GameStateSnapshot.from_ini(project_root=_PROJECT_ROOT)
        ini_detail = f"{len(snap_ini.party)} party members from INI"
    except Exception:
        ini_detail = "INI unavailable, mock OK"

    detail = ini_detail if ini_detail else f"{len(snap.party)} party members from mock"
    return CheckResult("Game state", "OK", detail)


def check_command_executor() -> CheckResult:
    """Verify CommandExecutor can write and deactivate without error."""
    try:
        from tools.gemini_commander.command_executor import CommandExecutor
    except Exception as exc:
        return CheckResult("Command executor", "FAIL", f"import error: {exc}")

    # Use a temporary directory to avoid polluting the real config.
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="gc_healthcheck_")
        executor = CommandExecutor(tmp_dir)

        # Write a deactivate command (safest possible operation).
        executor.deactivate()

        # Verify the file was created.
        ini_path = os.path.join(
            tmp_dir, "Widgets", "Config", "GeminiCommander", "commands.ini"
        )
        if not os.path.isfile(ini_path):
            return CheckResult("Command executor", "FAIL", "deactivate wrote no file")

        # Verify it parses and has active=false.
        cp = configparser.ConfigParser()
        cp.read(ini_path, encoding="utf-8")
        active = cp.getboolean("Commander", "active", fallback=True)
        if active:
            return CheckResult("Command executor", "FAIL", "deactivate did not set active=false")

        return CheckResult("Command executor", "OK", "write + deactivate OK")

    except Exception as exc:
        return CheckResult("Command executor", "FAIL", str(exc))

    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


def check_gemini_api() -> CheckResult:
    """Optionally ping the Gemini API if a key is configured."""
    try:
        from tools.gemini_commander.config import GEMINI_API_KEY
    except Exception:
        GEMINI_API_KEY = ""

    if not GEMINI_API_KEY:
        return CheckResult("Gemini API", "SKIP", "no key set")

    try:
        import google.generativeai as genai
    except ImportError:
        return CheckResult("Gemini API", "FAIL", "google-generativeai not installed")

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            "Reply with exactly: PONG",
            generation_config={"max_output_tokens": 10},
        )
        text = (response.text or "").strip()
        if "PONG" in text.upper():
            return CheckResult("Gemini API", "OK", "ping/pong OK")
        return CheckResult("Gemini API", "WARN", f"unexpected reply: {text[:40]}")

    except Exception as exc:
        return CheckResult("Gemini API", "FAIL", f"API error: {exc}")


def check_disk() -> CheckResult:
    """Verify the config directory is writable and has reasonable free space."""
    config_dir = _PROJECT_ROOT / "Widgets" / "Config"

    if not config_dir.is_dir():
        return CheckResult("Disk", "FAIL", f"Config dir missing: {config_dir}")

    # Test writability by creating and removing a temp file.
    test_file = config_dir / ".healthcheck_probe"
    try:
        test_file.write_text("probe", encoding="utf-8")
        test_file.unlink()
    except Exception as exc:
        return CheckResult("Disk", "FAIL", f"Config dir not writable: {exc}")

    # Check free space on the drive.
    try:
        usage = shutil.disk_usage(str(config_dir))
        free_mb = usage.free / (1024 * 1024)
        if free_mb < 50:
            return CheckResult("Disk", "WARN", f"Config dir writable, only {free_mb:.0f} MB free")
        return CheckResult("Disk", "OK", f"Config dir writable, {free_mb:.0f} MB free")
    except Exception:
        # disk_usage may fail on some setups; writability is the key check.
        return CheckResult("Disk", "OK", "Config dir writable")


def check_py4gw_verification() -> CheckResult:
    """Run verify_custom_changes.py if it exists."""
    script = _PROJECT_ROOT / "verify_custom_changes.py"
    if not script.is_file():
        return CheckResult("Py4GW verification", "SKIP", "verify_custom_changes.py not found")

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_PROJECT_ROOT),
        )
        output = result.stdout + result.stderr

        # Parse the "Results: X/Y checks passed" line from verify output.
        import re
        results_match = re.search(r"Results:\s*(\d+)/(\d+)\s*checks passed", output)

        # Count "FAIL:" or "MISSING:" prefixed lines (real failures).
        fail_lines = [
            ln for ln in output.splitlines()
            if ln.strip().startswith("FAIL:") or ln.strip().startswith("MISSING:")
        ]

        if result.returncode != 0 or fail_lines:
            lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
            summary_line = lines[-1] if lines else "unknown failure"
            return CheckResult(
                "Py4GW verification",
                "FAIL",
                f"{len(fail_lines)} failures -- {summary_line[:80]}",
            )

        if results_match:
            passed, total = results_match.group(1), results_match.group(2)
            return CheckResult("Py4GW verification", "OK", f"{passed}/{total} pass")

        return CheckResult("Py4GW verification", "OK", "all checks passed")

    except subprocess.TimeoutExpired:
        return CheckResult("Py4GW verification", "FAIL", "timed out after 30s")
    except Exception as exc:
        return CheckResult("Py4GW verification", "FAIL", str(exc))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_checks() -> list[CheckResult]:
    """Execute every health check and return the results."""
    return [
        check_module_imports(),
        check_config(),
        check_commands_ini(),
        check_outpost_ini(),
        check_game_state(),
        check_command_executor(),
        check_gemini_api(),
        check_disk(),
        check_py4gw_verification(),
    ]


def print_report(results: list[CheckResult]) -> int:
    """Print a formatted report and return an exit code (0=healthy, 1=unhealthy)."""
    print()
    print("=== Gemini Commander Health Check ===")

    for r in results:
        print(r.line())

    total = len(results)
    passed = sum(1 for r in results if r.status == "OK")
    skipped = sum(1 for r in results if r.status == "SKIP")
    warned = sum(1 for r in results if r.status == "WARN")
    failed = sum(1 for r in results if r.status == "FAIL")

    parts: list[str] = []
    if passed:
        parts.append(f"{passed} passed")
    if skipped:
        parts.append(f"{skipped} skipped")
    if warned:
        parts.append(f"{warned} warned")
    if failed:
        parts.append(f"{failed} failed")

    print()
    print(f"Result: {passed}/{total} checks passed ({', '.join(parts)})")

    if failed > 0:
        print("Status: UNHEALTHY")
        return 1
    elif warned > 0:
        print("Status: DEGRADED")
        return 0
    else:
        print("Status: HEALTHY")
        return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    results = run_all_checks()
    return print_report(results)


if __name__ == "__main__":
    sys.exit(main())
