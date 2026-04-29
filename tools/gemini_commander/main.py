"""
Py4GW Gemini Commander -- Strategic AI layer for GW1 multibox farming.

Runs as a standalone process alongside Py4GW. Reads game state,
asks Gemini 2.5 Flash for tactical decisions, writes commands to
a shared INI that HeroAI can optionally read.

Usage:
    python -m tools.gemini_commander.main
    python -m tools.gemini_commander.main --dry-run  (no API calls, mock responses)
    python -m tools.gemini_commander.main --interval 10  (poll every 10s)

Environment:
    GEMINI_API_KEY=your_key_here
"""
import argparse
import time
import signal
import sys
from pathlib import Path

# Add parent to path so we can import from tools.gemini_commander
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.gemini_commander.config import GEMINI_API_KEY, POLL_INTERVAL_S
from tools.gemini_commander.game_state import GameStateSnapshot
from tools.gemini_commander.gemini_client import GeminiCommander
from tools.gemini_commander.command_executor import CommandExecutor


def main():
    parser = argparse.ArgumentParser(description="Py4GW Gemini Commander")
    parser.add_argument("--dry-run", action="store_true", help="Mock mode, no API calls")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL_S, help="Poll interval in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    py4gw_root = str(Path(__file__).resolve().parent.parent.parent)

    print("=" * 50)
    print("Py4GW Gemini Commander v0.1")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Poll interval: {args.interval}s")
    print(f"Py4GW root: {py4gw_root}")
    print("=" * 50)

    if not args.dry_run and not GEMINI_API_KEY:
        print("ERROR: Set GEMINI_API_KEY environment variable")
        print("  Windows: set GEMINI_API_KEY=your_key")
        print("  Or create tools/gemini_commander/.env with GEMINI_API_KEY=your_key")
        sys.exit(1)

    # Initialize components
    executor = CommandExecutor(py4gw_root)
    commander = None if args.dry_run else GeminiCommander(GEMINI_API_KEY)

    # Graceful shutdown
    running = True
    def shutdown(sig, frame):
        nonlocal running
        print("\nShutting down...")
        executor.deactivate()
        running = False
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    cycle = 0
    while running:
        cycle += 1
        try:
            # 1. Read game state
            state = GameStateSnapshot.from_outpost_prep_ini(py4gw_root)
            state_text = state.to_prompt_text()

            if args.verbose:
                print(f"\n--- Cycle {cycle} ---")
                print(state_text[:500])

            # 2. Get Gemini commands (or mock)
            if args.dry_run:
                commands = []  # No commands in dry run
                print(f"[{cycle}] Dry run -- state read OK, {len(state.party)} party members detected")
            else:
                commands = commander.get_commands(state_text)
                if commands:
                    print(f"[{cycle}] Gemini: {[c.cmd for c in commands]}")
                elif args.verbose:
                    print(f"[{cycle}] Gemini: no action needed")

            # 3. Execute commands
            if commands:
                executor.execute(commands)

            # 4. Stats
            if commander and cycle % 12 == 0:  # Every ~60s at 5s interval
                stats = commander.get_stats()
                print(f"[Stats] Calls: {stats['calls']}, Errors: {stats['errors']}, Est. cost: ${stats['cost_estimate_usd']:.4f}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[{cycle}] Error: {e}")

        time.sleep(args.interval)

    executor.deactivate()
    print("Gemini Commander stopped. HeroAI will use default behavior.")


if __name__ == "__main__":
    main()
