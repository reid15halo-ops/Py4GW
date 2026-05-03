---
name: Gemini Commander security audit
description: Path traversal in debugger traceback reader, PII leak via to_dict/to_json email field, MASTER_EMAIL duplication across config.py and game_state.py
type: project
---

Security audit of tools/gemini_commander/ on 2026-03-28.

## Critical Findings

1. **Path traversal in debugger.py:diagnose_traceback()** -- reads any file whose path appears in a traceback string, sends content to Gemini API. No validation that the path is under py4gw_root. Could leak SSH keys, env files, etc. if a crafted traceback is fed in.

2. **Email PII in serialization** -- PartyMember.to_dict() uses asdict() which includes the email field. The to_prompt_text() path (live API path) is safe, but to_json()/to_dict() would leak all 6 account emails if ever routed to Gemini. Mock data in from_mock() hardcodes 6 real email addresses.

3. **MASTER_EMAIL duplicated** -- identical string in config.py:7 and game_state.py:75.

## Good Patterns Found

- API key loaded from env var, never hardcoded or logged
- subprocess.run uses list-form args, no shell=True, has timeout
- Atomic write pattern (temp + os.replace) is correct
- Gemini output validation whitelists command names, caps at 3 commands -- solid defense-in-depth against prompt injection
- Exponential backoff on consecutive errors
- Graceful fallback chain: bridge -> INI -> mock

**Why:** User priority is zero-crash stability + security for 6-account setup. These findings affect data safety, not runtime stability.

**How to apply:** When reviewing future gemini_commander changes, verify (a) no new file reads without path validation, (b) email field still excluded from any Gemini-bound serialization path.
