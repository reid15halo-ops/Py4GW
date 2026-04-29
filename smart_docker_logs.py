#!/usr/bin/env python3
"""
smart_docker_logs.py  --  AI-powered Docker log anomaly detector
Runs every 4 hours via cron, sends Telegram alerts for real issues.

Crontab entry:
0 */4 * * *  /usr/bin/python3 /home/freyai/workspace/scripts/smart_docker_logs.py >> /var/log/smart_docker_logs.log 2>&1
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

STATE_FILE = Path("/home/freyai/.freyai/smart_docker_logs_state.json")
MAX_LOG_CHARS = 500          # per container, sent to Gemini
LOG_TAIL_LINES = 100         # lines fetched from docker logs

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
    ":generateContent"
)

# Noise patterns to strip before sending to Gemini
NOISE_RE = re.compile(
    r"|".join([
        r'"GET /health',
        r'"GET /healthz',
        r'"GET /api/health',
        r'"HEAD / HTTP',
        r"health.?check",
        r"StatusMonitor",
        r"kube-probe",
        r"UptimeRobot",
        r"Uptime-Kuma",
        r"NetcraftSurveyAgent",
        r"GPTBot",
        r"OAI-SearchBot",
        r'"GET / HTTP/1\.[01]" 200',
        r'"GET /favicon\.ico',
        r'"GET /robots\.txt',
        r"healthcheck.*OK",
        r"alive and well",
    ]),
    re.IGNORECASE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_secret(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return ""


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as exc:
        return f"[cmd error: {exc}]"


# ── Docker ────────────────────────────────────────────────────────────────────

def list_containers() -> list[dict]:
    """Return list of {name, status} for running containers."""
    raw = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
    containers = []
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            containers.append({"name": parts[0], "status": parts[1]})
    return containers


def get_logs(container_name: str) -> str:
    """Get last N lines of logs, filter noise, truncate to MAX_LOG_CHARS."""
    raw = _run(["docker", "logs", "--tail", str(LOG_TAIL_LINES), container_name],
               timeout=15)
    # Filter noise lines
    lines = [ln for ln in raw.splitlines() if not NOISE_RE.search(ln)]
    text = "\n".join(lines)
    if len(text) > MAX_LOG_CHARS:
        text = text[-MAX_LOG_CHARS:]  # keep the most recent part
    return text.strip()


# ── Gemini ────────────────────────────────────────────────────────────────────

def _curl_post(url: str, payload: dict, timeout: int = 30) -> dict:
    """POST JSON via curl (urllib hangs on this VPS due to IPv6). Returns parsed JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        tmp = f.name
    try:
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", url,
             "-H", "Content-Type: application/json",
             "-d", f"@{tmp}",
             "--max-time", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        Path(tmp).unlink(missing_ok=True)


def ask_gemini(api_key: str, log_blob: str) -> str:
    """Send combined log blob to Gemini, return analysis."""
    prompt = (
        "Analyze these Docker container logs. Report ONLY: errors, warnings, "
        "crashes, restarts, authentication failures, database connection issues, "
        "memory warnings, security scan attempts. Ignore routine health checks "
        "and normal GET/POST requests. Be concise (max 3 bullet points per "
        "container). If everything looks normal, respond with exactly: ALL_CLEAR\n\n"
        + log_blob
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 512, "temperature": 0.2},
    }
    url = f"{GEMINI_ENDPOINT}?key={api_key}"
    try:
        data = _curl_post(url, payload)
        if "error" in data and isinstance(data["error"], str):
            return f"[Gemini error: {data['error']}]"
        return (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
    except Exception as exc:
        return f"[Gemini error: {exc}]"


# ── Telegram ──────────────────────────────────────────────────────────────────

import time as _time

_tg_last_send: float = 0.0
_tg_backoff_until: float = 0.0
_TG_MIN_INTERVAL: float = 60.0       # minimum seconds between sends
_TG_BACKOFF_DURATION: float = 300.0   # 5 minutes on 429


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Send a Telegram message with rate limiting. Falls back to plain text if Markdown fails."""
    global _tg_last_send, _tg_backoff_until

    now = _time.time()

    if now < _tg_backoff_until:
        print(f"[Telegram rate-limited, backing off for {int(_tg_backoff_until - now)}s more]", file=sys.stderr)
        return False

    elapsed = now - _tg_last_send
    if _tg_last_send > 0 and elapsed < _TG_MIN_INTERVAL:
        print(f"[Telegram send skipped, only {elapsed:.0f}s since last send]", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Try Markdown first, fall back to plain text
    for parse_mode in ("Markdown", None):
        payload = {
            "chat_id": chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            data = _curl_post(url, payload, timeout=15)
            _tg_last_send = _time.time()

            # Handle 429 from curl response
            if data.get("error_code") == 429:
                _tg_backoff_until = _time.time() + _TG_BACKOFF_DURATION
                print(f"[Telegram 429, backing off for {_TG_BACKOFF_DURATION:.0f}s]", file=sys.stderr)
                return False

            if data.get("ok") is True:
                return True
            if parse_mode:
                print(f"[Telegram Markdown failed, retrying plain]", file=sys.stderr)
                continue
            print(f"[Telegram error: {data}]", file=sys.stderr)
            return False
        except Exception as exc:
            print(f"[Telegram error: {exc}]", file=sys.stderr)
            return False
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] smart_docker_logs starting")

    # Load secrets
    gemini_key = os.environ.get("GEMINI_API_KEY") or _read_secret(
        "/home/freyai/.freyai/env/gemini_api_key"
    )
    tg_token = _read_secret("/home/freyai/.freyai/env/telegram_bot_token")
    tg_chat = _read_secret("/home/freyai/.freyai/env/telegram_chat_id")

    if not gemini_key:
        print("FATAL: No GEMINI_API_KEY", file=sys.stderr)
        sys.exit(1)
    if not tg_token or not tg_chat:
        print("WARN: Telegram creds missing, alerts disabled", file=sys.stderr)

    # Get containers
    containers = list_containers()
    if not containers:
        print("No running containers found")
        return

    print(f"Found {len(containers)} containers")

    # Build log blob grouped by container
    state = _load_state()
    log_sections = []

    for c in containers:
        name = c["name"]
        logs = get_logs(name)
        if not logs:
            continue
        # De-duplicate: hash logs, skip if identical to last run
        log_hash = str(hash(logs))
        if state.get(name) == log_hash:
            continue
        state[name] = log_hash
        log_sections.append(f"=== {name} ({c['status']}) ===\n{logs}")

    if not log_sections:
        print("No new log content since last run")
        _save_state(state)
        return

    # Combine and cap total size (Gemini sees all containers at once)
    combined = "\n\n".join(log_sections)
    if len(combined) > 8000:
        combined = combined[:8000]

    print(f"Sending {len(combined)} chars across {len(log_sections)} containers to Gemini")

    # Ask Gemini
    analysis = ask_gemini(gemini_key, combined)
    print(f"Gemini response: {analysis[:200]}...")

    # Decide whether to alert
    if "ALL_CLEAR" in analysis.upper():
        print("All clear, no alert needed")
    else:
        msg = (
            f"*Docker Log Alert*\n"
            f"_{ts}_\n\n"
            f"{analysis}"
        )
        if tg_token and tg_chat:
            ok = send_telegram(tg_token, tg_chat, msg)
            print(f"Telegram alert sent: {ok}")
        else:
            print(f"ALERT (no TG creds):\n{analysis}")

    # Persist state
    state["_last_run"] = ts
    _save_state(state)
    print(f"[{ts}] Done")


if __name__ == "__main__":
    main()
