# Py4GW Gemini Commander

Strategic AI layer for Guild Wars 1 multibox farming.
Runs alongside Py4GW as a standalone process — zero modifications to game code.

## Quick Start

1. Install dependencies:
   ```bash
   pip install google-generativeai python-dotenv
   ```

2. Get a Gemini API key from https://aistudio.google.com/apikey

3. Set the API key:
   ```bash
   # Windows
   set GEMINI_API_KEY=your_key_here

   # Or create tools/gemini_commander/.env file
   cp tools/gemini_commander/.env.example tools/gemini_commander/.env
   # Edit .env with your key
   ```

4. Run health check:
   ```bash
   python -m tools.gemini_commander.healthcheck
   ```

5. Run tests:
   ```bash
   python -m tools.gemini_commander.tests
   ```

6. Launch:
   ```bash
   python -m tools.gemini_commander.launcher
   ```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Gemini Commander                       │
│                                                          │
│  game_state.py ──► gemini_client.py ──► command_executor │
│  (read 6 INIs)     (Gemini 2.5 Flash)   (write cmds.ini)│
│       ▲                                        │         │
│       │            debugger.py                  ▼         │
│       │            (error logs ──►          HeroAI/      │
│       │             Gemini 3.1 Lite)     gemini_reader   │
│       │                                   (read cmds)    │
│       └──────────── Py4GW shared state ─────────┘        │
└─────────────────────────────────────────────────────────┘
```

## Modes

### Strategic Commander (main.py)
Polls game state every 5s, asks Gemini for tactical decisions.

### Real-time Debugger (watch.py)
Tails error logs, sends to Gemini for instant diagnosis.

### Interactive Debug (watch.py -i)
Paste any error for GW1-specific analysis.

## Cost Estimate

~$0.52 per 8-hour session with Gemini 2.5 Flash at 5s polling.

## Files

| File | Purpose |
|------|---------|
| config.py | API keys, model selection, ports |
| game_state.py | Reads 6-client state from INI/bridge |
| gemini_client.py | Gemini API + GW1 system prompt |
| command_executor.py | Writes commands.ini (atomic) |
| gemini_reader.py | HeroAI reads commands (in HeroAI/) |
| debugger.py | Error diagnosis engine |
| watch.py | Real-time log watcher CLI |
| main.py | Strategic commander main loop |
| launcher.py | One-click deployment menu |
| healthcheck.py | System health verification |
| tests.py | Automated test suite |
