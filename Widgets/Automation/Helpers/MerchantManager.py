"""
Merchant Manager — Auto-sell junk to merchants and handle inventory-full scenarios.

Extracted from AutoLootManager.py.

Handles:
  - Auto-sell white salvage-type junk and optionally common materials when
    a merchant window is open in an outpost.
  - Inventory-full logic: when bags are completely full in explorable areas
    and valuable loot is on the ground, drop the lowest-value white item
    to make room.
"""
import time
import Py4GW

MODULE_NAME = "Merchant Manager"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Item, ItemArray, Inventory,
    Agent, AgentArray, Trading,
)
from Py4GWCoreLib.py4gwcorelib_src.ActionQueue import ActionQueueManager

# ─── Normal Mode Keys — auto-sold to merchants ───────────────────────────────
# These are dungeon/explorable area keys that have no value for farming bots.
_NORMAL_MODE_KEYS = {
    # Prophecies
    5966,   # Ascalonian Key
    5967,   # Steel Key
    5964,   # Krytan Key
    5965,   # Maguuma Key
    5960,   # Elonian Key
    5962,   # Shiverpeak Key
    5963,   # Darkstone Key
    5961,   # Miner's Key
    # Factions
    6537,   # Shing Jea Key
    6540,   # Canthan Key
    6535,   # Kurzick Key
    6536,   # Stoneroot Key
    6538,   # Luxon Key
    6539,   # Deep Jade Key
    6534,   # Forbidden Key
    # Nightfall
    15557,  # Istani Key
    15559,  # Kournan Key
    15558,  # Vabbian Key
    15556,  # Ancient Elonian Key
    15560,  # Margonite Key
    19174,  # Demonic Key
    # Core
    5882,   # Phantom Key
    5971,   # Obsidian Key
}

# ---------------------------------------------------------------------------
# Helpers that will eventually come from PickupManager once it is extracted.
# For now we provide thin wrappers so this module works standalone.
# ---------------------------------------------------------------------------
try:
    from Widgets.Automation.Helpers.PickupManager import (
        get_free_slot_count,
        is_bags_completely_full,
    )
except ImportError:
    def get_free_slot_count():
        """Return the number of free inventory slots in bags 1-4."""
        return Inventory.GetFreeSlotCount()

    def is_bags_completely_full():
        """True if there are zero free slots."""
        return get_free_slot_count() == 0


def _item_name(item_id):
    """Human-readable item name with safe fallback."""
    try:
        return Item.GetName(item_id) if Item.IsNameReady(item_id) else f"Item {item_id}"
    except Exception:
        return f"Item {item_id}"


# ---------------------------------------------------------------------------
#region Auto-Merchant Junk
# ---------------------------------------------------------------------------

def _is_salvage_type(item_id):
    """Check if an item is a Salvage-type item (junk drops meant to be salvaged/sold).
    These are broken armor pieces, torn clothing, remnants — NOT equippable weapons/armor."""
    try:
        type_id, type_name = Item.GetItemType(item_id)
        return type_name == "Salvage"
    except Exception:
        return False  # Item may have been consumed — safe to treat as non-salvage


def _get_junk_items_to_sell(config):
    """Return a list of item IDs in bags 1-4 that should be sold to a merchant.

    Sells:
      - White Salvage-type items (broken armor, torn bits — the junk drops)
      - Optionally common materials (if merchant_sell_materials is on)
      - Optionally normal mode keys (if merchant_sell_keys is on)
    Never sells:
      - Actual weapons or equippable armor (these are kept or salvaged, never vendored)
      - Blue, purple, gold, green items
      - Kits (ID, salvage, expert)
      - Tomes, rare materials, dyes, consumables worth keeping
    """
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    items = ItemArray.GetItemArray(bags)
    sell_list = []

    for item_id in items:
        try:
            model_id = Item.GetModelID(item_id)

            # Sell normal mode keys regardless of rarity (they're white anyway)
            if getattr(config, 'merchant_sell_keys', False) and model_id in _NORMAL_MODE_KEYS:
                sell_list.append(item_id)
                continue

            # Never sell non-white rarity items (except keys handled above)
            if not Item.Rarity.IsWhite(item_id):
                continue

            # Sell white Salvage-type items (junk drops, NOT equippable gear)
            if config.merchant_sell_whites and _is_salvage_type(item_id):
                # Skip zero-value items
                try:
                    if Item.Properties.GetValue(item_id) <= 0:
                        continue
                except Exception:
                    continue  # Can't read value — skip item to be safe
                sell_list.append(item_id)
                continue

            # Optionally sell common materials
            if config.merchant_sell_materials:
                try:
                    if Item.Type.IsMaterial(item_id):
                        sell_list.append(item_id)
                        continue
                except Exception:
                    pass  # Type check failed — item may have been consumed
        except Exception:
            continue  # Item may have been consumed between array fetch and check

    return sell_list


def process_merchant(config):
    """Auto-sell junk items when at a merchant in an outpost."""
    now = time.time()
    if now - config.last_merchant_time < config.merchant_interval:
        return
    config.last_merchant_time = now

    if not Player.IsPlayerLoaded():
        return
    if Map.IsMapLoading() or not Map.IsMapReady():
        return
    # Only sell in outposts
    if not Map.IsOutpost():
        return

    # Check if merchant window is open by verifying offered items exist
    try:
        offered = Trading.Merchant.GetOfferedItems()
        if not offered or len(offered) == 0:
            return  # Merchant window not open
    except Exception:
        return  # Merchant API not available or window closed

    junk = _get_junk_items_to_sell(config)
    if not junk:
        return

    # Sell one item per cycle to avoid flooding
    item_id = junk[0]
    try:
        quantity = Item.Properties.GetQuantity(item_id)
        value = Item.Properties.GetValue(item_id)
        cost = quantity * value
        if cost <= 0:
            return  # Don't sell zero-value items
        name = _item_name(item_id)
        # DIAGNOSTIC_2026_04_06: log each merchant sell (rare — gated by
        # merchant_interval + one item per cycle). Identifies last item_id
        # before c0000005 if merchant-sell path is the crash source.
        try:
            Py4GW.Console.Log(
                "DIAG_2026_04_06",
                f"merchant_sell:item={item_id}:cost={cost}:name={name}",
                Py4GW.Console.MessageType.Info,
                False,
            )
        except Exception:
            pass
        ActionQueueManager().AddAction("ACTION", Trading.Merchant.SellItem, item_id, cost)
        config.items_sold += 1
        config.last_action = f"Sold: {name} ({cost}g)"
        config.last_action_time = now
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Merchant sell error: {e}", Py4GW.Console.MessageType.Warning, False)
#endregion


# ---------------------------------------------------------------------------
#region Inventory Full Handling
# ---------------------------------------------------------------------------

def _is_item_owned_by_me_or_free(agent_id):
    """Check if a ground item is owned by us or unassigned (free for all).

    This is a local re-implementation; callers that already have the pickup
    module loaded should prefer the canonical version there.
    """
    try:
        owner = Agent.GetItemAgentOwnerID(agent_id)
        if owner == 0:
            return True  # Unassigned — free for all
        return owner == Player.GetAgentID()
    except Exception:
        return False


def _classify_ground_item(agent_id):
    """Return (priority, reason) for a ground item agent.

    Priority levels (higher = more valuable):
      0 — skip / not interesting
      1 — white item (weapon/armor/salvage junk)
      2 — material / tome / dye
      3 — blue item
      4 — purple item
      5 — gold / green item
    """
    try:
        item_id = Agent.GetItemAgentItemID(agent_id)
        if item_id == 0:
            return 0, "no item"

        if Item.Rarity.IsGold(item_id) or Item.Rarity.IsGreen(item_id):
            return 5, "gold/green"
        if Item.Rarity.IsPurple(item_id):
            return 4, "purple"
        if Item.Rarity.IsBlue(item_id):
            return 3, "blue"

        # White-tier checks
        try:
            if Item.Type.IsMaterial(item_id):
                return 2, "material"
        except Exception:
            pass
        try:
            type_id, type_name = Item.GetItemType(item_id)
            if type_name == "Tome":
                return 2, "tome"
            if type_name == "Dye":
                return 2, "dye"
        except Exception:
            pass

        return 1, "white"
    except Exception:
        return 0, "error"


def process_inventory_full(config):
    """When bags are full and in explorable, drop the lowest-value white item
    to make room for higher rarity drops that might be on the ground.

    Only triggers when:
      - Bags are completely full (0 free slots)
      - There are blue+ items on the ground nearby worth picking up
      - There are white weapons/armor in bags we can drop
    """
    # Throttle: don't check every frame
    now = time.time()
    if now - getattr(process_inventory_full, '_last_check', 0) < 3.0:
        return
    process_inventory_full._last_check = now

    if not Player.IsPlayerLoaded():
        return
    if Map.IsMapLoading() or not Map.IsMapReady():
        return
    if not Map.IsExplorable():
        return
    if get_free_slot_count() > 0:
        return  # Not full, no action needed

    # Check if there are valuable ground items nearby worth making room for
    ground_items = AgentArray.GetItemArray()
    if not ground_items:
        return

    player_pos = Player.GetXY()
    ground_items = AgentArray.Filter.ByDistance(ground_items, player_pos, config.pickup_range)

    has_valuable_ground_item = False
    for agent_id in ground_items:
        if not _is_item_owned_by_me_or_free(agent_id):
            continue
        priority, _ = _classify_ground_item(agent_id)
        if priority >= 2:  # At least material/tome priority
            has_valuable_ground_item = True
            break

    if not has_valuable_ground_item:
        return  # No valuable ground items, don't bother dropping

    # Find the lowest-value white weapon/armor/salvage-junk in inventory to drop
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    items = ItemArray.GetItemArray(bags)

    lowest_value = 999999
    lowest_item = 0

    for item_id in items:
        try:
            if not Item.Rarity.IsWhite(item_id):
                continue
            is_weapon = Item.Type.IsWeapon(item_id)
            is_armor = Item.Type.IsArmor(item_id)
            is_salvage_junk = _is_salvage_type(item_id)
            if not is_weapon and not is_armor and not is_salvage_junk:
                continue
            # Never drop kits
            if Item.Usage.IsSalvageKit(item_id) or Item.Usage.IsIDKit(item_id):
                continue
            if Item.Usage.IsExpertSalvageKit(item_id) or Item.Usage.IsPerfectSalvageKit(item_id):
                continue
            val = Item.Properties.GetValue(item_id)
            if val < lowest_value:
                lowest_value = val
                lowest_item = item_id
        except Exception:
            continue  # Item may have been consumed — skip to next

    if lowest_item == 0:
        # No droppable white items — bags are full of blue+ or kits only
        config.last_action = "BAGS FULL: nothing to drop (all blue+ or kits)"
        config.last_action_time = time.time()
        Py4GW.Console.Log(MODULE_NAME, "Inventory full, no white items to drop — sell or store items manually.", Py4GW.Console.MessageType.Warning, False)
        return

    try:
        name = _item_name(lowest_item)
        ActionQueueManager().AddAction("ACTION", Inventory.DropItem, lowest_item, 1)
        config.last_action = f"Dropped white ({lowest_value}g): {name}"
        config.last_action_time = time.time()
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Drop item error: {e}", Py4GW.Console.MessageType.Warning, False)
#endregion
