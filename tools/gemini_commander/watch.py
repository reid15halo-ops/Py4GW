"""
Real-time error watcher — tails Py4GW logs and sends errors to Gemini for instant diagnosis.

Usage:
    python -m tools.gemini_commander.watch
    python -m tools.gemini_commander.watch --log "path/to/logfile.txt"
    python -m tools.gemini_commander.watch --interval 5

Watches for new errors in the injection log. When one appears:
1. Extracts the error text + traceback
2. Reads the referenced source file for context
3. Sends to Gemini 2.5 Flash
4. Prints severity, root cause, and fix suggestion in <2 seconds

Also supports pasting errors directly:
    python -m tools.gemini_commander.watch --interactive
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.gemini_commander.config import GEMINI_API_KEY
from tools.gemini_commander.debugger import GeminiDebugger, DiagnosisResult


def _on_error_detected(result: DiagnosisResult):
    """Callback when an error is diagnosed."""
    print(f"\n{'='*60}")
    print(f"  {result.severity.upper()} | {result.category}")
    print(f"  {result.root_cause}")
    print(f"{'='*60}")
    if result.fix_suggestion:
        print(f"\n  Fix: {result.fix_suggestion}")
    if result.file_hint:
        print(f"  File: {result.file_hint}")
    if result.related_rules:
        print(f"  CLAUDE.md: {', '.join(result.related_rules)}")
    print()


def interactive_mode(dbg: GeminiDebugger):
    """Paste errors directly for diagnosis."""
    print("Paste error/traceback (empty line to submit, 'quit' to exit):")
    while True:
        lines = []
        try:
            while True:
                line = input()
                if line.strip().lower() == "quit":
                    return
                if line == "" and lines:
                    break
                lines.append(line)
        except EOFError:
            break

        if not lines:
            continue

        error_text = "\n".join(lines)
        print("\nAnalyzing...")

        # Check if it looks like a traceback
        if "Traceback" in error_text or "File \"" in error_text:
            result = dbg.diagnose_traceback(error_text)
        else:
            result = dbg.diagnose_error(error_text)

        print(result.full_report())
        print("\nPaste next error (or 'quit'):")


def main():
    parser = argparse.ArgumentParser(description="Py4GW Real-time Error Watcher")
    parser.add_argument("--log", type=str, default="", help="Path to log file (default: Py4GW_injection_log.txt)")
    parser.add_argument("--interval", type=float, default=5.0, help="Check interval in seconds")
    parser.add_argument("--interactive", "-i", action="store_true", help="Paste errors manually")
    parser.add_argument("--verify", action="store_true", help="Run verify_custom_changes.py and diagnose failures")
    args = parser.parse_args()

    py4gw_root = str(Path(__file__).resolve().parent.parent.parent)

    if not GEMINI_API_KEY:
        print("ERROR: Set GEMINI_API_KEY environment variable")
        sys.exit(1)

    print("=" * 50)
    print("Py4GW Gemini Debugger v0.1")
    print(f"Py4GW root: {py4gw_root}")
    print("=" * 50)

    dbg = GeminiDebugger(api_key=GEMINI_API_KEY, py4gw_root=py4gw_root)

    if args.verify:
        print("\nRunning verification check...")
        result = dbg.check_verification()
        if result.error:
            print(f"Error: {result.error}")
        else:
            print(result.full_report() if result.severity != "low" else f"  {result.root_cause}")
        return

    if args.interactive:
        interactive_mode(dbg)
        return

    # Default: watch logs in real-time
    log_path = args.log or str(Path(py4gw_root) / "Py4GW_injection_log.txt")
    print(f"\nWatching: {log_path}")
    print(f"Interval: {args.interval}s")
    print("Press Ctrl+C to stop\n")

    dbg.watch_logs(
        log_path=log_path,
        interval=args.interval,
        callback=_on_error_detected,
    )


if __name__ == "__main__":
    main()
