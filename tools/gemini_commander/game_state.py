"""Game state serializer for the Gemini Commander.

Reads game state from the bridge daemon's control socket and/or shared INI
files, then serializes it into a compact JSON snapshot suitable for Gemini
prompts.

Three data sources, tried in order by ``capture_snapshot()``:
  1. Bridge daemon control socket (full live data: HP, energy, enemies, position)
  2. OutpostPrep_Status.ini (partial: roster, kit counts, map IDs)
  3. Mock data (for offline testing)

Runs OUTSIDE the injected DLL -- no Py4GWCoreLib imports.
"""

from __future__ import annotations

import configparser
import json
import random
import socket
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Inline protocol helpers (mirrors BridgeRuntime/protocol.py so this module
# stays standalone -- no imports from the DLL-side codebase).
# ---------------------------------------------------------------------------

_PROTOCOL_VERSION = 1


def _read_exact(sock: socket.socket, size: int, timeout: float | None = None) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    deadline = None if timeout is None else (time.time() + timeout)
    while remaining:
        if deadline is not None:
            left = deadline - time.time()
            if left <= 0:
                raise socket.timeout("read timed out")
            sock.settimeout(left)
        data = sock.recv(remaining)
        if not data:
            raise ConnectionError("socket closed")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def _send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    sock.sendall(struct.pack("<I", len(raw)))
    sock.sendall(raw)


def _recv_json(sock: socket.socket, timeout: float | None = None) -> dict[str, Any]:
    header = _read_exact(sock, 4, timeout=timeout)
    (size,) = struct.unpack("<I", header)
    if size <= 0 or size > 16 * 1024 * 1024:
        raise RuntimeError(f"invalid frame size: {size}")
    raw = _read_exact(sock, size, timeout=timeout)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("payload must be object")
    return payload


# ---------------------------------------------------------------------------
# Account roster helpers
# ---------------------------------------------------------------------------

# Import master email from shared config — never hardcode PII in multiple places
try:
    from tools.gemini_commander.config import MASTER_EMAIL
except ImportError:
    try:
        from config import MASTER_EMAIL
    except ImportError:
        MASTER_EMAIL = os.environ.get("PY4GW_MASTER_EMAIL", "")

# Default path to the project root (two levels up from this file).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_accounts(project_root: Path | None = None) -> list[dict[str, Any]]:
    """Load the first profile from accounts.json."""
    root = project_root or _PROJECT_ROOT
    accounts_path = root / "accounts.json"
    if not accounts_path.is_file():
        return []
    with open(accounts_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # accounts.json is keyed by profile name; take the first one found.
    if isinstance(data, dict):
        for _profile_name, entries in data.items():
            if isinstance(entries, list) and entries:
                return entries
    return []


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PartyMember:
    name: str
    email: str
    profession: str
    hp: float  # 0.0-1.0
    energy: float  # 0.0-1.0
    is_alive: bool
    is_master: bool
    map_id: int
    position: tuple[float, float]
    # INI-derived extras (optional)
    salvage_kits: int = 0
    expert_kits: int = 0
    ident_kits: int = 0
    needs_salvage: bool = False
    needs_ident: bool = False
    blessed_pending: bool = False
    role: str = ""  # "master" | "slave"
    timestamp: float = 0.0  # last INI write epoch

    def to_dict(self, strip_pii: bool = True) -> dict[str, Any]:
        d = asdict(self)
        d["position"] = list(d["position"])
        if strip_pii:
            d.pop("email", None)  # never send emails to external APIs
        return d


@dataclass
class Enemy:
    agent_id: int
    name: str
    hp: float
    is_casting: bool
    casting_skill: str
    distance: float
    is_healer: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GameStateSnapshot:
    timestamp: float
    map_name: str
    map_id: int
    is_explorable: bool
    is_outpost: bool
    party: list[PartyMember]
    enemies: list[Enemy]  # top 10 nearest
    party_avg_hp: float
    combat_active: bool
    enemy_count: int
    # Extra context
    blessing_enabled: bool = False
    source: str = "unknown"  # "bridge" | "ini" | "mock"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "map_name": self.map_name,
            "map_id": self.map_id,
            "is_explorable": self.is_explorable,
            "is_outpost": self.is_outpost,
            "party": [m.to_dict() for m in self.party],
            "enemies": [e.to_dict() for e in self.enemies],
            "party_avg_hp": round(self.party_avg_hp, 3),
            "combat_active": self.combat_active,
            "enemy_count": self.enemy_count,
            "blessing_enabled": self.blessing_enabled,
            "source": self.source,
        }

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=True)

    def to_prompt_text(self) -> str:
        """Convert to a compact text format for Gemini prompt injection.

        Keeps token count low -- Gemini 2.5 Flash pricing is per-token.
        """
        if not self.party:
            return "STATE: No party data available."

        lines: list[str] = []
        lines.append(f"MAP: {self.map_name} (id={self.map_id})")
        zone = "explorable" if self.is_explorable else ("outpost" if self.is_outpost else "unknown")
        lines.append(f"ZONE: {zone} | COMBAT: {'YES' if self.combat_active else 'no'}")
        lines.append(f"ENEMIES NEARBY: {self.enemy_count}")
        lines.append(f"PARTY AVG HP: {self.party_avg_hp:.0%}")
        if self.blessing_enabled:
            lines.append("BLESSING RUN: active")

        # Staleness check (INI-sourced data only)
        if self.source == "ini":
            now = time.time()
            stale = [m for m in self.party if m.timestamp > 0 and now - m.timestamp > 30]
            if stale:
                lines.append(f"WARNING: {len(stale)} members have stale data (>30s)")

        # Check if party is split across maps
        map_ids = {m.map_id for m in self.party if m.map_id > 0}
        if len(map_ids) > 1:
            lines.append(f"WARNING: Party SPLIT across maps {sorted(map_ids)}")

        lines.append("")
        lines.append("PARTY:")
        for m in self.party:
            tag = " [MASTER]" if m.is_master else ""
            alive = "alive" if m.is_alive else "DEAD"
            kits = ""
            if m.salvage_kits or m.expert_kits or m.ident_kits:
                kits = f" kits(s={m.salvage_kits} e={m.expert_kits} i={m.ident_kits})"
            flags: list[str] = []
            if m.needs_salvage:
                flags.append("NEEDS_SALVAGE")
            if m.needs_ident:
                flags.append("NEEDS_IDENT")
            if m.blessed_pending:
                flags.append("BLESSED_PENDING")
            if m.salvage_kits == 0 and m.expert_kits == 0:
                flags.append("NO_KITS")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            pos_str = ""
            if m.position != (0.0, 0.0):
                pos_str = f" pos=({m.position[0]:.0f},{m.position[1]:.0f})"
            lines.append(
                f"  {m.name}{tag} ({m.profession}) hp={m.hp:.0%} e={m.energy:.0%} "
                f"{alive} map={m.map_id}{pos_str}{kits}{flag_str}"
            )

        if self.enemies:
            lines.append("")
            lines.append("NEAREST ENEMIES:")
            for e in self.enemies:
                cast = f" CASTING:{e.casting_skill}" if e.is_casting else ""
                healer = " [HEALER]" if e.is_healer else ""
                lines.append(
                    f"  #{e.agent_id} {e.name} hp={e.hp:.0%} dist={e.distance:.0f}{cast}{healer}"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Factory: from bridge daemon (live game data)
    # ------------------------------------------------------------------
    @classmethod
    def from_bridge_daemon(
        cls,
        host: str = "127.0.0.1",
        port: int = 51935,
        timeout: float = 5.0,
        project_root: Path | None = None,
    ) -> "GameStateSnapshot":
        """Query the bridge daemon's control port for all connected clients.

        The daemon exposes per-client queries (map state, player state,
        agent list) via the control socket.  We open a connection, discover
        all connected clients, then query each one.

        The daemon's actual default control port is 47812; pass *port* to
        override if your daemon uses a different port.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            # --- list connected clients ---
            _send_json(sock, {
                "type": "request",
                "request_id": "gc-list",
                "command": "system.list_clients",
                "params": {},
            })
            list_resp = _recv_json(sock, timeout=timeout)
            clients_raw = (list_resp.get("result") or {}).get("clients", [])

            # Load accounts.json for role mapping
            accounts = _load_accounts(project_root)
            email_to_role: dict[str, str] = {}
            for acc in accounts:
                email_to_role[acc.get("email", "")] = acc.get("role", "slave")

            party: list[PartyMember] = []
            all_enemies: list[Enemy] = []
            master_map_id = 0
            master_map_name = "unknown"
            master_is_explorable = False
            master_is_outpost = False

            for i, client_info in enumerate(clients_raw):
                hwnd = client_info.get("hwnd", 0)
                pid = client_info.get("pid", 0)
                email = client_info.get("account_email", "")
                char_name = client_info.get("character_name", "unknown")
                target = {"hwnd": hwnd} if hwnd else {"pid": pid}

                # Query map state
                _send_json(sock, {
                    "type": "request",
                    "request_id": f"gc-map-{i}",
                    "command": "client.get_map_state",
                    "params": {"target": target},
                })
                map_resp = _recv_json(sock, timeout=timeout)
                map_state = (map_resp.get("result") or {}).get("map_state", {})
                if not isinstance(map_state, dict):
                    map_state = {}

                current_map_id = int(map_state.get("map_id", 0))
                current_is_explorable = bool(map_state.get("is_explorable", False))
                current_is_outpost = bool(map_state.get("is_outpost", False))
                current_map_name = str(map_state.get("map_name", "unknown"))

                # Query player state
                _send_json(sock, {
                    "type": "request",
                    "request_id": f"gc-player-{i}",
                    "command": "client.get_player_state",
                    "params": {"target": target},
                })
                player_resp = _recv_json(sock, timeout=timeout)
                player_state = (player_resp.get("result") or {}).get("player_state", {})
                if not isinstance(player_state, dict):
                    player_state = {}

                hp = float(player_state.get("hp", 0.0))
                energy = float(player_state.get("energy", 0.0))
                is_alive = bool(player_state.get("is_alive", True))
                profession = str(player_state.get("profession", "unknown"))
                pos_x = float(player_state.get("x", 0.0))
                pos_y = float(player_state.get("y", 0.0))

                is_master = (email == MASTER_EMAIL)
                if is_master:
                    master_map_id = current_map_id
                    master_map_name = current_map_name
                    master_is_explorable = current_is_explorable
                    master_is_outpost = current_is_outpost

                member = PartyMember(
                    name=char_name,
                    email=email,
                    profession=profession,
                    hp=hp,
                    energy=energy,
                    is_alive=is_alive,
                    is_master=is_master,
                    map_id=current_map_id,
                    position=(pos_x, pos_y),
                    role=email_to_role.get(email, "slave"),
                )
                party.append(member)

                # Query enemies only from master's perspective to avoid dupes
                if is_master:
                    _send_json(sock, {
                        "type": "request",
                        "request_id": f"gc-agents-{i}",
                        "command": "client.list_agents",
                        "params": {"target": target, "group": "enemy"},
                    })
                    agent_resp = _recv_json(sock, timeout=timeout)
                    agents_raw = (agent_resp.get("result") or {}).get("agents", [])
                    for ag in agents_raw:
                        if not isinstance(ag, dict):
                            continue
                        all_enemies.append(Enemy(
                            agent_id=int(ag.get("agent_id", 0)),
                            name=str(ag.get("name", "unknown")),
                            hp=float(ag.get("hp", 0.0)),
                            is_casting=bool(ag.get("is_casting", False)),
                            casting_skill=str(ag.get("casting_skill", "")),
                            distance=float(ag.get("distance", 9999.0)),
                            is_healer=bool(ag.get("is_healer", False)),
                        ))

            # Sort enemies by distance, take top 10
            all_enemies.sort(key=lambda e: e.distance)
            top_enemies = all_enemies[:10]

            alive_members = [m for m in party if m.is_alive]
            avg_hp = (sum(m.hp for m in alive_members) / len(alive_members)) if alive_members else 0.0
            combat_active = len(all_enemies) > 0 and any(e.distance < 1200.0 for e in all_enemies)

            # Enrich with INI data (kit counts, needs flags)
            try:
                ini_data = _read_outpost_ini(project_root)
                for member in party:
                    if member.email in ini_data:
                        entry = ini_data[member.email]
                        member.salvage_kits = entry.get("salvage_kits", 0)
                        member.expert_kits = entry.get("expert_kits", 0)
                        member.ident_kits = entry.get("ident_kits", 0)
                        member.needs_salvage = entry.get("needs_salvage", False)
                        member.needs_ident = entry.get("needs_ident", False)
                        member.blessed_pending = entry.get("blessed_pending", False)
                        member.timestamp = entry.get("timestamp", 0.0)
            except Exception:
                pass  # INI enrichment is best-effort

            blessing = _read_blessing_config(project_root)

            return cls(
                timestamp=time.time(),
                map_name=master_map_name,
                map_id=master_map_id,
                is_explorable=master_is_explorable,
                is_outpost=master_is_outpost,
                party=party,
                enemies=top_enemies,
                party_avg_hp=avg_hp,
                combat_active=combat_active,
                enemy_count=len(all_enemies),
                blessing_enabled=blessing,
                source="bridge",
            )
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Factory: from INI files (fallback when daemon is unavailable)
    # ------------------------------------------------------------------
    @classmethod
    def from_ini(cls, project_root: Path | None = None) -> "GameStateSnapshot":
        """Build a partial snapshot from the shared INI files.

        This gives us party roster, kit counts, and map IDs -- but no HP,
        energy, position, or enemy data (those require the live bridge).
        Backward-compatible with the old from_outpost_prep_ini() interface.
        """
        root = project_root or _PROJECT_ROOT
        accounts = _load_accounts(root)
        email_to_role: dict[str, str] = {}
        for acc in accounts:
            email_to_role[acc.get("email", "")] = acc.get("role", "slave")

        ini_data = _read_outpost_ini(root)
        blessing = _read_blessing_config(root)

        party: list[PartyMember] = []
        master_map_id = 0

        for email, entry in ini_data.items():
            is_master = (email == MASTER_EMAIL)
            map_id = entry.get("map_id", 0)
            if is_master:
                master_map_id = map_id

            member = PartyMember(
                name=entry.get("character", email.split("@")[0]),
                email=email,
                profession="unknown",  # INI does not carry profession
                hp=1.0,  # assume alive/full if we have no live data
                energy=1.0,
                is_alive=True,
                is_master=is_master,
                map_id=map_id,
                position=(0.0, 0.0),
                salvage_kits=entry.get("salvage_kits", 0),
                expert_kits=entry.get("expert_kits", 0),
                ident_kits=entry.get("ident_kits", 0),
                needs_salvage=entry.get("needs_salvage", False),
                needs_ident=entry.get("needs_ident", False),
                blessed_pending=entry.get("blessed_pending", False),
                role=email_to_role.get(email, "slave"),
                timestamp=entry.get("timestamp", 0.0),
            )
            party.append(member)

        # Sort: master first, then alphabetical
        party.sort(key=lambda m: (not m.is_master, m.name))

        alive_members = [m for m in party if m.is_alive]
        avg_hp = (sum(m.hp for m in alive_members) / len(alive_members)) if alive_members else 0.0

        # All on same map = likely outpost (heuristic: OutpostPrep only
        # writes while clients are in an outpost).
        all_same_map = len({m.map_id for m in party}) == 1

        return cls(
            timestamp=time.time(),
            map_name=f"map_{master_map_id}",
            map_id=master_map_id,
            is_explorable=False,
            is_outpost=all_same_map,
            party=party,
            enemies=[],
            party_avg_hp=avg_hp,
            combat_active=False,
            enemy_count=0,
            blessing_enabled=blessing,
            source="ini",
        )

    @classmethod
    def from_outpost_prep_ini(cls, py4gw_root: str) -> "GameStateSnapshot":
        """Legacy wrapper -- delegates to from_ini().

        Kept for backward compatibility with existing callers.
        """
        return cls.from_ini(project_root=Path(py4gw_root))

    # ------------------------------------------------------------------
    # Factory: mock data for testing
    # ------------------------------------------------------------------
    @classmethod
    def from_mock(cls, num_enemies: int = 5, combat: bool = True) -> "GameStateSnapshot":
        """Generate a realistic mock snapshot for testing without game clients."""
        mock_party = [
            PartyMember(
                name="Janine Nazgul", email="jonasglawion@aol.com",
                profession="Dervish", hp=0.85, energy=0.60,
                is_alive=True, is_master=True, map_id=640,
                position=(-4200.0, 5100.0),
                salvage_kits=2, expert_kits=1, ident_kits=2, role="master",
            ),
            PartyMember(
                name="Reid Infuse", email="reid15halo@gmail.com",
                profession="Monk", hp=0.92, energy=0.45,
                is_alive=True, is_master=False, map_id=640,
                position=(-4180.0, 5120.0),
                salvage_kits=2, expert_kits=1, ident_kits=2, role="slave",
            ),
            PartyMember(
                name="Necro Reid", email="egtidal@gmail.com",
                profession="Necromancer", hp=0.78, energy=0.30,
                is_alive=True, is_master=False, map_id=640,
                position=(-4220.0, 5080.0),
                salvage_kits=2, expert_kits=1, ident_kits=2, role="slave",
            ),
            PartyMember(
                name="Reids Dwayna", email="vftidal@gmail.com",
                profession="Dervish", hp=0.65, energy=0.55,
                is_alive=True, is_master=False, map_id=640,
                position=(-4160.0, 5140.0),
                salvage_kits=2, expert_kits=2, ident_kits=2, role="slave",
            ),
            PartyMember(
                name="Reid Seed", email="gmjtidal@gmail.com",
                profession="Ritualist", hp=0.0, energy=0.0,
                is_alive=False, is_master=False, map_id=640,
                position=(-4240.0, 5060.0),
                salvage_kits=2, expert_kits=1, ident_kits=2, role="slave",
            ),
            PartyMember(
                name="Reid Tao", email="jregspotify@gmail.com",
                profession="Mesmer", hp=0.88, energy=0.70,
                is_alive=True, is_master=False, map_id=640,
                position=(-4200.0, 5160.0),
                salvage_kits=10, expert_kits=1, ident_kits=3, role="slave",
            ),
        ]

        mock_enemies: list[Enemy] = []
        enemy_names = [
            "Vaettir", "Ice Imp", "Modniir Hunter", "Stone Summit Ranger",
            "Mursaat Elementalist", "Jade Bow", "Ether Seal", "Mursaat Monk",
            "Jade Armor", "Stone Summit Healer",
        ]
        for i in range(min(num_enemies, 10)):
            name = enemy_names[i % len(enemy_names)]
            is_healer = "Monk" in name or "Healer" in name
            mock_enemies.append(Enemy(
                agent_id=2000 + i,
                name=name,
                hp=round(random.uniform(0.2, 1.0), 2),
                is_casting=random.random() < 0.3,
                casting_skill="Healing Prayers" if (is_healer and random.random() < 0.5) else "",
                distance=round(200.0 + i * 150.0 + random.uniform(-50.0, 50.0), 1),
                is_healer=is_healer,
            ))

        mock_enemies.sort(key=lambda e: e.distance)

        alive = [m for m in mock_party if m.is_alive]
        avg_hp = sum(m.hp for m in alive) / len(alive) if alive else 0.0

        return cls(
            timestamp=time.time(),
            map_name="Jaga Moraine",
            map_id=640,
            is_explorable=True,
            is_outpost=False,
            party=mock_party,
            enemies=mock_enemies,
            party_avg_hp=avg_hp,
            combat_active=combat and len(mock_enemies) > 0,
            enemy_count=len(mock_enemies),
            blessing_enabled=False,
            source="mock",
        )


# ---------------------------------------------------------------------------
# INI file readers
# ---------------------------------------------------------------------------

def _read_outpost_ini(project_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Parse Widgets/Config/OutpostPrep_Status.ini.

    Returns a dict keyed by email with parsed values.
    """
    root = project_root or _PROJECT_ROOT
    ini_path = root / "Widgets" / "Config" / "OutpostPrep_Status.ini"
    if not ini_path.is_file():
        return {}

    config = configparser.ConfigParser()
    try:
        config.read(str(ini_path), encoding="utf-8")
    except Exception:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for section in config.sections():
        try:
            entry: dict[str, Any] = {}
            entry["character"] = config.get(section, "character", fallback=section)
            entry["salvage_kits"] = config.getint(section, "salvage_kits", fallback=0)
            entry["expert_kits"] = config.getint(section, "expert_kits", fallback=0)
            entry["ident_kits"] = config.getint(section, "ident_kits", fallback=0)
            entry["needs_salvage"] = config.getboolean(section, "needs_salvage", fallback=False)
            entry["needs_ident"] = config.getboolean(section, "needs_ident", fallback=False)
            entry["blessed_pending"] = config.getboolean(section, "blessed_pending", fallback=False)
            entry["map_id"] = config.getint(section, "map_id", fallback=0)
            entry["timestamp"] = config.getfloat(section, "timestamp", fallback=0.0)
            result[section] = entry
        except Exception:
            continue  # skip corrupted sections, keep the rest
    return result


def _read_blessing_config(project_root: Path | None = None) -> bool:
    """Read Widgets/Config/Blessed_Config.ini and return whether blessing run is enabled."""
    root = project_root or _PROJECT_ROOT
    ini_path = root / "Widgets" / "Config" / "Blessed_Config.ini"
    if not ini_path.is_file():
        return False

    config = configparser.ConfigParser()
    try:
        config.read(str(ini_path), encoding="utf-8")
    except Exception:
        return False
    return config.getboolean("BlessingRun", "enabled", fallback=False)


# ---------------------------------------------------------------------------
# Convenience: try bridge first, fall back to INI
# ---------------------------------------------------------------------------

def capture_snapshot(
    bridge_host: str = "127.0.0.1",
    bridge_port: int = 51935,
    bridge_timeout: float = 3.0,
    project_root: Path | None = None,
) -> GameStateSnapshot:
    """Best-effort snapshot: try the bridge daemon, fall back to INI files.

    Returns a GameStateSnapshot regardless of whether the daemon is reachable.
    """
    try:
        return GameStateSnapshot.from_bridge_daemon(
            host=bridge_host,
            port=bridge_port,
            timeout=bridge_timeout,
            project_root=project_root,
        )
    except (ConnectionRefusedError, ConnectionError, OSError, TimeoutError):
        pass

    try:
        return GameStateSnapshot.from_ini(project_root=project_root)
    except Exception:
        pass

    # Absolute fallback: mock with no enemies
    return GameStateSnapshot.from_mock(num_enemies=0, combat=False)


# ---------------------------------------------------------------------------
# CLI for quick testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capture and print a game state snapshot")
    parser.add_argument("--source", choices=["auto", "bridge", "ini", "mock"], default="auto",
                        help="Data source (default: auto = bridge then ini then mock)")
    parser.add_argument("--host", default="127.0.0.1", help="Bridge daemon host")
    parser.add_argument("--port", type=int, default=51935, help="Bridge daemon control port")
    parser.add_argument("--format", choices=["json", "prompt"], default="prompt",
                        help="Output format (default: prompt)")
    parser.add_argument("--project-root", type=str, default=None,
                        help="Path to Py4GW-main root (auto-detected if omitted)")
    args = parser.parse_args()

    root = Path(args.project_root) if args.project_root else None

    if args.source == "bridge":
        snap = GameStateSnapshot.from_bridge_daemon(host=args.host, port=args.port, project_root=root)
    elif args.source == "ini":
        snap = GameStateSnapshot.from_ini(project_root=root)
    elif args.source == "mock":
        snap = GameStateSnapshot.from_mock()
    else:
        snap = capture_snapshot(bridge_host=args.host, bridge_port=args.port, project_root=root)

    if args.format == "json":
        print(snap.to_json(indent=2))
    else:
        print(snap.to_prompt_text())
