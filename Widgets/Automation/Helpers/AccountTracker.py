"""AccountTracker.py — Per-account snapshot widget for dashboard tracking.

Runs on every Py4GW client. Every 60s, snapshots the local account's state
(gold, inventory by rarity, current map, map ID) to a per-account JSONL file.

Robust against:
- Empty Player.GetAccountEmail() during transitions (uses ShMem fallback)
- LootConfig counters being reset (we count from inventory ourselves)
- Map-not-ready states (skip snapshot if not loaded)

Output: C:\\Users\\reid1\\Documents\\Py4GW\\Settings\\Global\\account_track\\<email>.jsonl
"""
import os
import json
import time

import Py4GW
from Py4GWCoreLib import (
    GLOBAL_CACHE,
    Map,
    Player,
    ItemArray,
    Item,
    Inventory,
    ThrottledTimer,
)
from Py4GWCoreLib.enums import Bags

MODULE = "AccountTracker"
OPTIONAL = True

_OUT_DIR = os.path.join(Py4GW.Console.get_projects_path(), "Settings", "Global", "account_track")
os.makedirs(_OUT_DIR, exist_ok=True)

_SNAPSHOT_INTERVAL_MS = 60 * 1000
_timer = ThrottledTimer(_SNAPSHOT_INTERVAL_MS)
_session_start = time.time()
_snapshot_count = 0
_last_status = "init"
_email_cache = ""
_char_cache = ""


def _resolve_account_id() -> tuple[str, str]:
    """Get (email, character_name) — via Player API first, fallback to ShMem."""
    email = ""
    char = ""
    try:
        email = Player.GetAccountEmail() or ""
    except Exception:
        pass
    try:
        char = Player.GetAccountName() or ""
    except Exception:
        pass

    if email and char:
        return email, char

    # Fallback: scan ShMem for the account whose AgentID matches local player
    try:
        local_id = Player.GetAgentID()
        if local_id <= 0:
            return email, char
        all_acc = GLOBAL_CACHE.ShMem.GetAllAccountData()
        for acc in all_acc:
            if not acc:
                continue
            try:
                if not acc.IsAccount:
                    continue
                if acc.AgentData.AgentID != local_id:
                    continue
                if not email:
                    email = acc.AccountEmail or ""
                if not char:
                    try:
                        char = acc.AgentData.CharacterName or ""
                    except Exception:
                        pass
                break
            except Exception:
                continue
    except Exception:
        pass
    return email, char


def _count_inventory_by_rarity() -> dict:
    """Return {white: N, blue: N, purple: N, gold: N, green: N, materials: N, total: N}."""
    counts = {"white": 0, "blue": 0, "purple": 0, "gold": 0, "green": 0, "materials": 0, "total": 0}
    try:
        bag_list = ItemArray.CreateBagList(Bags.Backpack, Bags.BeltPouch, Bags.Bag1, Bags.Bag2)
        item_array = ItemArray.GetItemArray(bag_list)
        for item_id in item_array:
            counts["total"] += 1
            try:
                _, rarity = GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)
            except Exception:
                rarity = "White"
            key = rarity.lower() if rarity else "white"
            if key in counts:
                counts[key] += 1
            try:
                if GLOBAL_CACHE.Item.Type.IsMaterial(item_id):
                    counts["materials"] += 1
            except Exception:
                pass
    except Exception as e:
        Py4GW.Console.Log(MODULE, f"inventory count error: {e}", Py4GW.Console.MessageType.Warning)
    return counts


def _gold_state() -> dict:
    """Gold on character + in storage."""
    g_char = 0
    g_storage = 0
    try:
        g_char = int(GLOBAL_CACHE.Inventory.GetGoldOnCharacter() or 0)
    except Exception:
        pass
    try:
        g_storage = int(GLOBAL_CACHE.Inventory.GetGoldInStorage() or 0)
    except Exception:
        try:
            g_storage = int(GLOBAL_CACHE.Inventory.GetGoldAmountInStorage() or 0)
        except Exception:
            pass
    return {"on_character": g_char, "in_storage": g_storage, "total": g_char + g_storage}


def _safe_filename(email: str) -> str:
    safe = "".join(c if c.isalnum() or c in ".-_@" else "_" for c in email)
    return safe or "unknown"


def _write_snapshot() -> bool:
    global _snapshot_count, _last_status, _email_cache, _char_cache

    if not Map.IsMapReady() or Map.IsMapLoading():
        _last_status = "skip:map_not_ready"
        return False
    if not Player.IsPlayerLoaded():
        _last_status = "skip:player_not_loaded"
        return False

    email, char = _resolve_account_id()
    if email:
        _email_cache = email
    else:
        email = _email_cache  # use cached if we had one before
    if char:
        _char_cache = char
    else:
        char = _char_cache

    if not email and not char:
        _last_status = "skip:no_identity"
        return False

    try:
        map_id = int(Map.GetMapID())
    except Exception:
        map_id = 0

    is_outpost = False
    try:
        is_outpost = bool(Map.IsOutpost())
    except Exception:
        pass

    record = {
        "v": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "email": email,
        "character": char,
        "map_id": map_id,
        "is_outpost": is_outpost,
        "gold": _gold_state(),
        "inv": _count_inventory_by_rarity(),
        "free_slots": int(GLOBAL_CACHE.Inventory.GetFreeSlotCount() or 0),
        "session_secs": int(time.time() - _session_start),
    }

    fname = _safe_filename(email or char) + ".jsonl"
    path = os.path.join(_OUT_DIR, fname)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        _snapshot_count += 1
        _last_status = f"ok:{record['ts']}"
        return True
    except Exception as e:
        Py4GW.Console.Log(MODULE, f"write error: {e}", Py4GW.Console.MessageType.Warning)
        _last_status = f"err:{e}"
        return False


def main() -> None:
    if not _timer.IsExpired():
        return
    _timer.Reset()
    _write_snapshot()


if __name__ == "__main__":
    main()
