import os
import random

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib import AgentArray
from Py4GWCoreLib import Botting
from Py4GWCoreLib import ConsoleLog, Console
from Py4GWCoreLib import Range
from Py4GWCoreLib import Routines
from Py4GWCoreLib import Utils
from Py4GWCoreLib import Map, Agent, Player, Party
from Py4GWCoreLib import SharedCommandType
from Py4GWCoreLib.enums_src.Model_enums import ModelID

BOT_NAME = "Asterius Scythe Farm"
MODULE_ICON = "Textures\\Module_Icons\\Asterius' Scythe.png"
TEXTURE = os.path.join(
    Py4GW.Console.get_projects_path(), "Bots", "marks_coding_corner", "textures", "asterius_scythe.png"
)
OUTPOST_TO_TRAVEL = Map.GetMapIDByName('Olafstead')
VARAJAR_FELLS_MAP_ID = 553
ASTERIUS_MODEL_ID = 6509

# ---------------------------------------------------------------------------
# Patrol intercept route: wider coverage of the SW quarter of Varajar Fells.
# Asterius patrols counter-clockwise around the area south/west of Olafstead.
# In Hard Mode his cycle is ~2 min, so we need good coverage to intercept him
# quickly regardless of where he is in his circuit.
# ---------------------------------------------------------------------------
INTERCEPT_PATH: list[tuple[float, float]] = [
    (-3357,  -741),    # Just outside Olafstead exit
    (-3200, -2000),    # Head south toward the valley
    (-2572, -3393),    # Continue south into open area
    (-4200, -3900),    # Swing southwest
    (-5767, -4300),    # Further west along the ridge
    (-7000, -3800),    # Northwest toward the pass
    (-8149, -2815),    # Continue NW
    (-9563, -2276),    # Along the northern edge of his circuit
    (-10800, -1500),   # Further west
    (-12105, -868),    # West end of patrol area
    (-13500, -2200),   # Curve south toward his SW corner
    (-14500, -3500),   # Deep SW
    (-15445, -4605),   # Southernmost point
    (-14000, -5500),   # Loop back east along southern edge
    (-12000, -5000),   # Continue east
    (-10000, -4500),   # Eastern return leg
    (-8000,  -4000),   # Back toward center
    (-6000,  -3500),   # Approaching start area from south
    (-4000,  -2500),   # Almost full loop
]

# ---------------------------------------------------------------------------
# Danger thresholds for the "stop and fight" check.
# Asterius travels with ~4 Berserking Bison. Nearby mobs include Modniir
# Centaurs, Ice Imps, Berserking Minotaurs, and Crypt Slashers.
# We only stop if the team is actually getting overwhelmed.
# ---------------------------------------------------------------------------
DANGER_ENEMY_THRESHOLD = 8        # More than this many enemies on compass = large pull
DANGER_HP_THRESHOLD = 0.50        # Any party member below 50% HP = team is stressed
DANGER_CHECK_INTERVAL_MS = 2000   # Check every 2 seconds (not every frame)

# ---------------------------------------------------------------------------
# State shared between coroutines.  All access is single-threaded within one
# client, so no locking is needed.
# ---------------------------------------------------------------------------
is_asterius_spotted = False
is_asterius_killed = False
asterius_agent_id = -1


bot = Botting(BOT_NAME)


# ===========================================================================
# Helper: scan compass for Asterius
# ===========================================================================
def _scan_for_asterius():
    """Scan enemy array within compass range for model ID 6509.
    Returns (found: bool, agent_id: int).  Safe to call when no enemies exist."""
    global is_asterius_spotted, asterius_agent_id

    if is_asterius_spotted and asterius_agent_id > 0:
        # Already tracking -- verify still valid
        try:
            if Agent.IsValid(asterius_agent_id):
                return True, asterius_agent_id
        except Exception as e:
            ConsoleLog(BOT_NAME, f"Agent validation error: {e}", Console.MessageType.Warning)
        # Lost reference, re-scan
        is_asterius_spotted = False
        asterius_agent_id = -1

    try:
        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            <= Range.SafeCompass.value,
        )
        for enemy_id in enemy_array:
            try:
                if Agent.GetModelID(enemy_id) == ASTERIUS_MODEL_ID:
                    is_asterius_spotted = True
                    asterius_agent_id = enemy_id
                    return True, enemy_id
            except Exception:
                continue  # Individual agent check failure is expected (stale IDs)
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Enemy scan error: {e}", Console.MessageType.Warning)

    return False, -1


# ===========================================================================
# Helper: check if team is in danger (overwhelmed)
# ===========================================================================
def _team_in_danger():
    """Returns True if there are many enemies AND any party member is low HP.
    This is the condition under which we should stop running and fight."""
    try:
        if not Map.IsMapReady() or not Map.IsExplorable():
            return False

        # Count enemies within compass range
        enemy_array = AgentArray.GetEnemyArray()
        nearby_enemies = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
            <= Range.SafeCompass.value,
        )
        enemy_count = len(nearby_enemies)

        if enemy_count <= DANGER_ENEMY_THRESHOLD:
            return False

        # Check party member HP -- any member below threshold triggers danger
        players = Party.GetPlayers()
        for player in players:
            try:
                agent_id = Party.Players.GetAgentIDByLoginNumber(player.login_number)
                if agent_id <= 0 or not Agent.IsValid(agent_id):
                    continue
                hp_pct = Agent.GetHealth(agent_id)
                if 0 < hp_pct < DANGER_HP_THRESHOLD:
                    return True
            except Exception:
                continue

        return False
    except Exception:
        return False


# ===========================================================================
# Zone entry: handle mobs near the Olafstead exit
# ===========================================================================
# The first waypoint of INTERCEPT_PATH is the safe direction AWAY from the
# downhill mob pack.  We define a "safe retreat point" further into the farm
# route, and a decision radius for enemies near the zone-in point.

ZONE_ENTRY_SAFE_POINT = INTERCEPT_PATH[2]  # 3rd waypoint = well into the route
ZONE_ENTRY_AGGRO_RADIUS = Range.Earshot.value  # ~1200 — earshot = aggro range
ZONE_ENTRY_SMALL_PACK = 4  # <=4 enemies = small, fight them
ZONE_ENTRY_WAIT_MAX_MS = 30000  # 30s max wait for entry combat to resolve


def _count_nearby_enemies(radius=None):
    """Count enemies within given radius (default compass)."""
    if radius is None:
        radius = Range.SafeCompass.value
    try:
        enemies = AgentArray.GetEnemyArray()
        return len(AgentArray.Filter.ByCondition(
            enemies,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid)) <= radius,
        ))
    except Exception:
        return 0


def _handle_zone_entry_mobs():
    """Coroutine: after zoning into Varajar Fells, handle the mob pack near
    the Olafstead exit before starting the patrol.

    Logic:
    1. Wait for map to be ready
    2. Scan for enemies within earshot (aggro range)
    3. If NO enemies nearby: proceed immediately (don't aggro them)
    4. If SMALL pack (<=4): stand and let HeroAI fight (quick cleanup)
    5. If LARGE pack (>4): run away toward the safe point (INTERCEPT_PATH[2])
       and let mobs deaggro or fight them spread out along the way
    """
    # Wait for map to be fully ready after zone
    for _ in range(20):
        if Map.IsMapReady() and Map.IsExplorable():
            break
        yield from Routines.Yield.wait(500)

    yield from Routines.Yield.wait(1500)  # Brief settle after zone-in

    nearby = _count_nearby_enemies(ZONE_ENTRY_AGGRO_RADIUS)
    ConsoleLog(BOT_NAME, f"Zone entry: {nearby} enemies within earshot")

    if nearby == 0:
        # Clean entry — no mobs nearby, proceed without aggroing
        ConsoleLog(BOT_NAME, "Zone entry: clear, proceeding to patrol")
        return

    if nearby <= ZONE_ENTRY_SMALL_PACK:
        # Small pack — fight them here, it's quick
        ConsoleLog(BOT_NAME, f"Zone entry: small pack ({nearby}), fighting")
        elapsed = 0
        while elapsed < ZONE_ENTRY_WAIT_MAX_MS:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if _count_nearby_enemies(ZONE_ENTRY_AGGRO_RADIUS) == 0:
                ConsoleLog(BOT_NAME, "Zone entry: pack cleared")
                break
            yield from Routines.Yield.wait(1000)
            elapsed += 1000
        yield from Routines.Yield.wait(2000)  # Brief loot window
        return

    # Large pack — run away toward the farm route
    ConsoleLog(BOT_NAME, f"Zone entry: large pack ({nearby}), retreating to safe point")
    sx, sy = ZONE_ENTRY_SAFE_POINT
    try:
        yield from bot.Move._coro_get_path_to(sx, sy)
        yield from bot.Move._coro_follow_path_to()
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Zone entry retreat error: {e}", Console.MessageType.Warning)

    # After reaching safe point, wait for combat to resolve if we dragged mobs
    elapsed = 0
    while elapsed < ZONE_ENTRY_WAIT_MAX_MS:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
            break
        yield from Routines.Yield.wait(1000)
        elapsed += 1000

    ConsoleLog(BOT_NAME, "Zone entry: resolved, starting patrol")


def _run_zone_entry_as_custom_state():
    """Returns the generator so the FSM waits for it to complete."""
    return _handle_zone_entry_mobs()


# ===========================================================================
# Helper: check if Asterius is dead
# ===========================================================================
def _check_asterius_dead():
    """Returns True if we have spotted and confirmed Asterius is dead."""
    global is_asterius_killed
    if is_asterius_killed:
        return True
    if is_asterius_spotted and asterius_agent_id > 0:
        try:
            if Agent.IsDead(asterius_agent_id):
                is_asterius_killed = True
                return True
        except Exception:
            pass
    return False


# ===========================================================================
# Managed coroutine: continuous Asterius scan + death detection
# Runs alongside the FSM to detect Asterius at any point during movement.
# ===========================================================================
def _background_asterius_scanner():
    """Managed coroutine that runs every second, scanning for Asterius
    and checking if he has died (e.g. killed by other mobs en route)."""
    global is_asterius_killed, is_asterius_spotted, asterius_agent_id

    while True:
        try:
            if not Map.IsExplorable() or not Map.IsMapReady():
                yield from Routines.Yield.wait(1000)
                continue

            if is_asterius_killed:
                yield from Routines.Yield.wait(1000)
                continue

            # Check if already-spotted Asterius has died
            if is_asterius_spotted and asterius_agent_id > 0:
                try:
                    if Agent.IsDead(asterius_agent_id):
                        is_asterius_killed = True
                        ConsoleLog(BOT_NAME, "Asterius died (detected by scanner)")
                except Exception:
                    pass
                yield from Routines.Yield.wait(1000)
                continue

            # Scan for Asterius
            _scan_for_asterius()

        except Exception as e:
            ConsoleLog(BOT_NAME, f"Scanner error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(1000)


# ===========================================================================
# Custom state: scan-and-intercept loop
# This replaces the simple FollowAutoPath + blind wait.  It walks the
# intercept path point by point, but at each waypoint it checks:
#   1) Is Asterius on compass?  -> break out and rush to him
#   2) Is the team overwhelmed? -> pause and let HeroAI fight
#   3) Has Asterius already died? -> skip to loot phase
# ===========================================================================
def _intercept_patrol():
    """Coroutine: walk intercept path, scanning for Asterius at each step.
    When Asterius is spotted, break from the patrol and move toward him.
    When the team is overwhelmed, pause movement until combat resolves."""
    global is_asterius_spotted, is_asterius_killed, asterius_agent_id

    MAX_PATROL_LOOPS = 3  # Maximum number of full loops before giving up

    for loop_count in range(MAX_PATROL_LOOPS):
        if is_asterius_killed:
            return

        ConsoleLog(BOT_NAME, f"Patrol loop {loop_count + 1}/{MAX_PATROL_LOOPS}")

        for i, (wx, wy) in enumerate(INTERCEPT_PATH):
            # --- Guard: map still valid? ---
            if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
                return
            if is_asterius_killed:
                return

            # --- Check: Asterius spotted? Rush to him instead of next waypoint ---
            if is_asterius_spotted and asterius_agent_id > 0:
                try:
                    if not Agent.IsDead(asterius_agent_id):
                        ax, ay = Agent.GetXY(asterius_agent_id)
                        if ax != 0 or ay != 0:
                            ConsoleLog(BOT_NAME, f"Asterius spotted! Rushing to ({ax:.0f}, {ay:.0f})")
                            # Move toward Asterius -- use his current position
                            yield from bot.Move._coro_get_path_to(ax, ay)
                            yield from bot.Move._coro_follow_path_to()
                            # After reaching him, re-check if still alive
                            if not Agent.IsDead(asterius_agent_id):
                                # We're close, let HeroAI engage.  Wait for kill.
                                yield from _wait_for_asterius_kill()
                            return
                    else:
                        is_asterius_killed = True
                        return
                except Exception as e:
                    ConsoleLog(BOT_NAME, f"Rush error: {e}", Console.MessageType.Warning)
                    # Reset tracking to avoid error loop on stale agent
                    is_asterius_spotted = False
                    asterius_agent_id = -1

            # --- Danger check: are we overwhelmed? ---
            if _team_in_danger():
                ConsoleLog(BOT_NAME, "Team overwhelmed -- pausing to fight")
                import time as _time
                _danger_start = _time.time()
                # Wait until combat resolves (max 60s to avoid infinite hang with dead slaves)
                while _team_in_danger() and (_time.time() - _danger_start) < 60:
                    if not Map.IsMapReady() or Map.IsMapLoading():
                        return
                    yield from Routines.Yield.wait(DANGER_CHECK_INTERVAL_MS)
                # Also wait until fully out of combat before resuming (max 30s)
                _ooc_start = _time.time()
                while Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot) and (_time.time() - _ooc_start) < 30:
                    if not Map.IsMapReady() or Map.IsMapLoading():
                        return
                    yield from Routines.Yield.wait(1000)
                ConsoleLog(BOT_NAME, "Combat resolved -- resuming patrol")

            # --- Move to next waypoint via A* pathing ---
            yield from bot.Move._coro_get_path_to(wx, wy)
            yield from bot.Move._coro_follow_path_to()

            # --- Post-move scan: maybe we spotted Asterius during movement ---
            _scan_for_asterius()

        # End of one full loop -- scan once more
        _scan_for_asterius()

    # Exhausted all loops without finding Asterius
    ConsoleLog(BOT_NAME, "Patrol loops exhausted -- Asterius not found", Console.MessageType.Warning)


def _wait_for_asterius_kill():
    """Wait for Asterius to die after we have engaged him.
    Includes a wall-clock safety timeout so we don't wait forever."""
    global is_asterius_killed
    import time

    KILL_TIMEOUT_S = 120  # 2 minutes max wall-clock
    MAX_REAPPROACH = 5
    start_time = time.time()
    reapproach_count = 0

    while (time.time() - start_time) < KILL_TIMEOUT_S:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return
        if _check_asterius_dead():
            ConsoleLog(BOT_NAME, "Asterius killed!")
            return

        # If Asterius is alive but moved out of range, re-approach (max 5 attempts)
        if asterius_agent_id > 0 and reapproach_count < MAX_REAPPROACH:
            try:
                dist = Utils.Distance(Player.GetXY(), Agent.GetXY(asterius_agent_id))
                if dist > Range.SafeCompass.value:
                    reapproach_count += 1
                    ConsoleLog(BOT_NAME, f"Asterius out of range — re-approaching ({reapproach_count}/{MAX_REAPPROACH})")
                    ax, ay = Agent.GetXY(asterius_agent_id)
                    if ax != 0 or ay != 0:
                        yield from bot.Move._coro_get_path_to(ax, ay)
                        yield from bot.Move._coro_follow_path_to()
                        yield from Routines.Yield.wait(2000)
                        continue
            except Exception as e:
                ConsoleLog(BOT_NAME, f"Re-approach error: {e}", Console.MessageType.Warning)

        yield from Routines.Yield.wait(3000)

    ConsoleLog(BOT_NAME, "Kill timeout reached", Console.MessageType.Warning)


# ===========================================================================
# Post-kill cleanup: kill remaining enemies, wait out of combat, loot
# ===========================================================================
_cleanup_done = False


def _cleanup_and_loot():
    """Coroutine: after Asterius is dead, clear remaining mobs then loot.
    Uses try/finally to guarantee _cleanup_done is set even on exception.
    Wall-clock safety: entire cleanup capped at 60s to prevent hangs."""
    global _cleanup_done
    import time
    _cleanup_done = False
    _cleanup_deadline = time.time() + 60  # 60s max for entire cleanup
    try:
        # Drive the inner coroutine manually with a wall-clock timeout
        inner = _cleanup_and_loot_inner()
        while True:
            if time.time() > _cleanup_deadline:
                ConsoleLog(BOT_NAME, "Cleanup timeout (60s) — skipping to resign", Console.MessageType.Warning)
                break
            try:
                val = next(inner)
                yield val
            except StopIteration:
                break
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Cleanup error: {e}", Console.MessageType.Warning)
    finally:
        _cleanup_done = True


def _cleanup_and_loot_inner():
    """Inner coroutine — actual cleanup logic. _cleanup_done managed by wrapper."""
    global _cleanup_done
    import time

    if not Map.IsMapReady() or not Map.IsExplorable():
        _cleanup_done = True
        return

    ConsoleLog(BOT_NAME, "Asterius dead -- clearing remaining enemies")

    # Phase 1: Wait for nearby enemies to die (earshot range, max 20s)
    start = time.time()
    while (time.time() - start) < 20:
        if not Map.IsMapReady() or Map.IsMapLoading():
            return

        try:
            enemies = AgentArray.GetEnemyArray()
            nearby = AgentArray.Filter.ByCondition(
                enemies,
                lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
                <= Range.Earshot.value,
            )
            alive = [e for e in nearby if not Agent.IsDead(e)]
        except Exception:
            alive = []

        if len(alive) == 0:
            ConsoleLog(BOT_NAME, "Nearby enemies cleared")
            break

        ConsoleLog(BOT_NAME, f"Cleanup: {len(alive)} enemies in earshot")
        yield from Routines.Yield.wait(2000)

    # Phase 2: Brief out-of-combat wait (max 10s)
    ConsoleLog(BOT_NAME, "Waiting for out-of-combat")
    start = time.time()
    while (time.time() - start) < 10:
        if not Map.IsMapReady() or Map.IsMapLoading():
            _cleanup_done = True
            return
        try:
            if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                break
        except Exception:
            break
        yield from Routines.Yield.wait(1000)

    # Phase 3: Walk back through recent waypoints to pick up loot drops
    ConsoleLog(BOT_NAME, "Looting phase -- walking through drop area")

    # Get player position and find the closest waypoint to start from
    try:
        px, py = Player.GetXY()
        closest_idx = 0
        closest_dist = float('inf')
        for i, (wx, wy) in enumerate(INTERCEPT_PATH):
            d = Utils.Distance((px, py), (wx, wy))
            if d < closest_dist:
                closest_dist = d
                closest_idx = i

        # Walk 1 nearby waypoint to pick up drops (auto-loot handles the rest)
        if closest_idx < len(INTERCEPT_PATH):
            wx, wy = INTERCEPT_PATH[closest_idx]
            if Map.IsMapReady() and not Map.IsMapLoading():
                yield from bot.Move._coro_get_path_to(wx, wy)
                yield from bot.Move._coro_follow_path_to()

    except Exception as e:
        ConsoleLog(BOT_NAME, f"Loot walk error: {e}", Console.MessageType.Warning)

    # Brief wait for auto-loot pickup
    yield from Routines.Yield.wait(3000)

    ConsoleLog(BOT_NAME, "Cleanup & loot complete -- resigning")
    _cleanup_done = True


def _run_cleanup_as_custom_state():
    """Non-generator wrapper: returns the cleanup coroutine for FSM."""
    return _cleanup_and_loot()


def _is_cleanup_done():
    """Condition check for FSM: returns True when cleanup is finished."""
    return _cleanup_done


# ===========================================================================
# Custom state wrapper: the FSM calls this as a plain function (not coroutine)
# via AddCustomState, so we wrap the coroutine into a managed coroutine.
# ===========================================================================
def _run_intercept_as_custom_state():
    """Non-generator wrapper for AddCustomState.  Schedules the intercept
    patrol as a managed coroutine and returns immediately."""
    global _patrol_launched
    fsm = bot.config.FSM
    fsm.AddManagedCoroutine("InterceptPatrol", _intercept_patrol)
    _patrol_launched = True


_patrol_launched = False

def _wait_for_intercept_done():
    """Condition function: returns True when the patrol/kill phase is over."""
    if not _patrol_launched:
        return False  # Not launched yet — don't skip ahead
    if is_asterius_killed:
        return True
    # Also check if the managed coroutine has finished
    if not bot.config.FSM.HasManagedCoroutine("InterceptPatrol"):
        return True
    return False


# ===========================================================================
# Per-account restock counters — each account has its own randomized interval
# Master always restocks when ANY account needs it
# ===========================================================================
_run_count = 0  # Global run counter
_account_restock: dict[str, dict] = {}  # email -> {"threshold": int, "last_restock_run": int}


def _get_account_restock(email: str) -> dict:
    """Get or create per-account restock tracking."""
    if email not in _account_restock:
        _account_restock[email] = {
            "threshold": random.randint(3, 5),
            "last_restock_run": 0,
        }
    return _account_restock[email]


def _account_needs_restock(email: str) -> bool:
    """Check if this specific account needs restocking this run."""
    info = _get_account_restock(email)
    return (_run_count - info["last_restock_run"]) >= info["threshold"]


def _mark_account_restocked(email: str):
    """Mark account as restocked and randomize next interval."""
    info = _get_account_restock(email)
    info["last_restock_run"] = _run_count
    info["threshold"] = random.randint(3, 5)
    ConsoleLog(BOT_NAME, f"{email[:15]}: restocked — next in {info['threshold']} runs")


def _should_restock_this_run() -> bool:
    """Returns True if master OR any slave needs restocking."""
    master = Player.GetAccountEmail()
    if _account_needs_restock(master):
        return True
    try:
        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
            if acc and acc.IsSlotActive and acc.AccountEmail and acc.AccountEmail != master:
                if _account_needs_restock(acc.AccountEmail):
                    return True
    except Exception:
        pass
    return False


def _increment_run_counter():
    """Called after each successful run."""
    global _run_count
    _run_count += 1
    # Log per-account status
    master = Player.GetAccountEmail()
    m_info = _get_account_restock(master)
    runs_until = m_info["threshold"] - (_run_count - m_info["last_restock_run"])
    ConsoleLog(BOT_NAME, f"Run {_run_count} complete — master restocks in {max(0, runs_until)} runs")


# ===========================================================================
# State reset between farm loops
# ===========================================================================
def reset_farm_flags():
    global is_asterius_killed, is_asterius_spotted, asterius_agent_id, _cleanup_done, _patrol_launched, _kits_done
    is_asterius_spotted = False
    is_asterius_killed = False
    asterius_agent_id = -1
    _cleanup_done = False
    _patrol_launched = False
    _kits_done = False
    _increment_run_counter()
    # Clean up managed coroutines from the previous loop
    try:
        bot.States.RemoveManagedCoroutine("InterceptPatrol")
    except Exception:
        pass
    try:
        bot.States.RemoveManagedCoroutine("AsteriusScanner")
    except Exception:
        pass


# ===========================================================================
# Kit restocking: buy ID + Salvage kits for master, send to slaves
# ===========================================================================
KIT_TARGET_ID = 2       # Target: 2 ID kits
KIT_TARGET_SALVAGE = 4  # Target: 4 salvage kits
KIT_TARGET_EXPERT = 1   # Target: 1 expert salvage kit


def _buy_kits_in_outpost():
    """Coroutine: find merchant in outpost, buy kits for master, send to slaves."""
    if not Map.IsMapReady() or not Map.IsOutpost():
        return

    # Find nearest NPC with [Merchant] in name
    def _is_merchant_npc(agent_id):
        try:
            name = Agent.GetNameByID(agent_id) or ""
            return "[Merchant]" in name or "Merchant" in name
        except Exception:
            return False

    try:
        px, py = Player.GetXY()
        npc_array = AgentArray.GetNPCMinipetArray()
        npc_array = AgentArray.Filter.ByCondition(npc_array, _is_merchant_npc)
        if not npc_array:
            ConsoleLog(BOT_NAME, "No merchant NPC found in outpost", Console.MessageType.Warning)
            return

        npc_array = AgentArray.Sort.ByDistance(npc_array, (px, py))
        merchant_id = npc_array[0]
        mx, my = Agent.GetXY(merchant_id)
        merchant_model = Agent.GetPlayerNumber(merchant_id)

        # Walk to merchant
        ConsoleLog(BOT_NAME, f"Walking to merchant (agent {merchant_id})")
        yield from Routines.Yield.Movement.FollowPath([(mx, my)])
        yield from Routines.Yield.wait(500)

        # Interact with merchant to open window
        Player.ChangeTarget(merchant_id)
        yield from Routines.Yield.Player.InteractTarget()
        yield from Routines.Yield.wait(1200)

        # Buy kits for master
        id_kits_in_inv = int(GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Identification_Kit.value))
        sup_id_in_inv = int(GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Superior_Identification_Kit.value))
        salvage_in_inv = int(GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Salvage_Kit.value))

        id_to_buy = max(0, KIT_TARGET_ID - (id_kits_in_inv + sup_id_in_inv))
        salvage_to_buy = max(0, KIT_TARGET_SALVAGE - salvage_in_inv)

        if id_to_buy > 0:
            yield from Routines.Yield.Merchant.BuyIDKits(id_to_buy, log=True)
        if salvage_to_buy > 0:
            yield from Routines.Yield.Merchant.BuySalvageKits(salvage_to_buy, log=True)

        ConsoleLog(BOT_NAME, f"Master kits restocked (ID: +{id_to_buy}, Salvage: +{salvage_to_buy})")

        # Send MerchantItems only to slaves that need restocking this run
        sender = Player.GetAccountEmail()
        all_accounts = GLOBAL_CACHE.ShMem.GetAllAccountData()
        for acc in all_accounts:
            try:
                if not acc or not acc.AccountEmail or acc.AccountEmail == sender:
                    continue
                if not acc.IsSlotActive:
                    continue
                if acc.AgentData.Map.MapID != Map.GetMapID():
                    continue
                if not _account_needs_restock(acc.AccountEmail):
                    ConsoleLog(BOT_NAME, f"Skipping {acc.AccountEmail[:15]} — not due yet")
                    continue
                GLOBAL_CACHE.ShMem.SendMessage(
                    sender, acc.AccountEmail, SharedCommandType.MerchantItems,
                    (mx, my, KIT_TARGET_ID, KIT_TARGET_SALVAGE),
                    ExtraData=(str(KIT_TARGET_EXPERT), str(merchant_model)))
                _mark_account_restocked(acc.AccountEmail)
                ConsoleLog(BOT_NAME, f"Kit buy sent to {acc.AccountEmail}")
                yield from Routines.Yield.wait(500)
            except Exception:
                continue

        yield from Routines.Yield.wait(2000)

    except Exception as e:
        ConsoleLog(BOT_NAME, f"Kit restock error: {e}", Console.MessageType.Warning)


_kits_done = False


def _run_kit_restock():
    """Wrapper coroutine that sets _kits_done flag when complete."""
    global _kits_done
    _kits_done = False
    try:
        yield from _buy_kits_in_outpost()
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Kit restock wrapper error: {e}", Console.MessageType.Warning)
    finally:
        _kits_done = True


def _run_kit_restock_as_custom_state():
    return _run_kit_restock()


def _is_kits_done():
    return _kits_done


# ===========================================================================
# Conditional restock — only go to merchant every 3-5 runs
# ===========================================================================
_restock_phase_done = False


def _maybe_restock_coro():
    """Coroutine: restock if any account needs it, otherwise skip."""
    global _restock_phase_done
    _restock_phase_done = False
    try:
        if _should_restock_this_run():
            ConsoleLog(BOT_NAME, f"Restock run {_run_count} — some accounts need merchant")
            yield from _buy_kits_in_outpost()
            # Mark master as restocked
            _mark_account_restocked(Player.GetAccountEmail())
            # Let InventoryPlus ID, salvage, deposit
            yield from Routines.Yield.wait(15000)
        else:
            ConsoleLog(BOT_NAME, f"Run {_run_count} — no accounts need restock, skipping merchant")
            # Brief pause for InventoryPlus outpost handler
            yield from Routines.Yield.wait(3000)
    except Exception as e:
        ConsoleLog(BOT_NAME, f"Restock phase error: {e}", Console.MessageType.Warning)
    finally:
        _restock_phase_done = True


def _maybe_restock():
    return _maybe_restock_coro()


def _is_restock_phase_done():
    return _restock_phase_done


# ===========================================================================
# Wipe handling (kept from original)
# ===========================================================================
def _on_party_wipe(bot_ref: "Botting"):
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot_ref.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            bot_ref.config.FSM.resume()
            return

    # Player revived on same map -- jump to combat (5th AddHeader call = _5)
    bot_ref.States.JumpToStepName("[H]Start Combat_6")
    bot_ref.config.FSM.resume()


def OnPartyWipe(bot_ref: "Botting"):
    ConsoleLog("on_party_wipe", "event triggered")
    fsm = bot_ref.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot_ref))


# ===========================================================================
# Main farm routine
# ===========================================================================
def farm_scythes(bot: Botting) -> None:
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    # Events
    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))

    bot.States.AddHeader(BOT_NAME)
    bot.Templates.Multibox_Aggressive()

    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_TO_TRAVEL)
    bot.Party.SetHardMode(True)

    # Initial kit restock before first farm run
    bot.States.AddHeader("Initial Restock")
    bot.States.AddCustomState(_run_kit_restock_as_custom_state, "Buy ID + Salvage kits")
    bot.Wait.UntilCondition(_is_kits_done, duration=1000)
    bot.Wait.ForTime(15000)  # Let InventoryPlus ID, salvage, deposit

    # Background scanner: runs continuously once we enter the explorable
    bot.States.AddManagedCoroutine('AsteriusScanner', _background_asterius_scanner)

    bot.States.AddHeader('Exit To Farm')
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(-2166, 861, target_map_id=VARAJAR_FELLS_MAP_ID)
    bot.Properties.Enable('pause_on_danger')  # Re-enable ASAP after zone exit
    bot.Wait.ForTime(3000)

    # Handle mobs near zone-in before starting patrol
    bot.States.AddHeader('Zone Entry Safety')
    bot.States.AddCustomState(_run_zone_entry_as_custom_state, "Handle exit mobs")
    bot.Wait.ForTime(2000)  # Let coroutine finish

    bot.States.AddHeader("Start Combat")
    # Schedule the intercept patrol as a custom state, then wait for completion
    bot.States.AddCustomState(_run_intercept_as_custom_state, "Launch Intercept Patrol")
    bot.Wait.UntilCondition(_wait_for_intercept_done, duration=1000)

    # Post-kill: clear remaining enemies, loot, then resign
    bot.States.AddHeader("Cleanup & Loot")
    bot.States.AddCustomState(_run_cleanup_as_custom_state, "Kill remaining + loot")
    bot.Wait.UntilCondition(_is_cleanup_done, duration=1000)

    # Resign and loop — retry resign to ensure all slaves accept
    bot.Multibox.ResignParty()
    bot.Wait.ForTime(5000)
    bot.Multibox.ResignParty()  # Retry in case any slave missed first resign
    bot.States.AddCustomState(reset_farm_flags, "Reset Farm detections")
    bot.Wait.UntilOnOutpost()

    # Restock kits + merchant — only every 3-5 runs (randomized)
    bot.States.AddHeader("Restock Kits")
    bot.States.AddCustomState(_maybe_restock, "Check if restock needed")
    bot.Wait.UntilCondition(_is_restock_phase_done, duration=1000)

    bot.States.JumpToStepName("[H]Exit To Farm_4")


bot.SetMainRoutine(farm_scythes)


def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Asterius Scythe Farmer bot", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    # Description
    PyImGui.text("multi-account bot to farm Asterius Scythe")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6-8 well-geared accounts")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.bullet_text("Hard Mode enabled for fast ~2 min boss patrol cycle.")
    PyImGui.spacing()
    PyImGui.bullet_text("Features:")
    PyImGui.bullet_text("- Smart patrol intercept (19-point route)")
    PyImGui.bullet_text("- Real-time Asterius scanning on compass")
    PyImGui.bullet_text("- Mob gathering by running through aggro")
    PyImGui.bullet_text("- Danger check: stops if overwhelmed")
    PyImGui.bullet_text("- Up to 3 patrol loops before timeout")

    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Mark")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window(icon_path=TEXTURE)


if __name__ == "__main__":
    main()
