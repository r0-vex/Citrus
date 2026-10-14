"""
test_bot_manager.py
===================
Comprehensive unit test suite for the Citrus Bot Manager subsystem:
- BotDefinition and BotRuntimeState models, deserialization, and validation
- Bot registry discovery and manifest parsing (bots/*.json)
- BotManager lifecycle controls (start, stop, restart, get_status)
- Process identity verification and recycled-PID safety
- Atomic state persistence and isolation from Service Manager state
- Bounded log-tail retrieval
- Event bus emission and credential protection

All tests use mocks, temporary directories, and in-memory fixtures.
Zero real processes are spawned or signaled.
"""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import config
from core.bot_manager import manager as bot_manager_module
from core.bot_manager import registry as bot_registry_module
from core.bot_manager.manager import BotManager
from core.bot_manager.models import (
    BotDefinition,
    BotOperationResult,
    BotRuntimeState,
    BotStatus,
)
from core.logging.events import Event, EventNames
from core.pages.bots import _format_status_badge
from core.service_manager import runner
from core.service_manager.models import (
    ProcessIdentity,
    ServiceDefinition,
    ServiceOperationResult,
    ServiceRuntimeState,
    ServiceStatus,
)
from core.service_manager.runner import IdentityVerificationResult, ProcessLaunchable


class TestBotModels(unittest.TestCase):
    """Tests for BotDefinition, BotRuntimeState, and BotOperationResult."""

    def test_bot_definition_fields_and_defaults(self) -> None:
        defn = BotDefinition(
            id="atlas",
            name="Atlas Bot",
            command=["python", "bots/atlas.py"],
            working_directory=Path("/test/dir"),
        )
        self.assertEqual(defn.id, "atlas")
        self.assertEqual(defn.name, "Atlas Bot")
        self.assertEqual(defn.command, ["python", "bots/atlas.py"])
        self.assertEqual(defn.working_directory, Path("/test/dir"))
        self.assertEqual(defn.environment, {})
        self.assertTrue(defn.enabled)
        self.assertFalse(defn.auto_restart)

    def test_bot_runtime_state_defaults(self) -> None:
        state = BotRuntimeState()
        self.assertEqual(state.status, BotStatus.STOPPED)
        self.assertIsNone(state.pid)
        self.assertIsNone(state.identity)
        self.assertIsNone(state.started_at)
        self.assertIsNone(state.last_exit_code)
        self.assertIsNone(state.last_error)
        self.assertIsNone(state.last_exit_at)
        self.assertIsNone(state.last_stopped_at)
        self.assertEqual(state.restart_count, 0)
        self.assertIsNone(state.runtime_seconds())

    def test_runtime_seconds_when_running(self) -> None:
        state = BotRuntimeState(
            status=BotStatus.RUNNING,
            started_at=datetime.now(),
        )
        runtime = state.runtime_seconds()
        self.assertIsNotNone(runtime)
        self.assertGreaterEqual(runtime, 0.0)

    def test_to_and_from_persisted_dict_roundtrip(self) -> None:
        identity = ProcessIdentity(pid=1234, platform="nt", start_time=13300000000, boot_id=None)
        now = datetime.now()
        original = BotRuntimeState(
            status=BotStatus.RUNNING,
            pid=1234,
            identity=identity,
            started_at=now,
            last_exit_code=0,
            last_error="None",
            last_exit_at=now,
            last_stopped_at=now,
            restart_count=3,
        )

        persisted = original.to_persisted_dict()
        restored = BotRuntimeState.from_persisted_dict(persisted)

        self.assertEqual(restored.status, BotStatus.RUNNING)
        self.assertEqual(restored.pid, 1234)
        self.assertEqual(restored.identity, identity)
        self.assertEqual(restored.started_at.isoformat(), now.isoformat())
        self.assertEqual(restored.last_exit_code, 0)
        self.assertEqual(restored.last_error, "None")
        self.assertEqual(restored.last_exit_at.isoformat(), now.isoformat())
        self.assertEqual(restored.last_stopped_at.isoformat(), now.isoformat())
        self.assertEqual(restored.restart_count, 3)

    def test_from_persisted_dict_missing_fields_defaults(self) -> None:
        entry = {"status": "STOPPED"}
        state = BotRuntimeState.from_persisted_dict(entry)
        self.assertEqual(state.status, BotStatus.STOPPED)
        self.assertIsNone(state.pid)
        self.assertIsNone(state.identity)
        self.assertIsNone(state.started_at)
        self.assertEqual(state.restart_count, 0)

    def test_from_persisted_dict_invalid_status_raises(self) -> None:
        with self.assertRaises(ValueError):
            BotRuntimeState.from_persisted_dict({"status": "INVALID_STATE"})

    def test_from_persisted_dict_invalid_pid_raises(self) -> None:
        for bad_pid in ("123", True, False, -5, 0, 3.14):
            with self.subTest(bad_pid=bad_pid):
                with self.assertRaises(ValueError):
                    BotRuntimeState.from_persisted_dict({"status": "STOPPED", "pid": bad_pid})

    def test_from_persisted_dict_pid_identity_mismatch_clears_identity(self) -> None:
        # Outer PID is 1234 but inner identity PID is 5678
        entry = {
            "status": "RUNNING",
            "pid": 1234,
            "identity": {
                "pid": 5678,
                "platform": "posix",
                "start_time": 1000,
                "boot_id": "test-boot",
            },
            "started_at": datetime.now().isoformat(),
        }
        state = BotRuntimeState.from_persisted_dict(entry)
        self.assertEqual(state.pid, 1234)
        self.assertIsNone(state.identity)
        self.assertIsNone(state.started_at)

    def test_from_persisted_dict_corrupt_identity_clears_identity(self) -> None:
        entry = {
            "status": "RUNNING",
            "pid": 1234,
            "identity": {"pid": "not-an-int", "platform": "invalid"},
            "started_at": datetime.now().isoformat(),
        }
        state = BotRuntimeState.from_persisted_dict(entry)
        self.assertEqual(state.pid, 1234)
        self.assertIsNone(state.identity)
        self.assertIsNone(state.started_at)

    def test_from_persisted_dict_invalid_restart_count_raises(self) -> None:
        for bad_count in (True, False, "1", -1, 2.5):
            with self.subTest(bad_count=bad_count):
                with self.assertRaises(ValueError):
                    BotRuntimeState.from_persisted_dict({"status": "STOPPED", "restart_count": bad_count})

    def test_from_persisted_dict_corrupt_datetime_cleared(self) -> None:
        entry = {
            "status": "STOPPED",
            "started_at": "not-a-datetime",
            "last_exit_at": "invalid-timestamp",
        }
        state = BotRuntimeState.from_persisted_dict(entry)
        self.assertIsNone(state.started_at)
        self.assertIsNone(state.last_exit_at)


class TestBotRegistry(unittest.TestCase):
    """Tests for bot manifest discovery in bots/*.json."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bots_dir = Path(self.temp_dir.name)
        self.patcher = patch.object(config, "BOTS_DIR", self.bots_dir)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _write_manifest(self, filename: str, content: dict) -> Path:
        path = self.bots_dir / filename
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def test_discover_valid_manifests(self) -> None:
        self._write_manifest(
            "atlas.json",
            {
                "id": "atlas",
                "name": "Atlas Bot",
                "command": ["python", "bots/atlas.py"],
                "working_directory": ".",
                "environment": {"PYTHONUNBUFFERED": "1"},
                "enabled": True,
                "auto_restart": False,
            },
        )
        self._write_manifest(
            "notifier.json",
            {
                "id": "notifier",
                "name": "Notifier Bot",
                "command": "python bots/notify.py --verbose",
            },
        )

        discovered = bot_registry_module.discover()
        self.assertEqual(len(discovered), 2)

        bot1 = discovered[0]
        self.assertEqual(bot1.id, "atlas")
        self.assertEqual(bot1.name, "Atlas Bot")
        self.assertEqual(bot1.command, ["python", "bots/atlas.py"])
        self.assertEqual(bot1.working_directory, config.PROJECT_ROOT)
        self.assertEqual(bot1.environment, {"PYTHONUNBUFFERED": "1"})
        self.assertTrue(bot1.enabled)
        self.assertFalse(bot1.auto_restart)

        bot2 = discovered[1]
        self.assertEqual(bot2.id, "notifier")
        self.assertEqual(bot2.name, "Notifier Bot")
        self.assertEqual(bot2.command, ["python", "bots/notify.py", "--verbose"])

    def test_discover_ignores_non_json_files(self) -> None:
        # Write .py script, .txt file, and .gitkeep
        (self.bots_dir / "atlas.py").write_text("print('hello')", encoding="utf-8")
        (self.bots_dir / "notes.txt").write_text("todo", encoding="utf-8")
        (self.bots_dir / ".gitkeep").write_text("", encoding="utf-8")

        discovered = bot_registry_module.discover()
        self.assertEqual(len(discovered), 0)

    def test_discover_skips_malformed_json(self) -> None:
        (self.bots_dir / "broken.json").write_text("{ unclosed json", encoding="utf-8")
        self._write_manifest(
            "valid.json",
            {"id": "valid", "name": "Valid Bot", "command": ["python", "bot.py"]},
        )
        discovered = bot_registry_module.discover()
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].id, "valid")

    def test_discover_skips_non_dict_json(self) -> None:
        (self.bots_dir / "array.json").write_text("[]", encoding="utf-8")
        (self.bots_dir / "scalar.json").write_text('"string"', encoding="utf-8")
        discovered = bot_registry_module.discover()
        self.assertEqual(len(discovered), 0)

    def test_discover_skips_missing_required_fields(self) -> None:
        self._write_manifest("missing_id.json", {"name": "No ID", "command": ["python", "bot.py"]})
        self._write_manifest("missing_name.json", {"id": "no_name", "command": ["python", "bot.py"]})
        self._write_manifest("missing_cmd.json", {"id": "no_cmd", "name": "No Command"})

        discovered = bot_registry_module.discover()
        self.assertEqual(len(discovered), 0)

    def test_discover_skips_invalid_id(self) -> None:
        for bad_id in ("bad id", "bad/id", "../bad", "", "bad@id"):
            with self.subTest(bad_id=bad_id):
                self._write_manifest(
                    "test.json",
                    {"id": bad_id, "name": "Test", "command": ["python", "bot.py"]},
                )
                discovered = bot_registry_module.discover()
                self.assertEqual(len(discovered), 0)

    def test_discover_skips_invalid_commands(self) -> None:
        for bad_cmd in ([], ["python", 123], {}, 42):
            with self.subTest(bad_cmd=bad_cmd):
                self._write_manifest(
                    "test.json",
                    {"id": "test", "name": "Test", "command": bad_cmd},
                )
                discovered = bot_registry_module.discover()
                self.assertEqual(len(discovered), 0)

    def test_discover_skips_invalid_booleans(self) -> None:
        for bad_enabled in ("true", 1, "false", 0):
            with self.subTest(bad_enabled=bad_enabled):
                self._write_manifest(
                    "test.json",
                    {
                        "id": "test",
                        "name": "Test",
                        "command": ["python", "bot.py"],
                        "enabled": bad_enabled,
                    },
                )
                discovered = bot_registry_module.discover()
                self.assertEqual(len(discovered), 0)

    def test_discover_duplicate_id_retains_first(self) -> None:
        self._write_manifest(
            "01_first.json",
            {"id": "shared_id", "name": "First Bot", "command": ["python", "bot1.py"]},
        )
        self._write_manifest(
            "02_second.json",
            {"id": "shared_id", "name": "Second Bot", "command": ["python", "bot2.py"]},
        )

        discovered = bot_registry_module.discover()
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].name, "First Bot")

    def test_get_by_id(self) -> None:
        self._write_manifest(
            "atlas.json",
            {"id": "atlas", "name": "Atlas Bot", "command": ["python", "bot.py"]},
        )
        self.assertIsNotNone(bot_registry_module.get("atlas"))
        self.assertIsNone(bot_registry_module.get("nonexistent"))


class TestBotManagerPersistence(unittest.TestCase):
    """Tests for BotManager state file loading, atomic persistence, and isolation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "temp" / "bot_manager_state.json"
        self.service_state_file = Path(self.temp_dir.name) / "temp" / "service_manager_state.json"

        self.patch_bot_state = patch.object(config, "BOT_STATE_FILE", self.state_file)
        self.patch_svc_state = patch.object(config, "SERVICE_STATE_FILE", self.service_state_file)
        self.patch_bot_state.start()
        self.patch_svc_state.start()

    def tearDown(self) -> None:
        self.patch_bot_state.stop()
        self.patch_svc_state.stop()
        self.temp_dir.cleanup()

    def test_atomic_save_and_load(self) -> None:
        manager = BotManager()
        state = BotRuntimeState(status=BotStatus.RUNNING, pid=9999, restart_count=2)
        manager._runtime_state["atlas"] = state
        manager._save_state()

        self.assertTrue(self.state_file.is_file())
        self.assertFalse(self.state_file.with_suffix(".tmp").exists())

        # Load fresh manager instance
        fresh_manager = BotManager()
        loaded_state = fresh_manager._runtime_state.get("atlas")
        self.assertIsNotNone(loaded_state)
        self.assertEqual(loaded_state.status, BotStatus.RUNNING)
        self.assertEqual(loaded_state.pid, 9999)
        self.assertEqual(loaded_state.restart_count, 2)

    def test_corrupt_state_file_handled_gracefully(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text("{ invalid json", encoding="utf-8")

        manager = BotManager()
        self.assertEqual(len(manager._runtime_state), 0)

    def test_individual_corrupt_entry_discarded_preserves_valid_entries(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "valid_bot": {"status": "STOPPED", "restart_count": 1},
            "corrupt_bot": {"status": "INVALID_STATUS"},
        }
        self.state_file.write_text(json.dumps(payload), encoding="utf-8")

        manager = BotManager()
        self.assertIn("valid_bot", manager._runtime_state)
        self.assertNotIn("corrupt_bot", manager._runtime_state)

    def test_bot_and_service_state_isolation(self) -> None:
        # Write bot state
        manager = BotManager()
        manager._runtime_state["bot1"] = BotRuntimeState(status=BotStatus.STOPPED)
        manager._save_state()

        # Verify service state file was never touched
        self.assertTrue(self.state_file.is_file())
        self.assertFalse(self.service_state_file.exists())


class TestBotManagerLifecycle(unittest.TestCase):
    """Tests for start_bot, stop_bot, restart_bot, and status checks."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "temp" / "bot_manager_state.json"
        self.log_dir = Path(self.temp_dir.name) / "logs" / "bots"

        self.patch_state = patch.object(config, "BOT_STATE_FILE", self.state_file)
        self.patch_logs = patch.object(config, "BOT_LOG_DIR", self.log_dir)
        self.patch_state.start()
        self.patch_logs.start()

        self.definition = BotDefinition(
            id="atlas",
            name="Atlas Bot",
            command=["python", "bots/atlas.py"],
            working_directory=Path(self.temp_dir.name),
            enabled=True,
        )

    def tearDown(self) -> None:
        self.patch_state.stop()
        self.patch_logs.stop()
        self.temp_dir.cleanup()

    @patch("core.service_manager.runner.start")
    @patch("core.bot_manager.manager.emit")
    def test_start_bot_success(self, mock_emit: MagicMock, mock_runner_start: MagicMock) -> None:
        identity = ProcessIdentity(pid=5555, platform="nt", start_time=1000)
        runner_state = ServiceRuntimeState(
            status=ServiceStatus.RUNNING,
            pid=5555,
            identity=identity,
            started_at=datetime.now(),
        )
        mock_runner_start.return_value = ServiceOperationResult(
            success=True,
            message="Started.",
            state=runner_state,
        )

        manager = BotManager()
        result = manager.start_bot(self.definition)

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Started.")
        self.assertIsNotNone(result.state)
        self.assertEqual(result.state.status, BotStatus.RUNNING)
        self.assertEqual(result.state.pid, 5555)

        mock_emit.assert_any_call(EventNames.BOT_START_REQUESTED, bot_id="atlas")
        mock_emit.assert_any_call(EventNames.BOT_STARTED, bot_id="atlas", pid=5555)

    def test_start_bot_already_running_refused(self) -> None:
        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(status=BotStatus.RUNNING, pid=5555)

        with patch.object(manager, "get_status", return_value=manager._runtime_state["atlas"]):
            result = manager.start_bot(self.definition)
            self.assertFalse(result.success)
            self.assertIn("already running", result.message)

    def test_start_bot_disabled_refused(self) -> None:
        disabled_def = BotDefinition(
            id="disabled",
            name="Disabled Bot",
            command=["python", "bot.py"],
            working_directory=Path(self.temp_dir.name),
            enabled=False,
        )
        manager = BotManager()
        result = manager.start_bot(disabled_def)
        self.assertFalse(result.success)
        self.assertIn("disabled", result.message)

    @patch("core.service_manager.runner.start")
    @patch("core.bot_manager.manager.emit")
    def test_start_bot_early_exit_transitions_to_failed(
        self, mock_emit: MagicMock, mock_runner_start: MagicMock
    ) -> None:
        runner_state = ServiceRuntimeState(
            status=ServiceStatus.FAILED,
            pid=None,
            identity=None,
            last_exit_code=1,
            last_error="Process exited immediately with code 1.",
        )
        mock_runner_start.return_value = ServiceOperationResult(
            success=False,
            message="Failed to start.",
            error="Process exited immediately with code 1.",
            state=runner_state,
        )

        manager = BotManager()
        result = manager.start_bot(self.definition)

        self.assertFalse(result.success)
        self.assertEqual(manager._runtime_state["atlas"].status, BotStatus.FAILED)
        self.assertEqual(manager._runtime_state["atlas"].last_exit_code, 1)
        mock_emit.assert_any_call(
            EventNames.BOT_START_FAILED, bot_id="atlas", error="Process exited immediately with code 1."
        )

    @patch("core.service_manager.runner.stop")
    @patch("core.bot_manager.manager.emit")
    def test_stop_bot_success(self, mock_emit: MagicMock, mock_runner_stop: MagicMock) -> None:
        identity = ProcessIdentity(pid=6666, platform="nt", start_time=1000)
        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING,
            pid=6666,
            identity=identity,
        )
        mock_runner_stop.return_value = ServiceOperationResult(success=True, message="Stopped.")

        with patch.object(manager, "get_status", return_value=manager._runtime_state["atlas"]):
            result = manager.stop_bot(self.definition)
            self.assertTrue(result.success)
            self.assertEqual(manager._runtime_state["atlas"].status, BotStatus.STOPPED)
            self.assertIsNotNone(manager._runtime_state["atlas"].last_stopped_at)
            mock_emit.assert_any_call(EventNames.BOT_STOPPED, bot_id="atlas")

    def test_stop_bot_already_stopped_is_noop(self) -> None:
        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(status=BotStatus.STOPPED)

        with patch.object(manager, "get_status", return_value=manager._runtime_state["atlas"]):
            result = manager.stop_bot(self.definition)
            self.assertTrue(result.success)
            self.assertIn("not running", result.message)

    @patch("core.service_manager.runner.stop")
    @patch("core.bot_manager.manager.emit")
    def test_stop_bot_failure_restores_running_state(
        self, mock_emit: MagicMock, mock_runner_stop: MagicMock
    ) -> None:
        identity = ProcessIdentity(pid=6666, platform="nt", start_time=1000)
        manager = BotManager()
        initial_state = BotRuntimeState(
            status=BotStatus.RUNNING,
            pid=6666,
            identity=identity,
        )
        manager._runtime_state["atlas"] = initial_state
        mock_runner_stop.return_value = ServiceOperationResult(
            success=False, message="Failed to stop.", error="Timeout"
        )

        with patch.object(manager, "get_status", return_value=initial_state):
            result = manager.stop_bot(self.definition)
            self.assertFalse(result.success)
            self.assertEqual(manager._runtime_state["atlas"].status, BotStatus.RUNNING)
            mock_emit.assert_any_call(EventNames.BOT_STOP_FAILED, bot_id="atlas", error="Timeout")

    def test_restart_bot_success_increments_restart_count(self) -> None:
        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING, pid=7777, restart_count=1
        )

        with patch.object(manager, "stop_bot", return_value=BotOperationResult(True, "Stopped.")) as mock_stop, \
             patch.object(manager, "start_bot", return_value=BotOperationResult(True, "Started.")) as mock_start, \
             patch.object(manager, "get_status", return_value=manager._runtime_state["atlas"]):
            result = manager.restart_bot(self.definition)
            self.assertTrue(result.success)
            self.assertEqual(manager._runtime_state["atlas"].restart_count, 2)
            mock_stop.assert_called_once()
            mock_start.assert_called_once()

    def test_restart_bot_aborts_if_stop_fails(self) -> None:
        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING, pid=7777, restart_count=1
        )

        with patch.object(manager, "stop_bot", return_value=BotOperationResult(False, "Failed", error="Stop error")), \
             patch.object(manager, "start_bot") as mock_start, \
             patch.object(manager, "get_status", return_value=manager._runtime_state["atlas"]):
            result = manager.restart_bot(self.definition)
            self.assertFalse(result.success)
            self.assertEqual(manager._runtime_state["atlas"].restart_count, 1)
            mock_start.assert_not_called()

    @patch("core.service_manager.runner.start")
    def test_start_bot_refused_when_unknown_with_preserved_pid(self, mock_runner_start: MagicMock) -> None:
        manager = BotManager()
        unknown_state = BotRuntimeState(status=BotStatus.UNKNOWN, pid=1234)
        manager._runtime_state["atlas"] = unknown_state

        with patch.object(manager, "get_status", return_value=unknown_state):
            result = manager.start_bot(self.definition)
            self.assertFalse(result.success)
            self.assertIn("UNKNOWN", result.message)
            self.assertIn("1234", result.message)
            mock_runner_start.assert_not_called()

    @patch("core.service_manager.runner.stop")
    def test_stop_bot_refused_when_unknown_with_preserved_pid_no_signal(self, mock_runner_stop: MagicMock) -> None:
        manager = BotManager()
        unknown_state = BotRuntimeState(status=BotStatus.UNKNOWN, pid=1234)
        manager._runtime_state["atlas"] = unknown_state

        with patch.object(manager, "get_status", return_value=unknown_state):
            result = manager.stop_bot(self.definition)
            self.assertFalse(result.success)
            self.assertIn("UNKNOWN", result.message)
            mock_runner_stop.assert_not_called()

    @patch("core.service_manager.runner.stop")
    @patch("core.service_manager.runner.start")
    def test_restart_bot_aborts_when_unknown_with_preserved_pid(
        self, mock_runner_start: MagicMock, mock_runner_stop: MagicMock
    ) -> None:
        manager = BotManager()
        unknown_state = BotRuntimeState(status=BotStatus.UNKNOWN, pid=1234, restart_count=2)
        manager._runtime_state["atlas"] = unknown_state

        with patch.object(manager, "get_status", return_value=unknown_state):
            result = manager.restart_bot(self.definition)
            self.assertFalse(result.success)
            self.assertEqual(manager._runtime_state["atlas"].restart_count, 2)
            mock_runner_stop.assert_not_called()
            mock_runner_start.assert_not_called()

    @patch("core.service_manager.runner.start")
    def test_start_bot_runner_failure_without_runtime_state(self, mock_runner_start: MagicMock) -> None:
        mock_runner_start.return_value = ServiceOperationResult(
            success=False,
            message="Failed to start.",
            error="Working directory does not exist.",
            state=None,
        )
        manager = BotManager()
        result = manager.start_bot(self.definition)
        self.assertFalse(result.success)
        self.assertEqual(manager._runtime_state["atlas"].status, BotStatus.FAILED)
        self.assertEqual(manager._runtime_state["atlas"].last_error, "Working directory does not exist.")


class TestBotManagerIdentityVerification(unittest.TestCase):
    """Tests for get_status identity checks and recycled-PID protection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "temp" / "bot_manager_state.json"
        self.patch_state = patch.object(config, "BOT_STATE_FILE", self.state_file)
        self.patch_state.start()

        self.definition = BotDefinition(
            id="atlas",
            name="Atlas Bot",
            command=["python", "bots/atlas.py"],
            working_directory=Path(self.temp_dir.name),
        )

    def tearDown(self) -> None:
        self.patch_state.stop()
        self.temp_dir.cleanup()

    def test_status_no_pid_returns_stored_state(self) -> None:
        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(status=BotStatus.STOPPED)
        state = manager.get_status(self.definition)
        self.assertEqual(state.status, BotStatus.STOPPED)
        self.assertIsNone(state.pid)

    def test_status_legacy_state_no_identity_marks_unknown_clears_pid(self) -> None:
        manager = BotManager()
        # Stored state has PID 8888 but identity=None
        manager._runtime_state["atlas"] = BotRuntimeState(status=BotStatus.RUNNING, pid=8888, identity=None)

        state = manager.get_status(self.definition)
        self.assertEqual(state.status, BotStatus.UNKNOWN)
        self.assertIsNone(state.pid)
        self.assertIn("Legacy state", state.last_error)

    @patch("core.service_manager.runner.verify_process_identity")
    def test_status_match_maintains_running(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = IdentityVerificationResult.MATCH
        identity = ProcessIdentity(pid=8888, platform="nt", start_time=1000)

        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING, pid=8888, identity=identity
        )

        state = manager.get_status(self.definition)
        self.assertEqual(state.status, BotStatus.RUNNING)
        self.assertEqual(state.pid, 8888)

    @patch("core.service_manager.runner.verify_process_identity")
    def test_status_exited_marks_failed(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = IdentityVerificationResult.EXITED
        identity = ProcessIdentity(pid=8888, platform="nt", start_time=1000)

        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING, pid=8888, identity=identity
        )

        state = manager.get_status(self.definition)
        self.assertEqual(state.status, BotStatus.FAILED)
        self.assertIsNone(state.pid)
        self.assertIsNotNone(state.last_exit_at)

    @patch("core.service_manager.runner.verify_process_identity")
    def test_status_mismatch_marks_failed_clears_pid(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = IdentityVerificationResult.MISMATCH
        identity = ProcessIdentity(pid=8888, platform="nt", start_time=1000)

        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING, pid=8888, identity=identity
        )

        state = manager.get_status(self.definition)
        self.assertEqual(state.status, BotStatus.FAILED)
        self.assertIsNone(state.pid)
        self.assertIn("mismatch", state.last_error)

    @patch("core.service_manager.runner.verify_process_identity")
    def test_status_inconclusive_marks_unknown_preserves_pid(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = IdentityVerificationResult.INCONCLUSIVE
        identity = ProcessIdentity(pid=8888, platform="nt", start_time=1000)

        manager = BotManager()
        manager._runtime_state["atlas"] = BotRuntimeState(
            status=BotStatus.RUNNING, pid=8888, identity=identity
        )

        state = manager.get_status(self.definition)
        self.assertEqual(state.status, BotStatus.UNKNOWN)
        self.assertEqual(state.pid, 8888)  # Preserved for triage
        self.assertIn("inconclusive", state.last_error)


class TestBotManagerLogs(unittest.TestCase):
    """Tests for bounded log-tail retrieval."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name) / "logs" / "bots"
        self.patch_logs = patch.object(config, "BOT_LOG_DIR", self.log_dir)
        self.patch_logs.start()

        self.definition = BotDefinition(
            id="atlas",
            name="Atlas Bot",
            command=["python", "bots/atlas.py"],
            working_directory=Path(self.temp_dir.name),
        )

    def tearDown(self) -> None:
        self.patch_logs.stop()
        self.temp_dir.cleanup()

    def test_get_log_tail_missing_file_returns_empty(self) -> None:
        manager = BotManager()
        lines = manager.get_log_tail(self.definition)
        self.assertEqual(lines, [])

    def test_get_log_tail_bounded_lines(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.log_dir / "atlas.log"
        content = "\n".join(f"log line {i}" for i in range(1, 51))
        log_file.write_text(content, encoding="utf-8")

        manager = BotManager()
        lines = manager.get_log_tail(self.definition, max_lines=10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[-1], "log line 50")
        self.assertEqual(lines[0], "log line 41")


class TestBotManagerRefinements(unittest.TestCase):
    """Tests for protocol compatibility, UI badge formatting, and hardened launch cleanup."""

    def test_bot_page_format_status_badge(self) -> None:
        for status in BotStatus:
            state = BotRuntimeState(status=status)
            self.assertEqual(_format_status_badge(state), status.value)

    def test_runner_process_launchable_protocol_compatibility(self) -> None:
        bot_def = BotDefinition(
            id="bot1",
            name="Bot One",
            command=["python", "main.py"],
            working_directory=Path("/tmp"),
            environment={"A": "1"},
        )
        svc_def = ServiceDefinition(
            id="svc1",
            name="Service One",
            command=["python", "svc.py"],
            working_directory=Path("/tmp"),
            environment={"B": "2"},
        )
        self.assertIsInstance(bot_def, ProcessLaunchable)
        self.assertIsInstance(svc_def, ProcessLaunchable)

        class IncompleteDefinition:
            id = "incomplete"
            name = "Incomplete"

        self.assertNotIsInstance(IncompleteDefinition(), ProcessLaunchable)

    @patch("subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity", return_value=None)
    @patch("core.service_manager.runner.time.sleep")
    def test_runner_require_identity_terminates_unmanaged_process(
        self, mock_sleep: MagicMock, mock_capture: MagicMock, mock_popen: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 9876
        # poll() returns None during grace-period check, then -15 after terminate
        mock_proc.poll.side_effect = [None, -15]
        mock_popen.return_value = mock_proc

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            defn = BotDefinition(
                id="test_bot",
                name="Test Bot",
                command=["python", "-c", "pass"],
                working_directory=tmp_path,
            )
            result = runner.start(defn, log_path=tmp_path / "test.log", require_identity=True)

            self.assertFalse(result.success)
            self.assertIn("Failed to capture process identity", result.error)
            self.assertEqual(result.state.status, ServiceStatus.FAILED)
            self.assertIsNone(result.state.pid)
            self.assertEqual(result.state.last_exit_code, -15)
            mock_proc.terminate.assert_called_once()
            mock_proc.wait.assert_called()

    @patch("subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity", return_value=None)
    @patch("core.service_manager.runner.time.sleep")
    def test_runner_require_identity_terminate_oserror_unconfirmed_exit_preserves_pid(
        self, mock_sleep: MagicMock, mock_capture: MagicMock, mock_popen: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 9876
        mock_proc.poll.side_effect = [None, None]  # Still alive after terminate fails
        mock_proc.terminate.side_effect = OSError("Permission denied")
        mock_popen.return_value = mock_proc

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            defn = BotDefinition(
                id="test_bot",
                name="Test Bot",
                command=["python", "-c", "pass"],
                working_directory=tmp_path,
            )
            result = runner.start(defn, log_path=tmp_path / "test.log", require_identity=True)

            self.assertFalse(result.success)
            self.assertIn("could not be confirmed", result.error)
            self.assertEqual(result.state.status, ServiceStatus.UNKNOWN)
            self.assertEqual(result.state.pid, 9876)
            self.assertIsNone(result.state.last_exit_code)
            mock_proc.terminate.assert_called_once()

    @patch("subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity", return_value=None)
    @patch("core.service_manager.runner.time.sleep")
    def test_runner_require_identity_escalates_to_kill_on_timeout(
        self, mock_sleep: MagicMock, mock_capture: MagicMock, mock_popen: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 9876
        mock_proc.poll.side_effect = [None, -9]  # Alive at start, dead after kill
        # First wait times out on terminate, second wait succeeds on kill
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=1.0), 0]
        mock_popen.return_value = mock_proc

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            defn = BotDefinition(
                id="test_bot",
                name="Test Bot",
                command=["python", "-c", "pass"],
                working_directory=tmp_path,
            )
            result = runner.start(defn, log_path=tmp_path / "test.log", require_identity=True)

            self.assertFalse(result.success)
            self.assertEqual(result.state.status, ServiceStatus.FAILED)
            self.assertIsNone(result.state.pid)
            self.assertEqual(result.state.last_exit_code, -9)
            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_called_once()

    @patch("subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity", return_value=None)
    @patch("core.service_manager.runner.time.sleep")
    def test_runner_require_identity_kill_oserror_preserves_pid(
        self, mock_sleep: MagicMock, mock_capture: MagicMock, mock_popen: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 9876
        mock_proc.poll.side_effect = [None, None]  # Still alive
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1.0)
        mock_proc.kill.side_effect = OSError("Kill failed")
        mock_popen.return_value = mock_proc

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            defn = BotDefinition(
                id="test_bot",
                name="Test Bot",
                command=["python", "-c", "pass"],
                working_directory=tmp_path,
            )
            result = runner.start(defn, log_path=tmp_path / "test.log", require_identity=True)

            self.assertFalse(result.success)
            self.assertIn("could not be confirmed", result.error)
            self.assertEqual(result.state.status, ServiceStatus.UNKNOWN)
            self.assertEqual(result.state.pid, 9876)
            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_called_once()

    @patch("subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity", return_value=None)
    @patch("core.service_manager.runner.time.sleep")
    def test_runner_require_identity_second_wait_timeout_preserves_pid(
        self, mock_sleep: MagicMock, mock_capture: MagicMock, mock_popen: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 9876
        mock_proc.poll.side_effect = [None, None]  # Still alive after both timeouts
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=1.0),
            subprocess.TimeoutExpired(cmd="test", timeout=1.0),
        ]
        mock_popen.return_value = mock_proc

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            defn = BotDefinition(
                id="test_bot",
                name="Test Bot",
                command=["python", "-c", "pass"],
                working_directory=tmp_path,
            )
            result = runner.start(defn, log_path=tmp_path / "test.log", require_identity=True)

            self.assertFalse(result.success)
            self.assertIn("could not be confirmed", result.error)
            self.assertEqual(result.state.status, ServiceStatus.UNKNOWN)
            self.assertEqual(result.state.pid, 9876)
            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_called_once()

    def test_unconfirmed_cleanup_preserves_safe_non_duplicate_starting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = Path(tmp_dir) / "bot_state.json"
            log_dir = Path(tmp_dir) / "logs"
            with patch.object(config, "BOT_STATE_FILE", state_file), \
                 patch.object(config, "BOT_LOG_DIR", log_dir):
                defn = BotDefinition(
                    id="unconfirmed_bot",
                    name="Unconfirmed Bot",
                    command=["python", "-c", "pass"],
                    working_directory=Path(tmp_dir),
                )
                manager = BotManager()

                unconfirmed_runner_state = ServiceRuntimeState(
                    status=ServiceStatus.UNKNOWN,
                    pid=7711,
                    identity=None,
                    last_error="Process termination could not be confirmed.",
                )
                runner_failure = ServiceOperationResult(
                    success=False,
                    message="Failed to start.",
                    error="Process termination could not be confirmed.",
                    state=unconfirmed_runner_state,
                )

                with patch("core.service_manager.runner.start", return_value=runner_failure):
                    result1 = manager.start_bot(defn)

                self.assertFalse(result1.success)
                self.assertEqual(manager._runtime_state["unconfirmed_bot"].status, BotStatus.UNKNOWN)
                self.assertEqual(manager._runtime_state["unconfirmed_bot"].pid, 7711)

                # Attempt second start: must be refused to prevent duplicate spawn
                with patch("core.service_manager.runner.start") as mock_second_start:
                    result2 = manager.start_bot(defn)
                    self.assertFalse(result2.success)
                    self.assertIn("UNKNOWN", result2.message)
                    self.assertIn("7711", result2.message)
                    mock_second_start.assert_not_called()

                # Attempt stop: must be refused to prevent bare-PID signalling
                with patch("core.service_manager.runner.stop") as mock_stop:
                    result3 = manager.stop_bot(defn)
                    self.assertFalse(result3.success)
                    self.assertIn("UNKNOWN", result3.message)
                    mock_stop.assert_not_called()

                # Attempt restart: must abort
                result4 = manager.restart_bot(defn)
                self.assertFalse(result4.success)
                self.assertEqual(manager._runtime_state["unconfirmed_bot"].restart_count, 0)
                self.assertEqual(manager._runtime_state["unconfirmed_bot"].pid, 7711)


if __name__ == "__main__":
    unittest.main()
