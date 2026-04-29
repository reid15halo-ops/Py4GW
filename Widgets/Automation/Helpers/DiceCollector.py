"""DiceCollector.py — Auto-transfer items between accounts on explorable entry.

Three categories:
  DICE       → all accounts drop → reid15halo picks up → Xunlai storage
  MAP_PIECES → all accounts drop → any slave picks up (round-robin)
  TROPHIES   → slaves drop on explorable entry → master picks up

Runs on ALL 6 clients. Each client handles its own drops and the designated
collector handles pickups. Trophy drops trigger automatically.
"""

import time
import Py4GW
import PyImGui as imgui
from Py4GWCoreLib import (
    Agent, AgentArray, GLOBAL_CACHE, Inventory, Item, ItemArray, Map,
    Party, Player, Range, Routines, SharedCommandType, Utils,
)
from Py4GWCoreLib.Py4GWcorelib import ActionQueueManager, ConsoleLog, Console
from Py4GWCoreLib.enums import Bags
from Py4GWCoreLib.enums_src.Model_enums import ModelID

MODULE = "ItemTransfer"
WIDGET_NAME = "DiceCollector"
ITEM_TYPE_TROPHY = 30

# Load account emails from accounts.json — no hardcoded PII
import json as _json
import os as _os
_ACCOUNTS_PATH = _os.path.join(Py4GW.Console.get_projects_path(), "accounts.json")
try:
    with open(_ACCOUNTS_PATH, "r") as _f:
        _accts = _json.load(_f).get("Standard", [])
    MASTER_EMAIL = _accts[0]["email"] if _accts else ""
    DICE_COLLECTOR = _accts[1]["email"] if len(_accts) > 1 else ""
    SLAVE_EMAILS = [a["email"] for a in _accts[1:]]
except Exception:
    MASTER_EMAIL = ""
    DICE_COLLECTOR = ""
    SLAVE_EMAILS = []

# Known chest model IDs
CHEST_MODELS = {
    8141, 69, 4579, 6064, 73, 9, 9274, 8932, 9284,
}

# ═══════════════════════════════════════════════════════════════
# Item definitions
# ═══════════════════════════════════════════════════════════════

DICE_MODELS = {
    ModelID.Lunar_Fortune_2007_Pig, ModelID.Lunar_Fortune_2008_Rat, ModelID.Lunar_Fortune_2009_Ox,
    ModelID.Lunar_Fortune_2010_Tiger, ModelID.Lunar_Fortune_2011_Rabbit, ModelID.Lunar_Fortune_2012_Dragon,
    ModelID.Lunar_Fortune_2013_Snake, ModelID.Lunar_Fortune_2014_Horse, ModelID.Lunar_Fortune_2015_Sheep,
    ModelID.Lunar_Fortune_2016_Monkey, ModelID.Lunar_Fortune_2017_Rooster, ModelID.Lunar_Fortune_2018_Dog,
    ModelID.Festival_Prize, ModelID.Lunar_Token,
}

TOME_MODELS = {
    ModelID.Warrior_Tome, ModelID.Warrior_Elite_Tome,
    ModelID.Ranger_Tome, ModelID.Ranger_Elite_Tome,
    ModelID.Monk_Tome, ModelID.Monk_Elite_Tome,
    ModelID.Necromancer_Tome, ModelID.Necromancer_Elite_Tome,
    ModelID.Mesmer_Tome, ModelID.Mesmer_Elite_Tome,
    ModelID.Elementalist_Tome, ModelID.Elementalist_Elite_Tome,
    ModelID.Assassin_Tome, ModelID.Assassin_Elite_Tome,
    ModelID.Ritualist_Tome, ModelID.Ritualist_Elite_Tome,
    ModelID.Paragon_Tome, ModelID.Paragon_Elite_Tome,
    ModelID.Dervish_Tome, ModelID.Dervish_Elite_Tome,
}

MASTER_PICKUP_MODELS = {
    ModelID.Passage_Scroll_Deep, ModelID.Passage_Scroll_Fow, ModelID.Passage_Scroll_Urgoz, ModelID.Passage_Scroll_Uw,
    ModelID.Scroll_Of_Adventurers_Insight, ModelID.Scroll_Of_Berserkers_Insight, ModelID.Scroll_Of_Heros_Insight,
    ModelID.Scroll_Of_Hunters_Insight, ModelID.Scroll_Of_Rampagers_Insight, ModelID.Scroll_of_Slayers_Insight,
    ModelID.Scroll_Of_Resurrection, ModelID.Scroll_Of_The_Lightbringer, ModelID.Coffer_Of_Whispers,
    ModelID.Imperial_Guard_Lockbox, ModelID.Monumental_Tapestry, ModelID.Victory_Token, ModelID.Medal_Of_Honor,
    ModelID.Bison_Championship_Token, ModelID.Seal_Of_The_Dragon_Empire, ModelID.Deldrimor_Armor_Remnant,
    ModelID.Ancient_Armor_Remnant, ModelID.Primeval_Armor_Remnant, ModelID.Stolen_Sunspear_Armor,
    ModelID.Mysterious_Armor_Piece, ModelID.Margonite_Gemstone, ModelID.Stygian_Gemstone, ModelID.Titan_Gemstone,
    ModelID.Torment_Gemstone, ModelID.Destroyer_Core, ModelID.Onyx_Gemstone, ModelID.Obsidian_Shard,
    ModelID.Lockpick, ModelID.Trade_Contract, ModelID.Deldrimor_Talisman, ModelID.Copper_Zaishen_Coin,
    ModelID.Silver_Zaishen_Coin, ModelID.Gold_Zaishen_Coin, ModelID.Champions_Zaishen_Strongbox,
    ModelID.Gladiators_Zaishen_Strongbox, ModelID.Heros_Zaishen_Strongbox, ModelID.Strategists_Zaishen_Strongbox,
}

REID15_PICKUP_MODELS = DICE_MODELS | TOME_MODELS

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _log(msg, level=Console.MessageType.Info):
    ConsoleLog(MODULE, msg, level)

def _my_email():
    try: return Player.GetAccountEmail() or ""
    except Exception: return ""

def _is_master():
    return _my_email().lower() == MASTER_EMAIL.lower()

def _is_dice_collector():
    return _my_email().lower() == DICE_COLLECTOR.lower()

def _get_items_by_filter(filter_fn):
    import PyItem
    result = []
    try:
        bags = ItemArray.CreateBagList(Bags.Backpack, Bags.BeltPouch, Bags.Bag1, Bags.Bag2)
        for item_id in ItemArray.GetItemArray(bags):
            try:
                pi = PyItem.PyItem(item_id)
                pi.GetContext()
                mid = pi.model_id
                tid, _ = Item.GetItemType(item_id)
                if filter_fn(mid, tid):
                    result.append((item_id, mid, pi.quantity))
            except Exception:
                continue
    except Exception as e:
        _log(f"Item filter error: {e}", Console.MessageType.Warning)
    return result

def _get_dice(): return _get_items_by_filter(lambda mid, tid: mid in DICE_MODELS)
def _get_reid15_items(): return _get_items_by_filter(lambda mid, tid: mid in REID15_PICKUP_MODELS)
def _get_trophies(): return _get_items_by_filter(lambda mid, tid: tid == ITEM_TYPE_TROPHY)
def _get_master_valuables(): return _get_items_by_filter(lambda mid, tid: mid in MASTER_PICKUP_MODELS)

# ═══════════════════════════════════════════════════════════════
# Coroutines
# ═══════════════════════════════════════════════════════════════

def _drop_items(items, label="items"):
    """Coroutine: drop items. Empty list is safe (yields once and exits)."""
    if not items:
        yield from Routines.Yield.wait(1)  # must yield at least once — generator rule
        return
    _log(f"Dropping {len(items)} {label}")
    for item_id, _, qty in items:
        if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
            return
        ActionQueueManager().AddAction("ACTION", Inventory.DropItem, item_id, qty)
        yield from Routines.Yield.wait(400)

def _pickup_ground_items(model_filter=None, label="items", radius=None):
    """Coroutine: pick up ground items. model_filter=None picks up everything."""
    import PyItem
    picked = 0
    if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
        yield from Routines.Yield.wait(1)
        return 0

    ground = AgentArray.GetItemArray()
    targets = []
    px, py = Player.GetXY()

    for aid in ground:
        try:
            if not Agent.IsValid(aid): continue
            ax, ay = Agent.GetXY(aid)
            dist = Utils.Distance((px, py), (ax, ay))
            if radius and dist > radius: continue
            if model_filter:
                pi = PyItem.PyItem(aid)
                pi.GetContext()
                if pi.model_id not in model_filter: continue
            targets.append((aid, dist))
        except Exception:
            continue

    if not targets:
        yield from Routines.Yield.wait(1)
        return 0
    targets.sort(key=lambda x: x[1])

    for t_aid, _ in targets:
        if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
            break
        if Inventory.GetFreeSlotCount() <= 0: break
        if not Agent.IsValid(t_aid): continue
        tx, ty = Agent.GetXY(t_aid)
        px, py = Player.GetXY()
        if Utils.Distance((px, py), (tx, ty)) > float(Range.Adjacent.value):
            Player.Move(tx, ty)
            for _ in range(20):
                yield from Routines.Yield.wait(100)
                if not Agent.IsValid(t_aid): break
                if Utils.Distance(Player.GetXY(), (tx, ty)) <= float(Range.Adjacent.value): break

        if Agent.IsValid(t_aid):
            ActionQueueManager().AddAction("ACTION", Player.Interact, t_aid)
            picked += 1
            yield from Routines.Yield.wait(500)

    if picked: _log(f"Picked up {picked} {label}")
    return picked

def _slave_open_chest(agent_id):
    """Coroutine for slaves to move to, open, and LOOT the chest."""
    if not Agent.IsValid(agent_id): return
    
    # 1. Move to chest
    _log(f"Moving to chest {agent_id}")
    tx, ty = Agent.GetXY(agent_id)
    Player.Move(tx, ty)
    for _ in range(50):
        yield from Routines.Yield.wait(100)
        if Utils.Distance(Player.GetXY(), (tx, ty)) < float(Range.Adjacent.value): break
    
    # 2. Interact (Open)
    if Agent.IsValid(agent_id):
        ActionQueueManager().AddAction("ACTION", Player.Interact, agent_id)
        _log("Opening chest...")
    
    # 3. Wait for loot to drop
    yield from Routines.Yield.wait(2000)
    
    # 4. Pick up items near the chest (radius 400)
    _log("Looting items from chest...")
    yield from _pickup_ground_items(label="chest loot", radius=400.0)
    
    _log("Chest looting complete")

# ═══════════════════════════════════════════════════════════════
# Chest Scanning (Master Only)
# ═══════════════════════════════════════════════════════════════

_last_chest_scan = 0
_chest_cooldowns = {}

def _scan_for_chests():
    global _last_chest_scan
    now = time.time()
    if now - _last_chest_scan < 2.0 or not _is_master() or not Map.IsExplorable(): return
    _last_chest_scan = now

    px, py = Player.GetXY()
    all_agents = AgentArray.GetAgentArray()
    
    for agent_id in all_agents:
        if not Agent.IsValid(agent_id): continue
        if Agent.GetModelID(agent_id) in CHEST_MODELS:
            ax, ay = Agent.GetXY(agent_id)
            if Utils.Distance((px, py), (ax, ay)) < float(Range.Earshot.value):
                if agent_id not in _chest_cooldowns or (now - _chest_cooldowns[agent_id] > 60):
                    _chest_cooldowns[agent_id] = now
                    _log(f"Chest detected! Sending slave to loot.")
                    
                    # Send to first available slave on map
                    my_email = _my_email()
                    my_map = Map.GetMapID()
                    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
                        if not acc or not acc.IsSlotActive or not acc.AccountEmail: continue
                        if acc.AccountEmail.lower() == my_email.lower(): continue
                        
                        # Guard: Check if slave is on same map
                        try:
                            if acc.AgentData.Map.MapID != my_map: continue
                        except: continue

                        GLOBAL_CACHE.ShMem.SendMessage(my_email, acc.AccountEmail, SharedCommandType.OpenChest, (agent_id, 1, 0, 0))
                        break # One slave per chest
                    break

# ═══════════════════════════════════════════════════════════════
# Main Logic & UI
# ═══════════════════════════════════════════════════════════════

_slaves_enabled_timer = 0

def _enable_on_all_slaves():
    """Periodically try to enable widget on slaves to ensure sync."""
    global _slaves_enabled_timer
    if not _is_master(): return
    
    now = time.time()
    if now - _slaves_enabled_timer < 30.0: return
    _slaves_enabled_timer = now

    email = _my_email()
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if not acc or not acc.IsSlotActive or not acc.AccountEmail: continue
        if acc.AccountEmail.lower() == email.lower(): continue
        # Broadcast widget start
        GLOBAL_CACHE.ShMem.SendMessage(email, acc.AccountEmail, SharedCommandType.EnableWidget, (0,0,0,0), (WIDGET_NAME, "", "", ""))

def _draw_ui():
    if imgui.begin("Item Transfer", True):
        email = _my_email()
        imgui.text(f"Account: {email[:18]}...")
        imgui.separator()

        if Map.IsExplorable():
            if imgui.button("Collect Dice+Tomes"):
                def _run():
                    if not _is_dice_collector(): yield from _drop_items(_get_reid15_items(), "dice+tomes")
                    else: 
                        yield from Routines.Yield.wait(12000)
                        yield from _pickup_ground_items(REID15_PICKUP_MODELS, "dice+tomes")
                GLOBAL_CACHE.Coroutines.append(_run())
            
            if imgui.button("Force Drop Valuables"):
                GLOBAL_CACHE.Coroutines.append(_drop_items(_get_master_valuables() + _get_trophies(), "valuables"))
        
        if Map.IsOutpost():
            imgui.text("Outpost Mode Active")
            if imgui.button("Manual Force Sync"):
                global _slaves_enabled_timer
                _slaves_enabled_timer = 0
                _enable_on_all_slaves()

        imgui.separator()
        imgui.text("Lockpicks: " + str(Inventory.GetModelCount(ModelID.Lockpick)))
    imgui.end()

def _handle_messages():
    email = _my_email().lower()
    try:
        messages = GLOBAL_CACHE.ShMem.GetMessages(email)
        for msg in messages:
            if msg.Command == SharedCommandType.OpenChest:
                agent_id = int(msg.Params[0])
                GLOBAL_CACHE.Coroutines.append(_slave_open_chest(agent_id))
    except: pass

def main():
    if not Map.IsMapReady(): return
    _enable_on_all_slaves()
    _scan_for_chests()
    _handle_messages()
    _draw_ui()

if __name__ == "__main__":
    main()