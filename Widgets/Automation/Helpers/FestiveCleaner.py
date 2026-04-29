"""FestiveCleaner.py — Destroy festive items with no combat effect on outpost entry.

Runs on ALL accounts (no master-only guard). Triggers once per outpost entry.

Destroys (hardcoded minimal list — pure decoration, zero gameplay effect):
  Sparkler, Champagne_Popper, Party_Beacon

Protected forever (NEVER destroyed, even if user adds to extras):
  - Your PCons: Birthday_Cupcake, Golden_Egg, Candy_Corn, Candy_Apple,
    Slice_Of_Pumpkin_Pie, Drake_Kabob, Bowl_Of_Skalefin_Soup, Pahnai_Salad, War_Supplies
  - DP removal / movement / drunken: Peppermint/Wintergreen/Rainbow Candy Cane,
    Bottle_Of_Grog, Eggnog, Hunters_Ale, Sugary_Blue_Drink, Honeycomb, Chocolate_Bunny,
    Four_Leaf_Clover
  - Combat PCons: Grail_Of_Might, Essence_Of_Celerity, Armor_Of_Salvation
  - All 62 Everlasting tonics (any ModelID starting with El_)

User can add extra model IDs via the INI ("extras" comma-separated list).
Those are still blocked by PROTECTED_MODELS even if added — safety net against fat-fingering.
"""
import os
import time
import random

import Py4GW
import PyImGui as imgui

from Py4GWCoreLib import GLOBAL_CACHE, Player, Map, Inventory, ItemArray, Item, Routines
from Py4GWCoreLib.Py4GWcorelib import ActionQueueManager, ConsoleLog, Console
from Py4GWCoreLib.py4gwcorelib_src.IniHandler import IniHandler
from Py4GWCoreLib.enums import Bags
from Py4GWCoreLib.enums_src.Model_enums import ModelID

MODULE = "FestiveCleaner"

# ─── Hardcoded destroy list (no combat/movement/DP/drunken effect) ───
DEFAULT_DESTROY_MODELS = {
    ModelID.Sparkler,
    ModelID.Champagne_Popper,
    ModelID.Party_Beacon,
}

# ─── Protected list (hardcoded, cannot be overridden by INI extras) ───
_PCON_PROTECTED = {
    ModelID.Birthday_Cupcake, ModelID.Golden_Egg, ModelID.Candy_Corn, ModelID.Candy_Apple,
    ModelID.Slice_Of_Pumpkin_Pie, ModelID.Drake_Kabob, ModelID.Bowl_Of_Skalefin_Soup,
    ModelID.Pahnai_Salad, ModelID.War_Supplies,
    ModelID.Grail_Of_Might, ModelID.Essence_Of_Celerity, ModelID.Armor_Of_Salvation,
}
_EFFECT_PROTECTED = {
    ModelID.Peppermint_Candy_Cane, ModelID.Wintergreen_Candy_Cane, ModelID.Rainbow_Candy_Cane,
    ModelID.Bottle_Of_Grog, ModelID.Eggnog, ModelID.Hunters_Ale, ModelID.Sugary_Blue_Drink,
    ModelID.Honeycomb, ModelID.Chocolate_Bunny, ModelID.Four_Leaf_Clover,
}
_EVERLASTING_PROTECTED = {
    getattr(ModelID, name) for name in dir(ModelID)
    if name.startswith("El_") and not name.startswith("El__")
}
PROTECTED_MODELS = _PCON_PROTECTED | _EFFECT_PROTECTED | _EVERLASTING_PROTECTED

# ─── INI config ───
_INI_PATH = os.path.join(Py4GW.Console.get_projects_path(), "Widgets", "Config", "festive_cleaner.ini")
os.makedirs(os.path.dirname(_INI_PATH), exist_ok=True)
_ini = IniHandler(_INI_PATH)

def _parse_int_list(s: str) -> set[int]:
    out: set[int] = set()
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out

_enabled = _ini.read_bool("General", "enabled", True)
_extra_models = _parse_int_list(_ini.read_key("General", "extra_model_ids", ""))
_throttle_ms = max(400, _ini.read_int("General", "throttle_ms", 500))
_jitter_ms = max(0, _ini.read_int("General", "jitter_ms", 250))

# ─── Runtime state ───
_last_map_id: int = -1
_last_was_outpost: bool = False
_destroyed_count: int = 0
_last_status: str = ""
_last_status_t: float = 0.0
_run_cooldown_until: float = 0.0

def _log(msg: str, level=Console.MessageType.Info) -> None:
    ConsoleLog(MODULE, msg, level)

def _set_status(msg: str) -> None:
    global _last_status, _last_status_t
    _last_status = msg
    _last_status_t = time.time()

def _active_destroy_set() -> set[int]:
    """Effective destroy set = defaults + user extras, minus hardcoded protected."""
    return (DEFAULT_DESTROY_MODELS | _extra_models) - PROTECTED_MODELS

def _scan_bags_for_targets(destroy_set: set[int]) -> list[tuple[int, int, int]]:
    """Return list of (item_id, model_id, quantity) for items matching destroy_set.
    Skips any item whose model_id is in PROTECTED_MODELS (defense in depth).
    """
    import PyItem
    results: list[tuple[int, int, int]] = []
    bags = ItemArray.CreateBagList(Bags.Backpack, Bags.BeltPouch, Bags.Bag1, Bags.Bag2)
    for item_id in ItemArray.GetItemArray(bags):
        try:
            pi = PyItem.PyItem(item_id)
            pi.GetContext()
            mid = pi.model_id
            if mid in PROTECTED_MODELS:
                continue
            if mid not in destroy_set:
                continue
            results.append((item_id, mid, pi.quantity))
        except Exception as e:
            _log(f"scan error on item {item_id}: {e}", Console.MessageType.Warning)
            continue
    return results

def _destroy_coroutine(items: list[tuple[int, int, int]]):
    """Queue destroy actions with throttle + jitter. Aborts on map change."""
    global _destroyed_count
    if not items:
        yield from Routines.Yield.wait(1)
        return

    email = ""
    try:
        email = Player.GetAccountEmail() or ""
    except Exception:
        pass
    _log(f"[{email[:24]}] destroying {len(items)} festive item(s)")

    for item_id, mid, qty in items:
        if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsOutpost():
            _log("map changed mid-destroy, aborting")
            return
        if mid in PROTECTED_MODELS:
            continue
        try:
            ActionQueueManager().AddAction("ACTION", Inventory.DestroyItem, item_id)
            _destroyed_count += 1
            _log(f"destroyed model={mid} qty={qty} id={item_id}")
        except Exception as e:
            _log(f"destroy failed model={mid} id={item_id}: {e}", Console.MessageType.Warning)
        wait_ms = _throttle_ms + random.randint(0, _jitter_ms) if _jitter_ms > 0 else _throttle_ms
        yield from Routines.Yield.wait(wait_ms)

    _set_status(f"last run: destroyed {len(items)}")

def _try_trigger_on_outpost_entry() -> None:
    """Detect outpost entry (map change into an outpost) and trigger once."""
    global _last_map_id, _last_was_outpost, _run_cooldown_until

    if not _enabled:
        return
    if not Map.IsMapReady() or Map.IsMapLoading():
        return

    try:
        current_map = Map.GetMapID()
    except Exception:
        return
    in_outpost = Map.IsOutpost()

    # Detect transition INTO an outpost we weren't just in
    entered_outpost = in_outpost and (current_map != _last_map_id or not _last_was_outpost)
    _last_map_id = current_map
    _last_was_outpost = in_outpost

    if not entered_outpost:
        return

    # Debounce: 8s cooldown so we don't fire again within the same outpost session
    now = time.time()
    if now < _run_cooldown_until:
        return
    _run_cooldown_until = now + 8.0

    destroy_set = _active_destroy_set()
    if not destroy_set:
        return

    items = _scan_bags_for_targets(destroy_set)
    if not items:
        _set_status("clean (no festive junk)")
        return

    GLOBAL_CACHE.Coroutines.append(_destroy_coroutine(items))

# ─── UI ───
def _draw_ui() -> None:
    global _enabled, _throttle_ms, _jitter_ms, _extra_models
    if imgui.begin("Festive Cleaner", True):
        changed, new_enabled = imgui.checkbox("Enabled", _enabled)
        if changed:
            _enabled = new_enabled
            _ini.write_key("General", "enabled", str(_enabled))

        imgui.separator()
        imgui.text(f"Destroyed (session): {_destroyed_count}")
        if _last_status:
            imgui.text(f"Status: {_last_status}")

        imgui.separator()
        destroy_set = _active_destroy_set()
        imgui.text(f"Will destroy on outpost entry ({len(destroy_set)} model ID(s)):")
        imgui.bullet_text("Sparkler, Champagne Popper, Party Beacon")
        if _extra_models:
            extras_active = _extra_models - PROTECTED_MODELS
            blocked = _extra_models & PROTECTED_MODELS
            if extras_active:
                imgui.bullet_text(f"Extras: {sorted(extras_active)}")
            if blocked:
                imgui.bullet_text(f"Blocked (protected): {sorted(blocked)}")

        imgui.separator()
        imgui.text(f"Protected: {len(PROTECTED_MODELS)} items (PCons, DP/movement/drunken, all Everlasting tonics)")

        imgui.separator()
        imgui.text("Add extra model IDs (comma-separated, saved to INI):")
        _changed, new_extras = imgui.input_text("##extras", ",".join(str(m) for m in sorted(_extra_models)))
        if _changed:
            _extra_models = _parse_int_list(new_extras)
            _ini.write_key("General", "extra_model_ids", ",".join(str(m) for m in sorted(_extra_models)))

        imgui.separator()
        if imgui.button("Run now (scan + destroy)"):
            if Map.IsMapReady() and Map.IsOutpost():
                items = _scan_bags_for_targets(_active_destroy_set())
                if items:
                    GLOBAL_CACHE.Coroutines.append(_destroy_coroutine(items))
                else:
                    _set_status("manual run: no festive junk")
            else:
                _set_status("manual run: not in outpost")
    imgui.end()

# ─── Entry point ───
def main() -> None:
    if not Map.IsMapReady():
        return
    _try_trigger_on_outpost_entry()
    _draw_ui()

if __name__ == "__main__":
    main()
