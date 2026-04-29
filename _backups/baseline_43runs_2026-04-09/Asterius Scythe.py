import os
import time

import Py4GW
from Py4GW_widget_manager import get_widget_handler
from Py4GWCoreLib import GLOBAL_CACHE
from Py4GWCoreLib import AgentArray
from Py4GWCoreLib import Botting
from Py4GWCoreLib import ConsoleLog
from Py4GWCoreLib import Range
from Py4GWCoreLib import Routines
from Py4GWCoreLib import Utils
from Py4GWCoreLib import Map, Agent, Player
from Py4GWCoreLib import Item, ItemArray, Bags, Inventory
from Py4GWCoreLib import LootConfig, Party
from Py4GWCoreLib import ModelID


# === Global loot blacklist: map pieces ===
# Applied once at module import so it persists on the LootConfig singleton for
# the entire Py4GW session. Other bots that start later will see the same
# blacklist. Harmless if re-applied (set semantics).
_MAP_PIECE_MODEL_IDS = (
    ModelID.Map_Piece_Bottom_Left.value,
    ModelID.Map_Piece_Bottom_Right.value,
    ModelID.Map_Piece_Top_Left.value,
    ModelID.Map_Piece_Top_Right.value,
)
try:
    _lc = LootConfig()
    for _mp_id in _MAP_PIECE_MODEL_IDS:
        _lc.AddToBlacklist(_mp_id)
except Exception as _e:
    Py4GW.Console.Log("AsteriusFarm", f"Failed to blacklist map pieces at import: {_e}", Py4GW.Console.MessageType.Warning)

BOT_NAME = "Asterius Scythe Farm"
LOG_CHANNEL = "AsteriusFarm"

# File sink for external monitoring (Claude reads this on demand).
# Marginal overhead: open-write-close per line, wrapped in try/except so disk
# errors never break the farm. Logs dir is created lazily on first write.
_LOG_FILE_PATH = os.path.join(
    Py4GW.Console.get_projects_path(),
    "Logs",
    "asterius_farm.log",
)
_log_file_init_done = False


def _ensure_log_file() -> None:
    global _log_file_init_done
    if _log_file_init_done:
        return
    try:
        os.makedirs(os.path.dirname(_LOG_FILE_PATH), exist_ok=True)
        # Session delimiter so multiple runs are easy to spot in the file.
        with open(_LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n===== Asterius Farm session start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    except Exception:
        pass  # Disk unavailable? Console sink still works.
    _log_file_init_done = True


def _log(msg: str) -> None:
    """Live logging helper — prefixes every message with elapsed run time.

    Dual sink: Py4GW in-game console + append-only file at
    Py4GW-fresh/Logs/asterius_farm.log for external monitoring.
    """
    run_t = time.time() - run_stats["run_start_ts"] if run_stats["run_start_ts"] else 0.0
    line = f"[t+{run_t:6.1f}s] {msg}"
    ConsoleLog(LOG_CHANNEL, line)
    try:
        _ensure_log_file()
        with open(_LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except Exception:
        pass  # Never let logging break the farm loop.


run_stats = {
    "run_start_ts": 0.0,
    "runs_total": 0,
    "runs_killed": 0,
    "runs_timeout": 0,
    "runs_wiped": 0,
    "last_spotted_ts": 0.0,
    "last_spotted_dist": 0.0,
    "session_start_ts": time.time(),
    # Inventory session totals
    "session_gold_gained": 0,
    "session_items_looted": 0,
    "session_items_identified": 0,
    "session_items_salvaged": 0,
    "session_id_kit_uses_consumed": 0,
    "session_salvage_kit_uses_consumed": 0,
    "session_greens_dropped": 0,
    "session_golds_dropped": 0,
}

# Last inventory snapshots for cross-phase diffing
_inv_snap_run_start: dict | None = None
_inv_snap_post_loot: dict | None = None


def _snapshot_inventory() -> dict:
    """Snapshot current inventory state in bags 1-4.

    Captures everything needed to detect what loot/identify/salvage did
    between two checkpoints. Wrapped in try/except — a bad API call must
    never break the farm loop.
    """
    snap = {
        "ts": time.time(),
        "gold": 0,
        "free_slots": 0,
        "total_items": 0,
        "unidentified": 0,
        "whites": 0,
        "blues": 0,
        "purples": 0,
        "golds": 0,
        "greens": 0,
        "id_kit_count": 0,
        "id_kit_uses": 0,
        "salvage_kit_count": 0,
        "salvage_kit_uses": 0,
        "lesser_salvage_uses": 0,
        "expert_salvage_uses": 0,
        "items_by_model": {},  # model_id -> qty (for diff)
    }
    try:
        snap["gold"] = int(Inventory.GetGoldOnCharacter())
    except Exception as e:
        _log(f"snapshot: GetGoldOnCharacter failed: {e}")
    try:
        snap["free_slots"] = int(Inventory.GetFreeSlotCount())
    except Exception as e:
        _log(f"snapshot: GetFreeSlotCount failed: {e}")
    try:
        bags = ItemArray.CreateBagList(1, 2, 3, 4)
        items = ItemArray.GetItemArray(bags)
        snap["total_items"] = len(items)
        for item_id in items:
            try:
                qty = Item.Properties.GetQuantity(item_id)
            except Exception:
                qty = 1
            try:
                model_id = Item.GetModelID(item_id)
                snap["items_by_model"][model_id] = snap["items_by_model"].get(model_id, 0) + qty
            except Exception:
                pass

            # Kits
            try:
                if Item.Usage.IsIDKit(item_id):
                    snap["id_kit_count"] += 1
                    snap["id_kit_uses"] += int(Item.Usage.GetUses(item_id))
                    continue
                if Item.Usage.IsSalvageKit(item_id):
                    snap["salvage_kit_count"] += 1
                    uses = int(Item.Usage.GetUses(item_id))
                    snap["salvage_kit_uses"] += uses
                    if Item.Usage.IsLesserKit(item_id):
                        snap["lesser_salvage_uses"] += uses
                    else:
                        snap["expert_salvage_uses"] += uses
                    continue
            except Exception:
                pass

            # Identified state
            try:
                if not Item.Usage.IsIdentified(item_id):
                    snap["unidentified"] += 1
            except Exception:
                pass

            # Rarity buckets
            try:
                _, rarity_name = Item.Rarity.GetRarity(item_id)
                if rarity_name == "White":
                    snap["whites"] += 1
                elif rarity_name == "Blue":
                    snap["blues"] += 1
                elif rarity_name == "Purple":
                    snap["purples"] += 1
                elif rarity_name == "Gold":
                    snap["golds"] += 1
                elif rarity_name == "Green":
                    snap["greens"] += 1
            except Exception:
                pass
    except Exception as e:
        _log(f"snapshot: bag scan failed: {e}")
    return snap


def _format_snap(snap: dict) -> str:
    return (
        f"gold={snap['gold']} "
        f"free={snap['free_slots']} "
        f"items={snap['total_items']} "
        f"unid={snap['unidentified']} "
        f"W{snap['whites']}/B{snap['blues']}/P{snap['purples']}/G{snap['golds']}/Gr{snap['greens']} "
        f"IDk={snap['id_kit_count']}({snap['id_kit_uses']}u) "
        f"Slvk={snap['salvage_kit_count']}(L{snap['lesser_salvage_uses']}/E{snap['expert_salvage_uses']})"
    )


def _diff_snapshots(label: str, before: dict, after: dict) -> None:
    """Log a delta between two snapshots, attributing what likely happened."""
    if not before or not after:
        return
    d_gold = after["gold"] - before["gold"]
    d_items = after["total_items"] - before["total_items"]
    d_unid = after["unidentified"] - before["unidentified"]
    d_id_uses = before["id_kit_uses"] - after["id_kit_uses"]
    d_slv_uses = before["salvage_kit_uses"] - after["salvage_kit_uses"]
    d_lesser = before["lesser_salvage_uses"] - after["lesser_salvage_uses"]
    d_expert = before["expert_salvage_uses"] - after["expert_salvage_uses"]
    d_greens = after["greens"] - before["greens"]
    d_golds = after["golds"] - before["golds"]
    d_purples = after["purples"] - before["purples"]
    d_blues = after["blues"] - before["blues"]
    d_whites = after["whites"] - before["whites"]

    # Item-level model diff
    added_models = []
    removed_models = []
    before_models = before.get("items_by_model", {})
    after_models = after.get("items_by_model", {})
    for model_id, qty in after_models.items():
        delta = qty - before_models.get(model_id, 0)
        if delta > 0:
            added_models.append((model_id, delta))
    for model_id, qty in before_models.items():
        delta = qty - after_models.get(model_id, 0)
        if delta > 0:
            removed_models.append((model_id, delta))

    parts = [f"DIFF [{label}]"]
    parts.append(f"Δgold={d_gold:+d}")
    parts.append(f"Δitems={d_items:+d}")
    if d_unid:
        parts.append(f"Δunid={d_unid:+d}")
    if d_id_uses:
        parts.append(f"ID_kit_uses_consumed={d_id_uses}")
    if d_slv_uses:
        parts.append(f"Slv_kit_uses_consumed={d_slv_uses}(L{d_lesser}/E{d_expert})")
    rarity_bits = []
    if d_whites:
        rarity_bits.append(f"W{d_whites:+d}")
    if d_blues:
        rarity_bits.append(f"B{d_blues:+d}")
    if d_purples:
        rarity_bits.append(f"P{d_purples:+d}")
    if d_golds:
        rarity_bits.append(f"G{d_golds:+d}")
    if d_greens:
        rarity_bits.append(f"Gr{d_greens:+d}")
    if rarity_bits:
        parts.append("rarity=" + "/".join(rarity_bits))
    _log(" ".join(parts))

    # Heuristic attribution to session totals
    if d_gold > 0:
        run_stats["session_gold_gained"] += d_gold
    if d_id_uses > 0:
        run_stats["session_id_kit_uses_consumed"] += d_id_uses
        run_stats["session_items_identified"] += d_id_uses
    if d_slv_uses > 0:
        run_stats["session_salvage_kit_uses_consumed"] += d_slv_uses
        run_stats["session_items_salvaged"] += d_slv_uses
    if label == "loot" and d_items > 0:
        run_stats["session_items_looted"] += d_items
    if d_greens > 0:
        run_stats["session_greens_dropped"] += d_greens
    if d_golds > 0:
        run_stats["session_golds_dropped"] += d_golds

    if added_models:
        sample = ", ".join(f"m{mid}x{q}" for mid, q in added_models[:8])
        more = "" if len(added_models) <= 8 else f" (+{len(added_models)-8} more)"
        _log(f"  + added: {sample}{more}")
    if removed_models:
        sample = ", ".join(f"m{mid}x{q}" for mid, q in removed_models[:8])
        more = "" if len(removed_models) <= 8 else f" (+{len(removed_models)-8} more)"
        _log(f"  - removed: {sample}{more}")
MODULE_ICON = "Textures\\Module_Icons\\Asterius' Scythe.png"
TEXTURE = os.path.join(
    Py4GW.Console.get_projects_path(), "Bots", "marks_coding_corner", "textures", "asterius_scythe.png"
)
OUTPOST_TO_TRAVEL = Map.GetMapIDByName('Olafstead')
VARAJAR_FELLS_MAP_ID = 553
ASTERIUS_MODEL_ID = 6509

TRAVEL_PATH: list[tuple[float, float]] = [
    (-3300, -771),
    (-2412, -3609),
    (-3463, -4259),
    (-6053, -3951),
    (-9523, -1743),
    (-11262, -746),
    (-13185, -1489),
    (-14009, -2740),
    (-14986, -4104),
    (-15067, -5162),
    (-14548, -6698),
    (-15823, -6778),
    (-17105, -5527),
    (-20475, -9155),
]

is_asterius_spotted = False
is_asterius_killed = False
asterius_agent_id = -1
elapsed = 0

# Bug A guard: suppress the spurious PARTY WIPE event that fires whenever
# Multibox.ResignParty() flips party_defeated=True. Set to True right before
# resign, cleared once we've safely reached the outpost again.
_resign_in_progress = False
_resign_armed_ts: float = 0.0
RESIGN_FLAG_MAX_AGE_S = 60.0  # safety: force-clear if flag sticks longer than this

bot = Botting(BOT_NAME)


def is_asterius_killed_or_time_elapsed():
    global is_asterius_killed
    global is_asterius_spotted
    global asterius_agent_id
    global elapsed

    elapsed += 1
    # Cap at 3 minutes to wait for Asterius on the final spot
    if elapsed > 180:
        run_stats["runs_timeout"] += 1
        _log(f"TIMEOUT — 180s reached without kill (spotted={is_asterius_spotted}, id={asterius_agent_id})")
        return True

    if is_asterius_killed:
        return True

    if is_asterius_spotted and asterius_agent_id >= 0:
        if not Agent.IsDead(asterius_agent_id):
            if elapsed % 10 == 0:
                dist = Utils.Distance(Player.GetXY(), Agent.GetXY(asterius_agent_id))
                hp = Agent.GetHealth(asterius_agent_id) if hasattr(Agent, "GetHealth") else -1.0
                _log(f"Asterius alive — dist={dist:.0f}, hp={hp:.2f}")
            return False
        is_asterius_killed = True
        # C5: runs_killed increment moved to reset_farm_flags so kills still
        # count even when the step watchdog advances past this state.
        _log(f"KILL CONFIRMED (agent_id={asterius_agent_id})")
        return True

    enemy_array = AgentArray.GetEnemyArray()
    enemy_array = AgentArray.Filter.ByCondition(
        enemy_array,
        lambda agent_id: Utils.Distance(Player.GetXY(), Agent.GetXY(agent_id))
        <= Range.SafeCompass.value,
    )
    enemy_array = AgentArray.Filter.ByCondition(
        enemy_array, lambda agent_id: Player.GetAgentID() != agent_id
    )
    if elapsed % 15 == 0:
        try:
            _cur_map = int(Map.GetMapID())
        except Exception:
            _cur_map = -1
        _log(f"Scanning — {len(enemy_array)} enemies in compass range, player@{Player.GetXY()} map_id={_cur_map}")
    for enemy_id in enemy_array:
        if Agent.GetModelID(enemy_id) == ASTERIUS_MODEL_ID:
            is_asterius_spotted = True
            asterius_agent_id = enemy_id
            dist = Utils.Distance(Player.GetXY(), Agent.GetXY(enemy_id))
            run_stats["last_spotted_ts"] = time.time()
            run_stats["last_spotted_dist"] = dist
            _log(f"ASTERIUS SPOTTED — agent_id={enemy_id}, dist={dist:.0f}, pos={Agent.GetXY(enemy_id)}")
    return False


# Post-kill loot phase state — rebuilt every run.
POST_KILL_STATE = {
    "phase_start_ts": 0.0,
    "combat_left_ts": None,   # timestamp of most recent transition out of combat
    "any_dead_seen": False,   # latched once we notice a dead party member
    "logged_clean_exit": False,
    "logged_dead_timer": False,
}
POST_KILL_MAX_WAIT_S = 120.0  # hard cap so we never sit forever
POST_KILL_DEAD_GRACE_S = 30.0  # how long to wait after leaving combat when party has dead members
POST_KILL_CLEAN_EXIT_BUFFER_S = 5.0  # after combat ends, wait this long before allowing clean exit (so loot has time to settle)


def _reset_post_kill_state():
    POST_KILL_STATE["phase_start_ts"] = time.time()
    POST_KILL_STATE["combat_left_ts"] = None
    POST_KILL_STATE["any_dead_seen"] = False
    POST_KILL_STATE["logged_clean_exit"] = False
    POST_KILL_STATE["logged_dead_timer"] = False
    _log("Post-kill wait: started (looting + resign gate)")


def _any_party_member_dead() -> bool:
    """Return True if any hero or henchman in the party is currently dead."""
    try:
        heroes = GLOBAL_CACHE.Party.GetHeroes()
        for hero in heroes:
            if Agent.IsValid(hero.agent_id) and Agent.IsDead(hero.agent_id):
                return True
    except Exception:
        pass
    try:
        henchmen = GLOBAL_CACHE.Party.GetHenchmen()
        for h in henchmen:
            if Agent.IsValid(h.agent_id) and Agent.IsDead(h.agent_id):
                return True
    except Exception:
        pass
    try:
        players = GLOBAL_CACHE.Party.GetPlayers()
        for p in players:
            agent_id = GLOBAL_CACHE.Party.Players.GetAgentIDByLoginNumber(p.login_number)
            if Agent.IsValid(agent_id) and Agent.IsDead(agent_id):
                return True
    except Exception:
        pass
    return False


def _loot_remaining_count() -> int:
    """Number of lootable items on the ground within compass range."""
    try:
        loot_singleton = LootConfig()
        loot_array = loot_singleton.GetfilteredLootArray(
            distance=Range.SafeCompass.value,
            multibox_loot=True,
            allow_unasigned_loot=True,
        )
        return len(loot_array)
    except Exception as e:
        _log(f"loot check failed: {e}")
        return 0


def _in_combat_now() -> bool:
    """True if any aggressive enemy is within earshot."""
    try:
        return bool(Routines.Checks.Agents.InAggro(aggressive_only=True))
    except Exception:
        return False


def is_ready_to_resign() -> bool:
    """Gate condition for leaving the kill site.

    Asterius is already confirmed dead before this runs. Resign when EITHER:
      (a) loot ground is empty AND we're not in combat  — the clean path
      (b) we have dead party members AND we've been out of combat for >=30s
      (c) hard cap: POST_KILL_MAX_WAIT_S elapsed since phase start
    """
    now = time.time()
    if POST_KILL_STATE["phase_start_ts"] == 0.0:
        _reset_post_kill_state()
        now = POST_KILL_STATE["phase_start_ts"]

    phase_elapsed = now - POST_KILL_STATE["phase_start_ts"]

    # Hard cap — never sit forever
    if phase_elapsed >= POST_KILL_MAX_WAIT_S:
        _log(f"Post-kill wait: HARD CAP {POST_KILL_MAX_WAIT_S:.0f}s reached, forcing resign")
        return True

    # Track dead-party latch
    if not POST_KILL_STATE["any_dead_seen"] and _any_party_member_dead():
        POST_KILL_STATE["any_dead_seen"] = True
        _log("Post-kill wait: dead party member detected — 30s-after-combat timer armed")

    in_combat = _in_combat_now()
    if in_combat:
        # (re-)reset out-of-combat timestamp while still fighting
        POST_KILL_STATE["combat_left_ts"] = None
        return False

    # Just left combat this poll — stamp it
    if POST_KILL_STATE["combat_left_ts"] is None:
        POST_KILL_STATE["combat_left_ts"] = now

    loot_left = _loot_remaining_count()

    # Path (b): dead members + 30s grace after combat
    if POST_KILL_STATE["any_dead_seen"]:
        out_of_combat_s = now - POST_KILL_STATE["combat_left_ts"]
        if not POST_KILL_STATE["logged_dead_timer"]:
            _log(f"Post-kill wait: party has dead members — waiting {POST_KILL_DEAD_GRACE_S:.0f}s after leaving combat (loot_left={loot_left})")
            POST_KILL_STATE["logged_dead_timer"] = True
        if out_of_combat_s >= POST_KILL_DEAD_GRACE_S:
            _log(f"Post-kill wait: dead-member grace elapsed ({out_of_combat_s:.0f}s out of combat), resigning (loot_left={loot_left})")
            return True
        return False

    # Path (a): clean exit — no combat, no loot on ground, and buffer elapsed
    # The buffer only starts counting AFTER combat ends so loot drops have time
    # to materialise and the party can walk to them. Resigning before the buffer
    # has elapsed risks leaving greens / gold drops on the ground.
    out_of_combat_s = now - POST_KILL_STATE["combat_left_ts"]
    if out_of_combat_s < POST_KILL_CLEAN_EXIT_BUFFER_S:
        # Buffer still ticking — even if loot_left == 0 right now, give it time
        if int(phase_elapsed) % 3 == 0 and phase_elapsed > 0:
            _log(
                f"Post-kill wait: out of combat {out_of_combat_s:.1f}s/{POST_KILL_CLEAN_EXIT_BUFFER_S:.0f}s buffer, "
                f"loot_left={loot_left}"
            )
        return False

    if loot_left == 0:
        if not POST_KILL_STATE["logged_clean_exit"]:
            _log(
                f"Post-kill wait: ground loot cleared and out of combat for "
                f"{out_of_combat_s:.1f}s — resigning (phase={phase_elapsed:.0f}s)"
            )
            POST_KILL_STATE["logged_clean_exit"] = True
        return True

    # Still picking up loot — periodic status line
    if int(phase_elapsed) % 10 == 0 and phase_elapsed > 0:
        _log(f"Post-kill wait: {loot_left} loot items remaining on ground, phase={phase_elapsed:.0f}s")
    return False


# ---------------------------------------------------------------------------
# Post-resign outpost wait with party-loaded grace period
# ---------------------------------------------------------------------------
# Root cause (confirmed 2026-04-09 via watchdog log):
# After Multibox.ResignParty() in an 8-client party, all clients reload in
# Olafstead but Party.party_instance().is_party_loaded stays False in the
# outpost for 180+ seconds. The default bot.Wait.UntilOnOutpost() helper
# chains through MapValid() → IsPartyLoaded() and hangs forever, which the
# 180s step watchdog eventually yanks out of — but only after destroying
# the run by re-zoning without inventory processing.
#
# This predicate:
#   1. Returns False while Map.IsOutpost() is False (bypassing party-loaded)
#   2. Once in the outpost, stamps a first-seen timestamp
#   3. Returns True immediately if Party.IsPartyLoaded() is True (clean path)
#   4. Returns True after 5s grace even if party still isn't loaded (HeroAI's
#      auto-invite will re-form the party asynchronously; subsequent bot
#      steps don't strictly need it before kit-restock / auto-ID)
_outpost_wait_first_seen_ts: float = 0.0
_OUTPOST_PARTY_LOAD_GRACE_S: float = 5.0


def _wait_for_outpost_with_party_grace() -> bool:
    global _outpost_wait_first_seen_ts
    # CRITICAL: Do NOT use Map.IsOutpost() here. Empirical result on 2026-04-09:
    # after Multibox.ResignParty(), master visibly returns to Olafstead and
    # Map.GetMapID() correctly reports 645 (verified via the step watchdog's
    # own successful read), but Map.IsOutpost() returns False for the entire
    # wait window. The native instance-type flag lies in this state.
    #
    # Use GetMapID() == OUTPOST_TO_TRAVEL instead — it's the same check the
    # step watchdog uses, and it's proven to return the correct value (645)
    # in the same scenario where IsOutpost() returns False.
    try:
        cur_map_id = int(Map.GetMapID())
    except Exception:
        cur_map_id = 0
    if cur_map_id != OUTPOST_TO_TRAVEL:
        _outpost_wait_first_seen_ts = 0.0
        return False

    if _outpost_wait_first_seen_ts == 0.0:
        _outpost_wait_first_seen_ts = time.time()
        # One-shot diagnostic: captures the raw party state at the exact moment
        # we first see the outpost post-resign. Fires once per run cycle, no
        # hot-path cost. If "IsPartyLoaded=False party_size=N" stays False for
        # 5s across many runs, the culprit is HeroAI's re-invite chain (or the
        # native is_party_loaded flag itself) rather than timing.
        try:
            party_loaded_raw = bool(Party.IsPartyLoaded())
        except Exception:
            party_loaded_raw = False
        try:
            party_size = int(Party.GetPartySize())
        except Exception:
            party_size = -1
        try:
            player_loaded = bool(Player.IsPlayerLoaded())
        except Exception:
            player_loaded = False
        _log(
            f"In outpost (Map.IsOutpost=True) — IsPartyLoaded={party_loaded_raw} "
            f"party_size={party_size} Player.IsPlayerLoaded={player_loaded} — "
            f"waiting up to {_OUTPOST_PARTY_LOAD_GRACE_S:.0f}s for party to reload"
        )

    try:
        party_loaded = bool(Party.IsPartyLoaded())
    except Exception:
        party_loaded = False

    elapsed_s = time.time() - _outpost_wait_first_seen_ts

    if party_loaded:
        _log(f"Party reloaded after {elapsed_s:.1f}s in outpost — proceeding")
        _outpost_wait_first_seen_ts = 0.0
        return True

    if elapsed_s >= _OUTPOST_PARTY_LOAD_GRACE_S:
        _log(f"Party still not loaded after {elapsed_s:.1f}s grace — proceeding anyway (HeroAI will re-invite asynchronously)")
        _outpost_wait_first_seen_ts = 0.0
        return True

    return False


def reset_farm_flags():
    global is_asterius_killed
    global is_asterius_spotted
    global asterius_agent_id
    global elapsed
    global _inv_snap_run_start, _inv_snap_post_loot, _outpost_wait_first_seen_ts

    # C5: count kill here (authoritative) so watchdog-advanced runs still
    # increment the counter instead of under-reporting.
    if is_asterius_killed:
        run_stats["runs_killed"] += 1

    # Summarise the run that just finished before wiping state.
    if run_stats["run_start_ts"]:
        run_t = time.time() - run_stats["run_start_ts"]
        outcome = "KILL" if is_asterius_killed else "NO_KILL"
        sess = time.time() - run_stats["session_start_ts"]
        rph = (run_stats["runs_total"] / sess * 3600.0) if sess > 0 else 0.0
        _log(
            f"RUN END — outcome={outcome}, duration={run_t:.1f}s | "
            f"session: runs={run_stats['runs_total']} "
            f"kills={run_stats['runs_killed']} "
            f"timeouts={run_stats['runs_timeout']} "
            f"wipes={run_stats['runs_wiped']} "
            f"rate={rph:.1f}/hr"
        )

    is_asterius_spotted = False
    is_asterius_killed = False
    asterius_agent_id = -1
    elapsed = 0
    run_stats["run_start_ts"] = 0.0

    # C2: wipe per-run snapshots and watchdog-style state so stale data from a
    # previous cycle can't leak into the next run's diagnostics or gates.
    _inv_snap_run_start = None
    _inv_snap_post_loot = None
    _outpost_wait_first_seen_ts = 0.0
    POST_KILL_STATE["phase_start_ts"] = 0.0
    POST_KILL_STATE["combat_left_ts"] = None
    POST_KILL_STATE["any_dead_seen"] = False
    POST_KILL_STATE["logged_clean_exit"] = False
    POST_KILL_STATE["logged_dead_timer"] = False


def _dump_gold_mod_diagnostic():
    """One-shot diagnostic: log every gold weapon's mod identifiers so we can
    build a mod-name → identifier lookup empirically.

    Runs each outpost entry AFTER auto-identify has completed. Expected output
    (for each gold in bags 1-4):

        [AsteriusFarm] GOLD_MOD_DUMP item=<id> model=<model> rarity=Gold
                        mods=[(identifier, arg, arg1, arg2), ...]

    Copy the [AsteriusFarm] output and send it to me after a few runs so I can
    map "Of the Necromancer", "Vampiric", "Zealous", etc. to their numeric
    identifiers. Once the table is built, this diagnostic becomes the filter.
    """
    try:
        bags = ItemArray.CreateBagList(1, 2, 3, 4)
        items = ItemArray.GetItemArray(bags)
    except Exception as e:
        _log(f"gold_mod_dump: bag scan failed: {e}")
        return

    golds_seen = 0
    for item_id in items:
        try:
            _, rarity_name = Item.Rarity.GetRarity(item_id)
            if rarity_name != "Gold":
                continue
            if not Item.Usage.IsIdentified(item_id):
                _log(f"GOLD_MOD_DUMP item={item_id} UNIDENTIFIED — auto-ID did not complete for this item")
                continue

            model_id = Item.GetModelID(item_id)
            mods = Item.Customization.Modifiers.GetModifiers(item_id)
            mod_tuples = []
            for m in mods:
                try:
                    ident = m.GetIdentifier()
                    arg = m.GetArg()
                    arg1 = m.GetArg1()
                    arg2 = m.GetArg2()
                    mod_tuples.append((ident, arg, arg1, arg2))
                except Exception as me:
                    mod_tuples.append(f"<err:{me}>")

            try:
                item_type_val, item_type_name = Item.GetItemType(item_id)
            except Exception:
                item_type_name = "?"

            _log(
                f"GOLD_MOD_DUMP item={item_id} model={model_id} "
                f"type={item_type_name} mods={mod_tuples}"
            )
            golds_seen += 1
        except Exception as e:
            _log(f"gold_mod_dump: per-item error on {item_id}: {e}")

    if golds_seen == 0:
        _log("GOLD_MOD_DUMP: no gold weapons in bags 1-4 this run")
    else:
        _log(f"GOLD_MOD_DUMP: dumped {golds_seen} gold item(s) — send this to Claude to build the filter table")


# TODO after user sends mod dumps:
#  1. Populate the two tables below with (identifier, required_arg_range) tuples
#  2. Wire _classify_gold_weapon() into a custom state that runs after the
#     diagnostic. Non-keepers get their item_id flagged for merchant disposal.
#  3. Add a post-filter step: walk to Olafstead merchant, sell flagged items,
#     close window. Use bot.Merchant.MerchantSellItem(item_id, qty) in a loop.
GOLD_WHITELIST_INSCRIPTIONS: set[int] = set()  # "Of the Necromancer/Ele/Ranger", "Of Aptitude 20%", "Strength and Honor", "Guided by Fate", "Shield of Devotion"
GOLD_WHITELIST_SCYTHE_MODS: set[int] = set()   # Thundering 20/20, Vampiric, Zealous, +5 armor, +5 energy "of any class"


def _post_resign_wait_coroutine():
    """Generator: wait until master is FULLY loaded in Olafstead post-resign.

    This replaces `bot.Wait.ForTime(15000)` which broke early because its
    underlying `_coro_for_time` uses `break_on_map_transition=True`. During
    the resign, master's map flips Varajar→Olafstead, the wait detects the
    transition, breaks after ~2-5 seconds, and the downstream inventory
    snapshot captures a half-loaded game state full of zeros.

    This custom generator uses plain `yield` (no Routines.Yield.wait) so it
    CANNOT be broken by map transition detection.

    Recovery behavior: if the hard cap elapses while we are still in Varajar
    (resign_party() silently no-op'd — observed 2026-04-09), re-trigger the
    resign up to MAX_RESIGN_RETRIES times before giving up. Simply proceeding
    as before leaves the farm in a broken state where XYAndExitMap_5 runs in
    the wrong map and burns a full 180s step watchdog per cycle.
    """
    start = time.time()
    PHASE_CAP_S = 20.0             # per-attempt cap
    MIN_WAIT_S = 3.0               # always wait at least this long per attempt
    MAX_RESIGN_RETRIES = 2         # total attempts = 1 initial + this many retries
    attempt = 0
    phase_start = start
    last_diag_log = 0.0

    def _ready_state():
        """Sample current game state. Returns (map_id, ready_bool, detail_str)."""
        try:
            map_id = int(Map.GetMapID())
            is_loading = bool(Map.IsMapLoading())
            player_loaded = bool(Player.IsPlayerLoaded())
            try:
                free_slots = int(Inventory.GetFreeSlotCount())
            except Exception:
                free_slots = 0
            try:
                gold = int(Inventory.GetGoldOnCharacter())
            except Exception:
                gold = 0
            inv_populated = (free_slots > 0) or (gold > 0)
            ready = (
                map_id == OUTPOST_TO_TRAVEL
                and not is_loading
                and player_loaded
                and inv_populated
            )
            detail = (
                f"map_id={map_id} loading={is_loading} player_loaded={player_loaded} "
                f"free_slots={free_slots} gold={gold}"
            )
            return map_id, ready, detail
        except Exception as e:
            return -1, False, f"ready-check error: {e}"

    while True:
        now = time.time()
        phase_elapsed = now - phase_start
        total_elapsed = now - start

        if phase_elapsed >= MIN_WAIT_S:
            map_id, ready, detail = _ready_state()
            if ready:
                _log(
                    f"Post-resign wait: master ready in Olafstead after "
                    f"{total_elapsed:.1f}s total ({attempt + 1} attempt(s)) — {detail}"
                )
                return

            if phase_elapsed >= PHASE_CAP_S:
                # Attempt exhausted. Are we actually in Olafstead?
                if map_id == OUTPOST_TO_TRAVEL:
                    _log(
                        f"Post-resign wait: in Olafstead but not fully loaded after "
                        f"{phase_elapsed:.1f}s — proceeding anyway ({detail})"
                    )
                    return

                # Still in Varajar (or unknown) — resign silently failed.
                if attempt < MAX_RESIGN_RETRIES:
                    attempt += 1
                    _log(
                        f"Post-resign wait: RESIGN FAILED after {phase_elapsed:.1f}s "
                        f"({detail}). Retriggering ResignParty (attempt {attempt + 1}/"
                        f"{MAX_RESIGN_RETRIES + 1})"
                    )
                    try:
                        bot.Multibox.ResignParty()
                    except Exception as e:
                        _log(f"Post-resign wait: ResignParty() raised: {e}")
                    phase_start = time.time()
                    last_diag_log = 0.0
                    yield
                    continue

                # All retries exhausted. Log CRITICAL and bail out — the step
                # watchdog + Varajar recovery path will take it from here.
                _log(
                    f"Post-resign wait: CRITICAL — resign failed {MAX_RESIGN_RETRIES + 1}x, "
                    f"total {total_elapsed:.1f}s, still not in Olafstead ({detail}). "
                    f"Giving up; letting step watchdog recover."
                )
                return

        # Periodic diagnostic every 5s
        if (now - last_diag_log) >= 5.0 and phase_elapsed >= MIN_WAIT_S:
            last_diag_log = now
            _, _, detail = _ready_state()
            _log(
                f"Post-resign wait: attempt={attempt + 1} phase_elapsed={phase_elapsed:.1f}s "
                f"total={total_elapsed:.1f}s {detail} (target: map_id={OUTPOST_TO_TRAVEL})"
            )

        yield  # hand control back to the FSM scheduler


def _verify_varajar_entry_or_log() -> None:
    """Custom state: log the actual game map after Move.XYAndExitMap completed.

    Move.XYAndExitMap's FSM step "completes" whenever its internal coroutine
    finishes, but that does NOT guarantee the map actually changed. Sometimes
    the portal is in a bad state and master stays in Olafstead. We need to
    know this from the log immediately, not infer it from scan positions an
    FSM-step later.
    """
    try:
        actual_map_id = int(Map.GetMapID())
    except Exception as e:
        _log(f"Post-map-exit: GetMapID failed: {e}")
        return
    try:
        pxy = Player.GetXY()
    except Exception:
        pxy = (0.0, 0.0)
    if actual_map_id == VARAJAR_FELLS_MAP_ID:
        _log(f"Entered Varajar Fells — sprinting to waypoint 1 {TRAVEL_PATH[0]} (map_id={actual_map_id}, player@{pxy})")
    else:
        _log(
            f"WARNING: XYAndExitMap returned but master is NOT in Varajar Fells. "
            f"actual map_id={actual_map_id} (expected {VARAJAR_FELLS_MAP_ID}), player@{pxy}. "
            f"Sprint will likely fail — the run will watchdog-timeout and recover."
        )


def _mark_run_start():
    global _inv_snap_run_start, _inv_snap_post_loot
    run_stats["run_start_ts"] = time.time()
    run_stats["runs_total"] += 1
    _inv_snap_run_start = _snapshot_inventory()
    _inv_snap_post_loot = None
    _log(f"RUN START — #{run_stats['runs_total']} exiting outpost to Varajar Fells")
    _log(f"  inv(pre-run): {_format_snap(_inv_snap_run_start)}")


def _snap_post_loot():
    global _inv_snap_post_loot
    _inv_snap_post_loot = _snapshot_inventory()
    _log(f"  inv(post-loot, pre-resign): {_format_snap(_inv_snap_post_loot)}")
    if _inv_snap_run_start:
        _diff_snapshots("loot", _inv_snap_run_start, _inv_snap_post_loot)


def _snap_post_outpost():
    """Snapshot after returning to outpost — InventoryPlus auto ID/salvage/deposit
    runs on outpost entry, so this captures what the auto-handler did."""
    snap = _snapshot_inventory()
    _log(f"  inv(post-outpost, post-auto): {_format_snap(snap)}")
    if _inv_snap_post_loot:
        _diff_snapshots("auto_process", _inv_snap_post_loot, snap)
    # Full-run diff for at-a-glance value
    if _inv_snap_run_start:
        d_gold = snap["gold"] - _inv_snap_run_start["gold"]
        d_greens = snap["greens"] - _inv_snap_run_start["greens"]
        d_golds = snap["golds"] - _inv_snap_run_start["golds"]
        _log(
            f"  RUN NET — Δgold={d_gold:+d}, Δgreens={d_greens:+d}, Δgolds={d_golds:+d} | "
            f"session: gold+{run_stats['session_gold_gained']} "
            f"looted={run_stats['session_items_looted']} "
            f"id'd={run_stats['session_items_identified']} "
            f"slv'd={run_stats['session_items_salvaged']} "
            f"greens={run_stats['session_greens_dropped']} "
            f"golds={run_stats['session_golds_dropped']}"
        )


def _on_party_wipe(bot: "Botting"):
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            # Map invalid → release FSM and exit
            bot.config.FSM.resume()
            return

    # Bug B fix: only jump into combat recovery if we're actually in Varajar
    # Fells. A wipe event fired from Olafstead (e.g., spurious post-resign)
    # should NOT jump the FSM to the combat path — that would re-enter the
    # kill route from the outpost, which is exactly the ghost-run bug.
    # C6: retry with logging — a transient GetMapID failure previously
    # silently defaulted to -1, which *happened* to be correct (releases FSM)
    # but masked real engine-side problems. Log the exception and retry once
    # after a short yield so we see the cause and get a real reading when the
    # engine stabilises.
    current_map_id = -1
    for attempt in (1, 2):
        try:
            current_map_id = int(Map.GetMapID())
            break
        except Exception as e:
            _log(f"Wipe handler: GetMapID failed (attempt {attempt}): {e}")
            if attempt == 1:
                yield from bot.Wait._coro_for_time(250)

    if current_map_id != VARAJAR_FELLS_MAP_ID:
        _log(
            f"Wipe handler: not in Varajar Fells (map_id={current_map_id}), "
            f"releasing FSM without jump"
        )
        bot.config.FSM.resume()
        return

    # Genuine wipe in Varajar → recover to Start Combat
    bot.States.JumpToStepName("[H]Start Combat_4")
    bot.config.FSM.resume()


def OnPartyWipe(bot: "Botting"):
    # Bug A fix: suppress during the resign-driven "party defeated" event.
    # bot.Multibox.ResignParty() internally sets party_defeated=True, which
    # triggers this event as a false positive. We know when we're resigning
    # because we set _resign_in_progress before the call.
    if _resign_in_progress:
        _log("PARTY WIPE event suppressed (resign in progress)")
        return

    run_stats["runs_wiped"] += 1
    _log(f"PARTY WIPE — spotted={is_asterius_spotted}, killed={is_asterius_killed}, pos={Player.GetXY()}")
    fsm = bot.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot))


def _set_resign_in_progress():
    """Custom state: arm the resign guard flag before Multibox.ResignParty()."""
    global _resign_in_progress, _resign_armed_ts
    _resign_in_progress = True
    _resign_armed_ts = time.time()
    _log("Resign-in-progress flag ARMED (PARTY WIPE events will be suppressed)")


def _clear_resign_in_progress():
    """Custom state: disarm the resign guard once we're safely in Olafstead."""
    global _resign_in_progress, _resign_armed_ts
    _resign_in_progress = False
    _resign_armed_ts = 0.0
    _log("Resign-in-progress flag DISARMED")


REGROUP_MAX_WAIT_S = 8.0
REGROUP_TIGHT_RANGE = 500


def _wait_for_party_regroup():
    """Yielding custom state: wait until all party members are within
    REGROUP_TIGHT_RANGE of the leader, capped at REGROUP_MAX_WAIT_S.

    Paragons (and any class with slow cast animations) fall behind on zone
    entry. Pixel stack gets their position close, but they may still be
    finishing the animation loop. Give them a brief window to catch up before
    we start the kill route so the first pack isn't engaged half-party.

    CRITICAL: this is a generator, NOT a blocking function. The previous
    implementation used `time.sleep(0.25)` inside a `while True` loop which
    froze the Py4GW main thread for the full 8s on every run. That starved
    the follow publisher, FSM tick, and ImGui — so slaves could never
    actually converge during the wait (publisher wasn't publishing updates),
    guaranteeing the 100% timeout rate, AND visibly froze the master's
    screen. See feedback_py4gw_main_thread_blockers in memory.

    Uses plain `yield` (not Routines.Yield.wait) so map-transition detection
    cannot break it early — same pattern as _post_resign_wait_coroutine.
    """
    start = time.time()
    last_log_ts = 0.0
    while True:
        elapsed_s = time.time() - start
        if elapsed_s >= REGROUP_MAX_WAIT_S:
            _log(f"Party regroup: timeout after {elapsed_s:.1f}s, continuing anyway")
            return
        try:
            in_range = Routines.Checks.Party.IsAllPartyMembersInRange(REGROUP_TIGHT_RANGE)
        except Exception as e:
            _log(f"Party regroup: IsAllPartyMembersInRange failed ({e}), continuing")
            return
        if in_range:
            _log(f"Party regroup: all members within {REGROUP_TIGHT_RANGE} gwinches after {elapsed_s:.1f}s")
            return
        if time.time() - last_log_ts >= 2.0:
            _log(f"Party regroup: waiting for slow members (elapsed={elapsed_s:.1f}s/{REGROUP_MAX_WAIT_S:.0f}s)")
            last_log_ts = time.time()
        # Hand control back to the Py4GW scheduler — follow publisher, FSM,
        # managed coroutines, and ImGui all get to run between checks.
        yield


STEP_TIMEOUT_S = 180.0
STEP_WATCHDOG_RECOVERY_STEP = "[H]Exit To Farm_3"


def step_watchdog():
    """Per-step watchdog: if any FSM step stays active longer than
    STEP_TIMEOUT_S, log it and force a jump back to the start of the
    farm loop. Runs as a managed coroutine for the lifetime of the bot.

    Note: this does not interrupt the *current* step's coroutine — it
    triggers a jump that will take effect on the next FSM tick. If the
    current step is blocked inside a long native call, the jump still
    arms but executes once control returns.
    """
    last_step_name: str | None = None
    step_started_ts: float = time.time()
    last_warn_step: str | None = None

    while True:
        try:
            yield from Routines.Yield.wait(2000)
            try:
                fsm = bot.config.FSM
                current_name = fsm.get_current_step_name()
            except Exception as e:
                _log(f"watchdog: failed to read current step: {e}")
                continue

            # CRITICAL auto-resume: the Move.FollowAutoPath coroutine contains
            # a party-wipe detector that calls fsm.pause() and never resumes.
            # After Multibox.ResignParty() triggers a party wipe, the stale
            # FollowPath coroutine (still in managed_coroutines from before the
            # step watchdog jumped past it at 180s) fires this pause path.
            # The FSM's state.execute() + state transitions are skipped while
            # paused, so every post-resign step (kit restock, auto-ID, 15s
            # blind wait, etc.) is frozen until the watchdog's 180s timeout.
            # We unpause here because managed coroutines run even when the FSM
            # is paused. The _resign_in_progress guard ensures this only fires
            # during the intentional resign window — normal wipes (combat
            # failure) still pause and hand off to the OnPartyWipe handler.
            try:
                if _resign_in_progress and getattr(fsm, "paused", False):
                    _log("Watchdog: FSM paused during resign window — auto-resuming (FollowPath wipe-detector pause bypass)")
                    fsm.resume()
            except Exception as e:
                _log(f"watchdog: auto-resume check failed: {e}")

            # C1: permanent-stick safety. If the resign flag stays armed longer
            # than RESIGN_FLAG_MAX_AGE_S the _clear custom state likely got
            # skipped (watchdog jump, exception, etc.). Force-clear so genuine
            # future wipes stop being silently suppressed.
            try:
                if _resign_in_progress and _resign_armed_ts > 0.0:
                    age = time.time() - _resign_armed_ts
                    if age > RESIGN_FLAG_MAX_AGE_S:
                        _log(f"Watchdog: resign flag stuck {age:.0f}s > {RESIGN_FLAG_MAX_AGE_S:.0f}s — force-clearing")
                        _clear_resign_in_progress()
            except Exception as e:
                _log(f"watchdog: resign-flag age check failed: {e}")

            if current_name != last_step_name:
                # Step changed → reset timer.
                last_step_name = current_name
                step_started_ts = time.time()
                last_warn_step = None
                continue

            elapsed_in_step = time.time() - step_started_ts

            # Pre-warning at 75% of the timeout, once per step.
            warn_at = STEP_TIMEOUT_S * 0.75
            if elapsed_in_step >= warn_at and last_warn_step != current_name:
                _log(f"WATCHDOG WARN — step '{current_name}' has been active {elapsed_in_step:.0f}s (timeout at {STEP_TIMEOUT_S:.0f}s)")
                last_warn_step = current_name

            if elapsed_in_step >= STEP_TIMEOUT_S:
                run_stats.setdefault("session_step_timeouts", 0)
                run_stats["session_step_timeouts"] += 1

                # Decide recovery based on which map we're on.
                try:
                    current_map_id = int(Map.GetMapID())
                except Exception as e:
                    _log(f"watchdog: GetMapID failed: {e}, defaulting to outpost-reset path")
                    current_map_id = -1

                if current_map_id == OUTPOST_TO_TRAVEL:
                    # In Olafstead → reset farm flags and re-zone into Varajar.
                    _log(
                        f"WATCHDOG TIMEOUT — step '{current_name}' stuck {elapsed_in_step:.0f}s in Olafstead "
                        f"(map_id={current_map_id}). Resetting flags and re-zoning."
                    )
                    try:
                        reset_farm_flags()
                    except Exception as e:
                        _log(f"watchdog: reset_farm_flags failed: {e}")
                    try:
                        fsm.jump_to_state_by_name(STEP_WATCHDOG_RECOVERY_STEP)
                    except Exception as e:
                        _log(f"watchdog: jump to '{STEP_WATCHDOG_RECOVERY_STEP}' failed: {e}")
                elif current_map_id == VARAJAR_FELLS_MAP_ID:
                    # In Varajar Fells → advance to the next FSM step.
                    try:
                        next_idx = fsm.get_current_state_number()  # 1-based current → 0-based next
                        next_name = fsm.get_state_name_by_number(next_idx + 1) or "<end>"
                        _log(
                            f"WATCHDOG TIMEOUT — step '{current_name}' stuck {elapsed_in_step:.0f}s in Varajar Fells "
                            f"(map_id={current_map_id}). Advancing to next step '{next_name}'."
                        )
                        fsm.jump_to_state_by_step_number(next_idx)
                    except Exception as e:
                        _log(f"watchdog: advance-to-next failed: {e}")
                else:
                    # Unknown map (loading screen, wrong map, dead in instance, etc.)
                    _log(
                        f"WATCHDOG TIMEOUT — step '{current_name}' stuck {elapsed_in_step:.0f}s on unknown "
                        f"map_id={current_map_id}. Forcing jump to '{STEP_WATCHDOG_RECOVERY_STEP}'."
                    )
                    try:
                        fsm.jump_to_state_by_name(STEP_WATCHDOG_RECOVERY_STEP)
                    except Exception as e:
                        _log(f"watchdog: fallback jump failed: {e}")

                # Reset the timer so we don't fire again immediately on the
                # same step if the jump didn't take effect.
                step_started_ts = time.time()
                last_warn_step = None
        except Exception as e:
            _log(f"watchdog: loop error: {e}")
            yield from Routines.Yield.wait(2000)


def handle_asterius_killed_en_route():
    global is_asterius_killed
    global is_asterius_spotted
    global asterius_agent_id

    while True:
        if not Map.IsExplorable():
            yield from Routines.Yield.wait(1000)
            continue

        if is_asterius_killed:
            yield from Routines.Yield.wait(1000)
            continue

        if is_asterius_spotted and asterius_agent_id:
            yield from Routines.Yield.wait(1000)
            if Agent.IsDead(asterius_agent_id):
                is_asterius_killed = True
            continue

        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda agent_id: Utils.Distance(Player.GetXY(), Agent.GetXY(agent_id))
            <= Range.SafeCompass.value,
        )
        enemy_array = AgentArray.Filter.ByCondition(
            enemy_array, lambda agent_id: Player.GetAgentID() != agent_id
        )
        for enemy_id in enemy_array:
            if Agent.GetModelID(enemy_id) == ASTERIUS_MODEL_ID:
                is_asterius_spotted = True
                asterius_agent_id = enemy_id
                yield from Routines.Yield.wait(1000)
        yield from Routines.Yield.wait(1000)


def farm_scythes(bot: Botting) -> None:
    widget_handler = get_widget_handler()
    widget_handler.enable_widget('Return to outpost on defeat')

    # events
    bot.Events.OnPartyWipeCallback(lambda: OnPartyWipe(bot))
    # end events

    bot.States.AddHeader(BOT_NAME)
    bot.Templates.Multibox_Aggressive()
    bot.Properties.Enable("auto_inventory_management")

    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=OUTPOST_TO_TRAVEL)
    bot.Party.SetHardMode(True)
    bot.States.AddManagedCoroutine('Detect en route Asterius kill', handle_asterius_killed_en_route)
    bot.States.AddManagedCoroutine('Step watchdog (180s timeout)', step_watchdog)

    bot.States.AddHeader('Exit To Farm')
    bot.States.AddCustomState(_mark_run_start, "Mark run start")
    bot.Properties.Disable('pause_on_danger')
    bot.Move.XYAndExitMap(-2166, 861, target_map_id=VARAJAR_FELLS_MAP_ID)
    # CRITICAL: do NOT wait after map load — sprint straight to waypoint 1
    # to clear the spawn area before patrols aggro. Move.XY is a blocking
    # direct move, issued before the autopath chain so it's the first
    # action the bot takes on Varajar Fells entry.
    bot.States.AddCustomState(
        _verify_varajar_entry_or_log,
        "Verify Varajar entry",
    )
    bot.Move.XY(TRAVEL_PATH[0][0], TRAVEL_PATH[0][1], step_name="Sprint to WP1")
    bot.States.AddCustomState(lambda: _log("Pixel stacking all slaves on WP1"), "Log pixel stack")
    bot.Multibox.PixelStack()
    # Bug: Paragon (or any caster) lags 15s behind due to cast animations on
    # zone entry. Block until all slaves are within tight range OR 8s cap.
    bot.States.AddCustomState(_wait_for_party_regroup, "Wait for party regroup")
    bot.Properties.Enable('pause_on_danger')

    bot.States.AddHeader("Start Combat")
    bot.Move.FollowAutoPath(TRAVEL_PATH, "Kill Route")
    bot.States.AddCustomState(lambda: _log("Kill route complete — waiting for Asterius detection/death"), "Log route done")
    bot.Wait.UntilCondition(
        is_asterius_killed_or_time_elapsed, duration=1000
    )  # check every second until boss is killed
    bot.States.AddCustomState(_reset_post_kill_state, "Init post-kill wait")
    bot.Wait.UntilCondition(
        is_ready_to_resign, duration=1000
    )  # pick up all loot, then either clean-exit or dead-member 30s-after-combat
    bot.States.AddCustomState(_snap_post_loot, "Snapshot post-loot inventory")
    bot.States.AddCustomState(_set_resign_in_progress, "Arm resign guard")
    bot.States.AddCustomState(lambda: _log("Resigning party"), "Log resign")
    bot.Multibox.ResignParty()
    bot.States.AddCustomState(reset_farm_flags, "Reset Farm detections")
    bot.States.AddCustomState(lambda: _log("Post-resign wait: starting (custom generator, NOT Wait.ForTime — that breaks on map transition)"), "Log wait outpost")
    # CRITICAL: Wait.ForTime breaks early on map transition because its
    # underlying _coro_for_time passes break_on_map_transition=True. During
    # the resign the map flips Varajar→Olafstead, the wait breaks after
    # ~2-5s instead of the requested 15s, and the downstream inventory
    # snapshot captures a half-loaded game state (all zeros). Then RUN START
    # #2 fires with bogus state and Move.XYAndExitMap hangs because master
    # isn't fully spawned. See _post_resign_wait_coroutine docstring for the
    # full chain.
    #
    # AddCustomState accepts a generator function via AddSelfManagedYieldStep.
    # Our generator uses plain `yield` (no Routines.Yield.wait wrapper) so it
    # CANNOT be broken by map-transition detection.
    bot.States.AddCustomState(_post_resign_wait_coroutine, "Post-resign wait (custom, non-breaking)")
    bot.States.AddCustomState(_clear_resign_in_progress, "Disarm resign guard")
    bot.States.AddCustomState(lambda: _log("Back in Olafstead — checking kit stock"), "Log outpost")
    # Conditional restock: the helper is self-gating — walks to merchant ONLY
    # when current stacks < target (defaults: 4 lesser salvage kits, 2 ID kits).
    # If kits are at target, this is a cheap no-op. If below, buys the shortfall.
    bot.Merchant.Restock.SalvageKits()
    bot.Merchant.Restock.IdentifyKits()
    bot.States.AddCustomState(lambda: _log("Kit stock verified — force-identifying all gold items"), "Log restock done")
    # Every outpost entry: force-identify everything so the gold filter has
    # complete mod data. Belt-and-braces on top of InventoryPlus auto-ID.
    bot.Items.AutoIdentifyItems()
    bot.States.AddCustomState(_dump_gold_mod_diagnostic, "Dump gold mods (diagnostic)")
    bot.States.AddCustomState(lambda: _log("10s regroup pause"), "Log regroup")
    bot.Wait.ForTime(10000)
    bot.States.AddCustomState(_snap_post_outpost, "Snapshot post-outpost inventory")
    bot.States.JumpToStepName("[H]Exit To Farm_3")


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
    PyImGui.bullet_text("Designed for Normal Mode (NM) for faster and easy run, but can be change editing True or False in the code.")
    
    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Mark")
    PyImGui.end_tooltip()

def main():
    bot.Update()
    bot.UI.draw_window(icon_path=TEXTURE)


if __name__ == "__main__":
    main()
