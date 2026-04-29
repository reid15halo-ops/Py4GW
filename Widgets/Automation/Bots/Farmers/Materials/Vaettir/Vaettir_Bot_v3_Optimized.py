"""
Vaettir Bot v3.0 Optimized
===========================
Solo A/Me Vaettir farm in Jaga Moraine using Yield/Coroutine pattern.
Combines best features from v2.3.3 (stats/UI), VaettirBot 3.0 (paths),
and Apo's YAVB (yield architecture + GLOBAL_CACHE).

Architecture: Yield/Coroutine via GLOBAL_CACHE.Coroutines
- RunBotLogic(): main farm loop coroutine
- SkillHandler(): parallel skill casting coroutine
- main(): per-frame entry point (DrawWindow + coroutine stepping + stuck detection)

Requirements: A/Me with OwVUI2h5lPP8Id2BkAiAvpLBTAA or D/A with Ogej8xpDLT8I6MHQEQIQjbjXihA
"""

import Py4GW
from Py4GWCoreLib import *
from Py4GWCoreLib import (
    GLOBAL_CACHE, Agent, AgentArray, ItemArray,
    Routines, Range, Map, Player,
    Timer, ImGui, PyImGui,
    ConsoleLog, ModelID, TitleID,
    LootConfig, VectorFields, Color, Utils
)
from typing import List, Tuple
import random

MODULE_NAME = "Vaettir Bot v3.0"

# ═══════════════════════════════════════════════════════════
# CONSTANTS (module-level, created once)
# ═══════════════════════════════════════════════════════════

LONGEYES_LEDGE = 650
BJORA_MARCHES = 482
JAGA_MORAINE = 546

MATERIAL_MODELS = frozenset({
    ModelID.Wood_Plank.value, ModelID.Iron_Ingot.value,
    ModelID.Pile_Of_Glittering_Dust.value, ModelID.Bone.value,
    ModelID.Bolt_Of_Cloth.value, ModelID.Granite_Slab.value,
})

KIT_MODELS = frozenset({
    ModelID.Salvage_Kit.value, ModelID.Superior_Identification_Kit.value,
})

EVENT_MODELS = frozenset({
    ModelID.Hard_Apple_Cider.value, ModelID.Slice_Of_Pumpkin_Pie.value,
})

MAP_PIECE_MODELS = frozenset({
    ModelID.Map_Piece_Top_Left.value, ModelID.Map_Piece_Top_Right.value,
    ModelID.Map_Piece_Bottom_Left.value, ModelID.Map_Piece_Bottom_Right.value,
})

BLACK_DYE_COLOR = 10
WHITE_DYE_COLOR = 12

# ═══════════════════════════════════════════════════════════
# PATHS (balanced waypoint density from VaettirBot 3.0)
# ═══════════════════════════════════════════════════════════

PATH_LEAVE_OUTPOST: List[Tuple[float, float]] = [
    (-24380, 15074), (-26375, 16180)
]

PATH_TRAVERSE_BJORA: List[Tuple[float, float]] = [
    (17810, -17649), (16582, -17136), (15257, -16568), (14084, -15748), (12940, -14873),
    (11790, -14004), (10640, -13136), (9404, -12411), (8677, -11176), (8581, -9742),
    (7892, -8494), (6989, -7377), (6184, -6180), (5384, -4980), (4549, -3809),
    (3622, -2710), (2601, -1694), (1185, -1535), (-251, -1514), (-1690, -1626),
    (-3122, -1771), (-4556, -1752), (-5809, -1109), (-6966, -291), (-8390, -142),
    (-9831, -138), (-11272, -156), (-12685, -198), (-13933, 267), (-14914, 1325),
    (-15822, 2441), (-16917, 3375), (-18048, 4223), (-19196, 4986), (-20000, 5595),
    (-20300, 5600)
]

PATH_TO_BOUNTY_NPC: List[Tuple[float, float]] = [(13367, -20771)]

PATH_AGGRO_LEFT: List[Tuple[float, float]] = [
    (12496, -22600), (11375, -22761), (10925, -23466), (10917, -24311), (9910, -24599),
    (8995, -23177), (8307, -23187), (8213, -22829), (8307, -23187), (8213, -22829),
    (8740, -22475), (8880, -21384), (8684, -20833), (9665, -20415)
]

PATH_AGGRO_RIGHT: List[Tuple[float, float]] = [
    (10196, -20124), (9976, -18338), (11316, -18056), (10392, -17512), (10114, -16948),
    (10729, -16273), (10810, -15058), (11120, -15105), (11670, -15457), (12604, -15320),
    (12476, -16157)
]

# Ball compression — decreasing tolerance pulls enemies tight
PATH_TO_KILL_SPOT: List[Tuple[float, float]] = [
    (12890, -16450), (12920, -17032), (12847, -17136),
    (12720, -17222), (12617, -17273), (12518, -17305), (12445, -17327)
]

PATH_EXIT_JAGA: List[Tuple[float, float]] = [
    (12289, -17700), (13970, -18920), (15400, -20400), (15850, -20550)
]

PATH_RETURN_JAGA: List[Tuple[float, float]] = [(-20300, 5600)]

# ═══════════════════════════════════════════════════════════
# CONFIGURATION CLASSES
# ═══════════════════════════════════════════════════════════

class LootSettings:
    def __init__(self):
        self.loot_whites = True
        self.loot_blues = True
        self.loot_purples = True
        self.loot_golds = True
        self.loot_tomes = False
        self.loot_white_dyes = True
        self.loot_black_dyes = True
        self.loot_lockpicks = True
        self.loot_dyes = False
        self.loot_glacial_stones = True
        self.loot_event_items = True
        self.loot_map_pieces = False

class SalvageSettings:
    def __init__(self):
        self.salvage_whites = True
        self.salvage_blues = True
        self.salvage_purples = True
        self.salvage_golds = False
        self.salvage_glacial_stones = False

class SellSettings:
    def __init__(self):
        self.sell_materials = True
        self.sell_wood = True
        self.sell_iron = True
        self.sell_dust = True
        self.sell_bones = True
        self.sell_cloth = True
        self.sell_granite = True

class IDSettings:
    def __init__(self):
        self.id_blues = True
        self.id_purples = True
        self.id_golds = True

class InventorySettings:
    def __init__(self):
        self.leave_free_slots = 3
        self.keep_id_kits = 2
        self.keep_salvage_kits = 5
        self.keep_gold_amount = 5000

# ═══════════════════════════════════════════════════════════
# STATISTICS
# ═══════════════════════════════════════════════════════════

class RunStatistics:
    def __init__(self):
        self.global_timer = Timer()
        self.lap_timer = Timer()
        self.lap_history = []
        self.min_time = 0
        self.max_time = 0
        self.avg_time = 0.0
        self.runs_attempted = 0
        self.runs_completed = 0
        self.deaths = 0
        self.kills = 0
        self.left_alive = 0

    @property
    def success_rate(self):
        return self.runs_completed / self.runs_attempted if self.runs_attempted > 0 else 0.0

    def record_lap(self):
        lap = self.lap_timer.GetElapsedTime()
        self.lap_timer.Stop()
        self.lap_history.append(lap)
        self.min_time = min(self.lap_history)
        self.max_time = max(self.lap_history)
        self.avg_time = sum(self.lap_history) / len(self.lap_history)

# ═══════════════════════════════════════════════════════════
# SKILL ID CACHE
# ═══════════════════════════════════════════════════════════

class SkillIDs:
    def __init__(self):
        self.deadly_paradox = 0
        self.shadow_form = 0
        self.shroud_of_distress = 0
        self.way_of_perfection = 0
        self.heart_of_shadow = 0
        self.wastrels_demise = 0   # Wastrel's Demise (NOT Worry)
        self.arcane_echo = 0
        self.channeling = 0

    def load(self):
        self.deadly_paradox = GLOBAL_CACHE.Skill.GetID("Deadly_Paradox")
        self.shadow_form = GLOBAL_CACHE.Skill.GetID("Shadow_Form")
        self.shroud_of_distress = GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress")
        self.way_of_perfection = GLOBAL_CACHE.Skill.GetID("Way_of_Perfection")
        self.heart_of_shadow = GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow")
        self.wastrels_demise = GLOBAL_CACHE.Skill.GetID("Wastrel's_Demise")
        self.arcane_echo = GLOBAL_CACHE.Skill.GetID("Arcane_Echo")
        self.channeling = GLOBAL_CACHE.Skill.GetID("Channeling")
        return self.shadow_form != 0

# ═══════════════════════════════════════════════════════════
# BOT STATE (single global instance)
# ═══════════════════════════════════════════════════════════

class BotState:
    def __init__(self):
        self.is_running = False
        self.in_killing_routine = False
        self.pause_stuck_routine = False
        self.finished_routine = False
        self.reset_from_jaga = False
        self.log_to_console = True
        self.current_step = "Idle"

        # Stuck detection
        self.stuck_count = 0
        self.auto_stuck_timer = Timer()
        self.auto_stuck_timer.Start()
        self.non_movement_timer = Timer()
        self.old_player_x = 0.0
        self.old_player_y = 0.0

        # Config
        self.loot = LootSettings()
        self.salvage = SalvageSettings()
        self.sell = SellSettings()
        self.id_cfg = IDSettings()
        self.inventory = InventorySettings()

        # Runtime
        self.stats = RunStatistics()
        self.skills = SkillIDs()

        # UI
        self.window_module = ImGui.WindowModule(
            MODULE_NAME, window_name=MODULE_NAME,
            window_size=(300, 300),
            window_flags=PyImGui.WindowFlags.AlwaysAutoResize
        )
        self.debug_throttle = Timer()
        self.debug_throttle.Start()
        self.cached_debug = {}

bot = BotState()

# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def jitter(base_ms, variance=0.12):
    """Add human-like timing variance."""
    offset = base_ms * variance
    return int(base_ms + random.uniform(-offset, offset))

def player_is_dead():
    return Agent.IsDead(Player.GetAgentID())

def player_is_dead_or_loading(dest_map=0):
    if Map.IsMapLoading():
        return True
    if Agent.IsDead(Player.GetAgentID()):
        return True
    if dest_map and Map.GetMapID() == dest_map:
        return True
    return False

def get_sf_time_remaining():
    pid = Player.GetAgentID()
    sf = bot.skills.shadow_form
    if Routines.Checks.Effects.HasBuff(pid, sf):
        return GLOBAL_CACHE.Effects.GetEffectTimeRemaining(pid, sf)
    return 0

def get_not_hexed_enemy():
    px, py = Player.GetXY()
    enemies = Routines.Agents.GetFilteredEnemyArray(px, py, Range.Spellcast.value)
    enemies = AgentArray.Filter.ByCondition(enemies, lambda a: not Agent.IsHexed(a))
    return enemies[0] if enemies else 0

def get_enemy_count(range_val=Range.Spellcast.value):
    px, py = Player.GetXY()
    return len(Routines.Agents.GetFilteredEnemyArray(px, py, range_val))

# --- Material checks (use module-level frozensets) ---

def is_material(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) in MATERIAL_MODELS

def is_wood(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) == ModelID.Wood_Plank.value

def is_iron(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) == ModelID.Iron_Ingot.value

def is_dust(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) == ModelID.Pile_Of_Glittering_Dust.value

def is_bones(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) == ModelID.Bone.value

def is_cloth(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) == ModelID.Bolt_Of_Cloth.value

def is_granite(item_id):
    return GLOBAL_CACHE.Item.GetModelID(item_id) == ModelID.Granite_Slab.value

# --- Inventory helpers ---

def get_id_kits_to_buy():
    current = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Superior_Identification_Kit.value)
    return max(0, bot.inventory.keep_id_kits - current)

def get_salvage_kits_to_buy():
    current = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Salvage_Kit.value)
    return max(0, bot.inventory.keep_salvage_kits - current)

def get_filtered_materials_to_sell():
    bags = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    items = GLOBAL_CACHE.ItemArray.GetItemArray(bags)
    items = ItemArray.Filter.ByCondition(items, is_material)

    sell = bot.sell
    filtered = []
    material_filters = [
        (sell.sell_wood, is_wood), (sell.sell_iron, is_iron),
        (sell.sell_dust, is_dust), (sell.sell_bones, is_bones),
        (sell.sell_cloth, is_cloth), (sell.sell_granite, is_granite),
    ]
    for enabled, fn in material_filters:
        if enabled:
            filtered.extend(ItemArray.Filter.ByCondition(items, fn))
    return filtered

def filter_identify_array():
    bags = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    items = GLOBAL_CACHE.ItemArray.GetItemArray(bags)
    items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsWhite(i))
    items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Usage.IsIdentified(i))
    cfg = bot.id_cfg
    if not cfg.id_blues:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsBlue(i))
    if not cfg.id_purples:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsPurple(i))
    if not cfg.id_golds:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsGold(i))
    return items

def filter_salvage_array():
    bags = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    items = GLOBAL_CACHE.ItemArray.GetItemArray(bags)
    items = ItemArray.Filter.ByCondition(items,
        lambda i: GLOBAL_CACHE.Item.Usage.IsIdentified(i) or GLOBAL_CACHE.Item.Rarity.IsWhite(i))
    items = ItemArray.Filter.ByCondition(items, lambda i: GLOBAL_CACHE.Item.Usage.IsSalvageable(i))
    cfg = bot.salvage
    if not cfg.salvage_whites:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsWhite(i))
    if not cfg.salvage_blues:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsBlue(i))
    if not cfg.salvage_purples:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsPurple(i))
    if not cfg.salvage_golds:
        items = ItemArray.Filter.ByCondition(items, lambda i: not GLOBAL_CACHE.Item.Rarity.IsGold(i))
    if not cfg.salvage_glacial_stones:
        items = ItemArray.Filter.ByCondition(items,
            lambda i: GLOBAL_CACHE.Item.GetModelID(i) != ModelID.Glacial_Stone.value)
    return items

def filter_items_to_deposit():
    bags = GLOBAL_CACHE.ItemArray.CreateBagList(1, 2, 3, 4)
    items = GLOBAL_CACHE.ItemArray.GetItemArray(bags)
    items = ItemArray.Filter.ByCondition(items,
        lambda i: GLOBAL_CACHE.Item.GetModelID(i) not in KIT_MODELS)
    return items

def needs_inventory_handling():
    if GLOBAL_CACHE.Inventory.GetFreeSlotCount() < bot.inventory.leave_free_slots:
        return True
    if get_id_kits_to_buy() > 0:
        return True
    if get_salvage_kits_to_buy() > 0:
        return True
    if len(get_filtered_materials_to_sell()) > 0:
        return True
    if len(filter_identify_array()) > 0:
        return True
    if len(filter_salvage_array()) > 0:
        return True
    return False

def build_loot_config():
    """Build a LootConfig from current settings."""
    lc = LootConfig()
    lc.SetProperties(
        loot_whites=bot.loot.loot_whites,
        loot_blues=bot.loot.loot_blues,
        loot_purples=bot.loot.loot_purples,
        loot_golds=bot.loot.loot_golds,
        loot_greens=True,
    )

    whitelist_map = {
        "loot_event_items": EVENT_MODELS,
        "loot_map_pieces": MAP_PIECE_MODELS,
        "loot_glacial_stones": {ModelID.Glacial_Stone.value},
        "loot_lockpicks": {ModelID.Lockpick.value},
        "loot_tomes": {ModelID.Mesmer_Tome.value},
    }
    for attr, model_ids in whitelist_map.items():
        enabled = getattr(bot.loot, attr, True)
        for mid in model_ids:
            if enabled:
                lc.AddToWhitelist(mid)
            else:
                lc.AddToBlacklist(mid)

    if bot.loot.loot_black_dyes:
        lc.AddToDyeWhitelist(BLACK_DYE_COLOR)
    else:
        lc.AddToDyeBlacklist(BLACK_DYE_COLOR)
    if bot.loot.loot_white_dyes:
        lc.AddToDyeWhitelist(WHITE_DYE_COLOR)
    else:
        lc.AddToDyeBlacklist(WHITE_DYE_COLOR)
    if bot.loot.loot_dyes:
        lc.AddToWhitelist(ModelID.Vial_Of_Dye.value)
    else:
        lc.AddToBlacklist(ModelID.Vial_Of_Dye.value)

    return lc

def get_escape_location(scaling_factor=50):
    """Calculate escape vector away from nearby enemies."""
    px, py = Player.GetXY()
    vf = VectorFields(probe_position=(px, py))
    enemies = AgentArray.GetEnemyArray()
    enemies = AgentArray.Filter.ByDistance(enemies, (px, py), Range.Area.value)
    enemies = AgentArray.Filter.ByAttribute(enemies, 'IsAlive')
    agent_arrays = [{
        'name': 'enemies', 'array': enemies,
        'radius': Range.Area.value, 'is_dangerous': True
    }]
    ev = vf.generate_escape_vector(agent_arrays)
    return (px - ev[0] * scaling_factor, py - ev[1] * scaling_factor)

# ═══════════════════════════════════════════════════════════
# INVENTORY HANDLER (Yield coroutine)
# ═══════════════════════════════════════════════════════════

def InventoryHandler(log=False):
    if not needs_inventory_handling():
        yield
        return

    bot.current_step = "Merchant: Walking"
    yield from Routines.Yield.Agents.InteractWithAgentXY(-23110, 14942)

    # Sell materials first to free space
    if bot.sell.sell_materials:
        bot.current_step = "Merchant: Selling materials"
        items = get_filtered_materials_to_sell()
        if items:
            yield from Routines.Yield.Merchant.SellItems(items, log)

    # Buy kits
    bot.current_step = "Merchant: Buying kits"
    id_count = get_id_kits_to_buy()
    if id_count > 0:
        yield from Routines.Yield.Merchant.BuyIDKits(id_count, log)
    salv_count = get_salvage_kits_to_buy()
    if salv_count > 0:
        yield from Routines.Yield.Merchant.BuySalvageKits(salv_count, log)

    # Identify
    bot.current_step = "Merchant: Identifying"
    to_id = filter_identify_array()
    if to_id:
        pass  # DISABLED: InventoryPlus handles identify
        # yield from Routines.Yield.Items.IdentifyItems(to_id, log)

    # Salvage
    bot.current_step = "Merchant: Salvaging"
    to_salv = filter_salvage_array()
    if to_salv:
        pass  # DISABLED: InventoryPlus handles salvage
        # yield from Routines.Yield.Items.SalvageItems(to_salv, log)

    # Sell post-salvage materials
    if bot.sell.sell_materials:
        bot.current_step = "Merchant: Selling post-salvage"
        items = get_filtered_materials_to_sell()
        if items:
            yield from Routines.Yield.Merchant.SellItems(items, log)

    # Deposit to storage
    bot.current_step = "Merchant: Depositing items"
    to_deposit = filter_items_to_deposit()
    if to_deposit:
        yield from Routines.Yield.Items.DepositItems(to_deposit, log)

    bot.current_step = "Merchant: Depositing gold"
    yield from Routines.Yield.Items.DepositGold(bot.inventory.keep_gold_amount, log)
    yield

# ═══════════════════════════════════════════════════════════
# STUCK DETECTION
# ═══════════════════════════════════════════════════════════

def Handle_Stuck():
    if Map.IsMapLoading() or Map.GetMapID() == LONGEYES_LEDGE:
        bot.auto_stuck_timer.Reset()
        bot.non_movement_timer.Reset()
        bot.stuck_count = 0
        return

    if bot.pause_stuck_routine or bot.in_killing_routine or bot.finished_routine:
        bot.auto_stuck_timer.Reset()
        bot.non_movement_timer.Reset()
        bot.stuck_count = 0
        return

    if bot.auto_stuck_timer.HasElapsed(5000):
        Player.SendChatCommand("stuck")
        bot.auto_stuck_timer.Reset()

    if bot.stuck_count > 10:
        ConsoleLog(MODULE_NAME, "Stuck recovery failed, restarting.",
                   Py4GW.Console.MessageType.Error, log=True)
        bot.stuck_count = 0
        reset_environment()
        return

    if not Agent.IsMoving(Player.GetAgentID()):
        if not bot.non_movement_timer.IsRunning():
            bot.non_movement_timer.Reset()
        if bot.non_movement_timer.HasElapsed(3000):
            bot.non_movement_timer.Reset()
            Player.SendChatCommand("stuck")
            escape = get_escape_location()
            Player.Move(escape[0], escape[1])
            bot.stuck_count += 1
    else:
        nx, ny = Player.GetXY()
        dx = nx - bot.old_player_x
        dy = ny - bot.old_player_y
        if dx * dx + dy * dy > 10000:
            bot.non_movement_timer.Reset()
            bot.old_player_x = nx
            bot.old_player_y = ny
            bot.stuck_count = 0

# ═══════════════════════════════════════════════════════════
# SKILL CASTING (Yield coroutines)
# ═══════════════════════════════════════════════════════════

def BjoraMarchesSkillCasting():
    """Defensive-only casting while traversing Bjora Marches."""
    if not Routines.Checks.Agents.InDanger(Range.Spellcast):
        yield from Routines.Yield.wait(100)
        return

    pid = Player.GetAgentID()
    sk = bot.skills

    # Shadow Form maintenance (top priority)
    has_sf = Routines.Checks.Effects.HasBuff(pid, sk.shadow_form)
    sf_remaining = GLOBAL_CACHE.Effects.GetEffectTimeRemaining(pid, sk.shadow_form) if has_sf else 0

    if sf_remaining <= 3500:
        has_dp = Routines.Checks.Effects.HasBuff(pid, sk.deadly_paradox)
        yield from Routines.Yield.Skills.CastSkillID(
            sk.deadly_paradox, extra_condition=(not has_dp), aftercast_delay=jitter(100))
        yield from Routines.Yield.Skills.CastSkillID(
            sk.shadow_form, aftercast_delay=jitter(1250))
        return

    # Shroud of Distress if hurt
    if Agent.GetHealth(pid) < 0.45:
        if (yield from Routines.Yield.Skills.CastSkillID(
                sk.shroud_of_distress, aftercast_delay=jitter(1250))):
            return

    # Heart of Shadow escape if enemy behind
    nearest = Routines.Agents.GetNearestEnemy(Range.Earshot.value)
    if nearest:
        behind = Routines.Checks.Agents.IsEnemyBehind(pid)
        if (yield from Routines.Yield.Skills.CastSkillID(
                sk.heart_of_shadow, extra_condition=behind, aftercast_delay=jitter(350))):
            return
    yield


def JagaMoraineSkillCasting():
    """Full combat rotation for Jaga Moraine farming."""
    pid = Player.GetAgentID()
    sk = bot.skills

    if not bot.reset_from_jaga:
        yield from Routines.Yield.wait(1000)
        return

    # === PRIORITY 1: Shadow Form maintenance (UNCONDITIONAL during killing) ===
    # During kill routine we ALWAYS check SF — don't gate on InDanger() which could
    # theoretically flicker. Outside killing, only check when enemies are nearby.
    should_check_sf = bot.in_killing_routine or Routines.Checks.Agents.InDanger(Range.Spellcast)
    if should_check_sf:
        has_sf = Routines.Checks.Effects.HasBuff(pid, sk.shadow_form)
        sf_remaining = GLOBAL_CACHE.Effects.GetEffectTimeRemaining(pid, sk.shadow_form) if has_sf else 0

        if sf_remaining <= 3500:
            has_dp = Routines.Checks.Effects.HasBuff(pid, sk.deadly_paradox)
            yield from Routines.Yield.Skills.CastSkillID(
                sk.deadly_paradox, extra_condition=(not has_dp), aftercast_delay=jitter(100))
            yield from Routines.Yield.Skills.CastSkillID(
                sk.shadow_form, aftercast_delay=jitter(1250))
            return

    # === PRIORITY 2: Shroud of Distress if hurt ===
    if Agent.GetHealth(pid) < 0.45:
        if (yield from Routines.Yield.Skills.CastSkillID(
                sk.shroud_of_distress, aftercast_delay=jitter(1250))):
            return

    # === PRIORITY 3: Channeling upkeep (energy engine) ===
    if not Routines.Checks.Effects.HasBuff(pid, sk.channeling):
        if (yield from Routines.Yield.Skills.CastSkillID(
                sk.channeling, aftercast_delay=jitter(1250))):
            return

    # === PRIORITY 4: Way of Perfection (only recast if buff expired) ===
    if not Routines.Checks.Effects.HasBuff(pid, sk.way_of_perfection):
        if (yield from Routines.Yield.Skills.CastSkillID(
                sk.way_of_perfection, aftercast_delay=jitter(350))):
            return

    # === PRIORITY 5: Heart of Shadow for survival/unstuck ===
    if not bot.in_killing_routine:
        if Agent.GetHealth(pid) < 0.35 or bot.stuck_count > 0:
            if bot.pause_stuck_routine:
                yield from Routines.Yield.Agents.ChangeTarget(pid)
            else:
                yield from Routines.Yield.Agents.TargetNearestEnemy(Range.Earshot.value)
            if (yield from Routines.Yield.Skills.CastSkillID(
                    sk.heart_of_shadow, aftercast_delay=jitter(350))):
                return

    # === PRIORITY 6: Kill rotation (Arcane Echo + Wastrel's Demise) ===
    # SF safety is handled by Priority 1 — if SF<3500ms, we recast and never reach here.
    # No additional SF guard needed on AE; the priority system ensures SF uptime.
    if bot.in_killing_routine:
        ae_slot = 7  # Arcane Echo / echoed Wastrel's Demise
        wd_slot = 6  # Wastrel's Demise

        both_ready = (Routines.Checks.Skills.IsSkillSlotReady(wd_slot) and
                      Routines.Checks.Skills.IsSkillSlotReady(ae_slot))

        target = get_not_hexed_enemy()
        if target:
            yield from Routines.Yield.Agents.ChangeTarget(target)
            # When both AE and WD are ready: cast AE to echo WD into slot 7
            # When only slot 7 is ready: it's the echoed WD copy (0.25s cast)
            if (yield from Routines.Yield.Skills.CastSkillSlot(
                    ae_slot, extra_condition=both_ready, aftercast_delay=jitter(750))):
                pass  # AE echoed successfully, WD now in slot 7
            else:
                if (yield from Routines.Yield.Skills.CastSkillSlot(
                        ae_slot, aftercast_delay=jitter(350))):
                    return

        # Cast original WD from slot 6 on next unhexed target
        target = get_not_hexed_enemy()
        if target:
            yield from Routines.Yield.Agents.ChangeTarget(target)
            if (yield from Routines.Yield.Skills.CastSkillSlot(
                    wd_slot, aftercast_delay=jitter(350))):
                return
    yield


def SkillHandler():
    """Coroutine: parallel skill casting manager."""
    while True:
        if not Routines.Checks.Map.MapValid() or not Map.IsExplorable():
            yield from Routines.Yield.wait(1000)
            continue

        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            continue

        if bot.finished_routine:
            yield from Routines.Yield.wait(1000)
            continue

        current_map = Map.GetMapID()
        if current_map == BJORA_MARCHES:
            yield from BjoraMarchesSkillCasting()
        elif current_map == JAGA_MORAINE:
            yield from JagaMoraineSkillCasting()

        yield

# ═══════════════════════════════════════════════════════════
# MAIN BOT LOGIC (Yield coroutine)
# ═══════════════════════════════════════════════════════════

def handle_death():
    if Agent.IsDead(Player.GetAgentID()) or not bot.is_running:
        bot.stats.deaths += 1
        ConsoleLog(MODULE_NAME, f"Death/stop on {Map.GetMapName(Map.GetMapID())}.",
                   Py4GW.Console.MessageType.Error, log=bot.log_to_console)
        return True
    return False

def reset_environment():
    bot.is_running = False
    bot.reset_from_jaga = False
    bot.in_killing_routine = False
    bot.finished_routine = False
    bot.pause_stuck_routine = False
    bot.stuck_count = 0
    bot.current_step = "Idle"
    GLOBAL_CACHE._ActionQueueManager.ResetAllQueues()


def RunBotLogic():
    """Main farm loop coroutine."""
    bot.reset_from_jaga = False
    yield from Routines.Yield.wait(1000)

    while True:
        if not bot.is_running:
            yield from Routines.Yield.wait(1000)
            continue

        log = bot.log_to_console

        # ─── OUTPOST PHASE ───
        if not bot.reset_from_jaga:
            bot.current_step = "Traveling to Longeye's Ledge"
            yield from Routines.Yield.Map.TravelToOutpost(LONGEYES_LEDGE, log)

            bot.current_step = "Loading skillbar"
            primary, _ = Agent.GetProfessionNames(Player.GetAgentID())
            if primary == "Assassin":
                yield from Routines.Yield.Skills.LoadSkillbar("OwVUI2h5lPP8Id2BkAiAvpLBTAA", log)
            elif primary == "Dervish":
                yield from Routines.Yield.Skills.LoadSkillbar("Ogej8xpDLT8I6MHQEQIQjbjXihA", log)
            else:
                ConsoleLog(MODULE_NAME, "Need A/Me or D/A profession.",
                           Py4GW.Console.MessageType.Error, log=True)
                reset_environment()
                break

            if not bot.skills.load():
                ConsoleLog(MODULE_NAME, "Failed to load skill IDs.",
                           Py4GW.Console.MessageType.Error, log=True)
                reset_environment()
                break

            bot.current_step = "Setting hard mode"
            yield from Routines.Yield.Map.SetHardMode(log)
            yield from Routines.Yield.Player.SetTitle(TitleID.Norn.value, log)

            bot.current_step = "Handling inventory"
            yield from InventoryHandler(log)

            bot.current_step = "Leaving outpost"
            yield from Routines.Yield.Movement.FollowPath(
                path_points=PATH_LEAVE_OUTPOST,
                custom_exit_condition=lambda: Map.IsMapLoading() or not bot.is_running)

            bot.current_step = "Loading Bjora Marches"
            yield from Routines.Yield.Map.WaitforMapLoad(BJORA_MARCHES, log)
            bot.pause_stuck_routine = False

            bot.current_step = "Traversing Bjora Marches"
            yield from Routines.Yield.Movement.FollowPath(
                path_points=PATH_TRAVERSE_BJORA,
                custom_exit_condition=lambda: player_is_dead_or_loading(JAGA_MORAINE) or not bot.is_running)
            bot.pause_stuck_routine = True

            if Routines.Checks.Map.MapValid() and handle_death():
                bot.reset_from_jaga = False
                continue

            bot.current_step = "Loading Jaga Moraine"
            yield from Routines.Yield.Map.WaitforMapLoad(JAGA_MORAINE, log)
            bot.reset_from_jaga = True

        # ─── FARMING PHASE ───
        bot.stats.runs_attempted += 1
        bot.stats.lap_timer.Reset()
        bot.stats.lap_timer.Start()

        # Skip bounty if Norn title maxed (>160k points)
        skip_bounty = False
        norn_title = Player.GetTitle(41)
        if norn_title and norn_title.current_points > 160000:
            skip_bounty = True

        if not skip_bounty:
            bot.current_step = "Going to bounty NPC"
            yield from Routines.Yield.Movement.FollowPath(
                path_points=PATH_TO_BOUNTY_NPC,
                custom_exit_condition=lambda: player_is_dead() or not bot.is_running)
            bot.current_step = "Taking bounty"
            yield from Routines.Yield.Agents.InteractWithAgentXY(13367, -20771)
            yield from Routines.Yield.Player.SendDialog("0x84")

        # Left aggro pull
        bot.current_step = "Left pull"
        bot.pause_stuck_routine = False
        yield from Routines.Yield.Movement.FollowPath(
            path_points=PATH_AGGRO_LEFT,
            custom_exit_condition=lambda: player_is_dead() or not bot.is_running)
        if handle_death():
            bot.reset_from_jaga = False
            continue

        # Wait for left ball compression (let enemies lock aggro + compress)
        bot.current_step = "Left ball compression"
        bot.pause_stuck_routine = True
        yield from Routines.Yield.wait(jitter(15000))
        bot.pause_stuck_routine = False

        # Right aggro pull
        bot.current_step = "Right pull"
        yield from Routines.Yield.Movement.FollowPath(
            path_points=PATH_AGGRO_RIGHT,
            custom_exit_condition=lambda: player_is_dead() or not bot.is_running)
        if handle_death():
            bot.reset_from_jaga = False
            continue

        # Wait for right ball compression
        bot.current_step = "Right ball compression"
        bot.pause_stuck_routine = True
        yield from Routines.Yield.wait(jitter(15000))
        bot.pause_stuck_routine = False

        # Move to kill spot (ball compression path)
        bot.current_step = "Compressing at kill spot"
        yield from Routines.Yield.Movement.FollowPath(
            path_points=PATH_TO_KILL_SPOT,
            custom_exit_condition=lambda: player_is_dead() or not bot.is_running)
        if handle_death():
            bot.reset_from_jaga = False
            continue

        # ─── KILL PHASE ───
        bot.current_step = "Killing"
        bot.in_killing_routine = True
        px, py = Player.GetXY()
        enemies = Routines.Agents.GetFilteredEnemyArray(px, py, Range.Spellcast.value)
        initial_count = len(enemies)

        while len(enemies) > 3:
            if handle_death():
                break
            yield from Routines.Yield.wait(jitter(1000))
            enemies = Routines.Agents.GetFilteredEnemyArray(px, py, Range.Spellcast.value)

        if handle_death():
            bot.in_killing_routine = False
            bot.reset_from_jaga = False
            continue

        final_count = len(enemies)
        bot.in_killing_routine = False
        bot.finished_routine = True

        # Record stats (FIXED: kills/left_alive were swapped in v2.3.3)
        bot.stats.kills += max(0, initial_count - final_count)
        bot.stats.left_alive += final_count
        bot.stats.runs_completed += 1
        bot.stats.record_lap()

        # ─── LOOT PHASE ───
        bot.current_step = "Looting"
        yield from Routines.Yield.wait(jitter(500))
        loot_config = build_loot_config()
        filtered_loot = loot_config.GetfilteredLootArray(Range.Earshot.value)
        if filtered_loot:
            yield from Routines.Yield.Items.LootItems(filtered_loot, log)

        if handle_death():
            bot.reset_from_jaga = False
            continue

        # ID + Salvage in field
        bot.current_step = "Identifying"
        to_id = filter_identify_array()
        if to_id:
            pass  # DISABLED: InventoryPlus handles identify
            # yield from Routines.Yield.Items.IdentifyItems(to_id, log)

        bot.current_step = "Salvaging"
        to_salv = filter_salvage_array()
        if to_salv:
            pass  # DISABLED: InventoryPlus handles salvage
            # yield from Routines.Yield.Items.SalvageItems(to_salv, log)

        # Check if outpost return needed
        needs_outpost = (
            GLOBAL_CACHE.Inventory.GetFreeSlotCount() < bot.inventory.leave_free_slots or
            GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Superior_Identification_Kit.value) <= 0 or
            GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Salvage_Kit.value) <= 0
        )
        if needs_outpost:
            bot.reset_from_jaga = False
            bot.finished_routine = False
            continue

        if handle_death():
            bot.reset_from_jaga = False
            continue

        # ─── REZONE PHASE ───
        bot.current_step = "Exiting Jaga Moraine"
        bot.finished_routine = False
        yield from Routines.Yield.Movement.FollowPath(
            path_points=PATH_EXIT_JAGA,
            custom_exit_condition=lambda: player_is_dead_or_loading(BJORA_MARCHES) or not bot.is_running)
        yield from Routines.Yield.Map.WaitforMapLoad(BJORA_MARCHES, log)

        bot.current_step = "Returning to Jaga Moraine"
        yield from Routines.Yield.Movement.FollowPath(
            path_points=PATH_RETURN_JAGA,
            custom_exit_condition=lambda: player_is_dead_or_loading(JAGA_MORAINE) or not bot.is_running)
        yield from Routines.Yield.Map.WaitforMapLoad(JAGA_MORAINE, log)

        bot.reset_from_jaga = True
        bot.pause_stuck_routine = True
        bot.current_step = "Run complete"
        yield from Routines.Yield.wait(jitter(100))

# ═══════════════════════════════════════════════════════════
# UI (ImGui DrawWindow)
# ═══════════════════════════════════════════════════════════

def DrawWindow():
    try:
        if bot.window_module.first_run:
            PyImGui.set_next_window_size(*bot.window_module.window_size)
            PyImGui.set_next_window_pos(*bot.window_module.window_pos)
            bot.window_module.first_run = False

        if PyImGui.begin(bot.window_module.window_name, bot.window_module.window_flags):
            # Header
            PyImGui.text(MODULE_NAME)
            PyImGui.separator()

            # Status + Control
            if PyImGui.begin_table("ControlTable", 2):
                PyImGui.table_next_row()
                PyImGui.table_next_column()
                PyImGui.text("Status:")
                PyImGui.table_next_column()
                if bot.is_running:
                    PyImGui.text_colored(bot.current_step, Color(0, 255, 0, 255).to_tuple())
                else:
                    PyImGui.text_colored("IDLE", Color(255, 0, 0, 255).to_tuple())

                PyImGui.table_next_row()
                PyImGui.table_next_column()
                PyImGui.text("Control:")
                PyImGui.table_next_column()
                btn_text = "Stop Bot" if bot.is_running else "Start Bot"
                if PyImGui.button(btn_text):
                    bot.is_running = not bot.is_running
                    if bot.is_running:
                        GLOBAL_CACHE.Coroutines.clear()
                        GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                        GLOBAL_CACHE.Coroutines.append(SkillHandler())
                        bot.stats.global_timer.Start()
                    else:
                        reset_environment()
                PyImGui.end_table()

            # ─── CONFIG ───
            if PyImGui.collapsing_header("Config"):
                bot.log_to_console = PyImGui.checkbox("Log to Console", bot.log_to_console)

                if PyImGui.tree_node("Loot"):
                    for label, attr in [
                        ("Whites", "loot_whites"), ("Blues", "loot_blues"),
                        ("Purples", "loot_purples"), ("Golds", "loot_golds"),
                        ("Lockpicks", "loot_lockpicks"), ("White Dyes", "loot_white_dyes"),
                        ("Black Dyes", "loot_black_dyes"), ("Glacial Stones", "loot_glacial_stones"),
                        ("Tomes", "loot_tomes"), ("Dyes", "loot_dyes"),
                        ("Event Items", "loot_event_items"), ("Map Pieces", "loot_map_pieces"),
                    ]:
                        setattr(bot.loot, attr, PyImGui.checkbox(label, getattr(bot.loot, attr)))
                    PyImGui.tree_pop()

                if PyImGui.tree_node("Identify"):
                    for label, attr in [("Blues", "id_blues"), ("Purples", "id_purples"), ("Golds", "id_golds")]:
                        setattr(bot.id_cfg, attr, PyImGui.checkbox(label, getattr(bot.id_cfg, attr)))
                    PyImGui.tree_pop()

                if PyImGui.tree_node("Salvage"):
                    for label, attr in [
                        ("Whites", "salvage_whites"), ("Blues", "salvage_blues"),
                        ("Purples", "salvage_purples"), ("Golds", "salvage_golds"),
                        ("Glacial Stones", "salvage_glacial_stones"),
                    ]:
                        setattr(bot.salvage, attr, PyImGui.checkbox(label, getattr(bot.salvage, attr)))
                    PyImGui.tree_pop()

                if PyImGui.tree_node("Sell"):
                    for label, attr in [
                        ("Materials", "sell_materials"), ("Wood", "sell_wood"),
                        ("Iron", "sell_iron"), ("Dust", "sell_dust"),
                        ("Bones", "sell_bones"), ("Cloth", "sell_cloth"),
                        ("Granite", "sell_granite"),
                    ]:
                        setattr(bot.sell, attr, PyImGui.checkbox(label, getattr(bot.sell, attr)))
                    PyImGui.tree_pop()

                if PyImGui.tree_node("Misc"):
                    for label, attr in [
                        ("Keep ID Kits", "keep_id_kits"), ("Keep Salvage Kits", "keep_salvage_kits"),
                        ("Keep Gold", "keep_gold_amount"), ("Leave Free Slots", "leave_free_slots"),
                    ]:
                        setattr(bot.inventory, attr,
                                PyImGui.input_int(label, getattr(bot.inventory, attr)))
                    PyImGui.tree_pop()

            # ─── STATISTICS ───
            if PyImGui.collapsing_header("Statistics"):
                if PyImGui.begin_tab_bar("StatsTabs"):
                    if PyImGui.begin_tab_item("Runs"):
                        s = bot.stats
                        headers = ["Metric", "Value"]
                        data = [
                            ("Total Time", s.global_timer.FormatElapsedTime("hh:mm:ss")),
                            ("Current Run", s.lap_timer.FormatElapsedTime("mm:ss")),
                            ("Min Run", FormatTime(s.min_time, "mm:ss") if s.min_time else "--"),
                            ("Max Run", FormatTime(s.max_time, "mm:ss") if s.max_time else "--"),
                            ("Avg Run", FormatTime(s.avg_time, "mm:ss") if s.avg_time else "--"),
                            ("Runs", f"{s.runs_completed}/{s.runs_attempted}"),
                            ("Success Rate", f"{s.success_rate * 100:.0f}%"),
                            ("Deaths", str(s.deaths)),
                            ("Total Kills", str(s.kills)),
                            ("Left Alive", str(s.left_alive)),
                        ]
                        ImGui.table("run_stats", headers, data)
                        PyImGui.end_tab_item()

                    # Debug tab — throttled to 500ms refresh
                    if PyImGui.begin_tab_item("Debug"):
                        if bot.debug_throttle.HasElapsed(500):
                            bot.debug_throttle.Reset()
                            px, py = Player.GetXY()
                            bot.cached_debug = {
                                "Position": f"({int(px)}, {int(py)})",
                                "Free Slots": str(GLOBAL_CACHE.Inventory.GetFreeSlotCount()),
                                "Enemies": str(get_enemy_count()),
                                "Stuck Count": str(bot.stuck_count),
                                "SF Remaining": f"{get_sf_time_remaining():.0f}ms",
                                "Map": Map.GetMapName(Map.GetMapID()),
                            }
                        if bot.cached_debug:
                            headers = ["Info", "Value"]
                            data = list(bot.cached_debug.items())
                            ImGui.table("debug_info", headers, data)

                        if PyImGui.button("Start from Jaga"):
                            bot.is_running = True
                            bot.reset_from_jaga = True
                            GLOBAL_CACHE.Coroutines.clear()
                            GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                            GLOBAL_CACHE.Coroutines.append(SkillHandler())

                        PyImGui.end_tab_item()
                    PyImGui.end_tab_bar()

        PyImGui.end()

    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"UI Error: {str(e)}", Py4GW.Console.MessageType.Error)

# ═══════════════════════════════════════════════════════════
# MAIN ENTRY POINT (called per frame by Py4GW)
# ═══════════════════════════════════════════════════════════

def main():
    try:
        if bot.is_running:
            Handle_Stuck()

        DrawWindow()

        if Map.IsMapLoading():
            GLOBAL_CACHE._ActionQueueManager.ResetAllQueues()
            bot.auto_stuck_timer.Reset()
            bot.stuck_count = 0
            return

        if bot.is_running:
            if not Routines.Checks.Skills.InCastingProcess() and not Agent.IsKnockedDown(Player.GetAgentID()):
                for coro in GLOBAL_CACHE.Coroutines[:]:
                    try:
                        next(coro)
                    except StopIteration:
                        GLOBAL_CACHE.Coroutines.remove(coro)

    except Exception as e:
        ConsoleLog(MODULE_NAME, f"Error: {str(e)}", Py4GW.Console.MessageType.Error, log=True)

if __name__ == "__main__":
    main()
