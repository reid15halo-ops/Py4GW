"""
Multibox Commander — Master-only widget for summoning and managing all slave accounts.
Enable via Widget Manager > Automation > Multiboxing > Multibox Commander
"""
import os
import time
import traceback
import configparser
import Py4GW
import PyImGui

MODULE_NAME = "Multibox Commander"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Party, Agent, Routines, SharedCommandType,
)

# Kit target counts for auto-restock
KIT_TARGET_ID = 2       # how many ID kits slaves should have
KIT_TARGET_SALVAGE = 4  # how many salvage kits slaves should have
KIT_TARGET_EXPERT = 2   # how many expert salvage kits slaves should have

# Blessed widget INI path (shared across all clients)
_BLESSED_INI = os.path.join(
    Py4GW.Console.get_projects_path(), "Widgets", "Config", "Blessed_Config.ini"
)

#region Startup Validation
_INIT_ERROR = None
_REQUIRED_METHODS = [
    "summon_all_accounts", "invite_all_accounts", "kick_all_accounts",
    "leave_party_on_all_accounts", "resign_party", "pixel_stack",
    "brute_force_unstuck",
]

try:
    _mb = Routines.Helpers.Multibox
    _missing = [m for m in _REQUIRED_METHODS if not hasattr(_mb, m)]
    if _missing:
        _INIT_ERROR = f"Routines.Helpers.Multibox missing methods: {', '.join(_missing)}"
        Py4GW.Console.Log(MODULE_NAME, _INIT_ERROR, Py4GW.Console.MessageType.Error)
except AttributeError as e:
    _INIT_ERROR = f"Routines.Helpers.Multibox not available: {e}"
    Py4GW.Console.Log(MODULE_NAME, _INIT_ERROR, Py4GW.Console.MessageType.Error)
#endregion

#region State
last_action = ""
last_action_time = 0.0
last_error = ""
last_error_time = 0.0
show_slaves = False
auto_outpost_prep = True  # auto-trigger enable widgets + blessing on outpost entry
_last_map_id = 0
_outpost_prep_done_for_map = 0  # prevent re-triggering on same map
coroutines = GLOBAL_CACHE.Coroutines

# Outpost prep status INI — written by each client's HeroAI widget
_OUTPOST_PREP_INI = os.path.join(
    Py4GW.Console.get_projects_path(), "Widgets", "Config", "OutpostPrep_Status.ini"
)
_outpost_prep_cache: list[dict] = []  # cached status entries
_outpost_prep_cache_time: float = 0.0
_OUTPOST_PREP_CACHE_TTL: float = 5.0  # re-read INI at most every 5 seconds
#endregion

#region Outpost Prep Status Reader
def _read_outpost_prep_status() -> list[dict]:
    """Read all account prep statuses from the shared INI file.

    Returns a list of dicts with keys: email, character, salvage_kits,
    expert_kits, ident_kits, needs_salvage, needs_ident, blessed_pending,
    map_id, timestamp.  Stale entries (>120s old) are excluded.
    """
    global _outpost_prep_cache, _outpost_prep_cache_time

    now = time.time()
    if now - _outpost_prep_cache_time < _OUTPOST_PREP_CACHE_TTL:
        return _outpost_prep_cache

    results = []
    try:
        if not os.path.exists(_OUTPOST_PREP_INI):
            _outpost_prep_cache = results
            _outpost_prep_cache_time = now
            return results

        cp = configparser.ConfigParser()
        cp.read(_OUTPOST_PREP_INI, encoding="utf-8")

        for section in cp.sections():
            try:
                ts = int(cp.get(section, "timestamp", fallback="0"))
                # Exclude stale entries older than 2 minutes
                if now - ts > 120:
                    continue
                entry = {
                    "email": section,
                    "character": cp.get(section, "character", fallback="?"),
                    "salvage_kits": int(cp.get(section, "salvage_kits", fallback="0")),
                    "expert_kits": int(cp.get(section, "expert_kits", fallback="0")),
                    "ident_kits": int(cp.get(section, "ident_kits", fallback="0")),
                    "needs_salvage": cp.get(section, "needs_salvage", fallback="False").strip().lower() == "true",
                    "needs_ident": cp.get(section, "needs_ident", fallback="False").strip().lower() == "true",
                    "blessed_pending": cp.get(section, "blessed_pending", fallback="False").strip().lower() == "true",
                    "map_id": int(cp.get(section, "map_id", fallback="0")),
                    "timestamp": ts,
                }
                results.append(entry)
            except Exception as e:
                Py4GW.Console.Log(MODULE_NAME, f"OutpostPrep parse error for {section}: {e}", Py4GW.Console.MessageType.Warning)
                continue
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"OutpostPrep read error: {e}", Py4GW.Console.MessageType.Warning)

    _outpost_prep_cache = results
    _outpost_prep_cache_time = now
    return results


def _get_slaves_needing_kits() -> list[dict]:
    """Return only slave entries that need restocking, excluding master."""
    my_email = get_my_email()
    statuses = _read_outpost_prep_status()
    return [s for s in statuses
            if s["email"] != my_email
            and (s["needs_salvage"] or s["needs_ident"])]
#endregion

#region Helpers
def get_my_email():
    return Player.GetAccountEmail()

def get_all_slaves():
    my_email = get_my_email()
    accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    return [a for a in accounts if a.AccountEmail != my_email and a.IsSlotActive]

def get_slaves_same_district():
    my_email = get_my_email()
    my_data = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(my_email)
    if not my_data:
        return []
    slaves = get_all_slaves()
    return [s for s in slaves
            if s.AgentData.Map.MapID == my_data.AgentData.Map.MapID
            and s.AgentData.Map.Region == my_data.AgentData.Map.Region
            and s.AgentData.Map.District == my_data.AgentData.Map.District]

def set_action(msg):
    global last_action, last_action_time
    last_action = msg
    last_action_time = time.time()
    Py4GW.Console.Log(MODULE_NAME, msg, Py4GW.Console.MessageType.Info)

def set_error(msg):
    global last_error, last_error_time
    last_error = msg
    last_error_time = time.time()
    Py4GW.Console.Log(MODULE_NAME, f"ERROR: {msg}", Py4GW.Console.MessageType.Error)

def safe_coroutine(gen_func):
    """Wrap a generator coroutine so exceptions surface in the UI instead of dying silently."""
    def wrapper(*args, **kwargs):
        try:
            gen = gen_func(*args, **kwargs)
            # Drive the generator, yielding each value through
            result = next(gen, None)
            while result is not None:
                yield result
                try:
                    result = next(gen, None)
                except Exception as inner_e:
                    set_error(f"{gen_func.__name__}: {inner_e}")
                    Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)
                    return
        except Exception as e:
            set_error(f"{gen_func.__name__}: {e}")
            Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)
    return wrapper
#endregion

#region HeroAI Options
def set_heroai_all(enable: bool):
    """Set Following+Combat+Looting+Targeting+Avoidance on all party members."""
    try:
        my_party_id = GLOBAL_CACHE.Party.GetPartyID()
        count = 0
        for i in range(12):
            account = GLOBAL_CACHE.ShMem.GetAccountDataFromPartyNumber(i)
            if not account or not account.IsSlotActive or account.IsHero:
                continue
            if account.AgentPartyData.PartyID != my_party_id:
                continue
            options = GLOBAL_CACHE.ShMem.GetHeroAIOptionsByPartyNumber(i)
            if not options:
                continue
            options.Following = enable
            options.Combat = enable
            options.Looting = enable
            options.Targeting = enable
            options.Avoidance = enable
            for s in range(8):
                options.Skills[s] = enable
            count += 1
            Py4GW.Console.Log(MODULE_NAME,
                f"{'ON' if enable else 'OFF'}: {account.AgentData.CharacterName} (slot {i})",
                Py4GW.Console.MessageType.Info)
        set_action(f"AI {'ON' if enable else 'OFF'}: {count} accounts")
    except Exception as e:
        set_error(f"set_heroai_all: {e}")

#region Smart Actions
@safe_coroutine
def interact_target_all():
    """All slaves interact with master's current target."""
    target = Player.GetTargetID()
    if target == 0:
        set_error("No target selected!")
        return
    target_name = Agent.GetNameByID(target) if Agent.IsValid(target) else "?"
    set_action(f"All interact: {target_name}")
    sender = get_my_email()
    for slave in get_all_slaves():
        GLOBAL_CACHE.ShMem.SendMessage(sender, slave.AccountEmail, SharedCommandType.InteractWithTarget, (target, 0, 0, 0))
    yield from Routines.Yield.wait(300)
    yield

@safe_coroutine
def dialog_target_all():
    """All slaves take dialog with master's current target."""
    target = Player.GetTargetID()
    if target == 0:
        set_error("No target selected!")
        return
    set_action(f"All take dialog: target {target}")
    sender = get_my_email()
    for slave in get_all_slaves():
        GLOBAL_CACHE.ShMem.SendMessage(sender, slave.AccountEmail, SharedCommandType.TakeDialogWithTarget, (target, 1, 0, 0))
    yield from Routines.Yield.wait(300)
    yield

#region Stuck Detection
_slave_positions = {}  # email -> (x, y, timestamp)
STUCK_THRESHOLD_SEC = 10
STUCK_DISTANCE = 50

def check_stuck_slaves():
    """Check if any slave hasn't moved for STUCK_THRESHOLD_SEC seconds."""
    global _slave_positions
    now = time.time()
    slaves = get_all_slaves()
    stuck_names = []

    for s in slaves:
        email = s.AccountEmail
        x = s.AgentData.Pos.x
        y = s.AgentData.Pos.y
        name = s.AgentData.CharacterName or email[:12]

        if email in _slave_positions:
            old_x, old_y, old_time = _slave_positions[email]
            dist = ((x - old_x)**2 + (y - old_y)**2)**0.5
            elapsed = now - old_time

            if dist < STUCK_DISTANCE and elapsed > STUCK_THRESHOLD_SEC:
                stuck_names.append(name)
                _slave_positions[email] = (x, y, now)
            elif dist >= STUCK_DISTANCE:
                _slave_positions[email] = (x, y, now)
        else:
            _slave_positions[email] = (x, y, now)

    return stuck_names

#region Coroutines — delegates to Routines.Helpers.Multibox
@safe_coroutine
def unstuck_all():
    set_action("Unstucking all slaves...")
    yield from Routines.Helpers.Multibox.brute_force_unstuck()
    set_action("Unstuck sent!")
    yield

@safe_coroutine
def summon_all():
    set_action("Summoning all slaves...")
    yield from Routines.Helpers.Multibox.summon_all_accounts()
    set_action("Summon sent!")
    yield

@safe_coroutine
def invite_all():
    set_action("Inviting all slaves...")
    yield from Routines.Helpers.Multibox.invite_all_accounts()
    set_action("Invites sent!")
    yield

@safe_coroutine
def kick_all():
    set_action("Kicking all...")
    yield from Routines.Helpers.Multibox.kick_all_accounts()
    set_action("All kicked!")
    yield

@safe_coroutine
def leave_all():
    set_action("All leaving party...")
    yield from Routines.Helpers.Multibox.leave_party_on_all_accounts()
    set_action("All left!")
    yield

@safe_coroutine
def resign_all():
    set_action("Resigning all...")
    yield from Routines.Helpers.Multibox.resign_party()
    set_action("All resigned!")
    yield

@safe_coroutine
def pixel_stack():
    set_action("Pixel stacking...")
    yield from Routines.Helpers.Multibox.pixel_stack()
    set_action("Stack sent!")
    yield

# Widget names to auto-enable on slaves during full setup
_SLAVE_WIDGETS = ["AutoLootManager", "Blessed"]

def _send_enable_widgets_sync():
    """Send EnableWidget messages to all slaves (non-coroutine, no delays)."""
    sender = get_my_email()
    if not sender:
        return
    for widget_name in _SLAVE_WIDGETS:
        for slave in get_all_slaves():
            GLOBAL_CACHE.ShMem.SendMessage(
                sender, slave.AccountEmail, SharedCommandType.EnableWidget,
                (0, 0, 0, 0), (widget_name, "", "", ""))

@safe_coroutine
def enable_slave_widgets():
    """Enable essential widgets on all slaves (coroutine with delays)."""
    set_action("Enabling slave widgets...")
    sender = get_my_email()
    for widget_name in _SLAVE_WIDGETS:
        for slave in get_all_slaves():
            GLOBAL_CACHE.ShMem.SendMessage(
                sender, slave.AccountEmail, SharedCommandType.EnableWidget,
                (0, 0, 0, 0), (widget_name, "", "", ""))
        yield from Routines.Yield.wait(300)
    set_action(f"Enabled {', '.join(_SLAVE_WIDGETS)} on all slaves")
    yield

@safe_coroutine
def collect_valuables():
    """Send DropValuables command to all slaves — they will drop flagged valuable items."""
    set_action("Requesting valuables from all slaves...")
    sender = get_my_email()
    my_map = Map.GetMapID()
    count = 0
    for slave in get_slaves_same_district():
        GLOBAL_CACHE.ShMem.SendMessage(
            sender, slave.AccountEmail, SharedCommandType.DropValuables,
            (float(my_map), 0.0, 0.0, 0.0))
        count += 1
        yield from Routines.Yield.wait(300)
    set_action(f"Collect Valuables sent to {count} slaves")
    yield

@safe_coroutine
def identify_all():
    """Send IdentifyItems to all slaves."""
    set_action("Identifying on all slaves...")
    sender = get_my_email()
    count = 0
    for slave in get_all_slaves():
        GLOBAL_CACHE.ShMem.SendMessage(
            sender, slave.AccountEmail, SharedCommandType.IdentifyItems,
            (0, 0, 0, 0))
        count += 1
        yield from Routines.Yield.wait(300)
    set_action(f"Identify sent to {count} slaves")
    yield

@safe_coroutine
def buy_kits_all():
    """Send MerchantItems to all slaves — target a merchant NPC first."""
    target = Player.GetTargetID()
    if target == 0 or not Agent.IsValid(target):
        set_error("Target a merchant NPC first!")
        return
    x, y = Agent.GetXY(target)
    # Send merchant model ID so slaves find the EXACT NPC, not just nearest to (x,y)
    merchant_model_id = Agent.GetPlayerNumber(target)
    sender = get_my_email()
    count = 0
    for slave in get_all_slaves():
        GLOBAL_CACHE.ShMem.SendMessage(
            sender, slave.AccountEmail, SharedCommandType.MerchantItems,
            (x, y, KIT_TARGET_ID, KIT_TARGET_SALVAGE),
            ExtraData=(str(KIT_TARGET_EXPERT), str(merchant_model_id)))
        count += 1
        yield from Routines.Yield.wait(500)
    set_action(f"Buy Kits sent to {count} slaves")
    yield

def bless_all():
    """Trigger the Blessed widget on all clients by writing the shared run flag."""
    try:
        os.makedirs(os.path.dirname(_BLESSED_INI), exist_ok=True)
        cp = configparser.ConfigParser()
        cp.read(_BLESSED_INI)
        if not cp.has_section("BlessingRun"):
            cp.add_section("BlessingRun")
        cp.set("BlessingRun", "Enabled", "True")
        with open(_BLESSED_INI, "w") as f:
            cp.write(f)
        set_action("Blessing triggered on all clients!")
    except Exception as e:
        set_error(f"bless_all: {e}")

@safe_coroutine
def full_setup():
    if not Map.IsOutpost():
        set_error("Must be in outpost!")
        return

    # Enable essential widgets on slaves first
    set_action("0/5 Enabling widgets...")
    _send_enable_widgets_sync()
    yield from Routines.Yield.wait(500)

    if Party.IsHardModeUnlocked() and not Party.IsHardMode():
        Party.SetHardMode()
        set_action("Hard Mode ON")
        yield from Routines.Yield.wait(500)

    set_action("1/5 Summoning...")
    yield from Routines.Helpers.Multibox.summon_all_accounts()
    yield from Routines.Yield.wait(3000)

    for i in range(90):
        arrived = len(get_slaves_same_district())
        total = len(get_all_slaves())
        if total > 0 and arrived >= total:
            break
        if i % 10 == 0:
            set_action(f"Waiting... {arrived}/{total}")
        yield from Routines.Yield.wait(500)

    yield from Routines.Yield.wait(1000)
    set_action("2/5 Inviting...")
    yield from Routines.Helpers.Multibox.invite_all_accounts()
    yield from Routines.Yield.wait(3000)

    for i in range(60):
        if Party.GetPartySize() >= len(get_all_slaves()) + 1:
            break
        yield from Routines.Yield.wait(500)

    # Buy kits — only if master has a target (merchant NPC)
    target = Player.GetTargetID()
    if target != 0 and Agent.IsValid(target):
        set_action("3/5 Buying kits...")
        x, y = Agent.GetXY(target)
        merchant_model_id = Agent.GetPlayerNumber(target)
        sender = get_my_email()
        for slave in get_all_slaves():
            GLOBAL_CACHE.ShMem.SendMessage(
                sender, slave.AccountEmail, SharedCommandType.MerchantItems,
                (x, y, KIT_TARGET_ID, KIT_TARGET_SALVAGE),
                ExtraData=(str(KIT_TARGET_EXPERT), str(merchant_model_id)))
            yield from Routines.Yield.wait(500)
        yield from Routines.Yield.wait(2000)
    else:
        set_action("3/5 Skipped kits (no merchant targeted)")
        yield from Routines.Yield.wait(500)

    # Trigger blessing on all clients
    set_action("4/5 Blessing...")
    bless_all()
    yield from Routines.Yield.wait(5000)

    set_action(f"5/5 Done! Party: {Party.GetPartySize()}")
    yield
#endregion

#region UI
def draw_window():
    global show_slaves, auto_outpost_prep

    if not Player.IsPlayerLoaded():
        return

    if PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.AlwaysAutoResize):
        # Init error — blocks entire widget with big red message
        if _INIT_ERROR:
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.15, 0.15, 1.0))
            PyImGui.text("BROKEN: " + _INIT_ERROR)
            PyImGui.text("Routines.Helpers.Multibox is not exposed.")
            PyImGui.text("Fix Py4GWCoreLib/Routines.py and reload.")
            PyImGui.pop_style_color(1)
            PyImGui.end()
            return

        slaves = get_all_slaves()
        same_map = get_slaves_same_district()
        party_size = Party.GetPartySize()
        is_outpost = Map.IsOutpost()

        PyImGui.text(f"Party: {party_size}  Slaves: {len(same_map)}/{len(slaves)}  {'[Outpost]' if is_outpost else '[Explorable]'}")
        PyImGui.separator()

        # Big summon button
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.15, 0.45, 0.15, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, (0.2, 0.6, 0.2, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, (0.1, 0.35, 0.1, 1.0))
        if PyImGui.button("SUMMON ALL", 270, 32):
            coroutines.append(summon_all())
        PyImGui.pop_style_color(3)

        if PyImGui.button("Invite All", 130, 24):
            coroutines.append(invite_all())
        PyImGui.same_line(0, 10)
        if PyImGui.button("Kick All", 130, 24):
            coroutines.append(kick_all())

        # HeroAI control
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.4, 0.25, 0.1, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, (0.5, 0.35, 0.15, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, (0.3, 0.2, 0.08, 1.0))
        if PyImGui.button("AI ON", 130, 24):
            set_heroai_all(True)
        PyImGui.pop_style_color(3)
        PyImGui.same_line(0, 10)
        if PyImGui.button("AI OFF", 130, 24):
            set_heroai_all(False)

        if PyImGui.button("Pixel Stack", 130, 24):
            coroutines.append(pixel_stack())
        PyImGui.same_line(0, 10)
        if PyImGui.button("Unstuck All", 130, 24):
            coroutines.append(unstuck_all())

        if PyImGui.button("Resign All", 130, 24):
            coroutines.append(resign_all())
        PyImGui.same_line(0, 10)
        if PyImGui.button("Leave Party", 130, 24):
            coroutines.append(leave_all())

        hm_label = "NM Mode" if Party.IsHardMode() else "HM Mode"
        if PyImGui.button(hm_label, 130, 24):
            if Party.IsHardMode():
                Party.SetNormalMode()
                set_action("Normal Mode")
            elif Party.IsHardModeUnlocked():
                Party.SetHardMode()
                set_action("Hard Mode ON")

        PyImGui.separator()

        # Smart Actions — target your shrine/NPC first, then click
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.45, 0.15, 0.4, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, (0.55, 0.2, 0.5, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, (0.35, 0.1, 0.3, 1.0))
        if PyImGui.button("All Interact Target", 130, 24):
            coroutines.append(interact_target_all())
        PyImGui.same_line(0, 10)
        if PyImGui.button("All Take Dialog", 130, 24):
            coroutines.append(dialog_target_all())
        PyImGui.pop_style_color(3)

        # Outpost actions — restock kits, blessing
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.15, 0.35, 0.45, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, (0.2, 0.45, 0.55, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, (0.1, 0.25, 0.35, 1.0))
        if PyImGui.button("ID All", 65, 24):
            coroutines.append(identify_all())
        PyImGui.same_line(0, 4)
        if PyImGui.button("Buy Kits", 65, 24):
            coroutines.append(buy_kits_all())
        PyImGui.same_line(0, 4)
        if PyImGui.button("Bless", 60, 24):
            bless_all()
        PyImGui.same_line(0, 4)
        if PyImGui.button("Widgets", 65, 24):
            coroutines.append(enable_slave_widgets())
        PyImGui.pop_style_color(3)

        # Collect Valuables — gold/yellow themed
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.55, 0.45, 0.1, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, (0.65, 0.55, 0.15, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, (0.45, 0.35, 0.08, 1.0))
        if PyImGui.button("Collect Valuables", 270, 24):
            coroutines.append(collect_valuables())
        PyImGui.pop_style_color(3)

        PyImGui.separator()

        # Full setup
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, (0.1, 0.3, 0.55, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, (0.15, 0.4, 0.65, 1.0))
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, (0.08, 0.25, 0.45, 1.0))
        if PyImGui.button("FULL SETUP", 270, 28):
            coroutines.append(full_setup())
        PyImGui.pop_style_color(3)

        # Error line — red, stays visible 30s
        if last_error and (time.time() - last_error_time) < 30:
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.15, 0.15, 1.0))
            PyImGui.text(f"ERR: {last_error}")
            PyImGui.pop_style_color(1)

        # Status line
        if last_action and (time.time() - last_action_time) < 15:
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.85, 0.0, 1.0))
            PyImGui.text(f" {last_action}")
            PyImGui.pop_style_color(1)

        # Auto stuck detection (runs every frame, lightweight)
        if Map.IsExplorable() and not Map.IsMapLoading():
            stuck = check_stuck_slaves()
            if stuck:
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.2, 0.2, 1.0))
                PyImGui.text(f"STUCK: {', '.join(stuck)}")
                PyImGui.pop_style_color(1)

        # Current target info
        target_id = Player.GetTargetID()
        if target_id and Agent.IsValid(target_id):
            target_name = Agent.GetNameByID(target_id) if Agent.IsNameReady(target_id) else f"Agent {target_id}"
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.8, 0.6, 1.0, 1.0))
            PyImGui.text(f"Target: {target_name}")
            PyImGui.pop_style_color(1)

        # Outpost prep status — show which slaves need kits (outpost only)
        if is_outpost:
            needs_kits = _get_slaves_needing_kits()
            if needs_kits:
                PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.6, 0.15, 1.0))
                for entry in needs_kits:
                    parts = []
                    if entry["needs_salvage"]:
                        parts.append(f"salvage:{entry['salvage_kits']}")
                    if entry["needs_ident"]:
                        parts.append(f"ident:{entry['ident_kits']}")
                    name = entry["character"][:14] if entry["character"] else entry["email"][:14]
                    PyImGui.text(f"KITS: {name} needs {', '.join(parts)}")
                PyImGui.pop_style_color(1)

        # Slave list with HP bars
        auto_outpost_prep = PyImGui.checkbox("Auto outpost prep", auto_outpost_prep)
        PyImGui.same_line(0, 10)
        show_slaves = PyImGui.checkbox("Show slaves", show_slaves)
        if show_slaves and slaves:
            if PyImGui.begin_table("##slave_table", 4, PyImGui.TableFlags.RowBg | PyImGui.TableFlags.BordersInnerH):
                PyImGui.table_setup_column("", PyImGui.TableColumnFlags.WidthFixed, 16)
                PyImGui.table_setup_column("Name", PyImGui.TableColumnFlags.WidthStretch)
                PyImGui.table_setup_column("HP", PyImGui.TableColumnFlags.WidthFixed, 45)
                PyImGui.table_setup_column("E", PyImGui.TableColumnFlags.WidthFixed, 35)

                for s in slaves:
                    in_dist = any(sm.AccountEmail == s.AccountEmail for sm in same_map)
                    name = s.AgentData.CharacterName or "?"
                    hp = s.AgentData.Health.Current
                    max_hp = s.AgentData.Health.Max
                    energy = s.AgentData.Energy.Current
                    max_energy = s.AgentData.Energy.Max

                    PyImGui.table_next_row()
                    PyImGui.table_set_column_index(0)
                    if in_dist:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 1.0, 0.3, 1.0))
                        PyImGui.text("+")
                    else:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.6, 0.3, 0.3, 1.0))
                        PyImGui.text("-")
                    PyImGui.pop_style_color(1)

                    PyImGui.table_set_column_index(1)
                    PyImGui.text(name[:14])

                    PyImGui.table_set_column_index(2)
                    hp_pct = hp / max_hp if max_hp > 0 else 0
                    if hp_pct < 0.3:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.2, 0.2, 1.0))
                    elif hp_pct < 0.6:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.8, 0.0, 1.0))
                    else:
                        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 1.0, 0.3, 1.0))
                    PyImGui.text(f"{hp_pct:.0%}")
                    PyImGui.pop_style_color(1)

                    PyImGui.table_set_column_index(3)
                    e_pct = energy / max_energy if max_energy > 0 else 0
                    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 0.6, 1.0, 1.0))
                    PyImGui.text(f"{e_pct:.0%}")
                    PyImGui.pop_style_color(1)

                PyImGui.end_table()

    PyImGui.end()
#endregion

#region Entry Points
def check_outpost_auto_prep():
    """Auto-trigger widget enable + blessing when master enters a new outpost."""
    global _last_map_id, _outpost_prep_done_for_map
    if not auto_outpost_prep or not Player.IsPlayerLoaded():
        return
    if Map.IsMapLoading() or not Map.IsMapReady():
        return

    current_map = Map.GetMapID()
    if current_map == _last_map_id:
        return
    _last_map_id = current_map

    if not Map.IsOutpost():
        return
    if current_map == _outpost_prep_done_for_map:
        return
    _outpost_prep_done_for_map = current_map

    # Enable essential widgets on slaves + trigger blessing
    _send_enable_widgets_sync()
    bless_all()
    set_action(f"Auto-prep: widgets + blessing (map {current_map})")

def main():
    try:
        check_outpost_auto_prep()
        draw_window()
    except Exception as e:
        # Last-resort catch — should never reach here now, but if it does, show it
        set_error(f"draw_window: {e}")
        Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)

def configure():
    PyImGui.text("Multibox Commander")
    PyImGui.text("Master widget: summon, invite, and manage all slave accounts.")
    if _INIT_ERROR:
        PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (1.0, 0.15, 0.15, 1.0))
        PyImGui.text(f"BROKEN: {_INIT_ERROR}")
        PyImGui.pop_style_color(1)
#endregion
