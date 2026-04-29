"""
Gemini Commander Launcher -- One-click deployment.

Usage:
    python -m tools.gemini_commander.launcher

Checks prerequisites, runs tests, then starts the selected mode.
"""
import sys
import os
import subprocess
import time
from pathlib import Path

# Resolve project root once (Py4GW-main/)
ROOT = Path(__file__).resolve().parent.parent.parent


def check_prerequisites() -> list[str]:
    """Return list of issues, empty = all good."""
    issues = []

    # Python version
    if sys.version_info < (3, 10):
        issues.append(f"Python 3.10+ required, got {sys.version}")

    # google-generativeai package
    try:
        import google.generativeai  # noqa: F401
    except ImportError:
        issues.append(
            "google-generativeai not installed. Run: pip install google-generativeai"
        )

    # API key
    if not os.environ.get("GEMINI_API_KEY"):
        issues.append("GEMINI_API_KEY environment variable not set")

    # Project root sanity check
    if not (ROOT / "CLAUDE.md").exists():
        issues.append(f"Py4GW root not found at {ROOT}")

    # Core modules present
    for mod in ("game_state", "command_executor", "gemini_client", "main", "watch"):
        mod_path = ROOT / "tools" / "gemini_commander" / f"{mod}.py"
        if not mod_path.exists():
            issues.append(f"Missing module: {mod_path.name}")

    return issues


def run_tests() -> bool:
    """Run test suite, return True if all pass."""
    tests_path = ROOT / "tools" / "gemini_commander" / "tests.py"
    if not tests_path.exists():
        print("\n--- Tests ---")
        print("  Skipped (tests.py not found yet)")
        return True

    print("\n--- Running Automated Tests ---")
    result = subprocess.run(
        [sys.executable, "-m", "tools.gemini_commander.tests"],
        cwd=str(ROOT),
        capture_output=False,
    )
    if result.returncode == 0:
        print("  All tests passed.")
    else:
        print(f"  Tests FAILED (exit code {result.returncode})")
    return result.returncode == 0


def run_dry_cycle() -> bool:
    """Run one dry-run cycle to verify the pipeline works without the API."""
    print("\n--- Dry Run Cycle ---")
    try:
        # Ensure the project root is importable
        root_str = str(ROOT)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        from tools.gemini_commander.game_state import GameStateSnapshot
        from tools.gemini_commander.command_executor import CommandExecutor
        from tools.gemini_commander.gemini_client import GeminiCommand

        # 1) Build a mock game-state snapshot
        state = GameStateSnapshot.from_mock()
        text = state.to_prompt_text()
        alive = sum(1 for p in state.party if p.is_alive)
        print(f"  State snapshot: {len(state.party)} party ({alive} alive), "
              f"{state.enemy_count} enemies")
        print(f"  Prompt text: {len(text)} chars")

        # 2) Write a test command through the executor
        executor = CommandExecutor(root_str)
        executor.execute([GeminiCommand(cmd="stance", params={"mode": "balanced"})])
        print(f"  INI written: {executor.ini_path}")

        # 3) Cleanup -- deactivate so HeroAI ignores stale commands
        executor.deactivate()
        print("  Cleanup: deactivated")
        print("  Dry run: OK")
        return True
    except Exception as e:
        print(f"  Dry run FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def show_status():
    """Show current system status."""
    import configparser

    ini = ROOT / "Widgets" / "Config" / "GeminiCommander" / "commands.ini"

    print("\n--- System Status ---")
    print(f"  Py4GW root : {ROOT}")
    print(f"  API key    : {'SET' if os.environ.get('GEMINI_API_KEY') else 'NOT SET'}")
    print(f"  Commands INI: {'EXISTS' if ini.exists() else 'not found'}")

    if not ini.exists():
        print("  (no active session)")
        return

    cp = configparser.ConfigParser()
    try:
        cp.read(str(ini), encoding="utf-8")
    except Exception as e:
        print(f"  Error reading INI: {e}")
        return

    active = cp.get("Commander", "active", fallback="?")
    heartbeat = cp.get("Commander", "heartbeat", fallback="0")
    try:
        age = time.time() - float(heartbeat)
        hb_display = f"{age:.0f}s ago" if age < 60 else "STALE"
    except (ValueError, TypeError):
        hb_display = "unknown"

    stance = cp.get("Strategy", "stance", fallback="?")
    focus = cp.get("Strategy", "focus_target", fallback="none")
    formation = cp.get("Strategy", "formation", fallback="?")

    print(f"  Active     : {active}")
    print(f"  Heartbeat  : {hb_display}")
    print(f"  Stance     : {stance}")
    print(f"  Focus      : {focus}")
    print(f"  Formation  : {formation}")

    # Show recent command history if available
    history_raw = cp.get("History", "commands", fallback="")
    if history_raw:
        try:
            import json
            history = json.loads(history_raw)
            if history:
                print(f"  Last cmds  : {len(history)} in history")
        except (json.JSONDecodeError, TypeError):
            pass


def _print_banner():
    print()
    print("=" * 55)
    print("  Gemini Commander Launcher v0.1")
    print("=" * 55)


def _print_menu():
    print()
    print("  [1] Start Strategic Commander")
    print("  [2] Start Real-time Debugger")
    print("  [3] Interactive Debug (paste errors)")
    print("  [4] Run Tests Only")
    print("  [5] System Status")
    print("  [6] Dry Run (no API needed)")
    print("  [q] Quit")
    print()


def main():
    _print_banner()

    # --- Prerequisite check ---
    issues = check_prerequisites()
    api_missing = any("GEMINI_API_KEY" in i for i in issues)
    pkg_missing = any("google-generativeai" in i for i in issues)

    if issues:
        print("\n  Prerequisites:")
        for issue in issues:
            print(f"    [!] {issue}")
    else:
        print("\n  All prerequisites OK.")

    _print_menu()

    try:
        choice = input("  Select: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  Aborted.")
        return

    # --- Option 4: Tests only ---
    if choice == "4":
        if pkg_missing:
            print("\n  Cannot run tests: google-generativeai not installed.")
            sys.exit(1)
        success = run_tests()
        sys.exit(0 if success else 1)

    # --- Option 5: Status ---
    if choice == "5":
        show_status()
        return

    # --- Option 6: Dry run ---
    if choice == "6":
        success = run_dry_cycle()
        sys.exit(0 if success else 1)

    # --- Quit ---
    if choice == "q":
        print("  Bye.")
        return

    # --- Options 1-3: require validation first ---
    if choice not in ("1", "2", "3"):
        print(f"  Unknown option: {choice!r}")
        sys.exit(1)

    # Modes 1-3 need the API
    if api_missing:
        print("\n  Cannot start: GEMINI_API_KEY environment variable not set.")
        print("  Set it with:  set GEMINI_API_KEY=your-key-here  (Windows)")
        print("            or: export GEMINI_API_KEY=your-key-here (bash)")
        sys.exit(1)

    # Run tests + dry run before launching
    if not pkg_missing:
        if not run_tests():
            print("\n  ABORT: Tests failed. Fix issues before deploying.")
            sys.exit(1)
        if not run_dry_cycle():
            print("\n  ABORT: Dry run failed. Fix issues before deploying.")
            sys.exit(1)
    else:
        print("\n  WARNING: Skipping pre-flight (google-generativeai not installed).")

    root_str = str(ROOT)

    # --- Launch selected mode ---
    try:
        if choice == "1":
            print("\n  Starting Strategic Commander... (Ctrl+C to stop)")
            print("  " + "-" * 50)
            subprocess.run(
                [sys.executable, "-m", "tools.gemini_commander.main", "--verbose"],
                cwd=root_str,
            )

        elif choice == "2":
            print("\n  Starting Real-time Debugger... (Ctrl+C to stop)")
            print("  " + "-" * 50)
            subprocess.run(
                [sys.executable, "-m", "tools.gemini_commander.watch"],
                cwd=root_str,
            )

        elif choice == "3":
            print("\n  Starting Interactive Debug... (Ctrl+C to stop)")
            print("  " + "-" * 50)
            subprocess.run(
                [sys.executable, "-m", "tools.gemini_commander.watch", "--interactive"],
                cwd=root_str,
            )

    except KeyboardInterrupt:
        print("\n\n  Stopped by user.")


if __name__ == "__main__":
    main()
