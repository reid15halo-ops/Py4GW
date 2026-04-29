"""
Dashboard — Minimal control panel. One window, no clutter.
"""
import time
import traceback
import Py4GW
import PyImGui

MODULE_NAME = "Dashboard"

from Py4GWCoreLib import (
    GLOBAL_CACHE, Player, Map, Party, Routines,
)
from Py4GWCoreLib.py4gwcorelib_src.WidgetManager import get_widget_handler

coroutines = GLOBAL_CACHE.Coroutines

# --- Status message with auto-expire ---
_status = ""
_status_t = 0.0
STATUS_TTL = 10

def _status_set(msg):
    global _status, _status_t
    _status = msg
    _status_t = time.time()

def _status_text():
    if _status and (time.time() - _status_t) < STATUS_TTL:
        return _status
    return ""

# --- Cached data (refreshed every N seconds, not every frame) ---
CACHE_INTERVAL = 2.0
_cache_t = 0.0
_cached_slaves = []
_cached_slaves_here = []

def _refresh_cache():
    global _cache_t, _cached_slaves, _cached_slaves_here
    now = time.time()
    if now - _cache_t < CACHE_INTERVAL:
        return
    _cache_t = now
    me = Player.GetAccountEmail()
    all_accs = GLOBAL_CACHE.ShMem.GetAllAccountData()
    _cached_slaves = [a for a in all_accs if a.AccountEmail != me and a.IsSlotActive]
    my = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(me)
    if my:
        _cached_slaves_here = [s for s in _cached_slaves
            if s.AgentData.Map.MapID == my.AgentData.Map.MapID
            and s.AgentData.Map.Region == my.AgentData.Map.Region
            and s.AgentData.Map.District == my.AgentData.Map.District]
    else:
        _cached_slaves_here = []

# --- Coroutine guard ---
_busy = False

def _co_wrap(label_start, routine, label_done):
    global _busy
    if _busy:
        _status_set("Busy...")
        return
    _busy = True
    try:
        _status_set(label_start)
        yield from routine()
        _status_set(label_done)
    except Exception as e:
        _status_set(f"Error: {e}")
    finally:
        _busy = False
    yield

# --- Widget toggles (edit to customize) ---
TOGGLES = [
    "HeroAI",
    "Auto Loot Manager",
    "Auto Mirror",
    "CombatPrep",
    "Multibox Commander",
    "FollowingModule",
    "Blessed",
    "Resurrection Scroll",
    "Custom Behaviors: Utility AI",
    "Pycons",
]

# --- AI toggle ---
def _ai_all(on):
    pid = GLOBAL_CACHE.Party.GetPartyID()
    n = 0
    for i in range(12):
        acc = GLOBAL_CACHE.ShMem.GetAccountDataFromPartyNumber(i)
        if not acc or not acc.IsSlotActive or acc.IsHero:
            continue
        if acc.AgentPartyData.PartyID != pid:
            continue
        opt = GLOBAL_CACHE.ShMem.GetHeroAIOptionsByPartyNumber(i)
        if not opt:
            continue
        opt.Following = on
        opt.Combat = on
        opt.Looting = on
        opt.Targeting = on
        opt.Avoidance = on
        for s in range(8):
            opt.Skills[s] = on
        n += 1
    _status_set(f"AI {'ON' if on else 'OFF'}: {n}")

# --- Coroutines ---
SUMMON_WAIT_CYCLES = 90
INVITE_WAIT_CYCLES = 60
POST_DELAY = 3000

def _co_summon():
    yield from _co_wrap("Summoning...", Routines.Helpers.Multibox.summon_all_accounts, "Summoned")

def _co_invite():
    yield from _co_wrap("Inviting...", Routines.Helpers.Multibox.invite_all_accounts, "Invited")

def _co_kick():
    yield from _co_wrap("Kicking...", Routines.Helpers.Multibox.kick_all_accounts, "Kicked")

def _co_resign():
    yield from _co_wrap("Resigning...", Routines.Helpers.Multibox.resign_party, "Resigned")

def _co_setup():
    global _busy
    if _busy:
        _status_set("Busy...")
        return
    if not Map.IsOutpost():
        _status_set("Must be in outpost!")
        return
    _busy = True
    try:
        if Party.IsHardModeUnlocked() and not Party.IsHardMode():
            Party.SetHardMode()
            yield from Routines.Yield.wait(500)

        _status_set("1/2 Summoning...")
        yield from Routines.Helpers.Multibox.summon_all_accounts()
        yield from Routines.Yield.wait(POST_DELAY)
        for i in range(SUMMON_WAIT_CYCLES):
            _refresh_cache()
            if len(_cached_slaves_here) >= len(_cached_slaves):
                break
            yield from Routines.Yield.wait(500)
        else:
            _status_set("Warning: not all slaves arrived")
            yield from Routines.Yield.wait(2000)

        _status_set("2/2 Inviting...")
        yield from Routines.Helpers.Multibox.invite_all_accounts()
        yield from Routines.Yield.wait(POST_DELAY)
        for i in range(INVITE_WAIT_CYCLES):
            if Party.GetPartySize() >= len(_cached_slaves) + 1:
                break
            yield from Routines.Yield.wait(500)
        else:
            _status_set("Warning: not all slaves joined")
            yield from Routines.Yield.wait(2000)

        _status_set(f"Ready ({Party.GetPartySize()})")
    except Exception as e:
        _status_set(f"Setup error: {e}")
    finally:
        _busy = False
    yield

# --- Button colors ---
CLR_GREEN =  ((0.15, 0.45, 0.15, 1.0), (0.2, 0.6, 0.2, 1.0),   (0.1, 0.35, 0.1, 1.0))
CLR_ORANGE = ((0.4, 0.25, 0.1, 1.0),   (0.5, 0.35, 0.15, 1.0),  (0.3, 0.2, 0.08, 1.0))
CLR_RED =    ((0.5, 0.12, 0.12, 1.0),  (0.6, 0.18, 0.18, 1.0),  (0.4, 0.08, 0.08, 1.0))
CLR_BLUE =   ((0.1, 0.3, 0.55, 1.0),   (0.15, 0.4, 0.65, 1.0),  (0.08, 0.25, 0.45, 1.0))

def _btn(label, width, height, colors=None):
    if colors:
        PyImGui.push_style_color(PyImGui.ImGuiCol.Button, colors[0])
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, colors[1])
        PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, colors[2])
    clicked = PyImGui.button(label, width, height)
    if colors:
        PyImGui.pop_style_color(3)
    return clicked

# --- UI ---
BTN_W = 88
BTN_H = 22

def draw_window():
    if not Player.IsPlayerLoaded():
        return
    if not PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.AlwaysAutoResize):
        PyImGui.end()
        return

    try:
        _refresh_cache()

        hm_label = "HM" if Party.IsHardMode() else "NM"
        PyImGui.text(f"{Party.GetPartySize()}P  {len(_cached_slaves_here)}/{len(_cached_slaves)}S  {hm_label}")
        status = _status_text()
        if status:
            PyImGui.same_line(0, 10)
            PyImGui.text(status)

        PyImGui.separator()

        if _btn("Setup##d", BTN_W, BTN_H, CLR_BLUE):
            coroutines.append(_co_setup())
        PyImGui.same_line(0, 4)
        if _btn("Summon##d", BTN_W, BTN_H, CLR_GREEN):
            coroutines.append(_co_summon())
        PyImGui.same_line(0, 4)
        if _btn("Invite##d", BTN_W, BTN_H, CLR_GREEN):
            coroutines.append(_co_invite())

        if _btn("AI On##d", BTN_W, BTN_H, CLR_ORANGE):
            _ai_all(True)
        PyImGui.same_line(0, 4)
        if _btn("AI Off##d", BTN_W, BTN_H):
            _ai_all(False)
        PyImGui.same_line(0, 4)
        if _btn("Kick##d", BTN_W, BTN_H, CLR_RED):
            coroutines.append(_co_kick())

        if _btn("Resign##d", BTN_W, BTN_H, CLR_RED):
            coroutines.append(_co_resign())
        PyImGui.same_line(0, 4)
        hard_mode = Party.IsHardMode()
        hm_btn = "Set NM##d" if hard_mode else "Set HM##d"
        if _btn(hm_btn, BTN_W, BTN_H):
            if hard_mode:
                Party.SetNormalMode()
                _status_set("Normal Mode")
            elif Party.IsHardModeUnlocked():
                Party.SetHardMode()
                _status_set("Hard Mode")
            else:
                _status_set("HM not unlocked")

        PyImGui.separator()

        wh = get_widget_handler()
        for name in TOGGLES:
            on = wh.is_widget_enabled(name)
            toggled = PyImGui.checkbox(f"{name}##t", on)
            if toggled != on:
                if toggled:
                    wh.enable_widget(name)
                else:
                    wh.disable_widget(name)
    except Exception as e:
        PyImGui.text(f"Error: {e}")

    PyImGui.end()

def main():
    try:
        draw_window()
    except Exception as e:
        Py4GW.Console.Log(MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error)
        Py4GW.Console.Log(MODULE_NAME, traceback.format_exc(), Py4GW.Console.MessageType.Error)

def configure():
    PyImGui.text("Minimal dashboard. Edit TOGGLES list to customize.")
