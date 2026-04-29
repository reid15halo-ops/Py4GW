"""OutpostSpeedBuff.py — Use sugar-rush/jolt candy on entering major hubs.

Runs on EVERY account independently (no master-only guard, no shared-memory
broadcast). Per-account: detect outpost entry into a major hub, then consume
one city-speed candy. Tries inventory first, falls back to Xunlai withdrawal.

Skips silently if any city-speed effect is already active.

Hubs (19): all 18 official Towns from the Guild Wars Wiki + Embark Beach.
INI override available via "hub_ids" comma-separated list.
"""
import os
import time

import Py4GW
import PyImGui as imgui

from Py4GWCoreLib import GLOBAL_CACHE, Map, Player, Routines
from Py4GWCoreLib.Py4GWcorelib import ConsoleLog, Console
from Py4GWCoreLib.py4gwcorelib_src.IniHandler import IniHandler
from Py4GWCoreLib.enums_src.Map_enums import map_variants_to_base

MODULE = "OutpostSpeedBuff"
OPTIONAL = True

# All 18 official Towns + Embark Beach (cross-campaign hub).
DEFAULT_HUB_IDS: set[int] = {
    81,   # Ascalon City
    55,   # Lion's Arch
    49,   # Henge of Denravi
    109,  # The Amnoon Oasis
    20,   # Droknar's Forge
    242,  # Shing Jea Monastery
    194,  # Kaineng Center
    77,   # House zu Heltzer
    193,  # Cavalon
    449,  # Kamadan, Jewel of Istan
    387,  # Sunspear Sanctuary
    414,  # The Kodash Bazaar
    450,  # Gate of Torment
    642,  # Eye of the North outpost
    640,  # Rata Sum
    644,  # Gunnar's Hold
    648,  # Doomlore Shrine
    248,  # Great Temple of Balthazar
    857,  # Embark Beach
}

# INI config
_INI_PATH = os.path.join(Py4GW.Console.get_projects_path(), "Widgets", "Config", "outpost_speed_buff.ini")
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
_cooldown_s = max(5, _ini.read_int("General", "cooldown_s", 30))
_custom_hubs = _parse_int_list(_ini.read_key("General", "hub_ids", ""))
_active_hubs: set[int] = _custom_hubs if _custom_hubs else DEFAULT_HUB_IDS

# Runtime state
_last_map_id: int = -1
_last_was_outpost: bool = False
_run_cooldown_until: float = 0.0
_used_count: int = 0
_last_status: str = ""


def _log(msg: str, level=Console.MessageType.Info) -> None:
    ConsoleLog(MODULE, msg, level)


def _set_status(msg: str) -> None:
    global _last_status
    _last_status = msg


def _canonical_map(map_id: int) -> int:
    return map_variants_to_base.get(map_id, map_id)


def _buff_coroutine():
    """Use one city-speed candy. Try inventory first, then Xunlai."""
    global _used_count

    yield from Routines.Yield.wait(500)

    if not Map.IsOutpost() or Map.IsMapLoading():
        return

    player_id = Player.GetAgentID()
    effect_ids = list(Routines.Yield.Upkeepers.CITY_SPEED_EFFECTS)
    if any(GLOBAL_CACHE.Effects.HasEffect(player_id, eid) for eid in effect_ids):
        _set_status("already buffed")
        return

    item_models = [
        (m.value if hasattr(m, "value") else int(m))
        for m in Routines.Yield.Upkeepers.CITY_SPEED_ITEMS
    ]

    # Try inventory first (priority order: longest-duration → 50%-boost short)
    for mid in item_models:
        item_id = GLOBAL_CACHE.Inventory.GetFirstModelID(mid)
        if item_id:
            GLOBAL_CACHE.Inventory.UseItem(item_id)
            _used_count += 1
            _set_status(f"used model={mid} from inventory")
            _log(f"used model={mid} from inventory")
            return

    # Inventory empty — try Xunlai withdrawal of one candy
    yield from Routines.Yield.Items.WithdrawFirstAvailable(item_models, 1)
    yield from Routines.Yield.wait(750)

    for mid in item_models:
        item_id = GLOBAL_CACHE.Inventory.GetFirstModelID(mid)
        if item_id:
            GLOBAL_CACHE.Inventory.UseItem(item_id)
            _used_count += 1
            _set_status(f"used model={mid} from xunlai")
            _log(f"used model={mid} from xunlai")
            return

    _set_status("no candy in inventory or xunlai")
    _log("no candy available in inventory or xunlai", Console.MessageType.Warning)


_buffed_this_map: bool = False

def _try_trigger_on_hub_entry() -> None:
    global _last_map_id, _last_was_outpost, _buffed_this_map

    if not _enabled:
        return
    if not Map.IsMapReady() or Map.IsMapLoading():
        return

    try:
        current_map = Map.GetMapID()
    except Exception:
        return
    in_outpost = Map.IsOutpost()

    # Detect map change — reset flag
    entered_outpost = in_outpost and (current_map != _last_map_id or not _last_was_outpost)
    if current_map != _last_map_id:
        _buffed_this_map = False
    _last_map_id = current_map
    _last_was_outpost = in_outpost

    if not entered_outpost:
        return

    # Only buff once per map load
    if _buffed_this_map:
        return

    if _canonical_map(current_map) not in _active_hubs:
        return

    _buffed_this_map = True
    GLOBAL_CACHE.Coroutines.append(_buff_coroutine())


def _draw_ui() -> None:
    global _enabled
    if imgui.begin("Outpost Speed Buff", True):
        changed, new_enabled = imgui.checkbox("Enabled", _enabled)
        if changed:
            _enabled = new_enabled
            _ini.write_key("General", "enabled", str(_enabled))

        imgui.separator()
        imgui.text(f"Used (session): {_used_count}")
        if _last_status:
            imgui.text(f"Status: {_last_status}")

        imgui.separator()
        imgui.text(f"Active hubs: {len(_active_hubs)}")
        if _custom_hubs:
            imgui.text(f"(INI override of default {len(DEFAULT_HUB_IDS)})")
        else:
            imgui.text(f"(default — all {len(DEFAULT_HUB_IDS)} official hubs)")

        imgui.separator()
        imgui.text(f"Cooldown per map: {_cooldown_s}s")
    imgui.end()


_last_tick: float = 0.0

def main() -> None:
    global _last_tick
    now = time.time()
    if now - _last_tick < 0.5:
        return
    _last_tick = now
    if not Map.IsMapReady():
        return
    _try_trigger_on_hub_entry()
    _draw_ui()


if __name__ == "__main__":
    main()
