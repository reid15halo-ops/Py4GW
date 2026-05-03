---
name: Gemini Commander full module review
description: 4/10 score -- PII leak (6 emails sent to Google API), unsafe atomic write, no Gemini output validation, stale INI on crash, byte-seek UTF-8 bug
type: project
---

## Review: tools/gemini_commander/ (7 files, 2026-03-28)

Score: 4/10 -- REVISION REQUIRED

### Critical findings

1. **PII leak**: 6 real email addresses hardcoded in config.py and game_state.py (including mock data), serialized via to_prompt_text() and sent to Gemini API on every call. Google receives full account emails.

2. **Debugger reads arbitrary files**: diagnose_traceback() reads any file referenced in a traceback and sends content to Gemini with no path allowlist, size cap, or credential filtering.

3. **Atomic write not robust on Windows**: os.replace() in command_executor.py fails if a reader has the file open. No retry, no .tmp cleanup, no fsync. 6 clients polling = high collision rate.

4. **Stale commands on crash**: If the process dies, commands.ini stays active=true. HeroAI follows ghost Gemini commands indefinitely. Needs heartbeat timestamp.

5. **No validation of Gemini output**: Command params written directly to INI without whitelisting valid values. int(value) in _handle_priority crashes on non-numeric input.

### High findings

- main.py line 72 uses legacy INI-only path, never queries bridge daemon for live HP/enemy data
- watch_logs byte-seek + UTF-8 encoding = UnicodeDecodeError on multi-byte chars
- watch_logs never recovers from log rotation (current_size < last_size)

### Recurring patterns (cross-ref with existing memory)

- MASTER_EMAIL duplicated between config.py and game_state.py (matches existing cross-file constant duplication pattern)
- Silent except:pass in game_state.py (matches existing review_security findings)
- Project root path computed via parent.parent.parent in 4 separate files

**Why:** First review of this module. It integrates an external LLM API into the multibox system, so security and robustness standards must be higher than internal-only code.
**How to apply:** Any revision of this module must address C1 (PII) and C3 (atomic write) first. H2 (output validation) must be in place before live use.
