#!/usr/bin/env python3
"""
FarmStatsReport.py - Gemini-powered farm stats analyzer with Telegram reports.

Reads farm_stats.jsonl, deduplicates multibox entries, calculates stats,
sends to Gemini for AI analysis, and delivers a formatted Telegram message.

Usage:
    python FarmStatsReport.py              # last 7 days
    python FarmStatsReport.py --days 30    # last 30 days
    python FarmStatsReport.py --days 1     # last 24h
    python FarmStatsReport.py --no-gemini  # skip AI analysis
    python FarmStatsReport.py --no-telegram # print only, don't send
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests library required. Install with: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Run-once guard: prevents duplicate execution if accidentally loaded as widget
# ---------------------------------------------------------------------------
_HAS_RUN = False
MASTER_EMAIL = "jonasglawion@aol.com"

# ---------------------------------------------------------------------------
# Telegram rate limiter (module-level)
# ---------------------------------------------------------------------------
import time as _time

_tg_last_send: float = 0.0
_tg_backoff_until: float = 0.0
_TG_MIN_INTERVAL: float = 60.0       # minimum seconds between sends
_TG_BACKOFF_DURATION: float = 300.0   # 5 minutes on 429

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STATS_FILE = Path(r"C:\Users\reid1\Documents\Py4GW-main\Settings\Global\farm_stats.jsonl")
SENT_FLAG = Path(r"C:\Users\reid1\Documents\Py4GW-main\Settings\Global\.farm_report_last_sent")
SEND_COOLDOWN_S = 3600  # 1 hour minimum between Telegram sends

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
    f":generateContent?key={GEMINI_API_KEY}"
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


# ---------------------------------------------------------------------------
# Data loading & dedup
# ---------------------------------------------------------------------------
def load_runs(days: int) -> list[dict]:
    """Load farm_stats.jsonl, deduplicate by timestamp, filter to last N days."""
    if not STATS_FILE.exists():
        print(f"ERROR: Stats file not found: {STATS_FILE}")
        sys.exit(1)

    cutoff = datetime.now() - timedelta(days=days)
    seen_timestamps: set[str] = set()
    runs: list[dict] = []

    with open(STATS_FILE, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                print(f"WARNING: Skipping malformed line {line_no}")
                continue

            ts = entry.get("timestamp", "")
            # Dedup: keep first occurrence per timestamp (multibox writes 5x)
            if ts in seen_timestamps:
                continue
            seen_timestamps.add(ts)

            # Filter by date
            try:
                run_dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if run_dt < cutoff:
                continue

            entry["_dt"] = run_dt
            runs.append(entry)

    runs.sort(key=lambda r: r["_dt"])
    return runs


# ---------------------------------------------------------------------------
# Stats calculation
# ---------------------------------------------------------------------------
def calc_stats(runs: list[dict]) -> dict:
    """Calculate aggregate statistics from deduplicated runs."""
    if not runs:
        return {"error": "No runs found in the specified period."}

    total = len(runs)
    wins = sum(1 for r in runs if r.get("success"))
    losses = total - wins
    win_rate = (wins / total * 100) if total else 0

    run_times_sec = [r["run_time_ms"] / 1000 for r in runs]
    avg_time = sum(run_times_sec) / len(run_times_sec)
    min_time = min(run_times_sec)
    max_time = max(run_times_sec)

    # Time span for runs/hour
    first_dt = runs[0]["_dt"]
    last_dt = runs[-1]["_dt"]
    span_hours = max((last_dt - first_dt).total_seconds() / 3600, 0.01)
    runs_per_hour = total / span_hours if span_hours > 0.1 else total

    # Win streak
    best_streak = 0
    current_streak = 0
    for r in runs:
        if r.get("success"):
            current_streak += 1
            best_streak = max(best_streak, current_streak)
        else:
            current_streak = 0

    # Per-strategy breakdown
    strat_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "wins": 0, "times": []
    })
    for r in runs:
        s = r.get("strategy", "unknown")
        strat_stats[s]["total"] += 1
        if r.get("success"):
            strat_stats[s]["wins"] += 1
        strat_stats[s]["times"].append(r["run_time_ms"] / 1000)

    strategy_summary = {}
    for name, data in strat_stats.items():
        wr = (data["wins"] / data["total"] * 100) if data["total"] else 0
        avg_t = sum(data["times"]) / len(data["times"]) if data["times"] else 0
        strategy_summary[name] = {
            "runs": data["total"],
            "win_rate": round(wr, 1),
            "avg_time_sec": round(avg_t, 1),
        }

    # Best / worst strategy by win rate (min 2 runs)
    viable = {k: v for k, v in strategy_summary.items() if v["runs"] >= 2}
    best_strat = max(viable, key=lambda k: viable[k]["win_rate"]) if viable else "N/A"
    worst_strat = min(viable, key=lambda k: viable[k]["win_rate"]) if viable else "N/A"

    # Per-bot breakdown
    bot_counts: dict[str, int] = defaultdict(int)
    for r in runs:
        bot_counts[r.get("bot_name", "unknown")] += 1

    # Hourly distribution
    hour_dist: dict[int, int] = defaultdict(int)
    for r in runs:
        hour_dist[r["_dt"].hour] += 1

    return {
        "period_days": (last_dt - first_dt).days + 1,
        "first_run": first_dt.strftime("%Y-%m-%d %H:%M"),
        "last_run": last_dt.strftime("%Y-%m-%d %H:%M"),
        "total_runs": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "avg_run_time_sec": round(avg_time, 1),
        "min_run_time_sec": round(min_time, 1),
        "max_run_time_sec": round(max_time, 1),
        "runs_per_hour": round(runs_per_hour, 2),
        "longest_win_streak": best_streak,
        "best_strategy": best_strat,
        "worst_strategy": worst_strat,
        "strategy_breakdown": strategy_summary,
        "bot_breakdown": dict(bot_counts),
        "hourly_distribution": dict(sorted(hour_dist.items())),
    }


# ---------------------------------------------------------------------------
# Gemini analysis
# ---------------------------------------------------------------------------
def gemini_analyze(stats: dict) -> str:
    """Send stats to Gemini and return AI analysis."""
    if not GEMINI_API_KEY:
        return "[Gemini skipped: GEMINI_API_KEY not set]"

    # Build a clean stats blob for the prompt (no internal fields)
    stats_text = json.dumps(
        {k: v for k, v in stats.items() if not k.startswith("_")},
        indent=2,
    )

    prompt = (
        "Analyze these Guild Wars 1 farm bot statistics. "
        "Identify: best performing strategy, optimal time of day, "
        "failure patterns, and recommendations to improve win rate "
        "and reduce run time. Be concise - max 10 lines.\n\n"
        f"```json\n{stats_text}\n```"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 512,
        },
    }

    # Rebuild URL with current key (in case env var was loaded after module init)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
        f":generateContent?key={GEMINI_API_KEY}"
    )

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text.strip()
    except requests.RequestException as e:
        return f"[Gemini API error: {e}]"
    except (KeyError, IndexError) as e:
        return f"[Gemini response parse error: {e}]"


# ---------------------------------------------------------------------------
# Telegram message
# ---------------------------------------------------------------------------
def format_telegram_message(stats: dict, ai_analysis: str) -> str:
    """Format a Telegram-friendly message with stats and AI analysis."""
    if "error" in stats:
        return f"Farm Stats Report\n\n{stats['error']}"

    lines = [
        "=== GW1 Farm Stats Report ===",
        "",
        f"Period: {stats['first_run']} - {stats['last_run']}",
        f"Total runs: {stats['total_runs']} ({stats['wins']}W / {stats['losses']}L)",
        f"Win rate: {stats['win_rate']}%",
        f"Avg run time: {stats['avg_run_time_sec']}s "
        f"(best: {stats['min_run_time_sec']}s / worst: {stats['max_run_time_sec']}s)",
        f"Runs/hour: {stats['runs_per_hour']}",
        f"Longest win streak: {stats['longest_win_streak']}",
        "",
        "--- Strategy Breakdown ---",
    ]

    for name, data in stats.get("strategy_breakdown", {}).items():
        lines.append(
            f"  {name}: {data['runs']} runs, "
            f"{data['win_rate']}% WR, "
            f"avg {data['avg_time_sec']}s"
        )

    if stats.get("bot_breakdown"):
        lines.append("")
        lines.append("--- Bot Breakdown ---")
        for bot, count in stats["bot_breakdown"].items():
            lines.append(f"  {bot}: {count} runs")

    if stats.get("hourly_distribution"):
        lines.append("")
        lines.append("--- Hourly Distribution ---")
        peak_hour = max(stats["hourly_distribution"], key=stats["hourly_distribution"].get)
        lines.append(f"  Peak hour: {peak_hour}:00 ({stats['hourly_distribution'][peak_hour]} runs)")

    lines.append("")
    lines.append("--- AI Analysis (Gemini) ---")
    lines.append(ai_analysis)

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send message via Telegram Bot API with rate limiting."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping send")
        return False

    global _tg_last_send, _tg_backoff_until

    now = _time.time()

    # Persistent file-based cooldown (survives process restarts)
    try:
        if SENT_FLAG.exists():
            last_sent_ts = float(SENT_FLAG.read_text().strip())
            if now - last_sent_ts < SEND_COOLDOWN_S:
                remaining = int(SEND_COOLDOWN_S - (now - last_sent_ts))
                print(f"FarmStatsReport: Telegram send skipped, sent {int(now - last_sent_ts)}s ago (cooldown {SEND_COOLDOWN_S}s, {remaining}s remaining)")
                return False
    except (ValueError, OSError):
        pass  # corrupt or unreadable flag file — proceed with send

    # Respect 429 backoff
    if now < _tg_backoff_until:
        remaining = int(_tg_backoff_until - now)
        print(f"WARNING: Telegram rate-limited, backing off for {remaining}s more")
        return False

    # Enforce minimum interval between sends (in-memory, same process)
    elapsed = now - _tg_last_send
    if _tg_last_send > 0 and elapsed < _TG_MIN_INTERVAL:
        print(f"WARNING: Telegram send skipped, only {elapsed:.0f}s since last send (min {_TG_MIN_INTERVAL:.0f}s)")
        return False

    # Telegram limit is 4096 chars
    if len(message) > 4096:
        message = message[:4090] + "\n..."

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(TELEGRAM_URL, json=payload, timeout=15)
        _tg_last_send = _time.time()

        if resp.status_code == 429:
            _tg_backoff_until = _time.time() + _TG_BACKOFF_DURATION
            print(f"WARNING: Telegram 429 Too Many Requests, backing off for {_TG_BACKOFF_DURATION:.0f}s")
            return False

        resp.raise_for_status()
        result = resp.json()
        if result.get("ok"):
            # Persist send timestamp to disk (prevents spam across process restarts)
            try:
                SENT_FLAG.parent.mkdir(parents=True, exist_ok=True)
                SENT_FLAG.write_text(str(_time.time()))
            except OSError:
                pass
            print("Telegram message sent successfully.")
            return True
        else:
            print(f"Telegram API error: {result}")
            return False
    except requests.RequestException as e:
        print(f"WARNING: Telegram send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GW1 Farm Stats Analyzer")
    parser.add_argument("--days", type=int, default=7, help="Analyze last N days (default: 7)")
    parser.add_argument("--no-gemini", action="store_true", help="Skip Gemini AI analysis")
    parser.add_argument("--no-telegram", action="store_true", help="Print only, don't send Telegram")
    parser.add_argument("--force", action="store_true", help="Bypass run-once guard (for manual CLI use)")
    args = parser.parse_args()

    print(f"Loading farm stats (last {args.days} days)...")
    runs = load_runs(args.days)
    print(f"Found {len(runs)} unique runs after deduplication.")

    if not runs:
        print("No runs found in the specified period. Nothing to report.")
        return

    stats = calc_stats(runs)

    # AI analysis
    if args.no_gemini:
        ai_analysis = "[Gemini analysis skipped by --no-gemini flag]"
    else:
        print("Requesting Gemini analysis...")
        ai_analysis = gemini_analyze(stats)

    # Format message
    message = format_telegram_message(stats, ai_analysis)
    print("\n" + message + "\n")

    # Send Telegram
    if not args.no_telegram:
        print("Sending to Telegram...")
        send_telegram(message)
    else:
        print("[Telegram send skipped by --no-telegram flag]")


if __name__ == "__main__":
    # Parse just --force early so guards can check it
    _pre_parser = argparse.ArgumentParser(add_help=False)
    _pre_parser.add_argument("--force", action="store_true")
    _pre_args, _ = _pre_parser.parse_known_args()

    # Guard 1: run-once per session (bypass with --force)
    if _HAS_RUN and not _pre_args.force:
        print("FarmStatsReport: already ran this session (use --force to override)")
        sys.exit(0)

    # Guard 2: master-only — only the master account should track farm stats
    try:
        from Py4GWCoreLib.Player import GetAccountEmail
        _email = GetAccountEmail()
        if _email and _email != MASTER_EMAIL:
            print(f"FarmStatsReport: skipping — not master account")
            sys.exit(0)
    except ImportError:
        pass  # Running outside Py4GW (CLI) — no restriction

    _HAS_RUN = True
    main()
