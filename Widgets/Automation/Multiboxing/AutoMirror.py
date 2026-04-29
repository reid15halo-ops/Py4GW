"""
Auto Mirror — Automatically mirrors master's actions to all slaves.
Detects: NPC interactions, quest dialog acceptance, movement, shrine visits.
No buttons needed — runs passively in the background.

Enable via Widget Manager > Automation > Multiboxing > Auto Mirror
Only run on the MASTER account.
"""
import time
import traceback
import Py4GW
import PyImGui

MODULE_NAME = "Auto Mirror"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Party, Agent, AgentArray,
    Routines, SharedCommandType, UIManager,
)
from HeroAI.constants import MASTER_EMAIL

#region State
class MirrorState:
    def __init__(self):
        self.enabled = True
        self.last_target_id = 0
        self.last_dialog_time = 0.0
        self.last_interact_time = 0.0
        self.last_interact_target = 0
        self.mirror_count = 0
        self.last_action = ""
        self.last_action_time = 0.0
        # Stuck detection
        self.slave_positions = {}  # email -> (x, y, time)

state = MirrorState()
coroutines = GLOBAL_CACHE.Coroutines

# Throttle: don't spam commands faster than this
INTERACT_COOLDOWN = 3.0  # seconds between mirrored interactions
DIALOG_COOLDOWN = 2.0
STUCK_CHECK_INTERVAL = 5.0  # check every 5 seconds
STUCK_THRESHOLD = 15.0  # seconds without movement
STUCK_DISTANCE = 60.0  # units
_last_stuck_check = 0.0

# Dialog mirroring: track master's NPC dialog state
_dialog_was_visible = False      # True while dialog is open on master
_dialog_button_count = 0         # button count when dialog was last seen
_last_dialog_mirror_time = 0.0   # cooldown tracker
DIALOG_MIRROR_COOLDOWN = 3.0     # seconds between dialog mirrors

# Auto-travel: check every 5 seconds if slaves are on wrong map
TRAVEL_CHECK_INTERVAL = 5.0
_last_travel_check = 0.0
_last_map_id = 0  # track map changes
_travel_cooldown = 0.0  # don't spam travel commands
TRAVEL_COOLDOWN_SEC = 30.0  # wait 30s between auto-travel commands per slave
_slave_travel_cooldowns = {}  # email -> last_travel_time
#endregion

#region Helpers
def log(msg):
    state.last_action = msg
    state.last_action_time = time.time()
    Py4GW.Console.Log(MODULE_NAME, msg, Py4GW.Console.MessageType.Info)

def get_slaves():
    my_email = Player.GetAccountEmail()
    accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
    return [a for a in accounts if a.AccountEmail != my_email and a.IsSlotActive]

def send_to_all_slaves(command, params):
    sender = Player.GetAccountEmail()
    for slave in get_slaves():
        GLOBAL_CACHE.ShMem.SendMessage(sender, slave.AccountEmail, command, params)
    state.mirror_count += 1
#endregion

#region Detection Logic
def detect_npc_interaction():
    """Detect if master just interacted with an NPC (target changed to NPC + close range)."""
    target_id = Player.GetTargetID()
    if target_id == 0 or target_id == state.last_target_id:
        return

    # Target changed
    state.last_target_id = target_id

    if not Agent.IsValid(target_id):
        return

    # Is it an NPC we're close to?
    if not Agent.IsNPC(target_id) and not Agent.IsGadget(target_id):
        return

    # Are we within interact range?
    px, py = Player.GetXY()
    tx, ty = Agent.GetXY(target_id)
    dist = ((px - tx)**2 + (py - ty)**2)**0.5

    if dist > 300:  # too far, probably just targeting from afar
        return

    now = time.time()
    if now - state.last_interact_time < INTERACT_COOLDOWN:
        return

    state.last_interact_time = now
    state.last_interact_target = target_id

    name = Agent.GetNameByID(target_id) if Agent.IsNameReady(target_id) else f"Agent {target_id}"
    log(f"Mirror interact: {name}")
    send_to_all_slaves(SharedCommandType.InteractWithTarget, (target_id, 0, 0, 0))


def detect_dialog():
    """Mirror quest/NPC dialog acceptance to slaves.

    Tracks the master's NPC dialog window.  When a dialog is open we record
    the button count.  When the dialog disappears (master clicked a button),
    we send ``SharedCommandType.SendDialog`` with button index 1 to every
    slave so they accept the same quest / dialog option.

    If the target NPC is still known from the last interaction we prefer
    ``TakeDialogWithTarget`` so the slave walks to the NPC first (in case it
    hasn't reached it yet).
    """
    global _dialog_was_visible, _dialog_button_count, _last_dialog_mirror_time

    now = time.time()

    try:
        dialog_visible = UIManager.IsNPCDialogVisible()
    except Exception:
        return

    if dialog_visible:
        # Dialog is currently open — remember it and the button count
        _dialog_was_visible = True
        try:
            _dialog_button_count = UIManager.GetDialogButtonCount()
        except Exception:
            _dialog_button_count = 1
        return

    # Dialog is NOT visible right now.
    if not _dialog_was_visible:
        return  # wasn't open before either — nothing to do

    # Dialog just closed (master clicked a button).
    _dialog_was_visible = False

    if now - _last_dialog_mirror_time < DIALOG_MIRROR_COOLDOWN:
        return  # cooldown — avoid spam
    _last_dialog_mirror_time = now

    # Decide which command to send.
    target_id = state.last_interact_target
    if target_id and Agent.IsValid(target_id):
        # Slave may not have reached NPC yet — use TakeDialogWithTarget
        # Params: (target_id, button_index_1based, 0, 0)
        log(f"Mirror dialog (target {target_id}, button 1)")
        send_to_all_slaves(
            SharedCommandType.TakeDialogWithTarget,
            (target_id, 1, 0, 0),
        )
    else:
        # No known target — just tell slaves to click button 1 on whatever
        # dialog they have open.
        log("Mirror dialog (button 1)")
        send_to_all_slaves(
            SharedCommandType.SendDialog,
            (1, 0, 0, 0),
        )


_no_allies_since = 0.0  # Timestamp when we first detected no allies in cast range

def detect_stuck_slaves():
    """Check if any slave hasn't moved for STUCK_THRESHOLD seconds.
    Only fires when NO friendly units are in cast range for 10+ seconds
    (meaning the slave is truly separated from the party, not just standing in combat)."""
    global _last_stuck_check, _no_allies_since
    now = time.time()
    if now - _last_stuck_check < STUCK_CHECK_INTERVAL:
        return
    _last_stuck_check = now

    if not Map.IsExplorable() or Map.IsMapLoading():
        _no_allies_since = 0.0
        return

    # Check if any friendly units (allies) are within cast range of master
    try:
        allies = AgentArray.GetAllyArray()
        if allies:
            allies = AgentArray.Filter.ByDistance(allies, Player.GetXY(), Range.Spellcast.value)
            if len(allies) > 0:
                _no_allies_since = 0.0  # Allies nearby — reset timer
                return
    except Exception:
        return

    # No allies in cast range — start or check the 10s timer
    if _no_allies_since == 0.0:
        _no_allies_since = now
        return  # First detection — wait 10s before acting

    if now - _no_allies_since < 10.0:
        return  # Not 10s yet — keep waiting

    slaves = get_slaves()
    stuck_list = []

    for s in slaves:
        email = s.AccountEmail
        x = s.AgentData.Pos.x
        y = s.AgentData.Pos.y
        name = s.AgentData.CharacterName or email[:12]

        if email in state.slave_positions:
            old_x, old_y, old_time = state.slave_positions[email]
            dist = ((x - old_x)**2 + (y - old_y)**2)**0.5
            elapsed = now - old_time

            if dist < STUCK_DISTANCE and elapsed > STUCK_THRESHOLD:
                stuck_list.append(name)
                px, py = Player.GetXY()
                GLOBAL_CACHE.ShMem.SendMessage(
                    Player.GetAccountEmail(), email,
                    SharedCommandType.BruteForceUnstuck, (px, py, 0, 0)
                )
                state.slave_positions[email] = (x, y, now)
            elif dist >= STUCK_DISTANCE:
                state.slave_positions[email] = (x, y, now)
        else:
            state.slave_positions[email] = (x, y, now)

    if stuck_list:
        log(f"Auto-unstuck: {', '.join(stuck_list)}")


def detect_map_change():
    """Auto-summon slaves when master changes map/outpost. Pattern from Multibox._summon_all_accounts."""
    global _last_travel_check, _last_map_id
    now = time.time()
    if now - _last_travel_check < TRAVEL_CHECK_INTERVAL:
        return
    _last_travel_check = now

    if Map.IsMapLoading() or not Map.IsMapReady():
        return

    my_email = Player.GetAccountEmail()
    if not my_email:
        return

    my_data = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(my_email)
    if not my_data:
        return

    current_map = my_data.AgentData.Map.MapID
    current_region = my_data.AgentData.Map.Region
    current_district = my_data.AgentData.Map.District
    current_language = my_data.AgentData.Map.Language

    # Track map changes to avoid spam on first load
    if _last_map_id != current_map:
        _last_map_id = current_map
        # Give 10s for map to fully load before summoning
        _slave_travel_cooldowns.clear()
        return

    # Check each slave
    slaves = get_slaves()
    summoned = []
    district_number = max(0, int(current_district) - 1)

    for s in slaves:
        email = s.AccountEmail
        name = s.AgentData.CharacterName or email[:12]

        # Check cooldown per slave
        if email in _slave_travel_cooldowns:
            if now - _slave_travel_cooldowns[email] < TRAVEL_COOLDOWN_SEC:
                continue

        # Is slave on a different map/district?
        slave_map = s.AgentData.Map.MapID
        slave_region = s.AgentData.Map.Region
        slave_district = s.AgentData.Map.District
        slave_language = s.AgentData.Map.Language

        if (slave_map != current_map or
            slave_region != current_region or
            slave_district != current_district or
            slave_language != current_language):

            GLOBAL_CACHE.ShMem.SendMessage(
                my_email, email,
                SharedCommandType.TravelToMap,
                (current_map, current_region, district_number, current_language)
            )
            _slave_travel_cooldowns[email] = now
            summoned.append(name)

    if summoned:
        map_name = Map.GetMapName() or f"Map {current_map}"
        log(f"Auto-travel: {', '.join(summoned)} -> {map_name}")
#endregion

#region UI
MASTER_EMAIL_UI = MASTER_EMAIL

def draw_window():
    if not Player.IsPlayerLoaded():
        return
    # Only show window on master — slaves don't need this UI
    try:
        if Player.GetAccountEmail() != MASTER_EMAIL_UI:
            return
    except Exception:
        return

    if PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.AlwaysAutoResize):
        state.enabled = PyImGui.checkbox("Enabled", state.enabled)
        PyImGui.same_line(0, 10)
        PyImGui.text(f"Mirrored: {state.mirror_count}")

        if state.last_action and (time.time() - state.last_action_time) < 10:
            PyImGui.push_style_color(PyImGui.ImGuiCol.Text, (0.3, 1.0, 0.7, 1.0))
            PyImGui.text(f" {state.last_action}")
            PyImGui.pop_style_color(1)

    PyImGui.end()
#endregion

#region Entry Points
def main():
    try:
        draw_window()

        if not state.enabled:
            return
        if not Player.IsPlayerLoaded():
            return
        if not Map.IsMapReady() or Map.IsMapLoading():
            return

        # Only run on the configured master account — prevents promoted slaves
        # from issuing commands if master disconnects (007 crash scenario)
        try:
            if Player.GetAccountEmail() != MASTER_EMAIL:
                return
        except Exception:
            return

        # Passive detections (run every frame, lightweight)
        detect_npc_interaction()
        detect_dialog()
        detect_stuck_slaves()
        detect_map_change()

    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error)

def configure():
    PyImGui.text("Auto Mirror")
    PyImGui.text("Automatically mirrors master actions to all slaves:")
    PyImGui.text("  - NPC interaction (shrine, merchant, quest giver)")
    PyImGui.text("  - Quest/NPC dialog acceptance")
    PyImGui.text("  - Auto-unstuck detection (15s no movement)")
    PyImGui.text("Run ONLY on master account.")
#endregion
