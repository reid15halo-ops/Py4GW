import os
import time
import random
import configparser

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import AgentArray, Botting, ConsoleLog, Range, Routines, Utils
from Py4GWCoreLib import Map, Agent, Player

BOT_NAME = "Asterius Scythe Farm"
MODULE_ICON = "Textures\\Module_Icons\\Asterius' Scythe.png"
TEXTURE = os.path.join(
    Py4GW.Console.get_projects_path(), "Bots", "marks_coding_corner", "textures", "asterius_scythe.png"
)
OUTPOST_TO_TRAVEL = Map.GetMapIDByName('Olafstead')
VARAJAR_FELLS_MAP_ID = 553
ASTERIUS_MODEL_ID = 6509


# ---------------------------------------------------------------------------
# ROUTE — follows Asterius' patrol arc (see Asterius_the_Mighty_map.jpg)
# ---------------------------------------------------------------------------
TRAVEL_PATH: list[tuple[float, float]] = [
    (-3357, -741),     # Exit area, heading SW
    (-2572, -3393),    # Follow patrol curve south
    (-5767, -4300),    # SW turn along patrol arc
    (-8149, -2815),    # Mid-patrol intercept (critical — Asterius often here)
    (-9563, -2276),    # Continue along patrol loop
    (-12105, -868),    # NW segment of patrol
    (-15445, -4605),   # Final patrol endpoint / Start zone (blue dot cluster)
]

# ---------------------------------------------------------------------------
# TUNING CONSTANTS
# ---------------------------------------------------------------------------
MAX_WAIT_SECONDS = 120
LOOT_PICKUP_RANGE = 1012.0
MAX_LOOT_SECONDS = 30
LOOT_SETTLE_MS = 2000

# Combat routing
AGGRO_SCAN_RANGE = 2500.0
PULL_RANGE = 1400.0
GROUP_CLUSTER_RADIUS = 700.0
MAX_PULL_GROUP_SIZE = 12
COMBAT_CLEAR_RANGE = 1012.0
WAYPOINT_TOLERANCE = 250.0
COMBAT_WAIT_TIMEOUT = 60
COMBAT_STALL_WINDOW = 30
STUCK_CHECK_INTERVAL = 3.0
STUCK_DISTANCE_THRESHOLD = 50
STUCK_MAX_ATTEMPTS = 5

# Party cohesion
PARTY_COHESION_RANGE = 2500.0
PARTY_CHECK_INTERVAL = 5.0
PARTY_WAIT_TIMEOUT = 15

# Blessed widget integration
BLESSED_INI_PATH = os.path.join(
    Py4GW.Console.get_projects_path(), "Widgets", "Config", "Blessed_Config.ini"
)


# ===========================================================================
# HUMAN-LIKE BEHAVIOR
# ===========================================================================

def jitter_wait(base_ms, variance=0.25):
    """Return a randomized wait time in ms. Variance 0.25 = +/- 25%."""
    return max(100, int(base_ms * random.uniform(1.0 - variance, 1.0 + variance)))


# Jitter radii — different contexts need different precision
JITTER_MOVE = 300.0      # Waypoint movement — large, human-like wandering
JITTER_PULL = 100.0      # Pull targets — smaller, need to enter aggro bubble
JITTER_INTERCEPT = 80.0  # Asterius intercept — need to get close

# Max distance for a single Player.Move call — longer segments get split
MAX_MOVE_SEGMENT = 1500.0


def densify_path(path):
    """Insert intermediate waypoints so no segment exceeds MAX_MOVE_SEGMENT.

    GW1's Player.Move uses the game's built-in pathfinding, but very long
    distances can cause issues. Shorter segments also let the bot scan for
    enemies and Asterius more frequently along the route.
    """
    dense = []
    for i, (x, y) in enumerate(path):
        if i == 0:
            dense.append((x, y))
            continue
        px, py = path[i - 1]
        dist = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
        if dist > MAX_MOVE_SEGMENT:
            segments = int(dist / MAX_MOVE_SEGMENT) + 1
            for s in range(1, segments):
                t = s / segments
                dense.append((px + (x - px) * t, py + (y - py) * t))
        dense.append((x, y))
    return dense


def jitter_pos(x, y, radius=JITTER_MOVE):
    """Add random offset to coordinates to avoid pixel-perfect movement."""
    return (
        x + random.uniform(-radius, radius),
        y + random.uniform(-radius, radius),
    )


# ===========================================================================
# PER-TICK CACHE — avoids redundant API calls within the same coroutine tick
# ===========================================================================

class TickCache:
    """Time-based cache for frequently-called Py4GW API data.

    Auto-invalidates every STALE_MS milliseconds. Multiple reads within
    the same tick reuse cached values. Reduces API calls from ~30+/tick
    to ~5/tick.
    """

    STALE_MS = 500  # Invalidate after 500ms — 6 GW clients on one PC

    def __init__(self):
        self._last_refresh = 0.0
        self._player_xy = None
        self._player_id = None
        self._enemies_raw = None
        self._allies_raw = None
        self._items_raw = None

    def _check_stale(self):
        now = time.time() * 1000
        if now - self._last_refresh > self.STALE_MS:
            self._last_refresh = now
            self._player_xy = None
            self._player_id = None
            self._enemies_raw = None
            self._allies_raw = None
            self._items_raw = None

    @property
    def player_xy(self):
        self._check_stale()
        if self._player_xy is None:
            try:
                self._player_xy = Player.GetXY()
            except Exception:
                self._player_xy = (0.0, 0.0)
        return self._player_xy

    @property
    def player_id(self):
        self._check_stale()
        if self._player_id is None:
            try:
                self._player_id = Player.GetAgentID()
            except Exception:
                self._player_id = 0
        return self._player_id

    @property
    def enemies(self):
        self._check_stale()
        if self._enemies_raw is None:
            try:
                self._enemies_raw = AgentArray.GetEnemyArray()
            except Exception:
                self._enemies_raw = []
        return self._enemies_raw

    @property
    def allies(self):
        self._check_stale()
        if self._allies_raw is None:
            try:
                self._allies_raw = AgentArray.GetAllyArray()
            except Exception:
                self._allies_raw = []
        return self._allies_raw

    @property
    def items(self):
        self._check_stale()
        if self._items_raw is None:
            try:
                self._items_raw = AgentArray.GetItemArray()
            except Exception:
                self._items_raw = []
        return self._items_raw


cache = TickCache()


# ===========================================================================
# SAFE API WRAPPERS
# ===========================================================================

def safe_agent_xy(agent_id):
    try:
        return Agent.GetXY(agent_id)
    except Exception:
        return (0.0, 0.0)


def safe_agent_alive(agent_id):
    try:
        return Agent.IsAlive(agent_id)
    except Exception:
        return False


def safe_agent_dead(agent_id):
    try:
        return Agent.IsDead(agent_id)
    except Exception:
        return False


def safe_agent_valid(agent_id):
    try:
        return Agent.IsValid(agent_id)
    except Exception:
        return False


def safe_agent_model(agent_id):
    try:
        return Agent.GetModelID(agent_id)
    except Exception:
        return -1


def safe_agent_aggressive(agent_id):
    try:
        return Agent.IsAggressive(agent_id)
    except Exception:
        return False


def safe_agent_is_player(agent_id):
    try:
        return Agent.IsPlayer(agent_id)
    except Exception:
        return False


# ===========================================================================
# FARM STATE — replaces scattered globals
# ===========================================================================

class FarmState:
    def __init__(self):
        self.asterius_spotted = False
        self.asterius_killed = False
        self.asterius_agent_id = -1
        self.elapsed = 0

    def reset(self):
        self.asterius_spotted = False
        self.asterius_killed = False
        self.asterius_agent_id = -1
        self.elapsed = 0


state = FarmState()
bot = Botting(BOT_NAME)


# ===========================================================================
# RUN STATS
# ===========================================================================

class FarmStats:
    def __init__(self):
        self.total_runs = 0
        self.asterius_kills = 0
        self.run_start_time = 0.0
        self.session_start_time = time.time()
        self.run_times: list[float] = []
        self.items_picked_up = 0
        self.items_this_run = 0

    def start_run(self):
        self.run_start_time = time.time()
        self.items_this_run = 0

    def end_run(self, killed: bool):
        self.total_runs += 1
        if killed:
            self.asterius_kills += 1
        duration = time.time() - self.run_start_time
        self.run_times.append(duration)
        self._log_summary()

    def record_pickup(self, count: int = 1):
        self.items_picked_up += count
        self.items_this_run += count

    def _log_summary(self):
        session_mins = (time.time() - self.session_start_time) / 60
        avg_run = sum(self.run_times) / len(self.run_times) if self.run_times else 0
        kph = (self.asterius_kills / session_mins * 60) if session_mins > 0 else 0
        ConsoleLog("stats",
            f"Run {self.total_runs} | "
            f"Kills: {self.asterius_kills} | "
            f"Avg: {avg_run:.0f}s | "
            f"K/hr: {kph:.1f} | "
            f"Items: {self.items_this_run}/{self.items_picked_up} | "
            f"Session: {session_mins:.0f}min")


farm_stats = FarmStats()


# ===========================================================================
# ENEMY ANALYSIS — uses TickCache, single pass where possible
# ===========================================================================

def get_alive_enemies_in_range(max_range):
    enemies = AgentArray.Filter.ByDistance(cache.enemies, cache.player_xy, max_range)
    return AgentArray.Filter.ByCondition(enemies, safe_agent_alive)


def get_aggroed_enemies():
    enemies = get_alive_enemies_in_range(Range.Compass.value)
    return AgentArray.Filter.ByCondition(enemies, safe_agent_aggressive)


def count_aggroed():
    return len(get_aggroed_enemies())


def is_in_combat():
    aggroed = get_aggroed_enemies()
    close = AgentArray.Filter.ByDistance(aggroed, cache.player_xy, COMBAT_CLEAR_RANGE)
    return len(close) > 0


def find_nearby_pullable_group():
    """Find the nearest pullable enemy cluster within range.

    Prefers the cluster CLOSEST to the player (not largest) for better
    path efficiency. Returns (cluster_center_id, group_members) or (None, []).
    """
    enemies = get_alive_enemies_in_range(AGGRO_SCAN_RANGE)
    not_aggroed = AgentArray.Filter.ByCondition(
        enemies, lambda aid: not safe_agent_aggressive(aid)
    )
    if len(not_aggroed) == 0:
        return None, []

    # Sort by distance — try closest cluster first
    not_aggroed = AgentArray.Sort.ByDistance(not_aggroed, cache.player_xy)

    cluster_target = AgentArray.Routines.DetectLargestAgentCluster(
        not_aggroed, GROUP_CLUSTER_RADIUS
    )
    if not cluster_target or cluster_target <= 0:
        return None, []

    cluster_pos = safe_agent_xy(cluster_target)
    group_members = AgentArray.Filter.ByDistance(
        not_aggroed, cluster_pos, GROUP_CLUSTER_RADIUS
    )

    dist_to_cluster = Utils.Distance(cache.player_xy, cluster_pos)
    if dist_to_cluster > PULL_RANGE:
        return None, []

    current = count_aggroed()
    if current + len(group_members) > MAX_PULL_GROUP_SIZE:
        ConsoleLog("route_intel",
            f"Skipping group ({len(group_members)}) — would exceed cap ({current} aggroed)")
        return None, []

    return cluster_target, group_members


# ===========================================================================
# PARTY MANAGEMENT
# ===========================================================================

def get_party_member_agents():
    allies = AgentArray.Filter.ByCondition(cache.allies, safe_agent_alive)
    return AgentArray.Filter.ByCondition(allies, safe_agent_is_player)


def get_furthest_party_member_distance():
    party = get_party_member_agents()
    if len(party) == 0:
        return 0.0

    my_id = cache.player_id
    my_pos = cache.player_xy
    max_dist = 0.0
    for member_id in party:
        if member_id == my_id:
            continue
        dist = Utils.Distance(my_pos, safe_agent_xy(member_id))
        if dist > max_dist:
            max_dist = dist
    return max_dist


def is_party_together():
    return get_furthest_party_member_distance() <= PARTY_COHESION_RANGE


def wait_for_party():
    if is_party_together():
        return

    furthest = get_furthest_party_member_distance()
    ConsoleLog("party", f"Waiting for party — furthest at {furthest:.0f}")

    elapsed = 0
    while not is_party_together() and elapsed < PARTY_WAIT_TIMEOUT:
        yield from Routines.Yield.wait(1000)
        elapsed += 1

    if elapsed >= PARTY_WAIT_TIMEOUT:
        ConsoleLog("party", "Party wait timeout — proceeding")
    else:
        ConsoleLog("party", f"Party regrouped in {elapsed}s")


def is_party_healthy(threshold=0.4):
    """Check if all party members are above HP threshold. Prevents pulling while low."""
    party = get_party_member_agents()
    for member_id in party:
        try:
            hp = Agent.GetHealth(member_id)
            if hp < threshold:
                return False
        except Exception:
            continue
    return True


EXPECTED_PARTY_SIZE = 6  # 6 player accounts (excluding NPC heroes)


def wait_for_party_zone_in():
    """Coroutine: wait until all player accounts have zoned in (max 15s).

    Checks that the expected number of player agents are present and alive.
    Prevents the leader from starting the route while clients are still loading.
    """
    ConsoleLog("party", f"Waiting for {EXPECTED_PARTY_SIZE} accounts to zone in...")
    elapsed = 0
    while elapsed < 15:
        party = get_party_member_agents()
        if len(party) >= EXPECTED_PARTY_SIZE:
            ConsoleLog("party", f"All {len(party)} accounts present after {elapsed}s")
            return
        yield from Routines.Yield.wait(1000)
        elapsed += 1
    party = get_party_member_agents()
    ConsoleLog("party", f"Zone-in timeout — {len(party)}/{EXPECTED_PARTY_SIZE} present, proceeding")


# ===========================================================================
# STUCK DETECTION
# ===========================================================================

_stuck_last_pos = None
_stuck_last_check = 0.0
_stuck_count = 0


def check_stuck():
    global _stuck_last_pos, _stuck_last_check, _stuck_count

    now = time.time()
    if now - _stuck_last_check < STUCK_CHECK_INTERVAL:
        return False

    _stuck_last_check = now
    current_pos = cache.player_xy

    if _stuck_last_pos is not None:
        dist = Utils.Distance(current_pos, _stuck_last_pos)
        if dist < STUCK_DISTANCE_THRESHOLD:
            _stuck_count += 1
            if _stuck_count >= STUCK_MAX_ATTEMPTS:
                ConsoleLog("stuck", f"Stuck ({_stuck_count} checks) — skipping waypoint")
                _stuck_count = 0
                return True
        else:
            _stuck_count = 0

    _stuck_last_pos = current_pos
    return False


def reset_stuck():
    global _stuck_last_pos, _stuck_last_check, _stuck_count
    _stuck_last_pos = None
    _stuck_last_check = 0.0
    _stuck_count = 0


# ===========================================================================
# ASTERIUS DETECTION
# ===========================================================================

def scan_for_asterius():
    if state.asterius_spotted and state.asterius_agent_id > 0:
        if safe_agent_valid(state.asterius_agent_id):
            return True
        state.asterius_spotted = False
        state.asterius_agent_id = -1

    enemies = get_alive_enemies_in_range(Range.SafeCompass.value)
    for eid in enemies:
        if safe_agent_model(eid) == ASTERIUS_MODEL_ID:
            state.asterius_spotted = True
            state.asterius_agent_id = eid
            ConsoleLog("asterius", f"Spotted! Agent ID: {eid}")
            return True
    return False


def is_asterius_dead():
    if state.asterius_spotted and state.asterius_agent_id > 0:
        if safe_agent_dead(state.asterius_agent_id):
            state.asterius_killed = True
            ConsoleLog("asterius", "Killed!")
            return True
    return False


def is_asterius_killed_or_time_elapsed():
    state.elapsed += 1
    if state.elapsed > MAX_WAIT_SECONDS:
        ConsoleLog("asterius", f"Timeout after {MAX_WAIT_SECONDS}s — resigning")
        return True
    if state.asterius_killed:
        return True
    scan_for_asterius()
    is_asterius_dead()
    return state.asterius_killed


# ===========================================================================
# BLESSED WIDGET
# ===========================================================================

def trigger_party_blessing():
    """Write the run flag and return. Call wait_for_blessing() after."""
    try:
        cp = configparser.ConfigParser()
        cp.read(BLESSED_INI_PATH)
        if not cp.has_section("BlessingRun"):
            cp.add_section("BlessingRun")
        cp.set("BlessingRun", "Enabled", "True")
        with open(BLESSED_INI_PATH, "w") as f:
            cp.write(f)
        ConsoleLog("blessing", "Triggered party blessing via Blessed widget")
    except Exception as e:
        ConsoleLog("blessing", f"Failed to trigger blessing: {e}")


def is_blessing_complete():
    """Check if the Blessed widget has finished (leader resets Enabled=False)."""
    try:
        cp = configparser.ConfigParser()
        cp.read(BLESSED_INI_PATH)
        return cp.get("BlessingRun", "Enabled", fallback="false").strip().lower() != "true"
    except Exception:
        return True  # Assume done if we can't read


def wait_for_blessing():
    """Coroutine: poll until all accounts report blessing complete (max 20s)."""
    ConsoleLog("blessing", "Waiting for all accounts to get blessed...")
    elapsed = 0
    while not is_blessing_complete() and elapsed < 20:
        yield from Routines.Yield.wait(1000)
        elapsed += 1

    if elapsed >= 20:
        ConsoleLog("blessing", "Blessing timeout — proceeding without full confirmation")
    else:
        ConsoleLog("blessing", f"All accounts blessed in {elapsed}s")


# ===========================================================================
# PARTY WIPE RECOVERY
# ===========================================================================

def _on_party_wipe(bot_ref: "Botting"):
    while safe_agent_dead(cache.player_id):
        yield from bot_ref.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            bot_ref.config.FSM.resume()
            return

    # Jump back to Start Combat header to re-enter the route
    # Note: step name depends on Botting's internal naming convention
    bot_ref.States.JumpToStepName("[H]Start Combat")
    bot_ref.config.FSM.resume()


def OnPartyWipe(bot_ref: "Botting"):
    ConsoleLog("wipe", "Party wipe triggered")
    fsm = bot_ref.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot_ref))


# ===========================================================================
# PORTAL ZONE-IN COMBAT
# ===========================================================================

def clear_portal_aggro():
    ConsoleLog("portal", "Checking for portal aggro...")
    yield from Routines.Yield.wait(jitter_wait(2000))

    if not is_in_combat():
        ConsoleLog("portal", "No portal aggro — proceeding")
        return

    ConsoleLog("portal", "Portal pack aggroed — waiting for clear")
    wait = 0
    while is_in_combat() and wait < COMBAT_WAIT_TIMEOUT:
        scan_for_asterius()
        yield from Routines.Yield.wait(1000)
        wait += 1

    yield from collect_loot_en_route()
    ConsoleLog("portal", f"Portal clear after {wait}s")


# ===========================================================================
# LOOT COLLECTION
# ===========================================================================

def collect_loot_en_route():
    """Quick loot sweep after combat. Not exhaustive."""
    items = AgentArray.Filter.ByDistance(cache.items, cache.player_xy, LOOT_PICKUP_RANGE)
    if len(items) == 0:
        return

    # Sort by distance — pick up closest first (human-like)
    items = AgentArray.Sort.ByDistance(items, cache.player_xy)
    count = len(items)
    ConsoleLog("loot", f"Picking up {count} items en route")

    for item_id in items:
        try:
            Player.Interact(item_id)
        except Exception:
            pass
        yield from Routines.Yield.wait(jitter_wait(400))

    farm_stats.record_pickup(count)
    yield from Routines.Yield.wait(jitter_wait(500))


def collect_all_loot():
    """Exhaustive loot collection after Asterius kill. Won't stop until ground is clear."""
    loot_elapsed = 0
    settle_timer = 0

    ConsoleLog("loot", "Starting final loot collection...")

    while True:
        if not Map.IsExplorable():
            return

        loot_elapsed += 1
        if loot_elapsed > MAX_LOOT_SECONDS:
            ConsoleLog("loot", f"Loot timeout after {MAX_LOOT_SECONDS}s")
            return

        items = AgentArray.Filter.ByDistance(cache.items, cache.player_xy, LOOT_PICKUP_RANGE)

        if len(items) == 0:
            settle_timer += 1
            if settle_timer >= (LOOT_SETTLE_MS // 1000):
                ConsoleLog("loot", "All loot collected — ground clear")
                return
            yield from Routines.Yield.wait(1000)
            continue

        settle_timer = 0
        count = len(items)
        items = AgentArray.Sort.ByDistance(items, cache.player_xy)

        for item_id in items:
            try:
                Player.Interact(item_id)
            except Exception:
                pass
            yield from Routines.Yield.wait(jitter_wait(500))

        farm_stats.record_pickup(count)
        yield from Routines.Yield.wait(1000)


def is_all_loot_collected():
    items = AgentArray.Filter.ByDistance(cache.items, cache.player_xy, LOOT_PICKUP_RANGE)
    return len(items) == 0


# ===========================================================================
# INTELLIGENT COMBAT ROUTING
# ===========================================================================

def intelligent_combat_route():
    """Managed coroutine: traverse TRAVEL_PATH with smart pulling and combat.

    Per-waypoint:
    1. Party cohesion — wait for EMO/slowest member
    2. Party health — don't pull if someone is low
    3. Scan for pullable groups (nearest cluster, not largest)
    4. Body-pull + chain-pull adjacent groups
    5. Combat wait with stall detection
    6. Loot after every combat
    7. Stuck detection
    8. Asterius intercept — abandon path when spotted
    9. Blessing trigger at end of route
    """
    dense_path = densify_path(TRAVEL_PATH)
    ConsoleLog("route", f"Starting route — {len(TRAVEL_PATH)} waypoints ({len(dense_path)} with sub-points)")
    reset_stuck()
    last_party_check = time.time()
    intercept_logged = False

    for wp_index, (wp_x, wp_y) in enumerate(dense_path):
        # Only log original waypoints, not intermediate sub-points
        if (wp_x, wp_y) in TRAVEL_PATH:
            orig_idx = TRAVEL_PATH.index((wp_x, wp_y)) + 1
            ConsoleLog("route", f"WP {orig_idx}/{len(TRAVEL_PATH)}")
        reset_stuck()

        while True:
            if not Map.IsExplorable():
                return

            if state.asterius_killed:
                ConsoleLog("route", "Asterius killed en route — stopping")
                return

            # --- ASTERIUS INTERCEPT ---
            if state.asterius_spotted and state.asterius_agent_id > 0 and not state.asterius_killed:
                a_pos = safe_agent_xy(state.asterius_agent_id)
                if not intercept_logged:
                    dist = Utils.Distance(cache.player_xy, a_pos)
                    ConsoleLog("route", f"Asterius on compass! Dist: {dist:.0f} — intercepting")
                    intercept_logged = True
                jx, jy = jitter_pos(a_pos[0], a_pos[1], JITTER_INTERCEPT)
                Player.Move(jx, jy)
                yield from Routines.Yield.wait(jitter_wait(1000))
                if safe_agent_alive(state.asterius_agent_id):
                    continue
                else:
                    return

            dist_to_wp = Utils.Distance(cache.player_xy, (wp_x, wp_y))

            if dist_to_wp < WAYPOINT_TOLERANCE:
                break

            if not is_in_combat() and check_stuck():
                ConsoleLog("route", f"Stuck — skipping to WP {wp_index + 2}")
                break

            # --- PARTY COHESION ---
            now = time.time()
            if now - last_party_check >= PARTY_CHECK_INTERVAL and not is_in_combat():
                last_party_check = now
                if not is_party_together():
                    yield from wait_for_party()
                    continue

            # --- SCAN & PULL ---
            if not is_in_combat():
                # Don't pull if party is hurt
                if not is_party_healthy(0.5):
                    # Just move, let healers top off
                    jx, jy = jitter_pos(wp_x, wp_y, JITTER_MOVE)
                    Player.Move(jx, jy)
                    yield from Routines.Yield.wait(jitter_wait(500))
                    continue

                cluster_target, group_members = find_nearby_pullable_group()

                if cluster_target and len(group_members) > 0:
                    c_pos = safe_agent_xy(cluster_target)
                    ConsoleLog("route_intel",
                        f"Pulling {len(group_members)} near ({c_pos[0]:.0f}, {c_pos[1]:.0f})")

                    jx, jy = jitter_pos(c_pos[0], c_pos[1], JITTER_PULL)
                    Player.Move(jx, jy)
                    yield from Routines.Yield.wait(jitter_wait(1500))
                    scan_for_asterius()

                    # Chain pull — only if second group is close to PLAYER (not just cluster1)
                    c2_target, c2_members = find_nearby_pullable_group()
                    if c2_target and len(c2_members) > 0:
                        c2_pos = safe_agent_xy(c2_target)
                        dist_to_player = Utils.Distance(cache.player_xy, c2_pos)
                        if dist_to_player < PULL_RANGE:
                            ConsoleLog("route_intel",
                                f"Chain-pulling {len(c2_members)} more — {dist_to_player:.0f} from leader")
                            jx2, jy2 = jitter_pos(c2_pos[0], c2_pos[1], JITTER_PULL)
                            Player.Move(jx2, jy2)
                            yield from Routines.Yield.wait(jitter_wait(1500))
                else:
                    jx, jy = jitter_pos(wp_x, wp_y, JITTER_MOVE)
                    Player.Move(jx, jy)
                    yield from Routines.Yield.wait(jitter_wait(500))

            # --- COMBAT WAIT WITH STALL DETECTION ---
            if is_in_combat():
                combat_wait = 0
                last_kill_check = 0
                prev_count = count_aggroed()

                while is_in_combat():
                    scan_for_asterius()
                    is_asterius_dead()

                    if state.asterius_killed:
                        ConsoleLog("route", "Asterius killed during combat — stopping")
                        return

                    yield from Routines.Yield.wait(1000)
                    combat_wait += 1
                    last_kill_check += 1

                    if last_kill_check >= 5:
                        current = count_aggroed()
                        if current < prev_count:
                            prev_count = current
                            last_kill_check = 0
                        else:
                            prev_count = current

                    if combat_wait >= COMBAT_WAIT_TIMEOUT and last_kill_check >= COMBAT_STALL_WINDOW:
                        ConsoleLog("route",
                            f"Stalled — {combat_wait}s, no kills in {last_kill_check}s")
                        break

                yield from collect_loot_en_route()

        yield from Routines.Yield.wait(jitter_wait(300))

    # Trigger blessing and wait for all accounts to confirm
    trigger_party_blessing()
    yield from wait_for_blessing()
    ConsoleLog("route", "Route complete — waiting for Asterius")


# ===========================================================================
# BACKGROUND COROUTINE
# ===========================================================================

def handle_asterius_killed_en_route():
    while True:
        if not Map.IsExplorable():
            yield from Routines.Yield.wait(1000)
            continue
        if state.asterius_killed:
            yield from Routines.Yield.wait(500)
            continue
        scan_for_asterius()
        is_asterius_dead()
        yield from Routines.Yield.wait(500)


# ===========================================================================
# MAIN ROUTINE
# ===========================================================================

def track_run_end():
    farm_stats.end_run(killed=state.asterius_killed)


def start_run_timer():
    farm_stats.start_run()


def reset_farm():
    state.reset()


def farm_scythes(bot: Botting) -> None:
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')
    # Widget name must match MODULE_NAME in the Blessed widget script
    widget_handler.enable_widget('Blessed')

    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))

    bot.States.AddHeader(BOT_NAME)
    bot.Templates.Multibox_Aggressive()
    bot.Properties.Disable("auto_inventory_management")

    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_TO_TRAVEL)
    bot.Party.SetHardMode(True)

    bot.States.AddManagedCoroutine('Asterius background scan', handle_asterius_killed_en_route)

    bot.States.AddHeader('Exit To Farm')
    bot.States.AddCustomState(start_run_timer, "Start run timer")
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(-2166, 861, target_map_id=VARAJAR_FELLS_MAP_ID)
    bot.Wait.ForTime(3000)
    bot.Properties.Enable('pause_on_danger')

    # Wait for all 6 accounts to zone in before proceeding
    bot.States.AddManagedCoroutine('Wait for party zone-in', wait_for_party_zone_in)
    bot.Wait.ForTime(1000)

    # Portal zone-in: clear elementals/kobolds
    bot.States.AddHeader("Zone-In Combat Clear")
    bot.States.AddManagedCoroutine('Clear portal aggro', clear_portal_aggro)
    bot.Wait.UntilCondition(lambda: not is_in_combat(), duration=1000)

    # Intelligent combat routing
    bot.States.AddHeader("Start Combat")
    bot.States.AddManagedCoroutine('Intelligent Combat Route', intelligent_combat_route)
    bot.Wait.UntilCondition(is_asterius_killed_or_time_elapsed, duration=1000)

    # Loot collection — resign only after ALL loot picked up
    bot.States.AddHeader("Loot Collection")
    bot.States.AddManagedCoroutine('Collect all loot', collect_all_loot)
    bot.Wait.UntilCondition(is_all_loot_collected, duration=1000)
    bot.Wait.ForTime(LOOT_SETTLE_MS)

    # End of run
    bot.States.AddCustomState(track_run_end, "Track run stats")
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(reset_farm, "Reset farm state")
    bot.Wait.UntilOnOutpost()
    bot.Wait.ForTime(5000)
    # Loop back — step name suffix depends on Botting's internal numbering
    bot.States.JumpToStepName("[H]Exit To Farm_3")


bot.SetMainRoutine(farm_scythes)


def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Asterius Scythe Farmer bot", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("Intelligent multi-account bot for Asterius Scythe in Hard Mode")
    PyImGui.spacing()
    PyImGui.bullet_text("Requirements:")
    PyImGui.bullet_text("- 6 accounts + 2 NPC heroes (8 total)")
    PyImGui.bullet_text("- Hero AI widget enabled on all accounts")
    PyImGui.bullet_text("- Launch the script on the party leader only")
    PyImGui.spacing()
    PyImGui.bullet_text("Features:")
    PyImGui.bullet_text("- Smart multi-group pulling with chain-pull")
    PyImGui.bullet_text("- Party cohesion + health monitoring")
    PyImGui.bullet_text("- Stall detection + stuck recovery")
    PyImGui.bullet_text("- Active loot collection after every fight")
    PyImGui.bullet_text("- Asterius intercept on compass detection")
    PyImGui.bullet_text("- Run stats tracking (kills/hr, items)")

    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Mark")
    PyImGui.end_tooltip()


def main():
    bot.Update()
    bot.UI.draw_window(icon_path=TEXTURE)


if __name__ == "__main__":
    main()
