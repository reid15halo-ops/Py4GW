import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv is optional

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BRIDGE_HOST = "localhost"
BRIDGE_PORT = 51935
POLL_INTERVAL_S = 5.0
MASTER_EMAIL = "jonasglawion@aol.com"

# Model selection — use 2.5 Flash for strategic (cheaper), 3.1 Flash Lite for debugging (faster TTFT)
MODEL_COMMANDER = os.environ.get("GEMINI_MODEL_COMMANDER", "gemini-2.5-flash")
MODEL_DEBUGGER = os.environ.get("GEMINI_MODEL_DEBUGGER", "gemini-3.1-flash-lite")

# Cost tracking (per million tokens, March 2026)
MODEL_COSTS = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.0-flash": {"input": 0.10, "output": 0.40},
}
