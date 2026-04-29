"""Party PCon Manager — broadcasts party-wide consumables via shared memory.

Runs as a standalone widget. Every N seconds it checks whether the local
character is missing a configured PCon buff. If so, it broadcasts a
SharedCommandType.PCon message to *all* registered accounts (including itself).
The built-in Messaging widget on each client handles consumption locally.
"""

import os
import time

import Py4GW  # type: ignore
from Py4GWCoreLib import (
    ConsoleLog,
    Effects,
    GLOBAL_CACHE,
    IniHandler,
    Map,
    Player,
    PyImGui,
    Routines,
    SharedCommandType,
)
from Py4GWCoreLib.enums import ModelID
from HeroAI.constants import MASTER_EMAIL

# ─── Defensive stubs ─────────────────────────────────────────────────────────
# If heavy imports fail, the widget manager still sees a valid widget.
def main():
    pass

def configure():
    pass


# ─── Module metadata ─────────────────────────────────────────────────────────
MODULE_NAME = "Party PCon Manager"
MODULE_ICON = "Textures/Module_Icons/Pycons.png"
OPTIONAL = True

# ─── INI persistence ─────────────────────────────────────────────────────────
_BASE_DIR = os.path.join(Py4GW.Console.get_projects_path(), "Widgets", "Config")
os.makedirs(_BASE_DIR, exist_ok=True)
_INI_PATH = os.path.join(_BASE_DIR, "party_pcon_manager.ini")
_ini = IniHandler(_INI_PATH)

# ─── Configuration helpers ───────────────────────────────────────────────────

def _read_bool(section: str, key: str, default: bool = False) -> bool:
    return _ini.read_bool(section, key, default)


def _write_bool(section: str, key: str, value: bool) -> None:
    _ini.write_key(section, key, str(value))


def _read_int(section: str, key: str, default: int = 0) -> int:
    return _ini.read_int(section, key, default)


def _write_int(section: str, key: str, value: int) -> None:
    _ini.write_key(section, key, str(value))


# ─── PCon definitions ────────────────────────────────────────────────────────
# Each entry: (label, model_id_enum, skill_name)
_PCONS = [
    ("Birthday Cupcake",   ModelID.Birthday_Cupcake,          "Birthday_Cupcake_skill"),
    ("Golden Egg",         ModelID.Golden_Egg,                "Golden_Egg_skill"),
    ("Candy Corn",         ModelID.Candy_Corn,                "Candy_Corn_skill"),
    ("Candy Apple",        ModelID.Candy_Apple,               "Candy_Apple_skill"),
    ("Pumpkin Pie",        ModelID.Slice_Of_Pumpkin_Pie,      "Pie_Induced_Ecstasy"),
    ("Drake Kabob",        ModelID.Drake_Kabob,               "Drake_Skin"),
    ("Skalefin Soup",      ModelID.Bowl_Of_Skalefin_Soup,     "Skale_Vigor"),
    ("Pahnai Salad",       ModelID.Pahnai_Salad,              "Pahnai_Salad_item_effect"),
    ("War Supplies",       ModelID.War_Supplies,              "Well_Supplied"),
    # ─── Conset (party-wide consumable set) ────────────────────────────────────
    ("Essence of Celerity", ModelID.Essence_Of_Celerity,      "Essence_of_Celerity_item_effect"),
    ("Armor of Salvation",  ModelID.Armor_Of_Salvation,       "Armor_of_Salvation_item_effect"),
    ("Grail of Might",      ModelID.Grail_Of_Might,           "Grail_of_Might_item_effect"),
]

# Alcohol is handled separately because it uses PyEffects.GetAlcoholLevel()
# instead of Effects.HasEffect(). Order matters: earlier = higher priority.
_ALCOHOL_MODELS = [
    ModelID.Aged_Dwarven_Ale, ModelID.Aged_Hunters_Ale, ModelID.Bottle_Of_Grog,
    ModelID.Flask_Of_Firewater, ModelID.Keg_Of_Aged_Hunters_Ale,
    ModelID.Krytan_Brandy, ModelID.Spiked_Eggnog,
    ModelID.Bottle_Of_Rice_Wine, ModelID.Eggnog, ModelID.Dwarven_Ale,
    ModelID.Hard_Apple_Cider, ModelID.Hunters_Ale, ModelID.Bottle_Of_Juniberry_Gin,
    ModelID.Shamrock_Ale, ModelID.Bottle_Of_Vabbian_Wine, ModelID.Vial_Of_Absinthe,
    ModelID.Witchs_Brew, ModelID.Zehtukas_Jug,
]

# Load enabled states from INI
for _label, _model, _skill in _PCONS:
    _key = _label.lower().replace(" ", "_")
    globals()[f"_enabled_{_key}"] = _read_bool("PCons", _key, False)

_enabled_alcohol = _read_bool("PCons", "alcohol", False)
_ALCOHOL_TARGET_LEVEL = _read_int("PCons", "alcohol_target_level", 2)
if _ALCOHOL_TARGET_LEVEL < 1:
    _ALCOHOL_TARGET_LEVEL = 1
if _ALCOHOL_TARGET_LEVEL > 5:
    _ALCOHOL_TARGET_LEVEL = 5

# Default: Birthday Cupcake on, everything else off
if not os.path.exists(_INI_PATH):
    _write_bool("PCons", "birthday_cupcake", True)
    globals()["_enabled_birthday_cupcake"] = True

# Check interval (seconds)
_CHECK_INTERVAL_S = _read_int("General", "check_interval_s", 60)
if _CHECK_INTERVAL_S < 5:
    _CHECK_INTERVAL_S = 5

# Widget toggle
_WIDGET_ENABLED = _read_bool("General", "enabled", False)

# Only broadcast when in explorable maps (not outposts)
_ONLY_EXPLORABLE = _read_bool("General", "only_explorable", False)

# ─── Conset one-shot tracking ────────────────────────────────────────────────
# Conset items (Essence, Armor, Grail) are party-wide 30-minute buffs.
# They should only be consumed ONCE per map/instance, not re-broadcasted
# when the buff expires. Normal PCons (cupcake, egg, etc.) are continuous.
_CONSET_KEYS = {
    "essence_of_celerity",
    "armor_of_salvation",
    "grail_of_might",
}
_conset_used: set[str] = set()
_last_map_id: int = 0

# ─── Runtime state ───────────────────────────────────────────────────────────
_last_check_time: float = 0.0


# ─── Master-only guard ───────────────────────────────────────────────────────
# Pcon broadcasting must originate from the master only — slaves enabling this
# widget would multiply broadcasts N times and burn pcons across the party.
def _is_master() -> bool:
    try:
        return (Player.GetAccountEmail() or "").strip().lower() == MASTER_EMAIL.strip().lower()
    except Exception:
        return False


# ─── Core logic ──────────────────────────────────────────────────────────────

def _broadcast_pcon(model_id: int, skill_id: int) -> None:
    """Send PCon message to every registered account."""
    sender = Player.GetAccountEmail()
    accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    params = (float(model_id), float(skill_id), 0.0, 0.0)

    sent = 0
    for account in accounts:
        if not account or not account.AccountEmail:
            continue
        try:
            GLOBAL_CACHE.ShMem.SendMessage(
                sender,
                account.AccountEmail,
                SharedCommandType.PCon,
                params,
            )
            sent += 1
        except Exception as e:
            ConsoleLog(
                MODULE_NAME,
                f"Failed to send PCon to {account.AccountEmail}: {e}",
                Py4GW.Console.MessageType.Error,
            )

    if sent:
        ConsoleLog(
            MODULE_NAME,
            f"Broadcasted PCon (model {model_id}) to {sent} account(s).",
            Py4GW.Console.MessageType.Info,
        )


def _should_broadcast() -> bool:
    """Return True if it's time for the next periodic check."""
    global _last_check_time
    return (time.time() - _last_check_time) >= _CHECK_INTERVAL_S


def _check_and_broadcast() -> None:
    """Check local buffs and broadcast missing ones to the whole party.

    Conset items are one-shot: once broadcasted in the current map they
    are skipped until the map changes or a manual "Broadcast Now" resets
    the tracker.
    """
    global _last_check_time

    if not Player.IsPlayerLoaded():
        return
    if _ONLY_EXPLORABLE and Map.IsOutpost():
        return

    agent_id = Player.GetAgentID()
    _last_check_time = time.time()

    for label, model_enum, skill_name in _PCONS:
        key = label.lower().replace(" ", "_")
        if not globals().get(f"_enabled_{key}", False):
            continue

        # Conset one-shot guard: skip if already used this session/map
        if key in _CONSET_KEYS and key in _conset_used:
            continue

        try:
            skill_id = GLOBAL_CACHE.Skill.GetID(skill_name)
        except Exception:
            continue

        has_effect = Effects.HasEffect(agent_id, skill_id)
        if not has_effect:
            model_id = model_enum.value
            _broadcast_pcon(model_id, skill_id)
            # Mark conset items as used so they don't spam
            if key in _CONSET_KEYS:
                _conset_used.add(key)
            # Note: we intentionally do NOT sleep here; main() is not a generator.
            # If multiple PCons are missing they will all broadcast in one frame.

    # ─── Alcohol (handled separately via PyEffects) ────────────────────────────
    if _enabled_alcohol:
        try:
            import PyEffects
            drunk_level = PyEffects.PyEffects.GetAlcoholLevel()
        except Exception:
            drunk_level = 0
        if drunk_level < _ALCOHOL_TARGET_LEVEL:
            # Only broadcast alcohol if this account has an alcohol-boosted skill
            try:
                from Py4GWCoreLib.Skillbar import SkillBar
                _ALCOHOL_SKILL_NAMES = [
                    "Drunken_Master", "Dwarven_Stability", "Feel_No_Pain",
                    "Drunken_Blow", "Frenzied_Defense",
                ]
                _ALCOHOL_SKILL_IDS = set()
                for name in _ALCOHOL_SKILL_NAMES:
                    try:
                        sid = GLOBAL_CACHE.Skill.GetID(name)
                        if sid:
                            _ALCOHOL_SKILL_IDS.add(int(sid))
                    except Exception:
                        pass

                has_alcohol_skill = False
                for slot in range(1, 9):
                    try:
                        equipped = int(SkillBar.GetSkillIDBySlot(slot) or 0)
                        if equipped in _ALCOHOL_SKILL_IDS:
                            has_alcohol_skill = True
                            break
                    except Exception:
                        pass

                if not has_alcohol_skill:
                    ConsoleLog(
                        MODULE_NAME,
                        "Skipping alcohol broadcast: no alcohol-boosted skill on skillbar.",
                        Py4GW.Console.MessageType.Info,
                    )
                    return
            except Exception:
                pass

            alcohol_model = 0
            for model in _ALCOHOL_MODELS:
                if GLOBAL_CACHE.Inventory.GetModelCount(model.value) > 0:
                    alcohol_model = model.value
                    break
            if alcohol_model:
                # skill_id=0 signals "alcohol" to the receiver
                _broadcast_pcon(alcohol_model, 0)


def broadcast_all_now() -> None:
    """Manual trigger: broadcast all enabled PCons immediately.

    Also resets the conset one-shot tracker so conset items can be
    re-broadcasted on demand.
    """
    global _last_check_time, _conset_used
    if not _is_master():
        return
    _conset_used.clear()
    _last_check_time = time.time() - _CHECK_INTERVAL_S  # Force immediate
    _check_and_broadcast()


# ─── ImGui UI ────────────────────────────────────────────────────────────────

def _draw_config_ui() -> None:
    """Draw the configuration window.

    NOTE: PyImGui.checkbox() in Py4GW returns a single bool (the new state),
    NOT a (changed, value) tuple like standard Dear ImGui.
    """
    global _WIDGET_ENABLED, _CHECK_INTERVAL_S, _ONLY_EXPLORABLE, _enabled_alcohol, _ALCOHOL_TARGET_LEVEL

    if not PyImGui.begin(MODULE_NAME):
        PyImGui.end()
        return

    is_master = _is_master()

    # Master-only widget: slaves cannot enable or broadcast.
    if not is_master:
        PyImGui.text(f"Master-only widget. Master: {MASTER_EMAIL}")
        if _WIDGET_ENABLED:
            _WIDGET_ENABLED = False
            _write_bool("General", "enabled", False)
        PyImGui.checkbox("Enabled (disabled on slaves)", False)
    else:
        prev = _WIDGET_ENABLED
        _WIDGET_ENABLED = PyImGui.checkbox("Enabled", _WIDGET_ENABLED)
        if _WIDGET_ENABLED != prev:
            _write_bool("General", "enabled", _WIDGET_ENABLED)

    # Interval slider
    PyImGui.text("Check Interval (seconds):")
    prev = _CHECK_INTERVAL_S
    _CHECK_INTERVAL_S = PyImGui.slider_int(
        "##interval", _CHECK_INTERVAL_S, 5, 300
    )
    if _CHECK_INTERVAL_S != prev:
        _write_int("General", "check_interval_s", _CHECK_INTERVAL_S)

    # Only explorable toggle
    prev = _ONLY_EXPLORABLE
    _ONLY_EXPLORABLE = PyImGui.checkbox(
        "Only in Explorable Maps", _ONLY_EXPLORABLE
    )
    if _ONLY_EXPLORABLE != prev:
        _write_bool("General", "only_explorable", _ONLY_EXPLORABLE)

    PyImGui.separator()
    PyImGui.text("PCons to maintain party-wide:")

    for label, _model, _skill in _PCONS:
        key = label.lower().replace(" ", "_")
        var_name = f"_enabled_{key}"
        current = globals().get(var_name, False)
        new_val = PyImGui.checkbox(label, current)
        if new_val != current:
            globals()[var_name] = new_val
            _write_bool("PCons", key, new_val)

    # ─── Alcohol toggle + target level ─────────────────────────────────────────
    PyImGui.separator()
    prev = _enabled_alcohol
    _enabled_alcohol = PyImGui.checkbox("Alcohol (drunkard)", _enabled_alcohol)
    if _enabled_alcohol != prev:
        _write_bool("PCons", "alcohol", _enabled_alcohol)

    if _enabled_alcohol:
        PyImGui.text("Target drunk level:")
        prev = _ALCOHOL_TARGET_LEVEL
        _ALCOHOL_TARGET_LEVEL = PyImGui.slider_int(
            "##alcohol_target", _ALCOHOL_TARGET_LEVEL, 1, 5
        )
        if _ALCOHOL_TARGET_LEVEL != prev:
            _write_int("PCons", "alcohol_target_level", _ALCOHOL_TARGET_LEVEL)

    PyImGui.separator()
    if is_master:
        if PyImGui.button("Broadcast Now"):
            broadcast_all_now()
    else:
        PyImGui.text("Broadcast Now: master-only")

    # Status
    PyImGui.separator()
    remaining = max(0, _CHECK_INTERVAL_S - int(time.time() - _last_check_time))
    PyImGui.text(f"Next check in: {remaining}s")
    try:
        PyImGui.text(f"Account: {Player.GetAccountEmail()}")
    except Exception:
        PyImGui.text("Account: (not loaded)")

    PyImGui.end()


# ─── Widget entry points (overrides the stubs above) ─────────────────────────

def configure() -> None:
    """Called by the widget manager when the cog icon is clicked."""
    try:
        _draw_config_ui()
    except Exception as e:
        ConsoleLog(MODULE_NAME, f"Config UI error: {e}", Py4GW.Console.MessageType.Error)


def main() -> None:
    """Called by the widget manager every frame while the widget is enabled."""
    global _last_map_id

    if not _WIDGET_ENABLED:
        return
    if not _is_master():
        return
    if not Routines.Checks.Map.MapValid():
        return
    if not Player.IsPlayerLoaded():
        return

    # Reset conset tracker when entering a new map so they can be used again
    current_map = Map.GetMapID()
    if current_map != _last_map_id:
        _last_map_id = current_map
        if _conset_used:
            _conset_used.clear()
            ConsoleLog(
                MODULE_NAME,
                "Map changed — conset tracker reset.",
                Py4GW.Console.MessageType.Info,
            )

    try:
        if _should_broadcast():
            _check_and_broadcast()
    except Exception as e:
        ConsoleLog(
            MODULE_NAME,
            f"Error in main loop: {e}",
            Py4GW.Console.MessageType.Error,
        )


if __name__ == "__main__":
    main()
