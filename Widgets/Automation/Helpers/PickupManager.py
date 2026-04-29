"""
Pickup Manager — Ground loot pickup helper for AutoLootManager.

Loot pickup is handled by HeroAI LootingNode → Messaging.py PickUpLoot.
This module provides a fallback ground-item pickup routine for bots that
need it when HeroAI looting is not active.
"""
import time
import Py4GW
from Py4GWCoreLib import (
    Inventory, Item, Agent, AgentArray, Range, Player, Map,
)

MODULE_NAME = "Pickup Manager"


def get_free_slot_count():
    """Return the number of free inventory slots in bags 1-4."""
    return Inventory.GetFreeSlotCount()


def is_bags_nearly_full(config):
    """True if free slots are at or below the reserve threshold."""
    return get_free_slot_count() <= config.bag_space_reserve


def is_bags_completely_full():
    """True if there are zero free slots."""
    return get_free_slot_count() == 0


def process_pickup(config):
    """Pick up nearby loot items from the ground in explorable areas."""
    if not config.auto_pickup:
        return
    if Map.IsOutpost():
        return
    if not Player.IsPlayerLoaded():
        return

    now = time.time()
    if now - config.last_pickup_time < config.pickup_interval:
        return
    config.last_pickup_time = now

    try:
        free_slots = get_free_slot_count()
        nearly_full = free_slots <= config.bag_space_reserve
        if free_slots <= 0:
            return

        px, py = Player.GetXY()
        # Ground items are agents, not inventory items
        agent_array = AgentArray.GetItemArray()
        picked = 0

        for agent_id in agent_array:
            if picked >= 3:  # throttle per tick
                break
            if free_slots <= 0:
                break

            # Skip if not a valid ground item agent
            if not Agent.IsValid(agent_id):
                continue

            # Only pick up unowned loot (owner == 0 means on ground)
            owner = Agent.GetItemAgentOwnerID(agent_id)
            if owner != 0:
                continue

            # Distance check
            ix, iy = Agent.GetXY(agent_id)
            dx = ix - px
            dy = iy - py
            if dx * dx + dy * dy > config.pickup_range * config.pickup_range:
                continue

            # Get the actual item ID from the agent
            item_id = Agent.GetItemAgentItemID(agent_id)
            if item_id == 0:
                continue

            model_id = Item.GetModelID(item_id)
            rarity_tuple = Item.GetRarity(item_id)
            rarity_value = rarity_tuple[0] if isinstance(rarity_tuple, tuple) else rarity_tuple

            # Always pick blues(2), purples(3), golds(4), greens(5)
            always_pick = rarity_value >= 2
            if not always_pick and nearly_full and not config.pickup_whites_when_space:
                continue

            Inventory.PickUpItem(item_id)
            picked += 1
            config.items_picked_up += 1
            free_slots -= 1
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"process_pickup error: {e}", Py4GW.Console.MessageType.Warning)
