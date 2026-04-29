"""
Auto Loot Manager — Smart identify + salvage + pickup + merchant for slaves.
Identifies all blue/purple items, reads their requirement and modifiers,
and independently decides whether to keep, salvage for mats, or expert-salvage
to extract valuable upgrades (runes, insignias, weapon mods).

Also handles:
  - Auto-pickup of loot from the ground (explorable areas)
  - Auto-sell junk white items to merchant (outposts, when merchant window open)
  - Inventory full handling (stop picking whites when bags nearly full,
    drop lowest-value white when full + valuable loot on ground)

Decision flow per item:
  1. Identify (blue/purple/gold)
  2. Read requirement level + all modifiers
  3. Decide: KEEP / EXPERT SALVAGE (extract upgrade) / LESSER SALVAGE (materials)

Blue vs Purple strategy (GW1 economy):
  - Blues have a single mod.  Only extract if that mod is a PERFECT (max) roll.
    Vampiric/Zealous on blues are too common and cheap to justify an expert kit.
  - Purples have two mods.  Extract if ANY mod is near-max value.
    Vampiric/Zealous on purples are acceptable because the second mod may add value.

Enable via Widget Manager > Automation > Helpers > Auto Loot Manager
Runs on ALL accounts (not just master).
"""
import time
import random
import traceback
import Py4GW
import PyImGui

MODULE_NAME = "Auto Loot Manager"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Party, Agent, Item, ItemArray,
    Inventory, Routines, AgentArray, Trading, Range, SharedCommandType,
    Utils,
)
from Py4GWCoreLib.py4gwcorelib_src.ActionQueue import ActionQueueManager
from Py4GWCoreLib.enums_src.Model_enums import ModelID

# --- Extracted sub-modules ---
from Widgets.Automation.Helpers.ItemModTables import (
    MOD_REQUIREMENT, MOD_REQUIREMENT_2,
    VALUABLE_WEAPON_MODS_PURPLE, VALUABLE_WEAPON_MODS_BLUE,
    VALUABLE_SHIELD_OFFHAND_MODS_PURPLE, VALUABLE_SHIELD_OFFHAND_MODS_BLUE,
    VALUABLE_ARMOR_RUNES,
    VALUABLE_MODEL_IDS, VALUABLE_SKIN_KEYWORDS,
    VALUABLE_RUNE_KEYWORDS, VALUABLE_INSIGNIA_KEYWORDS,
)

import Widgets.Automation.Helpers.SalvageDecision as SalvageDecision
from Widgets.Automation.Helpers.SalvageDecision import (
    evaluate_item, DECISION_KEEP, DECISION_SALVAGE_MATS, DECISION_EXTRACT,
    get_expert_kit, get_lesser_kit,
    _request_kit_from_team, _try_pickup_ground_kit,
    _is_lesser_kit, _is_expert_kit, _is_id_kit,
)

from Widgets.Automation.Helpers.PickupManager import (
    process_pickup, get_free_slot_count, is_bags_nearly_full,
    is_bags_completely_full,
)

from Widgets.Automation.Helpers.MerchantManager import (
    process_merchant, process_inventory_full,
    _is_salvage_type, _get_junk_items_to_sell,
)

#region Config
class LootConfig:
    def __init__(self):
        self.enabled = True
        self.auto_identify = False      # DISABLED — InventoryPlus handles via AutoInventoryHandler
        self.auto_salvage_white = False  # DISABLED — InventoryPlus handles via AutoInventoryHandler
        self.auto_salvage_smart = False  # DISABLED — InventoryPlus handles via AutoInventoryHandler
        self.expert_extract = True       # Use expert kit to extract valuable mods/runes
        self.auto_salvage_gold = True    # Smart gold salvage: evaluate mods/req/skin, salvage junk golds
        self.keep_green = True           # Never salvage green items
        self.check_interval = 1.0       # Base seconds between inventory checks
        self.jitter = random.uniform(0.0, 1.5)  # Random offset per account to desync
        self.last_check_time = 0.0
        self.items_identified = 0
        self.items_salvaged = 0
        self.items_extracted = 0
        self.items_kept = 0
        self.last_action = ""
        self.last_action_time = 0.0
        self.last_decision_log = []     # Recent keep/salvage decisions for UI
        self._evaluated_keep_ids = set()  # Track items already evaluated as KEEP (avoid re-processing)
        self._evaluated_keep_cleanup_time = 0.0  # Last time we pruned stale IDs from _evaluated_keep_ids
        self._salvage_pending = False     # True while salvage coroutine is running

        # --- Valuable items for master (cross-account loot distribution) ---
        self.valuable_items_for_master = []  # list of (item_id, rarity, reason, name) tuples
        self._valuable_flagged_ids = set()   # track already-flagged item IDs to avoid duplicates

        # --- Auto-Pickup settings ---
        self.auto_pickup = True         # Pick up nearby loot in explorable areas
        self.pickup_interval = 0.5      # Seconds between pickup attempts
        self.last_pickup_time = 0.0
        self.pickup_range = Range.Spellcast.value  # Max distance to pick up items
        self.pickup_whites_when_space = True  # Pick up whites only when bag space allows
        self.bag_space_reserve = 3      # Reserve this many slots for blue+ drops
        self.items_picked_up = 0

        # --- Auto-Merchant settings ---
        self.auto_merchant = True       # Auto-sell junk to merchant in outposts
        self.merchant_sell_whites = True  # Sell white weapons/armor
        self.merchant_sell_materials = False  # Sell common materials
        self.merchant_sell_keys = True  # Sell normal mode dungeon/explorable keys
        self.merchant_interval = 3.0    # Seconds between merchant sell attempts
        self.last_merchant_time = 0.0
        self.items_sold = 0

        # --- Auto-Restock settings ---
        self.auto_restock = True        # Auto-buy salvage/ID kits on outpost entry
        self.restock_min_kits = 2       # Buy kits when fewer than this many in inventory
        self.restock_buy_count = 2      # How many kits to buy per type when restocking
        self.restock_min_expert_kits = 1  # Buy expert kits when fewer than this many
        self.restock_buy_expert_count = 2 # How many expert kits to buy when restocking

config = LootConfig()
#endregion

# ---------------------------------------------------------------------------
# Wire up extracted modules with shared config and data tables
# ---------------------------------------------------------------------------
# SalvageDecision needs access to the config object (for config.expert_extract)
# and to the ItemModTables data (MOD constants + value tables).
SalvageDecision.config = config
SalvageDecision.install_tables(
    mod_requirement=MOD_REQUIREMENT,
    mod_requirement_2=MOD_REQUIREMENT_2,
    valuable_model_ids=VALUABLE_MODEL_IDS,
    valuable_skin_keywords=VALUABLE_SKIN_KEYWORDS,
    valuable_rune_keywords=VALUABLE_RUNE_KEYWORDS,
    valuable_insignia_keywords=VALUABLE_INSIGNIA_KEYWORDS,
    valuable_weapon_mods_purple=VALUABLE_WEAPON_MODS_PURPLE,
    valuable_weapon_mods_blue=VALUABLE_WEAPON_MODS_BLUE,
    valuable_shield_offhand_mods_purple=VALUABLE_SHIELD_OFFHAND_MODS_PURPLE,
    valuable_shield_offhand_mods_blue=VALUABLE_SHIELD_OFFHAND_MODS_BLUE,
    valuable_armor_runes=VALUABLE_ARMOR_RUNES,
)

#region Auto-Restock State
_restock_last_map_id = 0
_restock_was_explorable = False
_restock_running = False
_restock_status = ""
_restock_status_t = 0.0

RESTOCK_OUTPOST_SETTLE_MS = 12000  # Wait after entering outpost (12s — all clients must load first)
RESTOCK_INTERACT_WAIT_MS = 2000    # Wait for merchant window to populate
RESTOCK_MERCHANT_NAME = "[Merchant]"  # In-game display name for merchant NPCs

def _set_restock_status(msg):
    global _restock_status, _restock_status_t
    _restock_status = msg
    _restock_status_t = time.time()

def _count_kits_by_filter(filter_fn):
    """Count how many kit stacks match filter_fn in bags 1-4."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    all_items = ItemArray.GetItemArray(bags)
    count = 0
    for item_id in all_items:
        try:
            if filter_fn(item_id):
                count += 1
        except Exception:
            continue  # Item may have been consumed between array fetch and check
    return count

def _count_salvage_kits():
    """Count lesser/normal salvage kits in inventory."""
    return _count_kits_by_filter(
        lambda iid: Item.Usage.IsSalvageKit(iid) and Item.Usage.IsLesserKit(iid)
    )

def _count_id_kits():
    """Count ID kits in inventory."""
    return _count_kits_by_filter(lambda iid: Item.Usage.IsIDKit(iid))

def _count_expert_kits():
    """Count expert/perfect salvage kits in inventory."""
    return _count_kits_by_filter(
        lambda iid: Item.Usage.IsExpertSalvageKit(iid) or Item.Usage.IsPerfectSalvageKit(iid)
    )

XUNLAI_MODEL_ID = 5001  # Xunlai Chest NPC model ID

def _get_material_items():
    """Get all material item IDs from inventory bags 1-4 (common + rare)."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    all_items = ItemArray.GetItemArray(bags)
    materials = []
    for item_id in all_items:
        try:
            if Item.Type.IsMaterial(item_id) or Item.Type.IsRareMaterial(item_id):
                if Item.Usage.IsUsable(item_id):
                    continue  # Skip cons/pcons
                materials.append(int(item_id))
        except Exception:
            continue
    return materials

_pipeline_map_id = 0  # Track which map the pipeline started in
_pipeline_start_time = 0.0  # When the current pipeline was launched
_PIPELINE_TIMEOUT_S = 60  # Abort pipeline if it exceeds this duration

def _pipeline_still_valid():
    """Check if we're still in the same outpost the pipeline started in,
    and that the pipeline hasn't exceeded its timeout."""
    if time.time() - _pipeline_start_time > _PIPELINE_TIMEOUT_S:
        return False
    return (Map.IsOutpost() and not Map.IsMapLoading()
            and Map.IsMapReady() and Map.GetMapID() == _pipeline_map_id)

def _get_account_slot_index():
    """Get this account's index (0-5) among active accounts for stagger ordering."""
    try:
        my_email = Player.GetAccountEmail()
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData(sort_results=True)
        for i, acc in enumerate(all_accounts):
            try:
                if acc.AccountEmail == my_email and acc.IsAccount:
                    return i
            except Exception:
                continue
    except Exception:
        pass
    return random.randint(0, 5)

# Each account gets a different randomized task order to look like independent players
_PIPELINE_TASK_ORDERS = {
    # slot_index: [task_list] — different order per slot
    0: ["deposit", "merchant", "idle"],      # First account: chest first
    1: ["merchant", "deposit", "idle"],      # Second: merchant first
    2: ["idle", "deposit", "merchant"],      # Third: wait, then chest
    3: ["deposit", "idle", "merchant"],      # Fourth: chest, idle, merchant
    4: ["merchant", "idle", "deposit"],      # Fifth: merchant, idle, chest
    5: ["idle", "merchant", "deposit"],      # Sixth: idle first
}

def _pipeline_deposit(RoutineAgents):
    """Sub-task: walk to Xunlai Chest, deposit all materials."""
    xunlai_id = RoutineAgents.GetAgentIDByModelID(XUNLAI_MODEL_ID)
    if xunlai_id == 0:
        _set_restock_status("No Xunlai Chest found")
        return

    materials = _get_material_items()
    if not materials:
        _set_restock_status("No materials to deposit")
        return

    _set_restock_status(f"Walking to Xunlai ({len(materials)} materials)...")
    xx, xy = Agent.GetXY(xunlai_id)
    yield from Routines.Yield.Movement.FollowPath([(xx, xy)])
    yield from Routines.Yield.wait(random.randint(300, 800))

    if not _pipeline_still_valid():
        return

    _set_restock_status("Opening storage...")
    yield from Routines.Yield.Player.InteractAgent(xunlai_id)
    yield from Routines.Yield.wait(2000)

    if not _pipeline_still_valid():
        return

    if not Inventory.IsStorageOpen():
        Inventory.OpenXunlaiWindow()
        yield from Routines.Yield.wait(2000)

    if not _pipeline_still_valid():
        return

    if Inventory.IsStorageOpen():
        deposited = 0
        for item_id in materials:
            if not _pipeline_still_valid():
                break
            try:
                GLOBAL_CACHE.Inventory.DepositItemToStorage(item_id)
                deposited += 1
                yield from Routines.Yield.wait(random.randint(300, 600))
            except Exception:
                continue
        materials2 = _get_material_items()
        for item_id in materials2:
            if not _pipeline_still_valid():
                break
            try:
                GLOBAL_CACHE.Inventory.DepositItemToStorage(item_id)
                deposited += 1
                yield from Routines.Yield.wait(random.randint(300, 600))
            except Exception:
                continue
        _set_restock_status(f"Deposited {deposited} materials")
    else:
        _set_restock_status("Failed to open storage")


def _pipeline_merchant():
    """Sub-task: walk to Merchant, buy kits + sell junk."""
    salvage_count = _count_salvage_kits()
    id_count = _count_id_kits()
    need_salvage = max(0, config.restock_buy_count - salvage_count) if salvage_count < config.restock_min_kits else 0
    need_id = max(0, config.restock_buy_count - id_count) if id_count < config.restock_min_kits else 0

    junk_to_sell = _get_junk_items_to_sell(config) if config.auto_merchant else []
    has_junk = len(junk_to_sell) > 0

    if need_salvage == 0 and need_id == 0 and not has_junk:
        _set_restock_status(f"Kits OK (salv:{salvage_count} id:{id_count}), no junk")
        return

    merchant_id = Agent.GetAgentIDByName(RESTOCK_MERCHANT_NAME)
    if merchant_id == 0:
        _set_restock_status("No merchant found")
        return

    if not _pipeline_still_valid():
        return

    mx, my = Agent.GetXY(merchant_id)

    if Map.IsExplorable():
        has_no_kits = _count_salvage_kits() == 0 and _count_id_kits() == 0
        bags_full = Inventory.GetFreeSlotCount() <= 1
        if not (has_no_kits or bags_full):
            return
        if Utils.Distance(Player.GetXY(), (mx, my)) > Range.Earshot.value:
            return

    _set_restock_status("Walking to merchant...")
    yield from Routines.Yield.Movement.FollowPath([(mx, my)])
    yield from Routines.Yield.wait(random.randint(300, 800))

    if not _pipeline_still_valid():
        return

    _set_restock_status("Opening merchant...")
    yield from Routines.Yield.Player.InteractAgent(merchant_id)
    yield from Routines.Yield.wait(RESTOCK_INTERACT_WAIT_MS)

    if not _pipeline_still_valid():
        return

    offered = Trading.Merchant.GetOfferedItems()
    if not offered or len(offered) == 0:
        _set_restock_status("Merchant window failed")
        return

    if need_salvage > 0:
        _set_restock_status(f"Buying {need_salvage} salvage kit(s)...")
        yield from Routines.Yield.Merchant.BuySalvageKits(need_salvage)
        yield from Routines.Yield.wait(random.randint(400, 800))

    if not _pipeline_still_valid():
        return

    if need_id > 0:
        _set_restock_status(f"Buying {need_id} ID kit(s)...")
        yield from Routines.Yield.Merchant.BuyIDKits(need_id)
        yield from Routines.Yield.wait(random.randint(400, 800))

    final_salv = _count_salvage_kits()
    final_id = _count_id_kits()
    _set_restock_status(f"Done! salv:{final_salv} id:{final_id}")


def _co_outpost_pipeline():
    """Coroutine: anti-bot outpost pipeline — each account does tasks in different order.

    Each account gets a staggered start delay (2-8s per slot) and a randomized
    task order (deposit/merchant/idle) so all 6 clients look like independent
    players, not synchronized bots.

    CRITICAL: aborts immediately if map changes.
    """
    global _restock_running, _pipeline_map_id, _pipeline_start_time
    if _restock_running:
        return
    _restock_running = True
    _pipeline_map_id = Map.GetMapID()
    _pipeline_start_time = time.time()

    try:
        # Per-account stagger: slot 0 waits 8s, slot 1 waits 10s, etc.
        slot_index = _get_account_slot_index()
        stagger_ms = 8000 + (slot_index * random.randint(2000, 4000))
        _set_restock_status(f"Pipeline: waiting {stagger_ms//1000}s (slot {slot_index})...")
        yield from Routines.Yield.wait(stagger_ms)

        if not _pipeline_still_valid():
            _set_restock_status("Left outpost, aborting")
            return

        if not Player.IsPlayerLoaded():
            _set_restock_status("Player not loaded, aborting")
            return

        # Get this account's task order (different per slot for anti-bot)
        task_order = list(_PIPELINE_TASK_ORDERS.get(slot_index % 6, ["deposit", "merchant", "idle"]))
        # Add extra randomization so it's not always the same pattern
        if random.random() < 0.3:
            random.shuffle(task_order)

        # === Execute tasks in per-account randomized order ===
        from Py4GWCoreLib.routines_src.Agents import Agents as RoutineAgents

        for task in task_order:
            if not _pipeline_still_valid():
                return

            if task == "idle":
                # Random idle pause (2-5s) — looks like player AFK or reading chat
                idle_ms = random.randint(2000, 5000)
                _set_restock_status(f"Idle ({idle_ms//1000}s)...")
                yield from Routines.Yield.wait(idle_ms)
                continue

            if task == "deposit":
                yield from _pipeline_deposit(RoutineAgents)
                continue

            if task == "merchant":
                yield from _pipeline_merchant()
                continue

        _set_restock_status("Pipeline complete")
        Py4GW.Console.Log(MODULE_NAME, f"[Pipeline] Done (slot {slot_index})", Py4GW.Console.MessageType.Info)

    except Exception as e:
        _set_restock_status(f"Restock error: {e}")
        Py4GW.Console.Log(MODULE_NAME, f"Restock error: {e}", Py4GW.Console.MessageType.Warning)
        Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Warning)
    finally:
        elapsed = time.time() - _pipeline_start_time
        if elapsed > _PIPELINE_TIMEOUT_S:
            Py4GW.Console.Log(MODULE_NAME, f"[Pipeline] Aborted: timeout after {int(elapsed)}s", Py4GW.Console.MessageType.Warning)
            _set_restock_status(f"Pipeline timeout ({int(elapsed)}s)")
        _restock_running = False

def _check_restock_on_entry():
    """Detect explorable -> outpost transition and trigger outpost pipeline coroutine.
    Pipeline handles: deposit materials, buy kits, and sell junk."""
    global _restock_last_map_id, _restock_was_explorable

    if not Map.IsMapReady() or Map.IsMapLoading():
        return

    current_map = Map.GetMapID()
    is_outpost = Map.IsOutpost()
    is_explorable = Map.IsExplorable()

    # Detect: any outpost entry where map changed
    # ALL accounts run the pipeline, but slaves get a 30s hard deadline
    if is_outpost and current_map != _restock_last_map_id:
        if (config.auto_restock or config.auto_merchant) and not _restock_running:
            GLOBAL_CACHE.Coroutines.append(_co_outpost_pipeline())

    _restock_last_map_id = current_map
    _restock_was_explorable = is_explorable
#endregion

#region Processing Loop

_salvage_coroutines = GLOBAL_CACHE.Coroutines

# Strategy constants for HandleSalvageChoiceDialog:
# 0 = prefer crafting materials, 1 = prefer upgrades/mods
STRATEGY_MATERIALS = 0
STRATEGY_UPGRADES = 1

def _co_safe_salvage(item_id, kit_id, strategy, require_materials_confirm):
    """Coroutine: salvage an item and handle ALL dialogs properly.

    Matches the framework's AutoInventoryHandler.SalvageItems pattern exactly.
    Three possible flows depending on kit type and item rarity:

    A) Lesser kit on white junk:
       SalvageItem -> wait for item consumption -> done

    B) Lesser kit on purple (SALVAGE_MATS):
       SalvageItem -> "are you sure?" materials confirmation -> AcceptSalvageMaterialsWindow
       -> wait for item consumption -> done

    C) Expert kit on blue/purple with mods (EXTRACT):
       SalvageItem -> Salvage Choice Dialog (scan options, pick upgrade vs materials based
       on strategy) -> click option -> click Salvage -> possibly materials warning -> done

    strategy=0: prefer crafting materials (for SALVAGE_MATS / junk)
    strategy=1: prefer upgrades/inscriptions (for EXTRACT)
    require_materials_confirm: True for purple/gold items with lesser kits
    """
    from Py4GWCoreLib.enums import Bags

    try:
        # Abort if map is loading or player not loaded
        if not Player.IsPlayerLoaded() or Map.IsMapLoading():
            return

        # Queue the salvage action and wait for it to start processing
        ActionQueueManager().AddAction("ACTION", Inventory.SalvageItem, item_id, kit_id)
        yield from Routines.Yield.wait(250)

        # Flow B: Purple/gold with lesser kit -> handle "are you sure?" materials confirmation
        if require_materials_confirm:
            yield from Routines.Yield.wait(150)
            found_confirm = yield from Routines.Yield.Items._wait_for_salvage_materials_window(
                timeout_ms=1500,
                poll_ms=50,
                initial_wait_ms=0,
            )
            if not found_confirm:
                # Timed out -- salvage may have failed or item doesn't need confirm
                yield from Routines.Yield.wait(100)
            else:
                for _ in range(3):
                    ActionQueueManager().AddAction("ACTION", Inventory.AcceptSalvageMaterialsWindow)
                    yield from Routines.Yield.wait(50)

        # Wait for salvage to complete, handling choice dialog if it appears (Flow C)
        waited_ms = 0
        max_wait_ms = 10000
        while waited_ms < max_wait_ms:
            # Abort if map changes during salvage
            if Map.IsMapLoading() or not Map.IsMapReady():
                break

            # Flow C: Handle salvage choice dialog (expert kit on modded items)
            dialog_status = yield from Inventory.HandleSalvageChoiceDialog(
                auto_handle=True,
                strategy=strategy,
                auto_confirm_materials_warning=True,
                queue_name="ACTION",
                log_module="AutoLoot",
                queue_wait_timeout_ms=5000,
                poll_ms=50,
                close_timeout_ms=1500,
                debug_enabled=False,
                item_id=item_id,
            )

            if dialog_status == "handled":
                waited_ms = 0
                continue

            if dialog_status not in {"not_visible", "disabled", "confirm_pending"}:
                break

            yield from Routines.Yield.wait(50)
            waited_ms += 50

            # Check if item is consumed (salvage complete)
            try:
                bag_list = ItemArray.CreateBagList(Bags.Backpack, Bags.BeltPouch, Bags.Bag1, Bags.Bag2)
                remaining = ItemArray.GetItemArray(bag_list)
                if item_id not in remaining:
                    break  # Item consumed -- done
            except Exception:
                break  # Can't check inventory -- bail safely

            # Safety timeout for items that don't trigger any dialog
            # (white junk with lesser kit -- Flow A)
            if waited_ms >= 3000:
                break

        yield from Routines.Yield.wait(100)

    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Salvage coroutine error: {e}", Py4GW.Console.MessageType.Warning)
    finally:
        config._salvage_pending = False


def _safe_salvage(item_id, kit_id, strategy=STRATEGY_MATERIALS, require_materials_confirm=False):
    """Launch a coroutine to salvage item and handle all dialogs.

    strategy: STRATEGY_MATERIALS (0) for lesser kit / junk salvage
              STRATEGY_UPGRADES (1) for expert kit / mod extraction
    require_materials_confirm: True for purple/gold items (triggers "are you sure?" dialog)
    """
    config._salvage_pending = True
    _salvage_coroutines.append(_co_safe_salvage(item_id, kit_id, strategy, require_materials_confirm))


def _handle_salvage_pending():
    """Check if a salvage coroutine is still running. Returns True if busy."""
    return config._salvage_pending


def _flag_valuable_for_master(item_id, rarity, reason):
    """Flag an item as valuable for transfer to the master account.
    Only flags once per item_id to avoid duplicates."""
    if item_id in config._valuable_flagged_ids:
        return
    config._valuable_flagged_ids.add(item_id)
    name = _item_name(item_id)
    config.valuable_items_for_master.append((item_id, rarity, reason, name))
    Py4GW.Console.Log(MODULE_NAME,
        f"Flagged for master: [{rarity}] {name} ({reason})",
        Py4GW.Console.MessageType.Info)


def _cleanup_valuable_items():
    """Remove flagged items that are no longer in inventory (sold, salvaged, etc)."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    current_items = set(ItemArray.GetItemArray(bags))
    config.valuable_items_for_master = [
        entry for entry in config.valuable_items_for_master
        if entry[0] in current_items
    ]
    config._valuable_flagged_ids &= current_items


def process_inventory():
    """Main inventory processing loop. Called periodically."""
    now = time.time()

    # Handle pending salvage confirmation first
    if _handle_salvage_pending():
        return

    if now - config.last_check_time < config.check_interval + config.jitter:
        return
    config.last_check_time = now
    config.jitter = random.uniform(0.0, 0.5)  # Re-roll jitter each cycle

    if not Player.IsPlayerLoaded():
        return
    if Map.IsMapLoading() or not Map.IsMapReady():
        config._evaluated_keep_ids.clear()  # Reset on map load -- item IDs change
        config.valuable_items_for_master.clear()  # Reset on map load -- item IDs change
        config._valuable_flagged_ids.clear()
        return

    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    items = ItemArray.GetItemArray(bags)

    # Periodic cleanup: remove stale IDs from _evaluated_keep_ids every 30s.
    if now - config._evaluated_keep_cleanup_time >= 30.0:
        config._evaluated_keep_cleanup_time = now
        current_ids = set(items)
        config._evaluated_keep_ids &= current_ids
        _cleanup_valuable_items()

    # Step 1: Identify unidentified blue/purple/gold items
    if config.auto_identify:
        for item_id in items:
            if Item.Usage.IsIdentified(item_id):
                continue
            is_blue = Item.Rarity.IsBlue(item_id)
            is_purple = Item.Rarity.IsPurple(item_id)
            is_gold = Item.Rarity.IsGold(item_id)
            if not (is_blue or is_purple or is_gold):
                continue

            id_kit = Inventory.GetFirstIDKit()
            if id_kit == 0:
                if _try_pickup_ground_kit(_is_id_kit):
                    config.last_action = "Picked up shared ID kit!"
                    config.last_action_time = now
                    return
                _request_kit_from_team(SharedCommandType.ShareIDKit)
                config.last_action = "No ID kit! Requesting from team..."
                config.last_action_time = now
                return

            ActionQueueManager().AddAction("ACTION", Inventory.IdentifyItem, item_id, id_kit)
            config.items_identified += 1
            try:
                name = Item.GetName(item_id) if Item.IsNameReady(item_id) else f"Item {item_id}"
            except Exception:
                name = f"Item {item_id}"
            rarity = "purple" if is_purple else ("gold" if is_gold else "blue")
            config.last_action = f"ID [{rarity}]: {name}"
            config.last_action_time = now
            return  # One action per cycle

    # Step 1.5: Flag gold/green items for master (cross-account loot distribution)
    for item_id in items:
        if Item.Rarity.IsGold(item_id) and (Item.Type.IsWeapon(item_id) or Item.Type.IsArmor(item_id)):
            _flag_valuable_for_master(item_id, "gold", "gold weapon/armor")
        elif Item.Rarity.IsGreen(item_id):
            _flag_valuable_for_master(item_id, "green", "green item")

    # Step 2: Process salvageable items -- smart salvage decisions
    if config.auto_salvage_white or config.auto_salvage_smart:
        for item_id in items:
            if not Item.Usage.IsSalvageable(item_id):
                continue

            # Determine item category
            is_salvage_type = _is_salvage_type(item_id)  # Junk drops (broken armor, remnants)
            is_weapon = Item.Type.IsWeapon(item_id)       # Actual weapons/shields/foci
            is_armor = Item.Type.IsArmor(item_id)         # Actual equippable armor

            # Only process: salvage items, weapons, armor
            if not is_salvage_type and not is_weapon and not is_armor:
                continue

            # Green: skip always
            if Item.Rarity.IsGreen(item_id):
                continue

            # Gold: smart evaluation if enabled, otherwise skip
            if Item.Rarity.IsGold(item_id):
                if not config.auto_salvage_gold:
                    continue
                if not Item.Usage.IsIdentified(item_id):
                    continue  # Must be identified first
                if item_id in config._evaluated_keep_ids:
                    continue

                decision, reason = evaluate_item(item_id)
                _log_decision(item_id, decision, reason)

                if decision == DECISION_KEEP:
                    config.items_kept += 1
                    config._evaluated_keep_ids.add(item_id)
                    continue

                elif decision == DECISION_EXTRACT:
                    name = _item_name(item_id)
                    kit = get_expert_kit()
                    if kit == 0:
                        if _try_pickup_ground_kit(_is_expert_kit):
                            config.last_action = "Picked up shared expert kit!"
                            config.last_action_time = now
                            return
                        _request_kit_from_team(SharedCommandType.ShareExpertKit)
                        config.last_action = f"No expert kit! KEEPING gold: {name}"
                        config.last_action_time = now
                        config._evaluated_keep_ids.add(item_id)
                        continue
                    else:
                        _safe_salvage(item_id, kit, STRATEGY_UPGRADES, require_materials_confirm=False)
                        config.items_extracted += 1
                        config.last_action = f"GOLD EXTRACT: {name} ({reason})"
                    config.last_action_time = now
                    return

                elif decision == DECISION_SALVAGE_MATS:
                    name = _item_name(item_id)
                    kit = get_lesser_kit()
                    if kit == 0:
                        if _try_pickup_ground_kit(_is_lesser_kit):
                            config.last_action = "Picked up shared kit!"
                            config.last_action_time = now
                            return
                        _request_kit_from_team(SharedCommandType.ShareSalvageKit)
                        config.last_action = "No lesser kit! Requesting..."
                        config.last_action_time = now
                        return
                    _safe_salvage(item_id, kit, STRATEGY_MATERIALS, require_materials_confirm=True)
                    config.items_salvaged += 1
                    config.last_action = f"GOLD SALVAGE: {name} ({reason})"
                    config.last_action_time = now
                    return

                continue  # Default: skip if evaluation returned unexpected value

            # White Salvage-type items: always lesser salvage for materials
            if Item.Rarity.IsWhite(item_id) and config.auto_salvage_white and is_salvage_type:
                kit = get_lesser_kit()
                if kit == 0:
                    if _try_pickup_ground_kit(_is_lesser_kit):
                        config.last_action = "Picked up shared kit!"
                        config.last_action_time = now
                        return
                    _request_kit_from_team(SharedCommandType.ShareSalvageKit)
                    config.last_action = "No lesser kit! Requesting from team..."
                    config.last_action_time = now
                    return
                name = _item_name(item_id)
                _safe_salvage(item_id, kit)
                config.items_salvaged += 1
                config.last_action = f"Salvage junk: {name}"
                config.last_action_time = now
                return

            # White actual weapons/armor: skip (sell to merchant or keep, never salvage)
            if Item.Rarity.IsWhite(item_id):
                continue

            # Blue/Purple: smart evaluation
            if config.auto_salvage_smart and (Item.Rarity.IsBlue(item_id) or Item.Rarity.IsPurple(item_id)):
                if not Item.Usage.IsIdentified(item_id):
                    continue  # Wait for identification

                # Skip items already evaluated as KEEP (avoid re-processing every cycle)
                if item_id in config._evaluated_keep_ids:
                    continue

                decision, reason = evaluate_item(item_id)
                _log_decision(item_id, decision, reason)

                if decision == DECISION_KEEP:
                    config.items_kept += 1
                    config._evaluated_keep_ids.add(item_id)
                    rarity = "purple" if Item.Rarity.IsPurple(item_id) else "blue"
                    _flag_valuable_for_master(item_id, rarity, reason)
                    continue  # Skip this item permanently

                elif decision == DECISION_EXTRACT:
                    name = _item_name(item_id)
                    kit = get_expert_kit()
                    if kit == 0:
                        if _try_pickup_ground_kit(_is_expert_kit):
                            config.last_action = "Picked up shared expert kit!"
                            config.last_action_time = now
                            return
                        _request_kit_from_team(SharedCommandType.ShareExpertKit)
                        config.last_action = f"No expert kit! Requesting from team. KEEPING: {name}"
                        config.last_action_time = now
                        config._evaluated_keep_ids.add(item_id)
                        continue
                    else:
                        _safe_salvage(item_id, kit, STRATEGY_UPGRADES, require_materials_confirm=False)
                        config.items_extracted += 1
                        config.last_action = f"EXTRACT: {name} ({reason})"
                    config.last_action_time = now
                    return

                elif decision == DECISION_SALVAGE_MATS:
                    name = _item_name(item_id)
                    kit = get_lesser_kit()
                    if kit == 0:
                        if _try_pickup_ground_kit(_is_lesser_kit):
                            config.last_action = "Picked up shared kit!"
                            config.last_action_time = now
                            return
                        _request_kit_from_team(SharedCommandType.ShareSalvageKit)
                        config.last_action = "No lesser kit! Requesting from team..."
                        config.last_action_time = now
                        return
                    is_purple = Item.Rarity.IsPurple(item_id)
                    _safe_salvage(item_id, kit, STRATEGY_MATERIALS, require_materials_confirm=is_purple)
                    config.items_salvaged += 1
                    config.last_action = f"Salvage: {name} ({reason})"
                    config.last_action_time = now
                    return


def _item_name(item_id):
    try:
        return Item.GetName(item_id) if Item.IsNameReady(item_id) else f"Item {item_id}"
    except Exception:
        return f"Item {item_id}"


def _log_decision(item_id, decision, reason):
    """Log a keep/salvage/extract decision for the UI."""
    name = _item_name(item_id)
    entry = f"[{decision}] {name}: {reason}"
    config.last_decision_log.append(entry)
    if len(config.last_decision_log) > 15:
        config.last_decision_log.pop(0)
#endregion

#region UI
def draw_window():
    if not Player.IsPlayerLoaded():
        return

    if PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.AlwaysAutoResize):
        config.enabled = PyImGui.checkbox("Enabled", config.enabled)

        if config.enabled:
            # --- Prominent BAGS FULL warning (< reserve slots free) ---
            try:
                _warn_free = get_free_slot_count()
                if _warn_free == 0:
                    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.1, 0.1, 1.0))
                    PyImGui.text("!! BAGS FULL !! -- Drop/sell items or visit merchant")
                    PyImGui.pop_style_color(1)
                elif _warn_free < config.bag_space_reserve:
                    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.3, 0.3, 1.0))
                    PyImGui.text(f"!! BAGS FULL !! -- {_warn_free} slots left (reserve={config.bag_space_reserve})")
                    PyImGui.pop_style_color(1)
            except Exception:
                pass  # Inventory API not ready -- skip warning safely

            # --- Identify & Salvage section ---
            PyImGui.separator()
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.85, 0.4, 1.0))
            PyImGui.text("-- Identify & Salvage --")
            PyImGui.pop_style_color(1)
            config.auto_identify = PyImGui.checkbox("Auto-Identify (Blue/Purple/Gold)", config.auto_identify)
            config.auto_salvage_white = PyImGui.checkbox("Salvage White Junk (lesser kit)", config.auto_salvage_white)
            config.auto_salvage_smart = PyImGui.checkbox("Smart Salvage Blue/Purple", config.auto_salvage_smart)
            config.expert_extract = PyImGui.checkbox("Expert Extract Upgrades", config.expert_extract)
            # Gold/Green always kept -- hardcoded safety, no toggle exposed
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.5, 0.5, 0.5, 1.0))
            PyImGui.text("  Gold/Green: ALWAYS KEPT (safety lock)")
            PyImGui.pop_style_color(1)

            # --- Auto-Pickup section ---
            PyImGui.separator()
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.4, 1.0, 0.6, 1.0))
            PyImGui.text("-- Auto-Pickup (Explorable) --")
            PyImGui.pop_style_color(1)
            config.auto_pickup = PyImGui.checkbox("Auto-Pickup Loot", config.auto_pickup)
            if config.auto_pickup:
                config.pickup_whites_when_space = PyImGui.checkbox("Pick Up Whites (when space)", config.pickup_whites_when_space)
                config.bag_space_reserve = int(PyImGui.slider_int("Reserve Slots for Blue+", config.bag_space_reserve, 0, 10))

            # --- Auto-Merchant section ---
            PyImGui.separator()
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.6, 0.8, 1.0, 1.0))
            PyImGui.text("-- Auto-Merchant (Outpost) --")
            PyImGui.pop_style_color(1)
            config.auto_merchant = PyImGui.checkbox("Auto-Sell Junk at Merchant", config.auto_merchant)
            if config.auto_merchant:
                config.merchant_sell_whites = PyImGui.checkbox("Sell White Salvage Items", config.merchant_sell_whites)
                config.merchant_sell_materials = PyImGui.checkbox("Sell Common Materials", config.merchant_sell_materials)
                config.merchant_sell_keys = PyImGui.checkbox("Sell Normal Mode Keys", config.merchant_sell_keys)

            # --- Auto-Restock section ---
            PyImGui.separator()
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.9, 0.6, 1.0, 1.0))
            PyImGui.text("-- Auto-Restock Kits (Outpost) --")
            PyImGui.pop_style_color(1)
            config.auto_restock = PyImGui.checkbox("Auto-Buy Kits on Outpost Entry", config.auto_restock)
            if config.auto_restock:
                salv_n = _count_salvage_kits()
                id_n = _count_id_kits()
                PyImGui.text(f"  Salvage kits: {salv_n}  |  ID kits: {id_n}")
                if _restock_running and _restock_status:
                    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 1.0, 0.7, 1.0))
                    PyImGui.text(f"  {_restock_status}")
                    PyImGui.pop_style_color(1)
                elif _restock_status and (time.time() - _restock_status_t) < 15:
                    PyImGui.text(f"  {_restock_status}")

            # --- Stats ---
            PyImGui.separator()
            PyImGui.text(
                f"ID: {config.items_identified}  Salv: {config.items_salvaged}  "
                f"Ext: {config.items_extracted}  Kept: {config.items_kept}  "
                f"Pick: {config.items_picked_up}  Sold: {config.items_sold}"
            )

            try:
                used, total = GLOBAL_CACHE.Inventory.GetInventorySpace()
                free = max(total - used, 0)
            except Exception:
                free = get_free_slot_count()
                total = 0
            if free == 0:
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.2, 0.2, 1.0))
                if total > 0:
                    PyImGui.text(f"  Bag slots: {free}/{total} free -- FULL!")
                else:
                    PyImGui.text(f"  Bag space: FULL!")
                PyImGui.pop_style_color(1)
            elif free <= config.bag_space_reserve:
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.6, 0.3, 1.0))
                if total > 0:
                    PyImGui.text(f"  Bag slots: {free}/{total} free (LOW)")
                else:
                    PyImGui.text(f"  Bag space: {free} free (LOW)")
                PyImGui.pop_style_color(1)
            else:
                if total > 0:
                    PyImGui.text(f"  Bag slots: {free}/{total} free")
                else:
                    PyImGui.text(f"  Bag space: {free} free")

            if config.last_action and (time.time() - config.last_action_time) < 8:
                is_error = "No " in config.last_action or "ERROR" in config.last_action
                if is_error:
                    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.3, 0.3, 1.0))
                else:
                    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 1.0, 0.7, 1.0))
                PyImGui.text(f" {config.last_action}")
                PyImGui.pop_style_color(1)

            # Show recent decisions
            if config.last_decision_log:
                PyImGui.separator()
                PyImGui.text("Recent decisions:")
                for entry in config.last_decision_log[-6:]:
                    if entry.startswith(f"[{DECISION_KEEP}]"):
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 0.8, 1.0, 1.0))
                    elif entry.startswith(f"[{DECISION_EXTRACT}]"):
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.9, 0.2, 1.0))
                    else:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.6, 0.3, 1.0))
                    PyImGui.text(f"  {entry}")
                    PyImGui.pop_style_color(1)

            # --- Valuable items for master (cross-account transfer) ---
            if config.valuable_items_for_master:
                PyImGui.separator()
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.85, 0.0, 1.0))
                PyImGui.text(f"-- Valuable Items: {len(config.valuable_items_for_master)} for master --")
                PyImGui.pop_style_color(1)
                for item_id, rarity, reason, name in config.valuable_items_for_master[:10]:
                    if rarity == "gold":
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.84, 0.0, 1.0))
                    elif rarity == "green":
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.0, 0.9, 0.2, 1.0))
                    elif rarity == "purple":
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.75, 0.4, 1.0, 1.0))
                    else:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 0.6, 1.0, 1.0))
                    PyImGui.text(f"  [{rarity}] {name}: {reason}")
                    PyImGui.pop_style_color(1)
                if len(config.valuable_items_for_master) > 10:
                    PyImGui.text(f"  ... and {len(config.valuable_items_for_master) - 10} more")

    PyImGui.end()
#endregion

#region Entry Points
_pipeline_logged = False
_widgets_auto_enabled = False

def _auto_enable_sibling_widgets():
    global _widgets_auto_enabled
    if _widgets_auto_enabled:
        return
    if not Player.IsPlayerLoaded() or Map.IsMapLoading():
        return
    _widgets_auto_enabled = True
    try:
        from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import get_widget_handler
        wh = get_widget_handler()
        for name in ["Blessed", "AutoStore"]:
            if not wh.is_widget_enabled(name):
                wh.enable_widget(name)
                Py4GW.Console.Log(MODULE_NAME, f"Auto-enabled widget: {name}", Py4GW.Console.MessageType.Info)
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Auto-enable failed: {e}", Py4GW.Console.MessageType.Warning)

def main():
    global _pipeline_logged
    _auto_enable_sibling_widgets()
    try:
        if not _pipeline_logged and Player.IsPlayerLoaded():
            Py4GW.Console.Log(MODULE_NAME,
                "[Pipeline] Autonomous outpost pipeline active: "
                "restock -> identify -> evaluate -> salvage/extract -> merchant sell. "
                "Material deposit handled by AutoStore widget.",
                Py4GW.Console.MessageType.Info)
            _pipeline_logged = True

        # Restock detection runs every frame (lightweight map-change check)
        _check_restock_on_entry()
        draw_window()
        if config.enabled:
            # Auto-pickup runs in explorable areas (fast interval)
            if config.auto_pickup:
                process_pickup(config)
            # Inventory full handling -- drop whites to make room for better loot
            if config.auto_pickup:
                process_inventory_full(config)
            # Identify + salvage runs everywhere
            process_inventory()
            # Auto-merchant runs in outposts when merchant window is open
            if config.auto_merchant:
                process_merchant(config)
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error)
        Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)

def configure():
    PyImGui.text("Auto Loot Manager -- Full Loot Pipeline")
    PyImGui.text("")
    PyImGui.text("Identifies blue/purple items, reads req + mods,")
    PyImGui.text("then decides: KEEP / EXTRACT upgrade / SALVAGE for mats.")
    PyImGui.text("")
    PyImGui.text("KEEP: low req (<=9), valuable mods, rare skins, gold/green")
    PyImGui.text("EXTRACT: valuable runes/insignias/mods (expert kit)")
    PyImGui.text("SALVAGE: junk salvage items for materials (lesser kit)")
    PyImGui.text("")
    PyImGui.text("Blues: only extract on PERFECT (max) rolls.")
    PyImGui.text("Purples: extract on near-max valuable mods.")
    PyImGui.text("White weapons/armor: NEVER salvaged or sold -- kept.")
    PyImGui.text("White salvage items (junk drops): auto-salvaged.")
    PyImGui.text("")
    PyImGui.text("PICKUP: Auto-picks loot from ground in explorable areas")
    PyImGui.text("  Blue/Purple/Gold/Green/Mats/Tomes: always pick up")
    PyImGui.text("  White equip: only when bag space allows")
    PyImGui.text("")
    PyImGui.text("MERCHANT: Sells white salvage items at merchant")
    PyImGui.text("")
    PyImGui.text("RESTOCK: Auto-buys salvage/ID kits on outpost entry")
    PyImGui.text("  Triggers once per explorable->outpost transition")
    PyImGui.text("  Walks to nearest [Merchant], buys kits if < 2")
#endregion
