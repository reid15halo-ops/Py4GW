"""FarmStats — per-run tracking for all farm bots.

Append-only JSONL format with msvcrt file locking for 6-client safety.
Each bot calls start_run() / end_run() and the tracker handles persistence.
"""

import json
import msvcrt
import os
import time
from datetime import datetime
from statistics import mean, median

# Framework imports — available inside the GW client runtime
try:
    from Py4GWCoreLib import Inventory, Item, Map, Player
    from Py4GWCoreLib.enums import Bag
    _HAS_FRAMEWORK = True
except ImportError:
    _HAS_FRAMEWORK = False

MODULE = "FarmStats"

# Default paths
_SETTINGS_GLOBAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "Settings", "Global"
)
DEFAULT_STATS_PATH = os.path.join(_SETTINGS_GLOBAL, "farm_stats.jsonl")
DEFAULT_DASHBOARD_PATH = os.path.join(_SETTINGS_GLOBAL, "farm_dashboard.html")

SCHEMA_VERSION = 1


# =========================================================================
# JSONL I/O with file locking
# =========================================================================

def append_run_record(record: dict, stats_path: str = DEFAULT_STATS_PATH) -> bool:
    """Append one JSON line with msvcrt file locking. Safe for 6 concurrent clients."""
    os.makedirs(os.path.dirname(stats_path), exist_ok=True)
    line = json.dumps(record, separators=(',', ':')) + '\n'
    encoded = line.encode('utf-8')
    for attempt in range(5):
        fd = -1
        try:
            fd = os.open(stats_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            os.write(fd, encoded)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return True
        except OSError:
            time.sleep(0.1 + 0.05 * attempt)
        finally:
            if fd >= 0:
                os.close(fd)
    return False


def read_all_records(stats_path: str = DEFAULT_STATS_PATH) -> list:
    """Read all valid JSONL records, skipping corrupted lines."""
    if not os.path.exists(stats_path):
        return []
    records = []
    with open(stats_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# =========================================================================
# Aggregation helpers
# =========================================================================

def compute_aggregates(records: list, bot_name: str = None,
                       account_email: str = None) -> dict:
    """Compute stats from a list of run records, optionally filtered."""
    filtered = records
    if bot_name:
        filtered = [r for r in filtered if r.get("bot_name") == bot_name]
    if account_email:
        filtered = [r for r in filtered if r.get("account_email") == account_email]

    if not filtered:
        return {
            "total_runs": 0, "wins": 0, "losses": 0, "success_rate": 0.0,
            "avg_time_s": 0.0, "median_time_s": 0.0,
            "avg_gold": 0.0, "median_gold": 0.0, "total_gold": 0,
            "gold_per_hour": 0.0,
            "total_items_picked": 0, "total_greens": 0,
            "green_names": [], "items_per_run": 0.0,
        }

    wins = [r for r in filtered if r.get("success")]
    losses = [r for r in filtered if not r.get("success")]
    times_s = [r["run_time_ms"] / 1000.0 for r in filtered if r.get("run_time_ms", 0) > 0]
    golds = [r.get("gold_gained", 0) for r in filtered]
    items = [r.get("items_picked_up", 0) for r in filtered]
    all_greens = []
    for r in filtered:
        all_greens.extend(r.get("green_drops", []))

    total_time_s = sum(times_s) if times_s else 0
    total_gold = sum(golds)

    return {
        "total_runs": len(filtered),
        "wins": len(wins),
        "losses": len(losses),
        "success_rate": len(wins) / len(filtered) if filtered else 0.0,
        "avg_time_s": mean(times_s) if times_s else 0.0,
        "median_time_s": median(times_s) if times_s else 0.0,
        "avg_gold": mean(golds) if golds else 0.0,
        "median_gold": median(golds) if golds else 0.0,
        "total_gold": total_gold,
        "gold_per_hour": (total_gold / total_time_s * 3600) if total_time_s > 0 else 0.0,
        "total_items_picked": sum(items),
        "total_greens": len(all_greens),
        "green_names": all_greens,
        "items_per_run": mean(items) if items else 0.0,
    }


def compute_per_account(records: list, bot_name: str = None) -> dict:
    """Return {email: aggregates_dict} for each account."""
    filtered = records
    if bot_name:
        filtered = [r for r in filtered if r.get("bot_name") == bot_name]
    emails = sorted(set(r.get("account_email", "unknown") for r in filtered))
    return {email: compute_aggregates(filtered, account_email=email) for email in emails}


def compute_per_bot(records: list) -> dict:
    """Return {bot_name: aggregates_dict} for each bot."""
    bots = sorted(set(r.get("bot_name", "unknown") for r in records))
    return {name: compute_aggregates(records, bot_name=name) for name in bots}


# =========================================================================
# Inventory snapshot helpers (green detection)
# =========================================================================

def _snapshot_inventory_greens() -> set:
    """Return set of item_ids that are green rarity in player bags."""
    if not _HAS_FRAMEWORK:
        return set()
    greens = set()
    try:
        bags = [Bag.Backpack, Bag.Belt_Pouch, Bag.Bag_1, Bag.Bag_2]
        for bag_enum in bags:
            from Py4GWCoreLib.Imports import PyInventory, PyItem
            bag_inst = PyInventory.Bag(bag_enum.value, bag_enum.name)
            for item in bag_inst.GetItems():
                try:
                    if Item.Rarity.IsGreen(item.item_id):
                        greens.add(item.item_id)
                except Exception:
                    pass
    except Exception:
        pass
    return greens


def _get_green_names(item_ids: set) -> list:
    """Get item names for a set of green item IDs."""
    if not _HAS_FRAMEWORK:
        return []
    names = []
    for iid in item_ids:
        try:
            name = Item.GetName(iid)
            if name:
                names.append(name)
            else:
                names.append(f"Green#{iid}")
        except Exception:
            names.append(f"Green#{iid}")
    return names


# =========================================================================
# FarmRunTracker — the main API for bots
# =========================================================================

class FarmRunTracker:
    """Track per-run stats for a farm bot. Thread-safe via append-only JSONL."""

    def __init__(self, bot_name: str, stats_path: str = DEFAULT_STATS_PATH,
                 dashboard_path: str = DEFAULT_DASHBOARD_PATH):
        self.bot_name = bot_name
        self.stats_path = stats_path
        self.dashboard_path = dashboard_path
        self._run_active = False
        self._run_start_time = 0.0
        self._gold_start = 0
        self._greens_start = set()
        self._strategy = ""
        self._hard_mode = True
        self._items_picked_start = 0
        self._items_sold_start = 0
        self._items_salvaged_start = 0
        self._items_extracted_start = 0
        self._items_kept_start = 0
        # Session totals (in-memory, for ImGui display)
        self.session_runs = 0
        self.session_wins = 0
        self.session_gold = 0
        self.session_greens = []
        self.session_start = time.time()

    def start_run(self, strategy: str = "", hard_mode: bool = True,
                  loot_config=None):
        """Call at the start of each farm run."""
        self._run_active = True
        self._run_start_time = time.time()
        self._strategy = strategy
        self._hard_mode = hard_mode
        self._gold_start = 0
        self._greens_start = set()
        self._items_picked_start = 0
        self._items_sold_start = 0
        self._items_salvaged_start = 0
        self._items_extracted_start = 0
        self._items_kept_start = 0

        if _HAS_FRAMEWORK:
            try:
                self._gold_start = Inventory.GetGoldOnCharacter()
            except Exception:
                pass
            self._greens_start = _snapshot_inventory_greens()

        if loot_config is not None:
            self._items_picked_start = getattr(loot_config, 'items_picked_up', 0)
            self._items_sold_start = getattr(loot_config, 'items_sold', 0)
            self._items_salvaged_start = getattr(loot_config, 'items_salvaged', 0)
            self._items_extracted_start = getattr(loot_config, 'items_extracted', 0)
            self._items_kept_start = getattr(loot_config, 'items_kept', 0)

    def end_run(self, success: bool, loot_config=None) -> dict:
        """Call at run end. Persists record, regenerates dashboard. Returns the record."""
        if not self._run_active:
            return {}
        self._run_active = False

        run_time_ms = int((time.time() - self._run_start_time) * 1000)

        # Gold delta
        gold_end = 0
        if _HAS_FRAMEWORK:
            try:
                gold_end = Inventory.GetGoldOnCharacter()
            except Exception:
                pass
        gold_gained = gold_end - self._gold_start

        # Green detection
        greens_now = _snapshot_inventory_greens() if _HAS_FRAMEWORK else set()
        new_greens = greens_now - self._greens_start
        green_names = _get_green_names(new_greens)

        # Loot counter deltas
        items_picked = 0
        items_sold = 0
        items_salvaged = 0
        items_extracted = 0
        items_kept = 0
        if loot_config is not None:
            items_picked = getattr(loot_config, 'items_picked_up', 0) - self._items_picked_start
            items_sold = getattr(loot_config, 'items_sold', 0) - self._items_sold_start
            items_salvaged = getattr(loot_config, 'items_salvaged', 0) - self._items_salvaged_start
            items_extracted = getattr(loot_config, 'items_extracted', 0) - self._items_extracted_start
            items_kept = getattr(loot_config, 'items_kept', 0) - self._items_kept_start

        # Account info — prefer Player API, fall back to ShMem if API returns empty
        # (Player.GetAccountEmail returns "" during transitions / when not loaded)
        account_email = ""
        character_name = ""
        map_id = 0
        if _HAS_FRAMEWORK:
            try:
                account_email = Player.GetAccountEmail() or ""
            except Exception:
                pass
            try:
                character_name = Player.GetAccountName() or ""
            except Exception:
                pass
            try:
                map_id = Map.GetMapID()
            except Exception:
                pass
            # ShMem fallback when API returned empty
            if not account_email or not character_name:
                try:
                    from Py4GWCoreLib import GLOBAL_CACHE
                    local_id = Player.GetAgentID()
                    if local_id and local_id > 0:
                        for acc in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                            if not acc:
                                continue
                            try:
                                if not acc.IsAccount:
                                    continue
                                if acc.AgentData.AgentID != local_id:
                                    continue
                                if not account_email:
                                    account_email = acc.AccountEmail or ""
                                if not character_name:
                                    try:
                                        character_name = acc.AgentData.CharacterName or ""
                                    except Exception:
                                        pass
                                break
                            except Exception:
                                continue
                except Exception:
                    pass

        record = {
            "v": SCHEMA_VERSION,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
            "account_email": account_email,
            "character_name": character_name,
            "bot_name": self.bot_name,
            "strategy": self._strategy,
            "map_id": map_id,
            "hard_mode": self._hard_mode,
            "success": success,
            "run_time_ms": run_time_ms,
            "gold_start": self._gold_start,
            "gold_end": gold_end,
            "gold_gained": gold_gained,
            "items_picked_up": items_picked,
            "items_sold": items_sold,
            "items_salvaged": items_salvaged,
            "items_extracted": items_extracted,
            "items_kept": items_kept,
            "green_drops": green_names,
        }

        # Persist
        append_run_record(record, self.stats_path)

        # Update session counters
        self.session_runs += 1
        if success:
            self.session_wins += 1
        self.session_gold += gold_gained
        self.session_greens.extend(green_names)

        # Regenerate dashboard (non-critical)
        try:
            from Widgets.Automation.Helpers.FarmStatsDashboard import generate_dashboard
            generate_dashboard(self.stats_path, self.dashboard_path)
        except Exception:
            pass

        return record

    def get_session_summary(self) -> dict:
        """Return in-memory session stats for ImGui display."""
        elapsed_h = (time.time() - self.session_start) / 3600
        return {
            "runs": self.session_runs,
            "wins": self.session_wins,
            "success_rate": self.session_wins / self.session_runs if self.session_runs else 0.0,
            "gold": self.session_gold,
            "gold_per_hour": self.session_gold / elapsed_h if elapsed_h > 0 else 0.0,
            "greens": self.session_greens,
            "elapsed_h": elapsed_h,
        }
