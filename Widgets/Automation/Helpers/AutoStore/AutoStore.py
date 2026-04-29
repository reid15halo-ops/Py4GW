"""
AutoStore — Auto-deposit all materials to Xunlai material storage on outpost entry.
Runs on ALL accounts. Never touches consumables (cons/pcons).
"""
import time
import traceback
import Py4GW
import PyImGui

MODULE_NAME = "Auto Store"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Item, ItemArray, Inventory, Routines, Agent, AgentArray,
)
from Py4GWCoreLib.routines_src.Agents import Agents

coroutines = GLOBAL_CACHE.Coroutines

# --- State ---
_last_map_id = 0
_was_explorable = False
_depositing = False
_enabled = True
_last_status = ""
_last_status_t = 0.0
_items_deposited = 0

DEPOSIT_DELAY_MS = 400      # ms between each deposit action (safe for server)
OPEN_WAIT_MS = 2000         # ms to wait after opening Xunlai
OUTPOST_SETTLE_MS = 5000    # ms to wait after entering outpost before depositing
XUNLAI_MODEL_ID = 5001     # Xunlai Chest NPC model ID
INTERACT_RANGE = 250.0      # Range to be within to interact with NPC

def _set_status(msg):
    global _last_status, _last_status_t
    _last_status = msg
    _last_status_t = time.time()

def _get_material_items():
    """Get all material item IDs from inventory bags 1-4 (both common and rare)."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    all_items = ItemArray.GetItemArray(bags)
    materials = []
    for item_id in all_items:
        try:
            if Item.Type.IsMaterial(item_id) or Item.Type.IsRareMaterial(item_id):
                # Double-check: skip if it's usable (cons/pcons)
                if Item.Usage.IsUsable(item_id):
                    continue
                materials.append(int(item_id))
        except Exception:
            continue
    return materials

def _find_xunlai_agent():
    """Find the Xunlai Chest agent in the current map."""
    return Agents.GetAgentIDByModelID(XUNLAI_MODEL_ID)

def _co_deposit_materials():
    """Coroutine: walk to Xunlai chest, open storage, deposit all materials."""
    global _depositing, _items_deposited
    if _depositing:
        return
    _depositing = True
    _items_deposited = 0

    try:
        # Wait for outpost to settle
        _set_status("Waiting to deposit...")
        yield from Routines.Yield.wait(OUTPOST_SETTLE_MS)

        # Verify still in outpost
        if not Map.IsOutpost() or not Map.IsMapReady():
            _set_status("Not in outpost")
            return

        # Find the Xunlai Chest NPC
        xunlai_id = _find_xunlai_agent()
        if xunlai_id == 0:
            _set_status("No Xunlai Chest found")
            return

        # Walk to Xunlai Chest and interact
        _set_status("Walking to Xunlai...")
        xunlai_x, xunlai_y = Agent.GetXY(xunlai_id)
        yield from Routines.Yield.Movement.FollowPath([(xunlai_x, xunlai_y)])
        yield from Routines.Yield.wait(300)

        if not Map.IsOutpost():
            _set_status("Left outpost")
            return

        # Interact with Xunlai Chest to open storage
        _set_status("Opening storage...")
        yield from Routines.Yield.Player.InteractAgent(xunlai_id)
        yield from Routines.Yield.wait(OPEN_WAIT_MS)

        # Fallback: try direct open if interact didn't work
        if not Inventory.IsStorageOpen():
            Inventory.OpenXunlaiWindow()
            yield from Routines.Yield.wait(OPEN_WAIT_MS)

        if not Inventory.IsStorageOpen():
            _set_status("Failed to open storage")
            return

        # Scan and deposit
        materials = _get_material_items()
        if not materials:
            _set_status("No materials to store")
            return

        _set_status(f"Depositing {len(materials)} materials...")
        for item_id in materials:
            if not Map.IsOutpost():
                _set_status("Left outpost, stopping")
                return
            try:
                GLOBAL_CACHE.Inventory.DepositItemToStorage(item_id)
                _items_deposited += 1
                yield from Routines.Yield.wait(DEPOSIT_DELAY_MS)
            except Exception:
                continue

        # Second pass — catch any that were in partial stacks
        materials2 = _get_material_items()
        for item_id in materials2:
            if not Map.IsOutpost():
                return
            try:
                GLOBAL_CACHE.Inventory.DepositItemToStorage(item_id)
                _items_deposited += 1
                yield from Routines.Yield.wait(DEPOSIT_DELAY_MS)
            except Exception:
                continue

        _set_status(f"Stored {_items_deposited} items")

    except Exception as e:
        _set_status(f"Error: {e}")
    finally:
        _depositing = False

def _check_outpost_entry():
    """Detect transition from explorable -> outpost and trigger deposit."""
    global _last_map_id, _was_explorable

    if not Map.IsMapReady() or Map.IsMapLoading():
        return

    current_map = Map.GetMapID()
    is_outpost = Map.IsOutpost()
    is_explorable = Map.IsExplorable()

    # DISABLED: material deposit is now handled by AutoLootManager's outpost pipeline
    # (deposit → merchant → buy kits, in order). AutoStore is kept as manual-only.
    # if is_outpost and _was_explorable and current_map != _last_map_id:
    #     if _enabled and not _depositing:
    #         coroutines.append(_co_deposit_materials())

    _last_map_id = current_map
    _was_explorable = is_explorable

def draw_window():
    if not Player.IsPlayerLoaded():
        return
    if not PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.AlwaysAutoResize):
        PyImGui.end()
        return

    global _enabled
    _enabled = PyImGui.checkbox("Enabled", _enabled)

    if _depositing:
        PyImGui.text(_last_status if _last_status else "Depositing...")
    elif _last_status and (time.time() - _last_status_t) < 15:
        PyImGui.text(_last_status)
    else:
        loc = "Outpost" if Map.IsOutpost() else "Explorable"
        PyImGui.text(f"Idle [{loc}]")

    PyImGui.end()

def main():
    try:
        _check_outpost_entry()
        draw_window()
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error)
        Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)

def configure():
    PyImGui.text("Auto-deposits all materials to Xunlai")
    PyImGui.text("storage when entering an outpost.")
    PyImGui.text("")
    PyImGui.text("Deposits: common + rare materials")
    PyImGui.text("Never touches: cons, pcons, usable items")
    PyImGui.text("Runs on: ALL accounts (slaves + master)")
