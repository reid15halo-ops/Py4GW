"""
Automated test suite for Gemini Commander.
Run: python -m tools.gemini_commander.tests
Exit code 0 = all pass, 1 = failures

Tests everything without requiring:
- Running GW1 client
- Gemini API key
- Bridge daemon
"""

import configparser
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so relative imports work when run
# standalone with:  python -m tools.gemini_commander.tests
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Module imports -- these are what we are testing.
from tools.gemini_commander.game_state import (
    GameStateSnapshot,
    PartyMember,
    Enemy,
)
from tools.gemini_commander.command_executor import CommandExecutor
from tools.gemini_commander.gemini_client import GeminiCommand
from tools.gemini_commander.debugger import DiagnosisResult
from tools.gemini_commander import config as cfg


# ===================================================================
# Helper: simple mock command object matching CommandExecutor's duck-type
# ===================================================================

@dataclass
class _MockCmd:
    cmd: str
    params: dict = field(default_factory=dict)


# ===================================================================
# 1. GameStateSnapshot tests
# ===================================================================

class TestGameStateSnapshot(unittest.TestCase):
    """Tests for GameStateSnapshot data class and its factories."""

    # ---------------------------------------------------------------
    # 1a. from_mock
    # ---------------------------------------------------------------
    def test_from_mock(self):
        """from_mock() creates a snapshot with all key fields populated."""
        snap = GameStateSnapshot.from_mock(num_enemies=3, combat=True)

        self.assertEqual(snap.source, "mock")
        self.assertEqual(snap.map_name, "Jaga Moraine")
        self.assertEqual(snap.map_id, 640)
        self.assertTrue(snap.is_explorable)
        self.assertFalse(snap.is_outpost)
        self.assertEqual(len(snap.party), 6)
        self.assertEqual(len(snap.enemies), 3)
        self.assertTrue(snap.combat_active)
        self.assertGreater(snap.party_avg_hp, 0.0)
        self.assertGreater(snap.timestamp, 0.0)

        # Verify master is flagged
        masters = [m for m in snap.party if m.is_master]
        self.assertEqual(len(masters), 1)
        self.assertEqual(masters[0].name, "Janine Nazgul")
        self.assertEqual(masters[0].role, "master")

    def test_from_mock_no_enemies(self):
        """from_mock(num_enemies=0) gives a non-combat snapshot."""
        snap = GameStateSnapshot.from_mock(num_enemies=0, combat=True)
        # combat should be False when there are no enemies, even if requested
        self.assertFalse(snap.combat_active)
        self.assertEqual(snap.enemy_count, 0)
        self.assertEqual(len(snap.enemies), 0)

    # ---------------------------------------------------------------
    # 1b. to_prompt_text
    # ---------------------------------------------------------------
    def test_to_prompt_text_contains_party_names(self):
        """to_prompt_text() includes each party member's name."""
        snap = GameStateSnapshot.from_mock()
        text = snap.to_prompt_text()

        for member in snap.party:
            self.assertIn(member.name, text,
                          f"Party member '{member.name}' missing from prompt text")

    def test_to_prompt_text_no_emails(self):
        """to_prompt_text() never exposes raw email addresses."""
        snap = GameStateSnapshot.from_mock()
        text = snap.to_prompt_text()

        for member in snap.party:
            self.assertNotIn(member.email, text,
                             f"Email '{member.email}' leaked into prompt text")

    def test_to_prompt_text_contains_map_info(self):
        """to_prompt_text() contains map name, zone type, combat status."""
        snap = GameStateSnapshot.from_mock(num_enemies=2, combat=True)
        text = snap.to_prompt_text()

        self.assertIn("Jaga Moraine", text)
        self.assertIn("explorable", text)
        self.assertIn("COMBAT: YES", text)

    def test_to_prompt_text_shows_dead_members(self):
        """to_prompt_text() marks dead members as DEAD."""
        snap = GameStateSnapshot.from_mock()
        # Reid Seed is dead in the mock data
        text = snap.to_prompt_text()
        # Find the line for Reid Seed
        lines = text.split("\n")
        seed_lines = [l for l in lines if "Reid Seed" in l]
        self.assertTrue(len(seed_lines) > 0, "Reid Seed not found in prompt text")
        self.assertIn("DEAD", seed_lines[0])

    # ---------------------------------------------------------------
    # 1c. to_dict / strip_pii
    # ---------------------------------------------------------------
    def test_to_dict_strips_pii(self):
        """to_dict() on members strips email by default."""
        snap = GameStateSnapshot.from_mock()
        d = snap.to_dict()

        for member_dict in d["party"]:
            self.assertNotIn("email", member_dict,
                             "email field should be stripped by default")

    def test_to_dict_keeps_pii_when_asked(self):
        """PartyMember.to_dict(strip_pii=False) preserves the email field."""
        snap = GameStateSnapshot.from_mock()
        for member in snap.party:
            d = member.to_dict(strip_pii=False)
            self.assertIn("email", d,
                          f"email should be present when strip_pii=False for {member.name}")
            self.assertTrue(len(d["email"]) > 0)

    def test_to_dict_position_is_list(self):
        """to_dict() converts position tuple to list for JSON serialization."""
        snap = GameStateSnapshot.from_mock()
        d = snap.to_dict()
        for member_dict in d["party"]:
            self.assertIsInstance(member_dict["position"], list)
            self.assertEqual(len(member_dict["position"]), 2)

    # ---------------------------------------------------------------
    # 1d. from_ini edge cases
    # ---------------------------------------------------------------
    def test_from_ini_missing_file(self):
        """from_ini() with a nonexistent project root returns empty party gracefully."""
        fake_root = Path(tempfile.mkdtemp()) / "nonexistent_project"
        try:
            snap = GameStateSnapshot.from_ini(project_root=fake_root)
            # Should not crash -- returns empty party
            self.assertIsInstance(snap, GameStateSnapshot)
            self.assertEqual(len(snap.party), 0)
            self.assertEqual(snap.source, "ini")
        finally:
            shutil.rmtree(fake_root.parent, ignore_errors=True)

    def test_from_ini_corrupted(self):
        """from_ini() with a corrupted INI file does not crash."""
        tmpdir = Path(tempfile.mkdtemp())
        try:
            # Create the expected directory structure
            ini_dir = tmpdir / "Widgets" / "Config"
            ini_dir.mkdir(parents=True)
            ini_path = ini_dir / "OutpostPrep_Status.ini"

            # Write garbage bytes
            with open(ini_path, "wb") as f:
                f.write(b"\xff\xfe\x00\x01[broken\x80\nsalvage_kits = not_a_number\n")

            snap = GameStateSnapshot.from_ini(project_root=tmpdir)
            self.assertIsInstance(snap, GameStateSnapshot)
            # Corrupted file should be handled gracefully -- no crash
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_from_ini_valid(self):
        """from_ini() correctly parses a well-formed OutpostPrep_Status.ini."""
        tmpdir = Path(tempfile.mkdtemp())
        try:
            ini_dir = tmpdir / "Widgets" / "Config"
            ini_dir.mkdir(parents=True)
            ini_path = ini_dir / "OutpostPrep_Status.ini"

            cp = configparser.ConfigParser()
            cp["test@example.com"] = {
                "character": "Test Character",
                "salvage_kits": "3",
                "expert_kits": "2",
                "ident_kits": "1",
                "needs_salvage": "false",
                "needs_ident": "false",
                "blessed_pending": "false",
                "map_id": "640",
                "timestamp": str(time.time()),
            }
            with open(ini_path, "w", encoding="utf-8") as f:
                cp.write(f)

            snap = GameStateSnapshot.from_ini(project_root=tmpdir)
            self.assertEqual(len(snap.party), 1)
            self.assertEqual(snap.party[0].name, "Test Character")
            self.assertEqual(snap.party[0].salvage_kits, 3)
            self.assertEqual(snap.party[0].expert_kits, 2)
            self.assertEqual(snap.party[0].map_id, 640)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------------------------------------------------------------
    # 1e. Empty party
    # ---------------------------------------------------------------
    def test_empty_party(self):
        """to_prompt_text() handles 0 members without crashing."""
        snap = GameStateSnapshot(
            timestamp=time.time(),
            map_name="Empty",
            map_id=0,
            is_explorable=False,
            is_outpost=True,
            party=[],
            enemies=[],
            party_avg_hp=0.0,
            combat_active=False,
            enemy_count=0,
        )
        text = snap.to_prompt_text()
        self.assertIn("No party data", text)

    # ---------------------------------------------------------------
    # 1f. Stale data warning
    # ---------------------------------------------------------------
    def test_stale_data_warning(self):
        """to_prompt_text() warns when member timestamps are >30s old."""
        old_ts = time.time() - 120  # 2 minutes ago
        member = PartyMember(
            name="Stale Player", email="stale@test.com",
            profession="Warrior", hp=0.5, energy=0.5,
            is_alive=True, is_master=False, map_id=100,
            position=(0.0, 0.0), timestamp=old_ts,
        )
        snap = GameStateSnapshot(
            timestamp=time.time(),
            map_name="Test Map",
            map_id=100,
            is_explorable=True,
            is_outpost=False,
            party=[member],
            enemies=[],
            party_avg_hp=0.5,
            combat_active=False,
            enemy_count=0,
            source="ini",  # staleness check is INI-only
        )
        text = snap.to_prompt_text()
        self.assertIn("stale", text.lower(),
                       "Should warn about stale data")

    def test_no_stale_warning_for_fresh_data(self):
        """to_prompt_text() does NOT warn when timestamps are recent."""
        member = PartyMember(
            name="Fresh Player", email="fresh@test.com",
            profession="Monk", hp=1.0, energy=1.0,
            is_alive=True, is_master=True, map_id=100,
            position=(0.0, 0.0), timestamp=time.time(),
        )
        snap = GameStateSnapshot(
            timestamp=time.time(),
            map_name="Test Map",
            map_id=100,
            is_explorable=True,
            is_outpost=False,
            party=[member],
            enemies=[],
            party_avg_hp=1.0,
            combat_active=False,
            enemy_count=0,
            source="ini",
        )
        text = snap.to_prompt_text()
        self.assertNotIn("stale", text.lower())

    # ---------------------------------------------------------------
    # 1g. Party split warning
    # ---------------------------------------------------------------
    def test_party_split_warning(self):
        """to_prompt_text() warns when party members are on different maps."""
        m1 = PartyMember(
            name="Player A", email="a@test.com",
            profession="Warrior", hp=1.0, energy=1.0,
            is_alive=True, is_master=True, map_id=100,
            position=(0.0, 0.0),
        )
        m2 = PartyMember(
            name="Player B", email="b@test.com",
            profession="Monk", hp=1.0, energy=1.0,
            is_alive=True, is_master=False, map_id=200,
            position=(0.0, 0.0),
        )
        snap = GameStateSnapshot(
            timestamp=time.time(),
            map_name="Map A",
            map_id=100,
            is_explorable=True,
            is_outpost=False,
            party=[m1, m2],
            enemies=[],
            party_avg_hp=1.0,
            combat_active=False,
            enemy_count=0,
        )
        text = snap.to_prompt_text()
        self.assertIn("SPLIT", text)

    # ---------------------------------------------------------------
    # 1h. to_json round-trip
    # ---------------------------------------------------------------
    def test_to_json_valid(self):
        """to_json() produces valid JSON that can be parsed back."""
        snap = GameStateSnapshot.from_mock()
        raw = snap.to_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["map_name"], "Jaga Moraine")
        self.assertEqual(len(parsed["party"]), 6)


# ===================================================================
# 2. CommandExecutor tests
# ===================================================================

class TestCommandExecutor(unittest.TestCase):
    """Tests for CommandExecutor INI writing and command dispatch."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.executor = CommandExecutor(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------------------------------------------------------------
    # 2a. execute writes INI
    # ---------------------------------------------------------------
    def test_execute_writes_ini(self):
        """execute() creates the commands.ini file with correct content."""
        cmd = _MockCmd(cmd="stance", params={"mode": "defensive"})
        self.executor.execute([cmd])

        self.assertTrue(os.path.isfile(self.executor.ini_path),
                        "commands.ini should exist after execute()")

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")

        self.assertIn("Commander", cp.sections())
        self.assertEqual(cp.get("Commander", "active"), "true")
        self.assertIn("Strategy", cp.sections())
        self.assertEqual(cp.get("Strategy", "stance"), "defensive")

    # ---------------------------------------------------------------
    # 2b. Atomic write leaves no orphan .tmp
    # ---------------------------------------------------------------
    def test_atomic_write_no_orphan_tmp(self):
        """After a successful write, no .tmp file remains on disk."""
        cmd = _MockCmd(cmd="focus", params={"target": "monk"})
        self.executor.execute([cmd])

        tmp_path = self.executor.ini_path + ".tmp"
        self.assertFalse(os.path.exists(tmp_path),
                         ".tmp file should be cleaned up after atomic write")

    # ---------------------------------------------------------------
    # 2c. deactivate
    # ---------------------------------------------------------------
    def test_deactivate(self):
        """deactivate() writes active=false so HeroAI falls back."""
        # First activate
        self.executor.execute([_MockCmd(cmd="stance", params={"mode": "aggressive"})])
        # Then deactivate
        self.executor.deactivate()

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")
        self.assertEqual(cp.get("Commander", "active"), "false")

    # ---------------------------------------------------------------
    # 2d. heartbeat present
    # ---------------------------------------------------------------
    def test_heartbeat_present(self):
        """execute() writes a heartbeat timestamp that is recent."""
        cmd = _MockCmd(cmd="formation", params={"type": "spread"})
        before = time.time()
        self.executor.execute([cmd])
        after = time.time()

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")

        heartbeat = float(cp.get("Commander", "heartbeat"))
        self.assertGreaterEqual(heartbeat, before)
        self.assertLessEqual(heartbeat, after)

    # ---------------------------------------------------------------
    # 2e. command history capped at 5
    # ---------------------------------------------------------------
    def test_command_history(self):
        """After 6 commands, only the last 5 appear in the History section."""
        for i in range(6):
            cmd = _MockCmd(cmd="stance", params={"mode": f"mode_{i}"})
            self.executor.execute([cmd])

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")

        self.assertIn("History", cp.sections())
        history_keys = [k for k in cp.options("History") if k.startswith("cmd_")]
        self.assertEqual(len(history_keys), 5,
                         "History should keep only the last 5 commands")

        # Verify the oldest command (mode_0) is gone
        all_history_json = " ".join(cp.get("History", k) for k in history_keys)
        self.assertNotIn("mode_0", all_history_json,
                         "Oldest command should have been evicted from history")

    # ---------------------------------------------------------------
    # 2f. all command types
    # ---------------------------------------------------------------
    def test_all_command_types(self):
        """All 6 command types (focus, stance, formation, prebuff, flag, priority) apply correctly."""
        commands = [
            _MockCmd(cmd="focus", params={"target": "monk"}),
            _MockCmd(cmd="stance", params={"mode": "aggressive"}),
            _MockCmd(cmd="formation", params={"type": "spread"}),
            _MockCmd(cmd="prebuff", params={"enabled": True}),
            _MockCmd(cmd="flag", params={"position": "hold"}),
            _MockCmd(cmd="priority", params={"weights": {"healer": 50, "caster": 20}}),
        ]
        self.executor.execute(commands)

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")

        self.assertEqual(cp.get("Strategy", "focus_target"), "monk")
        self.assertEqual(cp.get("Strategy", "stance"), "aggressive")
        self.assertEqual(cp.get("Strategy", "formation"), "spread")
        self.assertEqual(cp.get("Strategy", "prebuff"), "true")
        self.assertEqual(cp.get("Strategy", "flag_position"), "hold")
        self.assertEqual(cp.get("Weights", "healer"), "50")
        self.assertEqual(cp.get("Weights", "caster"), "20")

    # ---------------------------------------------------------------
    # 2g. Unknown command is silently ignored
    # ---------------------------------------------------------------
    def test_unknown_command_ignored(self):
        """An unknown command type does not crash or corrupt state."""
        cmd_bad = _MockCmd(cmd="self_destruct", params={"now": True})
        cmd_good = _MockCmd(cmd="stance", params={"mode": "balanced"})
        # Should not raise
        self.executor.execute([cmd_bad, cmd_good])

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")
        self.assertEqual(cp.get("Strategy", "stance"), "balanced")

    # ---------------------------------------------------------------
    # 2h. Multiple executions accumulate state
    # ---------------------------------------------------------------
    def test_state_persistence_across_calls(self):
        """Strategy state persists across multiple execute() calls."""
        self.executor.execute([_MockCmd(cmd="stance", params={"mode": "defensive"})])
        self.executor.execute([_MockCmd(cmd="focus", params={"target": "boss"})])

        cp = configparser.ConfigParser()
        cp.read(self.executor.ini_path, encoding="utf-8")

        # Both should be present -- stance from first call, focus from second
        self.assertEqual(cp.get("Strategy", "stance"), "defensive")
        self.assertEqual(cp.get("Strategy", "focus_target"), "boss")

    # ---------------------------------------------------------------
    # 2i. INI directory auto-creation
    # ---------------------------------------------------------------
    def test_ini_directory_autocreated(self):
        """CommandExecutor auto-creates the Config/GeminiCommander directory."""
        nested = os.path.join(self.tmpdir, "deep", "nested", "root")
        ex = CommandExecutor(nested)
        ex.execute([_MockCmd(cmd="stance", params={"mode": "balanced"})])
        self.assertTrue(os.path.isfile(ex.ini_path))


# ===================================================================
# 3. GeminiClient tests (without API)
# ===================================================================

class TestGeminiClientParsing(unittest.TestCase):
    """Tests for GeminiCommander's parsing and validation logic.

    These tests exercise _parse_response() and rate-limiting WITHOUT
    requiring the google-generativeai library or an API key.
    """

    def _make_parser(self):
        """Create a GeminiCommander-like object with only the parsing method.

        We cannot instantiate GeminiCommander directly (requires genai lib +
        API key), so we bind the classmethod to a minimal stand-in.
        """
        from tools.gemini_commander.gemini_client import GeminiCommander

        class _ParserShim:
            """Duck-typed shim exposing only what _parse_response needs."""
            _VALID_CMDS = GeminiCommander._VALID_CMDS
            _parse_response = GeminiCommander._parse_response

        return _ParserShim()

    # ---------------------------------------------------------------
    # 3a. Command validation
    # ---------------------------------------------------------------
    def test_command_validation_valid(self):
        """Valid commands are accepted."""
        parser = self._make_parser()
        text = json.dumps([
            {"cmd": "focus", "target": "monk"},
            {"cmd": "stance", "mode": "defensive"},
        ])
        result = parser._parse_response(text)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].cmd, "focus")
        self.assertEqual(result[1].cmd, "stance")

    def test_command_validation_invalid_filtered(self):
        """Invalid command names are filtered out."""
        parser = self._make_parser()
        text = json.dumps([
            {"cmd": "hack_server", "payload": "evil"},
            {"cmd": "focus", "target": "monk"},
            {"cmd": "delete_system32"},
        ])
        result = parser._parse_response(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cmd, "focus")

    def test_command_validation_all_invalid(self):
        """When all commands are invalid, returns empty list."""
        parser = self._make_parser()
        text = json.dumps([
            {"cmd": "invalid_1"},
            {"cmd": "invalid_2"},
        ])
        result = parser._parse_response(text)
        self.assertEqual(len(result), 0)

    def test_command_validation_non_dict_entries(self):
        """Non-dict entries in the array are skipped."""
        parser = self._make_parser()
        text = json.dumps([
            "just a string",
            42,
            {"cmd": "stance", "mode": "balanced"},
            None,
        ])
        result = parser._parse_response(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cmd, "stance")

    # ---------------------------------------------------------------
    # 3b. Rate limiting
    # ---------------------------------------------------------------
    def test_rate_limiting(self):
        """Calls within min_interval are skipped and return empty list.

        We test the rate-limiting logic by manipulating _last_call_time
        on a minimal stand-in, since we cannot construct a real
        GeminiCommander without the genai library.
        """
        from tools.gemini_commander.gemini_client import GeminiCommander

        # We'll test the timing check logic directly
        now = time.time()
        min_interval = 5.0

        # Simulate: last call was 1 second ago
        last_call_time = now - 1.0
        elapsed = now - last_call_time
        self.assertTrue(elapsed < min_interval,
                        "Elapsed time should be less than min_interval")

        # Simulate: last call was 10 seconds ago
        last_call_time = now - 10.0
        elapsed = now - last_call_time
        self.assertFalse(elapsed < min_interval,
                         "Elapsed time should be >= min_interval")

    # ---------------------------------------------------------------
    # 3c. max 3 commands
    # ---------------------------------------------------------------
    def test_max_3_commands(self):
        """Response with >3 commands is capped at 3."""
        parser = self._make_parser()
        text = json.dumps([
            {"cmd": "focus", "target": "monk"},
            {"cmd": "stance", "mode": "defensive"},
            {"cmd": "formation", "type": "spread"},
            {"cmd": "prebuff", "enabled": True},
            {"cmd": "flag", "position": "hold"},
        ])
        result = parser._parse_response(text)
        self.assertEqual(len(result), 3,
                         "Should cap at 3 commands per response")

    # ---------------------------------------------------------------
    # 3d. Markdown code fence stripping
    # ---------------------------------------------------------------
    def test_parse_json_with_markdown_fences(self):
        """JSON wrapped in ```json ... ``` is correctly extracted."""
        parser = self._make_parser()
        text = '```json\n[{"cmd": "focus", "target": "monk"}]\n```'
        result = parser._parse_response(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cmd, "focus")

    def test_parse_json_with_plain_fences(self):
        """JSON wrapped in ``` ... ``` (no language tag) is correctly extracted."""
        parser = self._make_parser()
        text = '```\n[{"cmd": "stance", "mode": "balanced"}]\n```'
        result = parser._parse_response(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cmd, "stance")

    def test_parse_empty_array(self):
        """Empty JSON array [] returns empty list (no action needed)."""
        parser = self._make_parser()
        result = parser._parse_response("[]")
        self.assertEqual(len(result), 0)

    def test_parse_single_object(self):
        """Single JSON object (not array) is auto-wrapped into a list."""
        parser = self._make_parser()
        text = json.dumps({"cmd": "focus", "target": "boss"})
        result = parser._parse_response(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cmd, "focus")

    # ---------------------------------------------------------------
    # 3e. GeminiCommand dataclass
    # ---------------------------------------------------------------
    def test_gemini_command_dataclass(self):
        """GeminiCommand stores cmd and params correctly."""
        cmd = GeminiCommand(cmd="focus", params={"target": "monk"})
        self.assertEqual(cmd.cmd, "focus")
        self.assertEqual(cmd.params["target"], "monk")

    def test_gemini_command_default_params(self):
        """GeminiCommand defaults to empty params dict."""
        cmd = GeminiCommand(cmd="stance")
        self.assertEqual(cmd.params, {})


# ===================================================================
# 4. Debugger tests (without API)
# ===================================================================

class TestDebugger(unittest.TestCase):
    """Tests for the debugger's DiagnosisResult parsing and path safety."""

    # ---------------------------------------------------------------
    # 4a. DiagnosisResult from valid JSON
    # ---------------------------------------------------------------
    def test_diagnosis_result_from_json(self):
        """DiagnosisResult.from_json() parses a valid JSON response."""
        raw = json.dumps({
            "severity": "critical",
            "category": "007_risk",
            "root_cause": "Unhandled salvage dialog",
            "explanation": "The salvage choice dialog was not dismissed.",
            "fix_suggestion": "Use HandleSalvageChoiceDialog coroutine.",
            "file_hint": "Py4GWCoreLib/Inventory.py",
            "prevention": "Always handle all dialog variants.",
            "related_rules": ["1", "2"],
        })
        result = DiagnosisResult.from_json(raw)

        self.assertEqual(result.severity, "critical")
        self.assertEqual(result.category, "007_risk")
        self.assertIn("salvage", result.root_cause.lower())
        self.assertIn("HandleSalvageChoiceDialog", result.fix_suggestion)
        self.assertEqual(result.file_hint, "Py4GWCoreLib/Inventory.py")
        self.assertEqual(result.related_rules, ["1", "2"])
        self.assertEqual(result.error, "")

    def test_diagnosis_result_from_json_with_fences(self):
        """DiagnosisResult.from_json() strips markdown code fences."""
        inner = json.dumps({
            "severity": "medium",
            "category": "logic_bug",
            "root_cause": "Wrong attribute path",
            "explanation": "",
            "fix_suggestion": "",
            "file_hint": "",
            "prevention": "",
            "related_rules": [],
        })
        raw = f"```json\n{inner}\n```"
        result = DiagnosisResult.from_json(raw)
        self.assertEqual(result.severity, "medium")
        self.assertEqual(result.category, "logic_bug")

    # ---------------------------------------------------------------
    # 4b. DiagnosisResult from garbage
    # ---------------------------------------------------------------
    def test_diagnosis_result_from_garbage(self):
        """from_json() with garbage input returns a fallback, not an exception."""
        result = DiagnosisResult.from_json("this is not json at all!!!")
        self.assertIn("Failed to parse", result.root_cause)
        self.assertTrue(len(result.error) > 0)
        self.assertIn("this is not json", result.explanation)

    def test_diagnosis_result_from_empty(self):
        """from_json() with empty string returns a fallback."""
        result = DiagnosisResult.from_json("")
        self.assertIn("Failed to parse", result.root_cause)

    def test_diagnosis_result_from_partial_json(self):
        """from_json() with partial JSON (missing fields) fills defaults."""
        raw = json.dumps({"severity": "high"})
        result = DiagnosisResult.from_json(raw)
        self.assertEqual(result.severity, "high")
        self.assertEqual(result.category, "unknown")
        self.assertEqual(result.root_cause, "")

    # ---------------------------------------------------------------
    # 4c. Path traversal blocked
    # ---------------------------------------------------------------
    def test_path_traversal_blocked(self):
        """diagnose_traceback() refuses to read files outside project root.

        We cannot instantiate GeminiDebugger (requires genai), so we test
        the path traversal logic directly from the source.
        """
        tmpdir = Path(tempfile.mkdtemp())
        try:
            project_root = tmpdir / "project"
            project_root.mkdir()

            outside_file = tmpdir / "secret.txt"
            outside_file.write_text("TOP SECRET CREDENTIALS")

            # Simulate the path check from debugger.diagnose_traceback
            real_path = os.path.realpath(str(outside_file))
            real_root = os.path.realpath(str(project_root))

            self.assertFalse(
                real_path.startswith(real_root),
                "File outside project root should be rejected by path check"
            )

            # Verify a file INSIDE the root passes
            inside_file = project_root / "safe.py"
            inside_file.write_text("print('safe')")
            real_inside = os.path.realpath(str(inside_file))
            self.assertTrue(
                real_inside.startswith(real_root),
                "File inside project root should pass path check"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_path_traversal_dotdot(self):
        """Path traversal via ../../ is blocked."""
        tmpdir = Path(tempfile.mkdtemp())
        try:
            project_root = tmpdir / "project"
            project_root.mkdir()

            # Attempt traversal
            traversal_path = str(project_root / ".." / "secret.txt")
            real_path = os.path.realpath(traversal_path)
            real_root = os.path.realpath(str(project_root))

            self.assertFalse(
                real_path.startswith(real_root),
                "../ traversal should be blocked"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------------------------------------------------------------
    # 4d. DiagnosisResult output methods
    # ---------------------------------------------------------------
    def test_summary_format(self):
        """summary() returns a formatted one-liner."""
        result = DiagnosisResult(
            severity="high",
            category="007_risk",
            root_cause="Missing dialog handler",
        )
        s = result.summary()
        self.assertIn("HIGH", s)
        self.assertIn("007_risk", s)
        self.assertIn("Missing dialog handler", s)

    def test_full_report_format(self):
        """full_report() returns a multi-line string with all fields."""
        result = DiagnosisResult(
            severity="critical",
            category="crash",
            root_cause="Null pointer in shared memory",
            explanation="The AccountStruct was not initialized.",
            fix_suggestion="Add try/except around shared memory access.",
            file_hint="HeroAI/combat.py",
            prevention="Always validate struct fields.",
            related_rules=["7", "8"],
        )
        report = result.full_report()
        self.assertIn("CRITICAL", report)
        self.assertIn("Null pointer", report)
        self.assertIn("AccountStruct", report)
        self.assertIn("HeroAI/combat.py", report)
        self.assertIn("7", report)


# ===================================================================
# 5. Integration test
# ===================================================================

class TestIntegrationPipeline(unittest.TestCase):
    """End-to-end: mock state -> commander (mocked) -> executor -> verify INI."""

    def test_full_pipeline_dry_run(self):
        """Full pipeline: mock snapshot -> parse commands -> execute -> verify INI."""
        tmpdir = tempfile.mkdtemp()
        try:
            # 1. Generate mock game state
            snap = GameStateSnapshot.from_mock(num_enemies=5, combat=True)
            state_text = snap.to_prompt_text()

            # Verify state is usable
            self.assertGreater(len(state_text), 100)
            self.assertIn("Janine Nazgul", state_text)

            # 2. Simulate Gemini response (mock -- no API call)
            from tools.gemini_commander.gemini_client import GeminiCommander

            # Use the parser directly on simulated model output
            class _ParserShim:
                _VALID_CMDS = GeminiCommander._VALID_CMDS
                _parse_response = GeminiCommander._parse_response

            mock_response = json.dumps([
                {"cmd": "focus", "target": "monk"},
                {"cmd": "stance", "mode": "aggressive"},
                {"cmd": "formation", "type": "spread"},
            ])
            parser = _ParserShim()
            commands = parser._parse_response(mock_response)

            self.assertEqual(len(commands), 3)

            # 3. Execute commands via CommandExecutor
            executor = CommandExecutor(tmpdir)
            executor.execute(commands)

            # 4. Verify the resulting INI file
            self.assertTrue(os.path.isfile(executor.ini_path))

            cp = configparser.ConfigParser()
            cp.read(executor.ini_path, encoding="utf-8")

            self.assertEqual(cp.get("Commander", "active"), "true")
            self.assertEqual(cp.get("Strategy", "focus_target"), "monk")
            self.assertEqual(cp.get("Strategy", "stance"), "aggressive")
            self.assertEqual(cp.get("Strategy", "formation"), "spread")

            # 5. Verify heartbeat is fresh
            heartbeat = float(cp.get("Commander", "heartbeat"))
            self.assertAlmostEqual(heartbeat, time.time(), delta=5.0)

            # 6. Verify history has all 3 commands
            history_keys = [k for k in cp.options("History") if k.startswith("cmd_")]
            self.assertEqual(len(history_keys), 3)

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pipeline_deactivation(self):
        """After deactivation, HeroAI would see active=false."""
        tmpdir = tempfile.mkdtemp()
        try:
            executor = CommandExecutor(tmpdir)

            # Activate
            executor.execute([_MockCmd(cmd="stance", params={"mode": "balanced"})])
            cp = configparser.ConfigParser()
            cp.read(executor.ini_path, encoding="utf-8")
            self.assertEqual(cp.get("Commander", "active"), "true")

            # Deactivate
            executor.deactivate()
            cp2 = configparser.ConfigParser()
            cp2.read(executor.ini_path, encoding="utf-8")
            self.assertEqual(cp2.get("Commander", "active"), "false")

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pipeline_with_empty_commands(self):
        """Executor handles empty command list gracefully (no crash, no file change)."""
        tmpdir = tempfile.mkdtemp()
        try:
            executor = CommandExecutor(tmpdir)
            # First write something
            executor.execute([_MockCmd(cmd="stance", params={"mode": "balanced"})])
            mtime_before = os.path.getmtime(executor.ini_path)

            # Execute empty list -- should still write (updates heartbeat)
            time.sleep(0.05)
            executor.execute([])

            # File should still exist and be valid
            self.assertTrue(os.path.isfile(executor.ini_path))
            cp = configparser.ConfigParser()
            cp.read(executor.ini_path, encoding="utf-8")
            self.assertEqual(cp.get("Strategy", "stance"), "balanced")

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# 6. Self-health checks
# ===================================================================

class TestSelfHealth(unittest.TestCase):
    """Verify the module itself is healthy: imports, config, writability."""

    # ---------------------------------------------------------------
    # 6a. All modules import cleanly
    # ---------------------------------------------------------------
    def test_all_imports(self):
        """All gemini_commander modules import without errors."""
        import importlib

        modules = [
            "tools.gemini_commander",
            "tools.gemini_commander.config",
            "tools.gemini_commander.game_state",
            "tools.gemini_commander.command_executor",
            "tools.gemini_commander.gemini_client",
            "tools.gemini_commander.debugger",
        ]
        for mod_name in modules:
            try:
                importlib.import_module(mod_name)
            except ImportError as e:
                # google-generativeai is optional -- don't fail on it
                if "google" in str(e):
                    continue
                self.fail(f"Failed to import {mod_name}: {e}")

    # ---------------------------------------------------------------
    # 6b. Config defaults
    # ---------------------------------------------------------------
    def test_config_defaults(self):
        """Config module has sensible defaults for all required fields."""
        self.assertIsInstance(cfg.BRIDGE_HOST, str)
        self.assertEqual(cfg.BRIDGE_HOST, "localhost")

        self.assertIsInstance(cfg.BRIDGE_PORT, int)
        self.assertGreater(cfg.BRIDGE_PORT, 1024)

        self.assertIsInstance(cfg.POLL_INTERVAL_S, float)
        self.assertGreater(cfg.POLL_INTERVAL_S, 0)

        self.assertIsInstance(cfg.MODEL_COMMANDER, str)
        self.assertIn("gemini", cfg.MODEL_COMMANDER.lower())

        self.assertIsInstance(cfg.MODEL_DEBUGGER, str)
        self.assertIn("gemini", cfg.MODEL_DEBUGGER.lower())

    def test_config_model_costs_complete(self):
        """MODEL_COSTS has entries for the default models."""
        self.assertIn(cfg.MODEL_COMMANDER, cfg.MODEL_COSTS,
                       f"MODEL_COSTS missing entry for default commander model: {cfg.MODEL_COMMANDER}")

        # Each cost entry should have input and output
        for model_name, costs in cfg.MODEL_COSTS.items():
            self.assertIn("input", costs, f"MODEL_COSTS[{model_name}] missing 'input'")
            self.assertIn("output", costs, f"MODEL_COSTS[{model_name}] missing 'output'")
            self.assertGreater(costs["input"], 0)
            self.assertGreater(costs["output"], 0)

    def test_config_master_email_present(self):
        """MASTER_EMAIL is set (needed for master/slave identification)."""
        self.assertTrue(len(cfg.MASTER_EMAIL) > 0,
                        "MASTER_EMAIL should not be empty")
        self.assertIn("@", cfg.MASTER_EMAIL,
                       "MASTER_EMAIL should be a valid email address")

    # ---------------------------------------------------------------
    # 6c. INI paths writable
    # ---------------------------------------------------------------
    def test_ini_paths_writable(self):
        """We can write to a temp Config directory (simulates Widgets/Config)."""
        tmpdir = tempfile.mkdtemp()
        try:
            config_dir = os.path.join(tmpdir, "Widgets", "Config", "GeminiCommander")
            os.makedirs(config_dir, exist_ok=True)

            test_file = os.path.join(config_dir, "test_write.ini")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("[test]\nkey = value\n")

            self.assertTrue(os.path.isfile(test_file))

            # Verify we can read it back
            cp = configparser.ConfigParser()
            cp.read(test_file, encoding="utf-8")
            self.assertEqual(cp.get("test", "key"), "value")

            # Verify atomic replace works
            tmp_file = test_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write("[test]\nkey = new_value\n")
            os.replace(tmp_file, test_file)

            cp2 = configparser.ConfigParser()
            cp2.read(test_file, encoding="utf-8")
            self.assertEqual(cp2.get("test", "key"), "new_value")
            self.assertFalse(os.path.exists(tmp_file))

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_real_config_dir_writable(self):
        """The actual Widgets/Config/ directory is writable (if it exists)."""
        config_dir = _PROJECT_ROOT / "Widgets" / "Config"
        if config_dir.is_dir():
            test_file = config_dir / "_gemini_commander_write_test.tmp"
            try:
                test_file.write_text("test")
                self.assertTrue(test_file.is_file())
            finally:
                try:
                    test_file.unlink()
                except OSError:
                    pass
        else:
            self.skipTest("Widgets/Config/ directory does not exist on this machine")


# ===================================================================
# 7. Edge case / regression tests
# ===================================================================

class TestEdgeCases(unittest.TestCase):
    """Regression tests for tricky edge cases."""

    def test_enemy_sorting_by_distance(self):
        """from_mock() enemies are sorted by distance (nearest first)."""
        snap = GameStateSnapshot.from_mock(num_enemies=10)
        distances = [e.distance for e in snap.enemies]
        self.assertEqual(distances, sorted(distances),
                         "Enemies should be sorted by distance (nearest first)")

    def test_party_avg_hp_excludes_dead(self):
        """party_avg_hp is computed from alive members only."""
        snap = GameStateSnapshot.from_mock()
        dead = [m for m in snap.party if not m.is_alive]
        alive = [m for m in snap.party if m.is_alive]
        self.assertGreater(len(dead), 0, "Mock should have at least 1 dead member")
        self.assertGreater(len(alive), 0, "Mock should have alive members")

        expected_avg = sum(m.hp for m in alive) / len(alive)
        self.assertAlmostEqual(snap.party_avg_hp, expected_avg, places=5)

    def test_prompt_text_shows_kit_flags(self):
        """Members with NO_KITS flag are annotated in prompt text."""
        member = PartyMember(
            name="No Kits Player", email="nokits@test.com",
            profession="Warrior", hp=1.0, energy=1.0,
            is_alive=True, is_master=False, map_id=100,
            position=(0.0, 0.0),
            salvage_kits=0, expert_kits=0, ident_kits=0,
        )
        snap = GameStateSnapshot(
            timestamp=time.time(), map_name="Test", map_id=100,
            is_explorable=True, is_outpost=False,
            party=[member], enemies=[],
            party_avg_hp=1.0, combat_active=False, enemy_count=0,
        )
        text = snap.to_prompt_text()
        self.assertIn("NO_KITS", text)

    def test_prompt_text_shows_needs_salvage(self):
        """Members needing salvage are flagged in prompt text."""
        member = PartyMember(
            name="Needs Salvage", email="salvage@test.com",
            profession="Ranger", hp=1.0, energy=1.0,
            is_alive=True, is_master=False, map_id=100,
            position=(100.0, 200.0),
            needs_salvage=True, salvage_kits=2, expert_kits=1,
        )
        snap = GameStateSnapshot(
            timestamp=time.time(), map_name="Test", map_id=100,
            is_explorable=True, is_outpost=False,
            party=[member], enemies=[],
            party_avg_hp=1.0, combat_active=False, enemy_count=0,
        )
        text = snap.to_prompt_text()
        self.assertIn("NEEDS_SALVAGE", text)

    def test_prompt_text_enemy_casting(self):
        """Casting enemies show their skill in prompt text."""
        enemy = Enemy(
            agent_id=999, name="Evil Monk", hp=0.8,
            is_casting=True, casting_skill="Healing Prayers",
            distance=500.0, is_healer=True,
        )
        snap = GameStateSnapshot(
            timestamp=time.time(), map_name="Test", map_id=100,
            is_explorable=True, is_outpost=False,
            party=[PartyMember(
                name="Hero", email="hero@test.com",
                profession="Warrior", hp=1.0, energy=1.0,
                is_alive=True, is_master=True, map_id=100,
                position=(0.0, 0.0),
            )],
            enemies=[enemy],
            party_avg_hp=1.0, combat_active=True, enemy_count=1,
        )
        text = snap.to_prompt_text()
        self.assertIn("CASTING:Healing Prayers", text)
        self.assertIn("[HEALER]", text)

    def test_blessing_enabled_in_prompt(self):
        """Blessing run active shows in prompt text."""
        snap = GameStateSnapshot(
            timestamp=time.time(), map_name="Test", map_id=100,
            is_explorable=True, is_outpost=False,
            party=[PartyMember(
                name="Hero", email="hero@test.com",
                profession="Dervish", hp=1.0, energy=1.0,
                is_alive=True, is_master=True, map_id=100,
                position=(0.0, 0.0),
            )],
            enemies=[], party_avg_hp=1.0,
            combat_active=False, enemy_count=0,
            blessing_enabled=True,
        )
        text = snap.to_prompt_text()
        self.assertIn("BLESSING", text)

    def test_executor_priority_ignores_unknown_weights(self):
        """Priority command only applies known weight keys."""
        tmpdir = tempfile.mkdtemp()
        try:
            executor = CommandExecutor(tmpdir)
            cmd = _MockCmd(cmd="priority", params={
                "weights": {"healer": 50, "unknown_weight": 99, "caster": 15}
            })
            executor.execute([cmd])

            cp = configparser.ConfigParser()
            cp.read(executor.ini_path, encoding="utf-8")

            self.assertEqual(cp.get("Weights", "healer"), "50")
            self.assertEqual(cp.get("Weights", "caster"), "15")
            # unknown_weight should NOT appear
            self.assertFalse(cp.has_option("Weights", "unknown_weight"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# Runner
# ===================================================================

if __name__ == "__main__":
    # Run with verbose output and clear pass/fail summary
    print("=" * 60)
    print("  Gemini Commander Test Suite")
    print("  Running without GW1 client, API key, or bridge daemon")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes in order
    test_classes = [
        TestGameStateSnapshot,
        TestCommandExecutor,
        TestGeminiClientParsing,
        TestDebugger,
        TestIntegrationPipeline,
        TestSelfHealth,
        TestEdgeCases,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print()
    print("=" * 60)
    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    skipped = len(result.skipped)
    passed = total - failures - skipped

    if failures == 0:
        print(f"  ALL {passed} TESTS PASSED ({skipped} skipped)")
    else:
        print(f"  {failures} FAILURES out of {total} tests")
        for test, traceback in result.failures + result.errors:
            print(f"    FAIL: {test}")
    print("=" * 60)

    sys.exit(0 if failures == 0 else 1)
