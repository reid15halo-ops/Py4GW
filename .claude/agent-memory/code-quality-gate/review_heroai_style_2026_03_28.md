---
name: HeroAI code style review patterns
description: Recurring style issues found in HeroAI and Py4GWCoreLib review -- logging API inconsistency, repeated error boilerplate, mixed constant naming
type: feedback
---

Two logging APIs coexist within HeroAI/:
- `Py4GW.Console.Log(sender, msg, Py4GW.Console.MessageType.Warning)` -- used in targeting.py, combat.py
- `ConsoleLog(sender, msg, 1)` -- used in commands.py, following.py

Both produce identical output. The inconsistency is not harmful but makes log-based debugging harder.

**Why:** Grep for error patterns requires searching both `Py4GW.Console.Log` and `ConsoleLog` separately.

**How to apply:** When reviewing new HeroAI code, flag if a file introduces the opposite logging pattern from its neighbors. Do not require a mass migration -- just prevent further divergence.

Additional patterns found:
- targeting.py has an 11x-repeated error logging boilerplate that could be a helper function
- Profession ID magic numbers {3, 8} for Monk/Ritualist duplicated in 4 places across targeting.py and combat.py
- UIManager.py still has a `print()` at line 185 (incomplete migration)
- Redundant `pass` statements after logging in except blocks (UIManager lines 935/1004/1142, settings.py lines 79/182)
