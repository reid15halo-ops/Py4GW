"""
Salvage Decision Engine and Kit Selection.

Extracted from AutoLootManager.py — contains the pure decision logic for
evaluating whether to keep, salvage for mats, or expert-salvage an item,
plus helpers for locating salvage/ID kits in inventory and requesting
kits from teammates via shared memory.
"""
import json
import os
import time
import Py4GW

from Py4GWCoreLib import (
    Item, ItemArray, Inventory, Agent, AgentArray,
    Range, SharedCommandType, GLOBAL_CACHE, Player, Map,
)
from Py4GWCoreLib.py4gwcorelib_src.ActionQueue import ActionQueueManager

# Late-bound reference to the shared LootConfig instance.
# AutoLootManager sets this after import so the decision engine can read
# config.expert_extract without a circular import.
config = None  # type: ignore  — set by AutoLootManager at startup

# ---------------------------------------------------------------------------
# Modifier constants and value tables are expected to live in ItemModTables
# once the full extraction is complete.  For now we import everything we
# reference from AutoLootManager's inline definitions through a sibling
# module (to be created).  Until ItemModTables exists, callers must ensure
# the names below are injected into this module's namespace — see the
# ``install_tables`` helper at the bottom of this file.
# ---------------------------------------------------------------------------

# Sentinel so we can detect whether tables have been installed.
_tables_installed = False

# All MOD_* constant names referenced by the decision engine
MOD_REQUIREMENT: int = 0
MOD_REQUIREMENT_2: int = 0

# All VALUABLE_* table names referenced by the decision engine / kit selection
VALUABLE_MODEL_IDS: set = set()
VALUABLE_SKIN_KEYWORDS: set = set()
VALUABLE_RUNE_KEYWORDS: set = set()
VALUABLE_INSIGNIA_KEYWORDS: set = set()
VALUABLE_WEAPON_MODS_PURPLE: dict = {}
VALUABLE_WEAPON_MODS_BLUE: dict = {}
VALUABLE_SHIELD_OFFHAND_MODS_PURPLE: dict = {}
VALUABLE_SHIELD_OFFHAND_MODS_BLUE: dict = {}
VALUABLE_ARMOR_RUNES: dict = {}


def install_tables(
    *,
    mod_requirement: int,
    mod_requirement_2: int,
    valuable_model_ids: set,
    valuable_skin_keywords: set,
    valuable_rune_keywords: set,
    valuable_insignia_keywords: set,
    valuable_weapon_mods_purple: dict,
    valuable_weapon_mods_blue: dict,
    valuable_shield_offhand_mods_purple: dict,
    valuable_shield_offhand_mods_blue: dict,
    valuable_armor_runes: dict,
):
    """Inject modifier constants and value tables from the caller.

    This avoids a circular import: AutoLootManager owns the data today and
    calls ``install_tables(...)`` once at import time.  When the data moves
    to a dedicated ``ItemModTables`` module both this file and
    AutoLootManager can import from there instead.
    """
    global _tables_installed
    global MOD_REQUIREMENT, MOD_REQUIREMENT_2
    global VALUABLE_MODEL_IDS, VALUABLE_SKIN_KEYWORDS
    global VALUABLE_RUNE_KEYWORDS, VALUABLE_INSIGNIA_KEYWORDS
    global VALUABLE_WEAPON_MODS_PURPLE, VALUABLE_WEAPON_MODS_BLUE
    global VALUABLE_SHIELD_OFFHAND_MODS_PURPLE, VALUABLE_SHIELD_OFFHAND_MODS_BLUE
    global VALUABLE_ARMOR_RUNES

    MOD_REQUIREMENT = mod_requirement
    MOD_REQUIREMENT_2 = mod_requirement_2
    VALUABLE_MODEL_IDS = valuable_model_ids
    VALUABLE_SKIN_KEYWORDS = valuable_skin_keywords
    VALUABLE_RUNE_KEYWORDS = valuable_rune_keywords
    VALUABLE_INSIGNIA_KEYWORDS = valuable_insignia_keywords
    VALUABLE_WEAPON_MODS_PURPLE = valuable_weapon_mods_purple
    VALUABLE_WEAPON_MODS_BLUE = valuable_weapon_mods_blue
    VALUABLE_SHIELD_OFFHAND_MODS_PURPLE = valuable_shield_offhand_mods_purple
    VALUABLE_SHIELD_OFFHAND_MODS_BLUE = valuable_shield_offhand_mods_blue
    VALUABLE_ARMOR_RUNES = valuable_armor_runes
    _tables_installed = True


# ===================================================================
# region Market Price Integration
# ===================================================================

# Path to the JSON file written by tools/price_fetcher.py
_MARKET_PRICES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "Settings", "market_prices.json",
)

# Staleness threshold — fall back to static tables if data is older than 7 days
_MARKET_PRICE_MAX_AGE_S = 7 * 24 * 60 * 60

# Cached price data (lazy-loaded on first call)
_market_prices_cache = None   # type: dict | None
_market_prices_loaded = False

# Thresholds (in gold) for market-price overrides
MARKET_PRICE_EXTRACT_THRESHOLD = 5000   # mod worth > 5000g  -> always EXTRACT
MARKET_PRICE_JUNK_THRESHOLD    = 500    # mod worth < 500g   -> always SALVAGE_MATS

# ---------------------------------------------------------------------------
# Mapping from modifier IDs to the trade-chat names used in market_prices.json.
# The price_fetcher scrapes Kamadan trade chat where players use short names
# like "Fortitude +30", "Vampiric", "Zealous", etc.  We map the numeric
# MOD_* constants to those canonical lookup names so we can find them in the
# price data's "prices" dict.
#
# Keys are (mod_id, min_value) tuples when the same mod_id can map to
# different price names at different roll values (e.g. Vigor).
# For simple mods the key is just mod_id with min_value=0.
# ---------------------------------------------------------------------------
MOD_ID_TO_PRICE_NAMES: dict[int, list[tuple[str, int]]] = {}
# Populated lazily after tables are installed (needs MOD_* constants).


def _build_mod_price_mapping():
    """Build the MOD_ID -> [(price_name, min_arg_value), ...] mapping.

    Called once after ItemModTables constants are available (i.e. after
    install_tables or on first use).  Each entry is a list because a
    single modifier ID can correspond to several trade-chat names at
    different value thresholds (e.g. Major Vigor vs Superior Vigor).
    """
    global MOD_ID_TO_PRICE_NAMES
    # We reference the module-level MOD_* constants that were injected by
    # install_tables().  If tables aren't installed yet we import from
    # ItemModTables directly as a fallback.
    try:
        from Widgets.Automation.Helpers.ItemModTables import (
            MOD_HEALTH_FLAT, MOD_ENERGY_FLAT, MOD_ENCHANTING,
            MOD_SUNDERING, MOD_FURIOUS, MOD_ZEALOUS, MOD_VAMPIRIC,
            MOD_DMG_ABOVE_HP, MOD_DMG_ENCHANT, MOD_DMG_STANCE,
            MOD_HCT_ATTR, MOD_HSR_ALL, MOD_HSR_ATTR, MOD_ATTR_BONUS,
            MOD_HEALTH_RUNE, MOD_WARDING, MOD_SHELTER,
            MOD_ARMOR_FLAT, MOD_ENERGY_OFFHAND,
        )
    except ImportError:
        # Fall back to module-level values (set by install_tables)
        MOD_HEALTH_FLAT = globals().get("MOD_HEALTH_FLAT", 9032)
        MOD_ENERGY_FLAT = globals().get("MOD_ENERGY_FLAT", 8920)
        MOD_ENCHANTING = globals().get("MOD_ENCHANTING", 8888)
        MOD_SUNDERING = globals().get("MOD_SUNDERING", 9208)
        MOD_FURIOUS = globals().get("MOD_FURIOUS", 9144)
        MOD_ZEALOUS = globals().get("MOD_ZEALOUS", 9240)
        MOD_VAMPIRIC = globals().get("MOD_VAMPIRIC", 8424)
        MOD_DMG_ABOVE_HP = globals().get("MOD_DMG_ABOVE_HP", 8824)
        MOD_DMG_ENCHANT = globals().get("MOD_DMG_ENCHANT", 8808)
        MOD_DMG_STANCE = globals().get("MOD_DMG_STANCE", 8872)
        MOD_HCT_ATTR = globals().get("MOD_HCT_ATTR", 8712)
        MOD_HSR_ALL = globals().get("MOD_HSR_ALL", 9128)
        MOD_HSR_ATTR = globals().get("MOD_HSR_ATTR", 9112)
        MOD_ATTR_BONUS = globals().get("MOD_ATTR_BONUS", 10296)
        MOD_HEALTH_RUNE = globals().get("MOD_HEALTH_RUNE", 8408)
        MOD_WARDING = globals().get("MOD_WARDING", 8488)
        MOD_SHELTER = globals().get("MOD_SHELTER", 8536)
        MOD_ARMOR_FLAT = globals().get("MOD_ARMOR_FLAT", 8456)
        MOD_ENERGY_OFFHAND = globals().get("MOD_ENERGY_OFFHAND", 26568)

    # Format: mod_id -> [(price_lookup_name, min_arg_value), ...]
    # price_lookup_name is matched case-insensitively against market_prices keys.
    MOD_ID_TO_PRICE_NAMES = {
        # --- Weapon mods ---
        MOD_HEALTH_FLAT:   [("Fortitude +30", 30), ("Fortitude +29", 29), ("Fortitude +28", 28)],
        MOD_ENERGY_FLAT:   [("Insightful +5", 5), ("+5 energy", 5)],
        MOD_ENCHANTING:    [("Enchanting 20%", 20), ("of Enchanting", 19)],
        MOD_SUNDERING:     [("Sundering 20%", 20), ("Sundering", 19)],
        MOD_FURIOUS:       [("Furious 10%", 10), ("Furious", 9)],
        MOD_ZEALOUS:       [("Zealous", 1)],
        MOD_VAMPIRIC:      [("Vampiric", 1)],
        MOD_DMG_ABOVE_HP:  [("15^50", 15), ("Strength and Honor", 14)],
        MOD_DMG_ENCHANT:   [("Guided by Fate", 14)],
        MOD_DMG_STANCE:    [("Dance With Death", 14)],
        MOD_HCT_ATTR:      [("HCT 20%", 20), ("Halves Casting Time", 19)],
        MOD_HSR_ALL:       [("HSR 20%", 20), ("Halves Skill Recharge", 19)],
        MOD_HSR_ATTR:      [("HSR 20%", 20), ("Halves Skill Recharge", 19)],
        MOD_ATTR_BONUS:    [("+1 attribute 20%", 20), ("Aptitude", 19)],
        # --- Shield / offhand mods ---
        MOD_WARDING:       [("of Warding", 9), ("Warding +10", 10)],
        MOD_SHELTER:       [("of Shelter", 9), ("Shelter +10", 10)],
        MOD_ARMOR_FLAT:    [("Armor +8", 8)],
        MOD_ENERGY_OFFHAND:[("of the class +5", 5)],
        # --- Armor runes ---
        MOD_HEALTH_RUNE:   [("Superior Vigor", 50), ("Major Vigor", 41), ("Vigor", 30)],
    }


def load_market_prices():
    """Load market prices from JSON, caching the result.

    Returns the ``prices`` dict from market_prices.json (item_name -> {avg, min, max, count}),
    or ``None`` if the file is missing, unreadable, or stale (>7 days old).
    """
    global _market_prices_cache, _market_prices_loaded

    if _market_prices_loaded:
        return _market_prices_cache

    _market_prices_loaded = True  # only try once per session

    try:
        path = os.path.normpath(_MARKET_PRICES_PATH)
        if not os.path.isfile(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Staleness check
        fetched_at = data.get("fetched_at", 0)
        if time.time() - fetched_at > _MARKET_PRICE_MAX_AGE_S:
            Py4GW.Console.Log(
                "SalvageDecision",
                "Market price data is stale (>7 days), falling back to static tables.",
                Py4GW.Console.MessageType.Info,
                False,
            )
            return None

        prices = data.get("prices", {})
        if not prices:
            return None

        # Build a lowercase-keyed lookup for case-insensitive matching
        _market_prices_cache = {k.lower(): v for k, v in prices.items()}

        # Ensure the mod->price mapping is built
        if not MOD_ID_TO_PRICE_NAMES:
            _build_mod_price_mapping()

        return _market_prices_cache

    except Exception as e:
        Py4GW.Console.Log(
            "SalvageDecision",
            f"Failed to load market prices: {e}",
            Py4GW.Console.MessageType.Warning,
            False,
        )
        return None


def get_mod_market_price(mod_id, mod_value):
    """Look up the market price (avg gold) for a modifier.

    *mod_id*:    numeric modifier identifier (MOD_HEALTH_FLAT etc.)
    *mod_value*: the roll value on the item (e.g. 30 for Fortitude +30).

    Returns the average gold price if a match is found in market data,
    or 0 if no match / no data.
    """
    prices = load_market_prices()
    if prices is None:
        return 0

    candidates = MOD_ID_TO_PRICE_NAMES.get(mod_id, [])
    if not candidates:
        return 0

    # Walk candidates from highest min_value down; pick the first that the
    # item's roll meets or exceeds, then look that name up in market data.
    for price_name, min_val in candidates:
        if mod_value >= min_val:
            entry = prices.get(price_name.lower())
            if entry:
                return entry.get("avg", 0)

    # Fallback: try a fuzzy substring match against all price keys
    # (trade chat names are noisy — "Fortitude +30" might be listed as
    # "fort +30" or "+30hp").
    for price_name, min_val in candidates:
        if mod_value >= min_val:
            # Try partial match
            needle = price_name.lower()
            for key, entry in prices.items():
                if needle in key or key in needle:
                    return entry.get("avg", 0)

    return 0


def get_best_mod_market_price(item_id):
    """Return (best_price, price_name) for the most valuable mod on an item.

    Scans all modifiers on *item_id*, looks each up in market data, and
    returns the highest price found.  Returns (0, "") if no market data or
    no matches.
    """
    prices = load_market_prices()
    if prices is None:
        return 0, ""

    try:
        modifiers = Item.Customization.Modifiers.GetModifiers(item_id)
    except Exception:
        return 0, ""
    if not modifiers:
        return 0, ""

    best_price = 0
    best_name = ""

    for mod in modifiers:
        mod_id = mod.GetIdentifier()
        # Use whichever arg is larger as the "value" — tables specify which
        # arg to check, but for price lookup we just need the roll magnitude.
        val = max(mod.GetArg1() or 0, mod.GetArg2() or 0)
        price = get_mod_market_price(mod_id, val)
        if price > best_price:
            best_price = price
            # Find the matching name for logging
            candidates = MOD_ID_TO_PRICE_NAMES.get(mod_id, [])
            for pname, mval in candidates:
                if val >= mval:
                    best_name = pname
                    break

    return best_price, best_name

# endregion


# ===================================================================
# region Salvage Decision Engine
# ===================================================================

# Decision results
DECISION_KEEP = "KEEP"             # Don't touch this item
DECISION_SALVAGE_MATS = "MATS"    # Lesser kit — salvage for materials
DECISION_EXTRACT = "EXTRACT"       # Expert kit — extract the upgrade


def get_item_requirement(item_id):
    """Read the requirement level from item modifiers. Returns (attr_id, req_level) or (None, 0)."""
    # Try primary requirement modifier
    result = Item.Customization.Modifiers.GetModifierValues(item_id, MOD_REQUIREMENT)
    if result and result != (None, None, None):
        _, attr_id, req_level = result
        return attr_id, req_level
    # Try alternate requirement modifier
    result = Item.Customization.Modifiers.GetModifierValues(item_id, MOD_REQUIREMENT_2)
    if result and result != (None, None, None):
        _, attr_id, req_level = result
        return attr_id, req_level
    return None, 0


def check_mods_against_table(item_id, mod_table):
    """Check item modifiers against a value table. Returns list of (description, value)."""
    valuable_found = []
    try:
        modifiers = Item.Customization.Modifiers.GetModifiers(item_id)
    except Exception:
        return []  # API error — treat as no valuable mods (safe: defaults to salvage for mats, not keep)
    if modifiers is None:
        return []  # Defensive: shouldn't happen but treat same as empty
    for mod in modifiers:
        mod_id = mod.GetIdentifier()
        if mod_id in mod_table:
            arg_check, min_val, desc_template = mod_table[mod_id]
            value = mod.GetArg1() if arg_check == "arg1" else mod.GetArg2()
            if value >= min_val:
                valuable_found.append((desc_template.format(v=value), value))
    return valuable_found


def has_valuable_skin(item_id):
    """Check if item has a valuable skin by model_id or name keywords."""
    model_id = Item.GetModelID(item_id)
    if model_id in VALUABLE_MODEL_IDS:
        return True
    try:
        if Item.IsNameReady(item_id):
            name = Item.GetName(item_id).lower()
            for keyword in VALUABLE_SKIN_KEYWORDS:
                if keyword in name:
                    return True
    except Exception:
        pass  # Name not ready or item consumed — safe to skip skin check
    return False


def has_valuable_rune_or_insignia(item_id):
    """Check armor item name for valuable runes/insignias worth extracting."""
    try:
        if not Item.IsNameReady(item_id):
            Item.RequestName(item_id)
            return False, ""  # Name not ready yet, skip this cycle
        name = Item.GetName(item_id).lower()
        for keyword in VALUABLE_RUNE_KEYWORDS:
            if keyword in name:
                return True, f"rune: {keyword}"
        for keyword in VALUABLE_INSIGNIA_KEYWORDS:
            if keyword in name:
                return True, f"insignia: {keyword}"
    except Exception:
        pass  # Name not ready or item consumed — will retry next cycle
    return False, ""


def evaluate_weapon(item_id):
    """Evaluate a blue/purple weapon/shield/offhand.

    Blue items (single mod):  only EXTRACT on a perfect (max-value) roll.
    Purple items (two mods):  EXTRACT on any near-max valuable mod.

    Returns: (decision, reason)
    """
    attr_id, req_level = get_item_requirement(item_id)
    is_purple = Item.Rarity.IsPurple(item_id)
    is_blue = Item.Rarity.IsBlue(item_id)
    rarity_tag = "purple" if is_purple else "blue"

    # Low-req items are always valuable for trading
    if 0 < req_level <= 9:
        return DECISION_KEEP, f"low req q{req_level}"

    # Check for valuable skin
    if has_valuable_skin(item_id):
        return DECISION_KEEP, "valuable skin"

    # --- Market price override (if data available and fresh) ---
    market_price, market_name = get_best_mod_market_price(item_id)
    if market_price > 0:
        if market_price > MARKET_PRICE_EXTRACT_THRESHOLD:
            # Expensive mod on the market -> always expert-extract
            if config.expert_extract:
                return DECISION_EXTRACT, f"{rarity_tag} market extract: {market_name} ~{market_price}g"
            return DECISION_KEEP, f"market valuable: {market_name} ~{market_price}g"
        if market_price < MARKET_PRICE_JUNK_THRESHOLD:
            # Cheap mod on the market -> salvage for mats regardless of static table
            return DECISION_SALVAGE_MATS, f"{rarity_tag} market junk: {market_name} ~{market_price}g"
        # Between 500-5000g: fall through to static table evaluation below

    # Select the appropriate threshold tables based on rarity
    if is_purple:
        weapon_table = VALUABLE_WEAPON_MODS_PURPLE
        shield_table = VALUABLE_SHIELD_OFFHAND_MODS_PURPLE
    else:
        # Blue: strict perfect-roll-only thresholds
        weapon_table = VALUABLE_WEAPON_MODS_BLUE
        shield_table = VALUABLE_SHIELD_OFFHAND_MODS_BLUE

    # Check weapon mods against the rarity-appropriate table
    weapon_mods = check_mods_against_table(item_id, weapon_table)
    shield_mods = check_mods_against_table(item_id, shield_table)
    all_valuable = weapon_mods + shield_mods

    # Remove duplicates (same description from both tables)
    seen = set()
    unique_valuable = []
    for desc, val in all_valuable:
        if desc not in seen:
            seen.add(desc)
            unique_valuable.append((desc, val))

    if unique_valuable:
        mod_summary = ", ".join(m[0] for m in unique_valuable[:3])

        # Low-req items with good mods: keep entire item for trading value
        # (req_level 1-9 are always valuable whole; already caught above,
        #  but guard here too in case the early return is bypassed)
        if 0 < req_level <= 9:
            return DECISION_KEEP, f"q{req_level} {mod_summary}"

        # If item has valuable mods worth extracting (expert salvage)
        # req_level==0 means "no requirement found" (foci, some shields/wands)
        # — these are NOT low-req keepers, they should still be extracted.
        if config.expert_extract:
            return DECISION_EXTRACT, f"{rarity_tag} q{req_level} extract: {mod_summary}"

        # Expert extract disabled but item has valuable mods -> keep it
        return DECISION_KEEP, f"q{req_level} {mod_summary}"

    # Junk: no valuable mods at the required threshold -> lesser salvage for mats
    return DECISION_SALVAGE_MATS, f"{rarity_tag} q{req_level} junk"


def evaluate_armor(item_id):
    """Evaluate a blue/purple armor piece for rune/insignia extraction.
    Returns: (decision, reason)
    """
    # --- Market price override for armor runes/insignias ---
    market_price, market_name = get_best_mod_market_price(item_id)
    if market_price > 0:
        if market_price > MARKET_PRICE_EXTRACT_THRESHOLD:
            return DECISION_EXTRACT, f"market rune extract: {market_name} ~{market_price}g"
        if market_price < MARKET_PRICE_JUNK_THRESHOLD:
            return DECISION_SALVAGE_MATS, f"market rune junk: {market_name} ~{market_price}g"

    # Check modifier-based rune detection
    rune_mods = check_mods_against_table(item_id, VALUABLE_ARMOR_RUNES)
    if rune_mods:
        mod_summary = ", ".join(m[0] for m in rune_mods)
        return DECISION_EXTRACT, f"rune mod: {mod_summary}"

    # Check name-based rune/insignia detection
    has_valuable, desc = has_valuable_rune_or_insignia(item_id)
    if has_valuable:
        return DECISION_EXTRACT, desc

    # No valuable rune -> salvage for mats
    return DECISION_SALVAGE_MATS, "no valuable rune"


# Junk inscriptions that indicate the item should be salvaged/sold, not kept
JUNK_INSCRIPTION_KEYWORDS = (
    "show me the money",
    "too much information",
    "not the best choice",
    "nothing to see here",
    "brawn over brains",
    "leaf on the wind",
)

def _has_junk_inscription(item_id):
    """Check if item has a known junk inscription by name keywords."""
    try:
        name = Item.GetName(item_id) if Item.IsNameReady(item_id) else ""
        name_lower = name.lower()
        for keyword in JUNK_INSCRIPTION_KEYWORDS:
            if keyword in name_lower:
                return True, keyword
    except Exception:
        pass
    return False, ""

def _is_max_damage_weapon(item_id):
    """Check if a gold weapon has max damage roll."""
    try:
        result = Item.Customization.Modifiers.GetModifierValues(item_id, 42920)  # MOD_DAMAGE_RANGE
        if result and result != (None, None, None):
            _, max_dmg, min_dmg = result
            # Max damage varies by weapon type. A rough check: if max_dmg >= weapon's max possible
            # For simplicity: if the damage range spread is <= 3, it's likely max or near-max
            # This is imperfect but conservative (keeps borderline items)
            return True  # We'll check this differently — see below
    except Exception:
        pass
    return False

def evaluate_gold_item(item_id):
    """Evaluate a gold weapon/armor for salvage vs keep.

    KEEP if ANY of these are true:
    - Requirement <= 8 (q7, q8 — valuable for trading)
    - Has a valuable/rare skin
    - Has a perfect valuable mod (inscription, inherent)
    - Has a valuable rune/insignia (armor)

    SALVAGE if ALL of these are true:
    - Requirement >= 9 (or no requirement)
    - No valuable skin
    - No valuable mods OR has junk inscription ("show me the money" etc.)
    """
    is_weapon = Item.Type.IsWeapon(item_id)
    is_armor = Item.Type.IsArmor(item_id)

    if not is_weapon and not is_armor:
        return DECISION_KEEP, "gold non-weapon/armor"

    attr_id, req_level = get_item_requirement(item_id)

    # Low-req golds are always valuable
    if 0 < req_level <= 8:
        return DECISION_KEEP, f"gold q{req_level} (low req)"

    # Valuable skin check
    if has_valuable_skin(item_id):
        return DECISION_KEEP, f"gold q{req_level} valuable skin"

    # Check for junk inscription first — these are always salvage
    is_junk_insc, junk_name = _has_junk_inscription(item_id)

    if is_weapon:
        # Check mods against BOTH purple tables (gold has at least as many mods as purple)
        weapon_mods = check_mods_against_table(item_id, VALUABLE_WEAPON_MODS_PURPLE)
        shield_mods = check_mods_against_table(item_id, VALUABLE_SHIELD_OFFHAND_MODS_PURPLE)
        all_valuable = weapon_mods + shield_mods

        # Market price check
        market_price, market_name = get_best_mod_market_price(item_id)
        if market_price > MARKET_PRICE_EXTRACT_THRESHOLD:
            if config.expert_extract:
                return DECISION_EXTRACT, f"gold q{req_level} market extract: {market_name} ~{market_price}g"
            return DECISION_KEEP, f"gold q{req_level} market valuable: {market_name}"

        if is_junk_insc:
            if all_valuable:
                # Has junk inscription but also a valuable mod — extract the mod
                mod_summary = ", ".join(m[0] for m in all_valuable[:2])
                if config.expert_extract:
                    return DECISION_EXTRACT, f"gold q{req_level} junk insc '{junk_name}' but extract: {mod_summary}"
                return DECISION_KEEP, f"gold q{req_level} {mod_summary}"
            # Junk inscription, no valuable mods — salvage
            return DECISION_SALVAGE_MATS, f"gold q{req_level} junk insc '{junk_name}'"

        if all_valuable:
            mod_summary = ", ".join(m[0] for m in all_valuable[:2])
            if config.expert_extract:
                return DECISION_EXTRACT, f"gold q{req_level} extract: {mod_summary}"
            return DECISION_KEEP, f"gold q{req_level} {mod_summary}"

        # No valuable mods, no junk inscription — salvage for mats
        return DECISION_SALVAGE_MATS, f"gold q{req_level} no valuable mods"

    elif is_armor:
        has_valuable, desc = has_valuable_rune_or_insignia(item_id)
        if has_valuable:
            if config.expert_extract:
                return DECISION_EXTRACT, f"gold armor extract: {desc}"
            return DECISION_KEEP, f"gold armor: {desc}"

        market_price, market_name = get_best_mod_market_price(item_id)
        if market_price > MARKET_PRICE_EXTRACT_THRESHOLD:
            if config.expert_extract:
                return DECISION_EXTRACT, f"gold armor market extract: {market_name}"
            return DECISION_KEEP, f"gold armor market: {market_name}"

        return DECISION_SALVAGE_MATS, f"gold armor q{req_level} no valuable rune"

    return DECISION_KEEP, "gold unknown"


def evaluate_item(item_id):
    """Main evaluation entry point. Returns (decision, reason)."""
    # Green: never touch
    if Item.Rarity.IsGreen(item_id):
        return DECISION_KEEP, "green"
    # Gold: smart evaluation
    if Item.Rarity.IsGold(item_id):
        return evaluate_gold_item(item_id)

    is_weapon = Item.Type.IsWeapon(item_id)
    is_armor = Item.Type.IsArmor(item_id)

    if is_weapon:
        return evaluate_weapon(item_id)
    elif is_armor:
        return evaluate_armor(item_id)

    return DECISION_KEEP, "unknown type"

# endregion


# ===================================================================
# region Kit Selection
# ===================================================================

MODULE_NAME = "SalvageDecision"


def get_expert_kit():
    """Find an expert or perfect salvage kit in bags 1-4."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    items = ItemArray.GetItemArray(bags)
    # Prefer perfect kit, then expert kit
    expert_kits = ItemArray.Filter.ByCondition(
        items, lambda iid: Item.Usage.IsExpertSalvageKit(iid) or Item.Usage.IsPerfectSalvageKit(iid)
    )
    if not expert_kits:
        return 0
    return min(expert_kits, key=lambda iid: Item.Usage.GetUses(iid))


def get_lesser_kit():
    """Find a lesser salvage kit in bags 1-4."""
    return Inventory.GetFirstSalvageKit(use_lesser=True)


# --- Kit sharing between accounts ---
MIN_KITS_TO_SHARE = 2  # Only share if you have at least this many
KIT_REQUEST_COOLDOWN_S = 15.0

# Per-kit-type cooldowns so requesting a lesser kit doesn't block expert kit requests
_kit_cooldowns = {
    SharedCommandType.ShareSalvageKit: 0.0,
    SharedCommandType.ShareExpertKit: 0.0,
    SharedCommandType.ShareIDKit: 0.0,
}


def _request_kit_from_team(command=SharedCommandType.ShareSalvageKit):
    """Send a shared memory message to ONE other account requesting a kit.
    Only sends to the first eligible account (prevents all accounts dropping simultaneously)."""
    now = time.time()
    if now - _kit_cooldowns.get(command, 0.0) < KIT_REQUEST_COOLDOWN_S:
        return
    _kit_cooldowns[command] = now

    my_email = Player.GetAccountEmail()
    my_map = Map.GetMapID()

    try:
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData(sort_results=False)
    except Exception:
        return  # Shared memory not ready

    for acc in all_accounts:
        try:
            acc_email = acc.AccountEmail
            if not acc_email or acc_email == my_email:
                continue
            if not acc.IsAccount or not acc.IsSlotActive:
                continue
            acc_map = acc.AgentData.Map.MapID
            if acc_map != my_map:
                continue
            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, acc_email, command,
                params=(float(my_map), 0.0, 0.0, 0.0),
            )
            return  # Only send to ONE account — first responder wins
        except Exception:
            continue  # Skip accounts with bad/incomplete data


def _try_pickup_ground_kit(filter_fn):
    """Check if there's a kit on the ground nearby matching filter_fn and pick it up."""
    try:
        ground_items = AgentArray.GetItemArray()
        if not ground_items:
            return False
        player_pos = Player.GetXY()
        nearby = AgentArray.Filter.ByDistance(ground_items, player_pos, Range.Earshot.value)
        for agent_id in nearby:
            try:
                item_id = Agent.GetItemAgentItemID(agent_id)
                if item_id == 0:
                    continue
                if filter_fn(item_id):
                    ActionQueueManager().AddAction("ACTION", Inventory.PickUpItem, item_id)
                    return True
            except Exception as e:
                Py4GW.Console.Log(MODULE_NAME, f"Ground kit pickup error: {e}", Py4GW.Console.MessageType.Warning, False)
                continue
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Ground kit scan error: {e}", Py4GW.Console.MessageType.Warning, False)
    return False


def _is_lesser_kit(item_id):
    return Item.Usage.IsSalvageKit(item_id) and Item.Usage.IsLesserKit(item_id)

def _is_expert_kit(item_id):
    return Item.Usage.IsExpertSalvageKit(item_id) or Item.Usage.IsPerfectSalvageKit(item_id)

def _is_id_kit(item_id):
    return Item.Usage.IsIDKit(item_id)

# endregion
