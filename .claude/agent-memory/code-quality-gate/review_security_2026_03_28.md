---
name: Security review findings 2026-03-28
description: BT reset() only clears root, INI write race on 6 clients, Item.Usage throws during map load, _count_kits silent except
type: project
---

## BehaviorTree.reset() does NOT propagate to children

`SequenceNode` does NOT override `reset()`. The base `Node.reset()` only clears `self.last_state` and `self._last_tick_timestamp`.
This means `HeroAI_BT.reset()` (a SequenceNode) does NOT reset `self.current` (child index) and does NOT reset any child nodes.
The watchdog recovery calling `HeroAI_BT.reset()` may fail to actually unstick the BT if a child node is stuck in RUNNING.

**Why:** Found during security review of watchdog feature. Only `SubtreeNode` overrides `reset()` to propagate.
**How to apply:** Any code calling `BT.reset()` on a composite node should check whether the reset actually propagates. SequenceNode/SelectorNode need a `reset()` override that resets `self.current` and calls `child.reset()` for all children.

## INI file race: 6 clients writing OutpostPrep_Status.ini

`_write_outpost_prep_status()` does read-modify-write on a shared INI with no locking. On Windows, 6 clients can overlap and corrupt the file. configparser.read() on a half-written file returns partial data or empty. The catch-all exception handler prevents crashes but lost data is silent.

**Why:** Found during security review of outpost prep reporter feature.
**How to apply:** Either use per-account INI files or add atomic-write pattern (write to temp, rename).

## Item.Usage static methods throw on stale item IDs

`Item.item_instance(item_id)` calls `PyItem.PyItem(item_id)` which can throw during map transitions when the item array is cleared. The `_count_kits()` function has `except Exception: continue` which handles this, but the `_read_blessed_flag()` function has `except Exception: return False` which silently swallows the error without logging.

## Silent exception patterns in new code

- `_count_kits()` line 97: `except Exception: continue` -- no logging (CLAUDE.md rule 15 violation)
- `_read_blessed_flag()` line 108: `except Exception: return False` -- no logging
- `_check_outpost_prep()` line 198: `except Exception: pass` -- character name lookup, no logging
- MultiboxCommander `_read_outpost_prep_status()` line 110: `except Exception: continue` -- no logging
