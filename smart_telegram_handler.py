#!/usr/bin/env python3
"""Smart Telegram Handler - Gemini-powered NLP for natural language commands.

Unified handler that replaces telegram_mute_handler.py. Processes BOTH:
  - callback_query updates (mute/unmute ticket buttons - original functionality)
  - message updates (natural language server admin commands via Gemini NLP)

Runs via cron every 2 minutes. Uses a single offset file to avoid the
Telegram getUpdates offset conflict (two handlers on one bot = lost updates).

Author: FreyAI
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# === Configuration ===
MEMORY_DIR = "/home/freyai/workspace/memory"
# Use the SAME offset file as the original mute handler to avoid conflicts
OFFSET_FILE = os.path.join(MEMORY_DIR, "mute_handler_offset.json")
MUTE_FILE = os.path.join(MEMORY_DIR, "muted_notifications.json")
RATE_FILE = os.path.join(MEMORY_DIR, "smart_handler_rate.json")
LOG_DIR = "/home/freyai/workspace/logs"

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
MAX_GEMINI_CALLS_PER_HOUR = 20

# Allowed chat ID (only respond to owner)
ALLOWED_CHAT_ID = "5614665620"


# === Secret loading ===
def _read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


BOT_TOKEN = _read_file("/home/freyai/.freyai/env/telegram_bot_token")
GEMINI_API_KEY = _read_file("/home/freyai/.freyai/env/gemini_api_key")

if not BOT_TOKEN:
    sys.exit(0)


# === Telegram helpers ===
_tg_last_send = 0.0
_tg_backoff_until = 0.0
_TG_MIN_INTERVAL = 60.0       # minimum seconds between sends
_TG_BACKOFF_DURATION = 300.0   # 5 minutes on 429


def tg_api(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            global _tg_backoff_until
            _tg_backoff_until = time.time() + _TG_BACKOFF_DURATION
            _log(f"tg_api 429 Too Many Requests, backing off for {_TG_BACKOFF_DURATION:.0f}s")
        else:
            _log(f"tg_api HTTP error: {e}")
        return None
    except Exception as e:
        _log(f"tg_api error: {e}")
        return None


def send_reply(chat_id, text, reply_to=None):
    """Send a text message, truncated to Telegram's 4096 char limit, with rate limiting."""
    global _tg_last_send, _tg_backoff_until

    now = time.time()
    if now < _tg_backoff_until:
        _log(f"Telegram rate-limited, backing off for {int(_tg_backoff_until - now)}s more")
        return None
    elapsed = now - _tg_last_send
    if _tg_last_send > 0 and elapsed < _TG_MIN_INTERVAL:
        _log(f"Telegram send skipped, only {elapsed:.0f}s since last send (min {_TG_MIN_INTERVAL:.0f}s)")
        return None

    text = text[:4000]
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    result = tg_api("sendMessage", data)
    _tg_last_send = time.time()
    return result


# === Offset persistence ===
def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def save_offset(offset):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


# === Rate limiting ===
def check_rate_limit():
    """Returns True if we can make a Gemini call, False if rate limited."""
    now = time.time()
    try:
        with open(RATE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"calls": []}

    # Keep only calls from the last hour
    one_hour_ago = now - 3600
    data["calls"] = [t for t in data["calls"] if t > one_hour_ago]

    if len(data["calls"]) >= MAX_GEMINI_CALLS_PER_HOUR:
        return False

    data["calls"].append(now)
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(RATE_FILE, "w") as f:
        json.dump(data, f)
    return True


# === Gemini intent classification ===
CLASSIFICATION_PROMPT = """Du bist ein Intent-Classifier fuer einen Server-Admin-Bot.
Klassifiziere die folgende Nachricht in GENAU EINE der folgenden Kategorien:

- HEALTH_CHECK: Server-Status, Uptime, Health, wie geht es dem Server
- BACKUP_STATUS: Backup-Status, letzte Backups, Backup-Infos
- DOCKER_STATUS: Container-Status, welche Container laufen, Docker-Info
- DOCKER_RESTART: Container neustarten, restart, reboot eines Services
- DISK_CHECK: Speicherplatz, Disk Space, Festplatte, Storage
- SSL_CHECK: SSL-Zertifikate, HTTPS-Status, Zertifikat-Ablauf
- UNKNOWN: Alles andere, nicht zuordenbar

Antworte NUR mit dem Kategorie-Namen, nichts anderes.
Wenn ein Container-Name erwaehnt wird und es um Neustart geht: DOCKER_RESTART
Wenn ein Container-Name erwaehnt wird ohne Neustart-Kontext: DOCKER_STATUS

Nachricht: {message}

Kategorie:"""


def classify_intent(message_text):
    """Use Gemini to classify the user's intent. Returns (intent, success)."""
    if not GEMINI_API_KEY:
        _log("No GEMINI_API_KEY found")
        return "UNKNOWN", False

    if not check_rate_limit():
        _log("Rate limit reached")
        return "RATE_LIMITED", False

    prompt = CLASSIFICATION_PROMPT.format(message=message_text)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 20,
        },
    }

    url = f"{GEMINI_ENDPOINT}?key={GEMINI_API_KEY}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        text = (
            result.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
            .upper()
        )
        # Extract just the intent keyword
        for intent in [
            "HEALTH_CHECK", "BACKUP_STATUS", "DOCKER_STATUS",
            "DOCKER_RESTART", "DISK_CHECK", "SSL_CHECK", "UNKNOWN",
        ]:
            if intent in text:
                return intent, True
        return "UNKNOWN", True
    except Exception as e:
        _log(f"Gemini API error: {e}")
        return "UNKNOWN", False


# === Extract container name from message ===
def extract_container_hint(message_text):
    """Try to extract a container/service name from the message."""
    # Common service names to look for
    keywords = [
        "nginx", "coolify", "postgres", "redis", "n8n", "supabase",
        "calcom", "evolution", "postiz", "umami", "webserver",
        "proxy", "coolify-proxy",
    ]
    lower = message_text.lower()
    for kw in keywords:
        if kw in lower:
            return kw
    return None


# === Command handlers ===
def cmd_health_check():
    """Run system health checks and return summary."""
    lines = ["*Server Health Check*\n"]
    try:
        # Uptime
        up = subprocess.run(
            ["uptime", "-p"], capture_output=True, text=True, timeout=5
        )
        lines.append(f"Uptime: {up.stdout.strip()}")

        # Load average
        load = subprocess.run(
            ["cat", "/proc/loadavg"], capture_output=True, text=True, timeout=5
        )
        parts = load.stdout.strip().split()
        lines.append(f"Load: {parts[0]} / {parts[1]} / {parts[2]}")

        # Memory
        mem = subprocess.run(
            ["free", "-h", "--si"], capture_output=True, text=True, timeout=5
        )
        for line in mem.stdout.strip().splitlines():
            if line.startswith("Mem:"):
                cols = line.split()
                lines.append(f"RAM: {cols[2]} used / {cols[1]} total")

        # Docker containers
        dc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        running = len(dc.stdout.strip().splitlines()) if dc.stdout.strip() else 0
        lines.append(f"Container: {running} running")

        # Disk root
        df = subprocess.run(
            ["df", "-h", "/"], capture_output=True, text=True, timeout=5
        )
        for line in df.stdout.strip().splitlines()[1:]:
            cols = line.split()
            lines.append(f"Disk /: {cols[2]} used / {cols[1]} ({cols[4]})")

    except Exception as e:
        lines.append(f"Error: {e}")

    return "\n".join(lines)


def cmd_backup_status():
    """Check backup directories and return status."""
    lines = ["*Backup Status*\n"]
    backup_dirs = [
        ("/home/freyai/backups", "VPS Backups"),
        ("/home/freyai/workspace/backups", "Workspace Backups"),
        ("/home/freyai/workspace/backups/supabase", "Supabase Backups"),
    ]

    for path, label in backup_dirs:
        try:
            if not os.path.isdir(path):
                lines.append(f"{label}: Verzeichnis nicht gefunden")
                continue
            entries = sorted(os.listdir(path), reverse=True)[:5]
            if not entries:
                lines.append(f"{label}: leer")
            else:
                lines.append(f"\n*{label}* ({path}):")
                for entry in entries:
                    fp = os.path.join(path, entry)
                    try:
                        stat = os.stat(fp)
                        size_mb = stat.st_size / (1024 * 1024)
                        mtime = datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        lines.append(f"  {entry} ({size_mb:.1f}MB, {mtime})")
                    except OSError:
                        lines.append(f"  {entry}")
        except Exception as e:
            lines.append(f"{label}: Error - {e}")

    return "\n".join(lines)


def cmd_docker_status():
    """List running Docker containers."""
    lines = ["*Docker Container Status*\n"]
    try:
        result = subprocess.run(
            [
                "docker", "ps",
                "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # Truncate if too long for Telegram
            if len(output) > 3500:
                # Just show names and status
                result2 = subprocess.run(
                    [
                        "docker", "ps",
                        "--format", "{{.Names}}: {{.Status}}",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                output = result2.stdout.strip()
            lines.append(f"```\n{output}\n```")
        else:
            lines.append(f"Error: {result.stderr.strip()}")
    except Exception as e:
        lines.append(f"Error: {e}")

    return "\n".join(lines)


def cmd_docker_restart(message_text):
    """Restart a Docker container by name match."""
    hint = extract_container_hint(message_text)
    if not hint:
        return "Welchen Container soll ich neustarten? Bitte nenne den Namen (z.B. nginx, n8n, postgres)."

    # Find matching container
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        containers = result.stdout.strip().splitlines()
        matches = [c for c in containers if hint.lower() in c.lower()]

        if not matches:
            return f"Kein Container mit '{hint}' gefunden."
        if len(matches) > 3:
            return (
                f"Zu viele Treffer fuer '{hint}' ({len(matches)}). "
                f"Bitte genauer angeben.\nGefunden: {', '.join(matches[:5])}"
            )

        results = []
        for container in matches:
            _log(f"Restarting container: {container}")
            restart = subprocess.run(
                ["docker", "restart", container],
                capture_output=True, text=True, timeout=60,
            )
            if restart.returncode == 0:
                results.append(f"Container `{container}` neugestartet.")
            else:
                results.append(
                    f"Fehler bei `{container}`: {restart.stderr.strip()}"
                )

        return "*Docker Restart*\n\n" + "\n".join(results)

    except Exception as e:
        return f"Fehler: {e}"


def cmd_disk_check():
    """Show disk usage summary."""
    lines = ["*Disk Space*\n"]
    try:
        result = subprocess.run(
            ["df", "-h", "--type=ext4", "--type=xfs", "--type=btrfs",
             "--type=overlay", "--output=target,size,used,avail,pcent"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines.append(f"```\n{result.stdout.strip()}\n```")
        else:
            # Fallback to simple df -h
            result2 = subprocess.run(
                ["df", "-h", "/"], capture_output=True, text=True, timeout=5
            )
            lines.append(f"```\n{result2.stdout.strip()}\n```")

        # Docker disk usage
        docker_df = subprocess.run(
            ["docker", "system", "df"],
            capture_output=True, text=True, timeout=10,
        )
        if docker_df.returncode == 0:
            lines.append(f"\n*Docker Disk:*\n```\n{docker_df.stdout.strip()}\n```")

    except Exception as e:
        lines.append(f"Error: {e}")

    return "\n".join(lines)


def cmd_ssl_check():
    """Run SSL check script and return results."""
    lines = ["*SSL Status*\n"]
    ssl_script = "/home/freyai/workspace/scripts/ssl_check.sh"
    ssl_log = "/home/freyai/workspace/backups/ssl_check.log"

    try:
        # Run the existing SSL check script
        if os.path.isfile(ssl_script):
            result = subprocess.run(
                ["bash", ssl_script],
                capture_output=True, text=True, timeout=30,
            )
            output = result.stdout.strip() or result.stderr.strip()
            if output:
                lines.append(output[:3500])
            else:
                lines.append("SSL-Check ausgefuehrt (keine Ausgabe).")
        elif os.path.isfile(ssl_log):
            # Read last check results
            with open(ssl_log) as f:
                content = f.read().strip()
            lines.append(content[-3500:] if len(content) > 3500 else content)
        else:
            # Manual check of known domains
            domains = [
                "freyai.de", "app.freyai.de", "mail.freyai.de",
                "n8n.freyai.de", "cal.freyai.de",
            ]
            for domain in domains:
                try:
                    check = subprocess.run(
                        [
                            "openssl", "s_client", "-connect",
                            f"{domain}:443", "-servername", domain,
                        ],
                        input="",
                        capture_output=True, text=True, timeout=5,
                    )
                    if "Verify return code: 0" in check.stderr:
                        # Get expiry
                        exp = subprocess.run(
                            [
                                "bash", "-c",
                                f"echo | openssl s_client -connect {domain}:443 "
                                f"-servername {domain} 2>/dev/null | "
                                f"openssl x509 -noout -enddate 2>/dev/null",
                            ],
                            capture_output=True, text=True, timeout=10,
                        )
                        expiry = exp.stdout.strip().replace("notAfter=", "")
                        lines.append(f"{domain}: OK (bis {expiry})")
                    else:
                        lines.append(f"{domain}: WARNUNG")
                except Exception:
                    lines.append(f"{domain}: Fehler beim Pruefen")
    except Exception as e:
        lines.append(f"Error: {e}")

    return "\n".join(lines)


# === Mute handler (migrated from telegram_mute_handler.py) ===
def load_muted():
    try:
        with open(MUTE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_muted(data):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(MUTE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def handle_callback_query(cb):
    """Process mute/unmute callback queries (original mute handler logic)."""
    data = cb.get("data", "")
    cb_id = cb["id"]

    if data.startswith("mute_ticket_"):
        ticket_nr = data.replace("mute_ticket_", "")
        muted = load_muted()

        if ticket_nr in muted:
            del muted[ticket_nr]
            save_muted(muted)
            tg_api("answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": f"{ticket_nr} entmutet",
            })
        else:
            muted[ticket_nr] = {
                "reason": "via Telegram Button",
                "muted_at": datetime.now(timezone.utc).isoformat(),
            }
            save_muted(muted)
            tg_api("answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": f"{ticket_nr} gemutet — keine Benachrichtigungen mehr",
            })

            msg = cb.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            msg_id = msg.get("message_id")
            if chat_id and msg_id:
                orig_text = msg.get("text", "")
                tg_api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": orig_text + f"\n\n\U0001f507 {ticket_nr} gemutet",
                })
    else:
        tg_api("answerCallbackQuery", {
            "callback_query_id": cb_id,
            "text": "Unbekannte Aktion",
        })


# === Logging ===
def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


# === Main loop ===
def main():
    offset = load_offset()

    # Request ALL update types so we don't miss callback_query or message
    params = {"offset": offset, "timeout": 0}
    result = tg_api("getUpdates", params)
    if not result or not result.get("ok"):
        return

    for update in result.get("result", []):
        offset = update["update_id"] + 1

        # --- Handle callback queries (mute buttons) ---
        cb = update.get("callback_query")
        if cb:
            try:
                handle_callback_query(cb)
            except Exception as e:
                _log(f"Callback error: {e}")
            continue

        # --- Handle text messages (NLP commands) ---
        msg = update.get("message")
        if not msg:
            continue

        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != ALLOWED_CHAT_ID:
            continue

        text = msg.get("text", "").strip()
        if not text:
            continue

        # Skip bot commands that start with / (might be for other bots)
        if text.startswith("/"):
            continue

        msg_id = msg.get("message_id")
        _log(f"Processing message: {text[:80]}")

        # Classify intent via Gemini
        intent, success = classify_intent(text)
        _log(f"Intent: {intent} (success={success})")

        if intent == "RATE_LIMITED":
            send_reply(
                chat_id,
                "Rate Limit erreicht (max 20 Anfragen/Stunde). Bitte spaeter nochmal versuchen.",
                reply_to=msg_id,
            )
            continue

        # Dispatch to handler
        response = None
        if intent == "HEALTH_CHECK":
            response = cmd_health_check()
        elif intent == "BACKUP_STATUS":
            response = cmd_backup_status()
        elif intent == "DOCKER_STATUS":
            response = cmd_docker_status()
        elif intent == "DOCKER_RESTART":
            response = cmd_docker_restart(text)
        elif intent == "DISK_CHECK":
            response = cmd_disk_check()
        elif intent == "SSL_CHECK":
            response = cmd_ssl_check()
        elif intent == "UNKNOWN":
            _log(f"Unknown intent, skipping: {text[:50]}")
            continue

        if response:
            send_reply(chat_id, response, reply_to=msg_id)

    save_offset(offset)


if __name__ == "__main__":
    main()
