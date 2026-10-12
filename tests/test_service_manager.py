"""
tests/test_service_manager.py
=============================
Isolated unit tests for the Day-6 Service Manager reliability fixes.

All tests use only stdlib (unittest + unittest.mock) and tempfile.
No external services are touched, no real state files are read or
written, and no Win32 APIs or /proc paths are accessed directly -
all platform-specific paths are exercised through mocked ctypes /
mock_open so the suite runs on any platform.

Coverage:
    TestParsePid
        - None is accepted and returned as-is
        - Valid positive integers are accepted
        - Zero is rejected
        - Negative integers are rejected
        - Booleans are rejected (even though bool is a subclass of int)
        - Floats are rejected
        - Strings are rejected

    TestIsRunningWindows
        - WAIT_TIMEOUT (258) → True (process is alive)
        - WAIT_OBJECT_0 (0)  → False (process has terminated)
        - WAIT_FAILED        → False (conservative / safe default)
        - Unexpected value   → False
        - OpenProcess returns 0 (NULL) → False (no such process)
        - CloseHandle is always called when a handle was obtained

    TestStartGracePeriodTimestamp
        - When a process exits within the grace period, the resulting
          ServiceRuntimeState.last_exit_at is a non-None datetime close
          to the current time
        - runner.config.SERVICE_LOG_DIR is patched to a dedicated temporary
          directory for every test calling runner.start(), ensuring no
          writes occur to the global temporary directory or Citrus logs

    TestProcessIdentitySerialization
        - ProcessIdentity round-trips through to_persisted_dict / from_persisted_dict
        - Backward compatibility: old state files without "identity" key load cleanly
        - Invalid identity dict fields are silently dropped (identity=None)

    TestParseProcStat
        - Normal /proc/<pid>/stat content is parsed correctly
        - Process names containing spaces are handled via rfind(')')
        - Process names containing nested parentheses are handled correctly
        - Zombie state 'Z' is extracted correctly
        - Missing closing paren returns None
        - Too-few right-side fields returns None
        - Non-integer starttime returns None

    TestVerifyIdentityPosix
        - MATCH when starttime matches
        - MISMATCH when starttime differs
        - EXITED when /proc/<pid>/stat is missing (FileNotFoundError)
        - EXITED when state is 'Z' (zombie)
        - INCONCLUSIVE when /proc read raises PermissionError
        - INCONCLUSIVE when stat content is malformed
        - Boot-ID mismatch immediately returns MISMATCH
        - Boot-ID only available on one side does not prevent starttime check

    TestVerifyIdentityWindows
        - MATCH when creation time matches
        - MISMATCH when creation time differs
        - EXITED when OpenProcess returns NULL
        - INCONCLUSIVE when GetProcessTimes fails
        - Handle is always closed after verification

    TestStopIdentityRefusal
        - stop() refuses (success=False) when identity is None
        - stop() refuses on MISMATCH
        - stop() refuses on INCONCLUSIVE
        - stop() treats EXITED as already-stopped (success=True)
        - stop() proceeds to terminate on MATCH

    TestStartIdentityCapture
        - RUNNING state contains the identity returned by _capture_identity
        - When _capture_identity returns None, identity=None in RUNNING state
        - FAILED state (grace-period exit) always has identity=None
        - Log message notes whether identity was captured

    TestSendSignalPosix
        - pgid == pid → os.killpg is called with that pgid
        - pgid != pid → os.killpg is NOT called (no bare-PID fallback)
        - ProcessLookupError on getpgid → returns quietly, no kill
        - Generic OSError on getpgid → returns quietly, no kill
"""

import sys
import types
import unittest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path FIRST so ``import core.*`` resolves
# to the real package tree, not some interpreter default.
# unittest discover (with -s tests) inserts the start-directory into
# sys.path[0], which can shadow the project root; we insert the root at
# position 0 so it takes precedence.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)

# ---------------------------------------------------------------------------
# core/citrus.py removes 'core/' from sys.path at startup to avoid the
# local core/logging package shadowing stdlib logging.  The tests don't run
# citrus.py, so we must apply the same guard here: remove any entry that
# points inside core/ so that ``import logging`` resolves to the stdlib.
# ---------------------------------------------------------------------------
_CORE_DIR_STR = str(_PROJECT_ROOT / "core")
sys.path = [p for p in sys.path if p != _CORE_DIR_STR]

# ---------------------------------------------------------------------------
# Install lightweight stubs for the two leaf modules that runner.py /
# models.py import but that drag in the full application stack.
# We stub only the leaf modules, not the 'core' package itself, so the
# real core.__init__ and core.service_manager sub-packages load normally.
#
# Stubs must be installed BEFORE the modules under test are imported.
# ---------------------------------------------------------------------------
import importlib  # noqa: E402  (after sys.path setup)


def _stub_if_absent(module_name: str, attrs: dict) -> None:
    """Register a minimal stub module in sys.modules if not already loaded."""
    if module_name in sys.modules:
        return
    mod = types.ModuleType(module_name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[module_name] = mod


# core.logging.logger — provides get_logger(); used by runner.py
_stub_if_absent(
    "core.logging.logger",
    {"get_logger": lambda name: MagicMock()},
)

# core.config — provides SERVICE_LOG_DIR and ensure_service_runtime_directories()
# Points to an uncreated sentinel directory under tempfile.gettempdir() so that
# any test invoking runner.start() without explicitly patching SERVICE_LOG_DIR
# fails immediately instead of polluting the global temporary directory.
_stub_if_absent(
    "core.config",
    {
        "SERVICE_LOG_DIR": Path(tempfile.gettempdir()) / "citrus_unpatched_test_logs_sentinel",
        "ensure_service_runtime_directories": lambda: None,
    },
)

# ---------------------------------------------------------------------------
# Now it is safe to import the modules under test.
# ---------------------------------------------------------------------------
from core.service_manager.models import (  # noqa: E402
    ProcessIdentity,
    _parse_pid,
    _parse_process_identity,
    ServiceDefinition,
    ServiceRuntimeState,
    ServiceStatus,
)
from core.service_manager import runner  # noqa: E402
from core.service_manager.runner import (  # noqa: E402
    IdentityVerificationResult,
    _parse_proc_stat,
    _configure_kernel32,
    _get_kernel32,
)


# ===========================================================================
# TestParsePid
# ===========================================================================
class TestParsePid(unittest.TestCase):
    """Verify _parse_pid() rejects corrupt values and accepts valid ones."""

    # ---- accepted values ----

    def test_none_is_accepted(self) -> None:
        self.assertIsNone(_parse_pid(None))

    def test_positive_integer_is_accepted(self) -> None:
        self.assertEqual(_parse_pid(1), 1)
        self.assertEqual(_parse_pid(99999), 99999)
        self.assertEqual(_parse_pid(1_000_000), 1_000_000)

    # ---- rejected values ----

    def test_zero_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_pid(0)

    def test_negative_integer_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_pid(-1)
        with self.assertRaises(ValueError):
            _parse_pid(-9999)

    def test_boolean_true_is_rejected(self) -> None:
        # True == 1 in Python, but bool is not an acceptable pid type.
        with self.assertRaises(ValueError):
            _parse_pid(True)

    def test_boolean_false_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_pid(False)

    def test_float_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_pid(1234.0)

    def test_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_pid("1234")

    def test_string_none_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_pid("None")

    def test_from_persisted_dict_rejects_invalid_pid(self) -> None:
        """from_persisted_dict() propagates ValueError for a bad pid."""
        entry = {
            "status": "STOPPED",
            "pid": -5,
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
        }
        with self.assertRaises(ValueError):
            ServiceRuntimeState.from_persisted_dict(entry)

    def test_from_persisted_dict_accepts_null_pid(self) -> None:
        """from_persisted_dict() treats null pid as None (normal state)."""
        entry = {
            "status": "STOPPED",
            "pid": None,
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
        }
        state = ServiceRuntimeState.from_persisted_dict(entry)
        self.assertIsNone(state.pid)

    def test_from_persisted_dict_accepts_valid_pid(self) -> None:
        """from_persisted_dict() accepts a positive-integer pid."""
        entry = {
            "status": "RUNNING",
            "pid": 42,
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
        }
        state = ServiceRuntimeState.from_persisted_dict(entry)
        self.assertEqual(state.pid, 42)


# ===========================================================================
# TestIsRunningWindows
# ===========================================================================
class TestIsRunningWindows(unittest.TestCase):
    """Verify _is_running_windows() interprets WaitForSingleObject correctly.

    All Win32 API calls are mocked so these tests run on any platform.
    """

    # Win32 constants (mirrored from runner.py to keep tests self-contained)
    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_TIMEOUT = 0x00000102
    _WAIT_FAILED = 0xFFFFFFFF

    def _make_kernel32_mock(
        self,
        open_handle: int,
        wait_result: int,
    ) -> MagicMock:
        """Build a minimal kernel32 mock for one _is_running_windows call."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = open_handle
        k32.WaitForSingleObject.return_value = wait_result
        k32.CloseHandle.return_value = True
        return k32

    @patch("ctypes.windll")
    def test_wait_timeout_means_running(self, mock_windll: MagicMock) -> None:
        """WAIT_TIMEOUT (0x102) → process is alive → True."""
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=1234, wait_result=self._WAIT_TIMEOUT
        )
        self.assertTrue(runner._is_running_windows(42))

    @patch("ctypes.windll")
    def test_wait_object_0_means_terminated(self, mock_windll: MagicMock) -> None:
        """WAIT_OBJECT_0 (0x0) → process has exited → False."""
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=1234, wait_result=self._WAIT_OBJECT_0
        )
        self.assertFalse(runner._is_running_windows(42))

    @patch("ctypes.windll")
    def test_wait_failed_returns_false(self, mock_windll: MagicMock) -> None:
        """WAIT_FAILED (0xFFFFFFFF) → conservative False, not an exception."""
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=1234, wait_result=self._WAIT_FAILED
        )
        self.assertFalse(runner._is_running_windows(42))

    @patch("ctypes.windll")
    def test_unexpected_wait_result_returns_false(self, mock_windll: MagicMock) -> None:
        """Any other WaitForSingleObject value → False (safe default)."""
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=1234, wait_result=0xDEAD
        )
        self.assertFalse(runner._is_running_windows(42))

    @patch("ctypes.windll")
    def test_open_process_null_returns_false(self, mock_windll: MagicMock) -> None:
        """OpenProcess → NULL (0) → no such/accessible process → False."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0
        mock_windll.kernel32 = k32
        self.assertFalse(runner._is_running_windows(99))
        k32.WaitForSingleObject.assert_not_called()
        k32.CloseHandle.assert_not_called()

    @patch("ctypes.windll")
    def test_close_handle_called_when_open_succeeds(self, mock_windll: MagicMock) -> None:
        """CloseHandle must be called exactly once when OpenProcess succeeded."""
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=7, wait_result=self._WAIT_TIMEOUT
        )
        runner._is_running_windows(55)
        mock_windll.kernel32.CloseHandle.assert_called_once_with(7)

    @patch("ctypes.windll")
    def test_close_handle_called_on_terminated(self, mock_windll: MagicMock) -> None:
        """CloseHandle is also called when the process is found terminated."""
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=8, wait_result=self._WAIT_OBJECT_0
        )
        runner._is_running_windows(56)
        mock_windll.kernel32.CloseHandle.assert_called_once_with(8)

    @patch("ctypes.windll")
    def test_correct_access_flags_requested(self, mock_windll: MagicMock) -> None:
        """OpenProcess must request QUERY_LIMITED_INFO | SYNCHRONIZE."""
        expected_access = runner._WIN_PROCESS_QUERY_LIMITED_INFORMATION | runner._WIN_SYNCHRONIZE
        k32 = self._make_kernel32_mock(open_handle=1, wait_result=self._WAIT_TIMEOUT)
        mock_windll.kernel32 = k32
        runner._is_running_windows(10)
        actual_access = k32.OpenProcess.call_args[0][0]
        self.assertEqual(actual_access, expected_access)

    @patch("ctypes.windll")
    def test_exit_code_259_is_correctly_detected_as_terminated(
        self, mock_windll: MagicMock
    ) -> None:
        """Regression: a process that legitimately exits with code 259 must be
        reported as terminated (WAIT_OBJECT_0), not as running.

        Under the old GetExitCodeProcess == 259 logic, this would have
        returned True (false-positive). The new WaitForSingleObject-based
        implementation correctly returns WAIT_OBJECT_0 for a terminated
        process, regardless of its exit code.
        """
        # Simulate OS: process exited with code 259.
        # WaitForSingleObject returns WAIT_OBJECT_0 (terminated), not WAIT_TIMEOUT.
        mock_windll.kernel32 = self._make_kernel32_mock(
            open_handle=9, wait_result=self._WAIT_OBJECT_0
        )
        self.assertFalse(runner._is_running_windows(259))


# ===========================================================================
# TestStartGracePeriodTimestamp
# ===========================================================================
class TestStartGracePeriodTimestamp(unittest.TestCase):
    """Verify runner.start() populates last_exit_at when a process exits
    immediately during the grace period, and verify strict filesystem isolation
    for service log output."""

    def _make_definition(self, tmp_dir: Path) -> ServiceDefinition:
        """Return a minimal ServiceDefinition pointing at a real temp dir."""
        return ServiceDefinition(
            id="test_svc",
            name="Test Service",
            command=["python", "-c", "import sys; sys.exit(1)"],
            working_directory=tmp_dir,
        )

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    def test_last_exit_at_is_set_on_immediate_failure(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        """last_exit_at must be a non-None datetime when exit_code is not None."""
        # Simulate a process that exits immediately (poll() returns an exit code).
        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.pid = 99
        mock_popen.return_value = mock_process

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            definition = self._make_definition(tmp_path)
            before = datetime.now()
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(definition)
            after = datetime.now()

            self.assertTrue((log_dir / f"{definition.id}.log").is_file())

        self.assertFalse(result.success)
        self.assertIsNotNone(result.state)
        self.assertEqual(result.state.status, ServiceStatus.FAILED)
        self.assertIsNotNone(result.state.last_exit_at)
        # The timestamp must lie within the test's own execution window.
        self.assertGreaterEqual(result.state.last_exit_at, before)
        self.assertLessEqual(result.state.last_exit_at, after)
        self.assertFalse((Path(tempfile.gettempdir()) / "test_svc.log").exists())
        self.assertFalse((_PROJECT_ROOT / "logs" / "services" / "test_svc.log").exists())

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    def test_last_exit_at_is_none_when_process_stays_running(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        """last_exit_at must be None when the process survives the grace period."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None   # still running
        mock_process.pid = 100
        mock_popen.return_value = mock_process

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            definition = self._make_definition(tmp_path)
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(definition)

            self.assertTrue((log_dir / f"{definition.id}.log").is_file())

        self.assertTrue(result.success)
        self.assertIsNotNone(result.state)
        self.assertEqual(result.state.status, ServiceStatus.RUNNING)
        self.assertIsNone(result.state.last_exit_at)
        self.assertFalse((Path(tempfile.gettempdir()) / "test_svc.log").exists())
        self.assertFalse((_PROJECT_ROOT / "logs" / "services" / "test_svc.log").exists())

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    def test_last_exit_code_is_captured(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        """last_exit_code must reflect the actual exit code from poll()."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 42
        mock_process.pid = 101
        mock_popen.return_value = mock_process

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            definition = self._make_definition(tmp_path)
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(definition)

            self.assertTrue((log_dir / f"{definition.id}.log").is_file())

        self.assertEqual(result.state.last_exit_code, 42)
        self.assertFalse((Path(tempfile.gettempdir()) / "test_svc.log").exists())
        self.assertFalse((_PROJECT_ROOT / "logs" / "services" / "test_svc.log").exists())

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    def test_start_strictly_isolated_from_global_and_citrus_directories(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock
    ) -> None:
        """Confirm runner.start writes only to the dedicated temporary log dir
        and does not touch the global temp directory or Citrus runtime logs."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 102
        mock_popen.return_value = mock_process

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "isolated_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            definition = self._make_definition(tmp_path)

            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(definition)

            self.assertTrue(result.success)
            self.assertTrue((log_dir / f"{definition.id}.log").is_file())

        self.assertFalse((Path(tempfile.gettempdir()) / "test_svc.log").exists())
        self.assertFalse((_PROJECT_ROOT / "logs" / "services" / "test_svc.log").exists())
        self.assertFalse((_PROJECT_ROOT / "logs" / "test_svc.log").exists())


if __name__ == "__main__":
    unittest.main()


# ===========================================================================
# Process identity tests
# ===========================================================================

# Helper: build a minimal /proc/<pid>/stat line.
def _make_stat_line(pid: int = 1234, name: str = "myproc", state: str = "S",
                    starttime: int = 987654) -> str:
    """Produce a /proc/<pid>/stat string with the given fields.

    The post-paren token layout expected by _parse_proc_stat:
        [0]  state
        [1..18] padding (18 dummy values)
        [19] starttime
    """
    # 18 dummy values so that starttime lands at index 19 (0-indexed).
    padding = " ".join(str(i) for i in range(1, 19))  # 18 values → indices 1-18
    return f"{pid} ({name}) {state} {padding} {starttime} extra1 extra2"


# ===========================================================================
# TestProcessIdentitySerialization
# ===========================================================================
class TestProcessIdentitySerialization(unittest.TestCase):
    """Verify ProcessIdentity round-trips through ServiceRuntimeState persistence."""

    def _make_running_state(self, identity):
        return ServiceRuntimeState(
            status=ServiceStatus.RUNNING,
            pid=identity.pid if identity is not None else 1234,
            identity=identity,
        )

    def test_identity_round_trips_posix(self) -> None:
        """A POSIX ProcessIdentity survives to_persisted_dict/from_persisted_dict."""
        identity = ProcessIdentity(
            pid=1234, platform="posix", start_time=98765, boot_id="abc-123"
        )
        state = self._make_running_state(identity)
        d = state.to_persisted_dict()
        self.assertIsNotNone(d["identity"])
        self.assertEqual(d["identity"]["pid"], 1234)
        self.assertEqual(d["identity"]["platform"], "posix")
        self.assertEqual(d["identity"]["start_time"], 98765)
        self.assertEqual(d["identity"]["boot_id"], "abc-123")

        restored = ServiceRuntimeState.from_persisted_dict(d)
        self.assertIsNotNone(restored.identity)
        self.assertEqual(restored.identity.pid, 1234)
        self.assertEqual(restored.identity.platform, "posix")
        self.assertEqual(restored.identity.start_time, 98765)
        self.assertEqual(restored.identity.boot_id, "abc-123")

    def test_identity_round_trips_windows(self) -> None:
        """A Windows ProcessIdentity (no boot_id) survives persistence."""
        identity = ProcessIdentity(
            pid=5678, platform="nt", start_time=132700000000000, boot_id=None
        )
        state = self._make_running_state(identity)
        d = state.to_persisted_dict()
        self.assertIsNone(d["identity"]["boot_id"])

        restored = ServiceRuntimeState.from_persisted_dict(d)
        self.assertIsNone(restored.identity.boot_id)
        self.assertEqual(restored.identity.start_time, 132700000000000)

    def test_none_identity_serializes_to_null(self) -> None:
        """identity=None is serialized as a null JSON value."""
        state = self._make_running_state(None)
        d = state.to_persisted_dict()
        self.assertIsNone(d["identity"])

    def test_missing_identity_key_loads_as_none(self) -> None:
        """A state dict without an 'identity' key (legacy) loads with identity=None."""
        entry = {
            "status": "RUNNING",
            "pid": 42,
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
            # No "identity" key - simulates legacy state file without identity
        }
        state = ServiceRuntimeState.from_persisted_dict(entry)
        self.assertIsNone(state.identity)

    def test_null_identity_key_loads_as_none(self) -> None:
        """An explicit null identity value loads as identity=None."""
        entry = {
            "status": "RUNNING",
            "pid": 42,
            "identity": None,
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
        }
        state = ServiceRuntimeState.from_persisted_dict(entry)
        self.assertIsNone(state.identity)

    def test_corrupt_identity_dict_loads_as_none(self) -> None:
        """A corrupt identity dict is silently dropped - does not raise."""
        entry = {
            "status": "STOPPED",
            "pid": None,
            "identity": {"pid": -1, "platform": "posix", "start_time": 100},
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
        }
        state = ServiceRuntimeState.from_persisted_dict(entry)
        self.assertIsNone(state.identity)  # negative pid rejected silently

    def test_mismatched_outer_and_identity_pid_drops_identity(self) -> None:
        """When outer PID and identity.pid mismatch, identity is dropped to None."""
        entry = {
            "status": "RUNNING",
            "pid": 1234,
            "identity": {
                "pid": 5678,
                "platform": "posix",
                "start_time": 98765,
                "boot_id": "abc-123",
            },
            "last_exit_code": None,
            "last_error": None,
            "last_exit_at": None,
            "last_stopped_at": None,
            "restart_count": 0,
        }
        state = ServiceRuntimeState.from_persisted_dict(entry)
        self.assertEqual(state.pid, 1234)
        self.assertIsNone(state.identity)

    def test_parse_process_identity_invalid_platform(self) -> None:
        """Unknown platform string returns None."""
        result = _parse_process_identity({
            "pid": 1, "platform": "beos", "start_time": 100
        })
        self.assertIsNone(result)

    def test_parse_process_identity_boolean_start_time(self) -> None:
        """Boolean start_time is rejected."""
        result = _parse_process_identity({
            "pid": 1, "platform": "posix", "start_time": True
        })
        self.assertIsNone(result)

    def test_parse_process_identity_non_dict_returns_none(self) -> None:
        """A non-dict value returns None."""
        self.assertIsNone(_parse_process_identity("not-a-dict"))
        self.assertIsNone(_parse_process_identity(42))
        self.assertIsNone(_parse_process_identity([1, 2]))


# ===========================================================================
# TestParseProcStat
# ===========================================================================
class TestParseProcStat(unittest.TestCase):
    """Verify _parse_proc_stat handles normal and adversarial /proc/<pid>/stat content."""

    def test_normal_line_returns_state_and_starttime(self) -> None:
        line = _make_stat_line(pid=100, name="bash", state="S", starttime=12345)
        result = _parse_proc_stat(line)
        self.assertIsNotNone(result)
        state, starttime = result
        self.assertEqual(state, "S")
        self.assertEqual(starttime, 12345)

    def test_name_with_spaces(self) -> None:
        """A process name containing spaces must not shift field indices."""
        line = _make_stat_line(pid=200, name="my service", state="R", starttime=99999)
        result = _parse_proc_stat(line)
        self.assertIsNotNone(result)
        state, starttime = result
        self.assertEqual(state, "R")
        self.assertEqual(starttime, 99999)

    def test_name_with_nested_parens(self) -> None:
        """A process name containing nested parentheses is handled via rfind(')')."""
        # The comm field is: (test (worker) proc)
        pid = 300
        starttime = 77777
        # 18 padding values → D at 0, padding at 1-18, starttime at 19
        padding = " ".join(str(i) for i in range(1, 19))
        line = f"{pid} (test (worker) proc) D {padding} {starttime} x y"
        result = _parse_proc_stat(line)
        self.assertIsNotNone(result)
        state, st = result
        self.assertEqual(state, "D")
        self.assertEqual(st, starttime)

    def test_zombie_state_is_parsed(self) -> None:
        """Zombie ('Z') processes are parsed correctly."""
        line = _make_stat_line(state="Z", starttime=11111)
        result = _parse_proc_stat(line)
        self.assertIsNotNone(result)
        state, _ = result
        self.assertEqual(state, "Z")

    def test_missing_closing_paren_returns_none(self) -> None:
        """A line without a closing paren returns None."""
        result = _parse_proc_stat("1234 (broken S 1 2 3")
        self.assertIsNone(result)

    def test_too_few_fields_returns_none(self) -> None:
        """Only 5 tokens after closing paren (need 20) returns None."""
        result = _parse_proc_stat("1234 (proc) S 1 2 3 4")
        self.assertIsNone(result)

    def test_non_integer_starttime_returns_none(self) -> None:
        """Non-integer starttime field returns None."""
        # 18 padding values → state at 0, padding at 1-18, "notanint" at 19
        padding = " ".join(str(i) for i in range(1, 19))
        line = f"1 (x) S {padding} notanint extra"
        result = _parse_proc_stat(line)
        self.assertIsNone(result)

    def test_empty_string_returns_none(self) -> None:
        result = _parse_proc_stat("")
        self.assertIsNone(result)


# ===========================================================================
# TestVerifyIdentityPosix
# ===========================================================================
class TestVerifyIdentityPosix(unittest.TestCase):
    """Verify _verify_identity_posix() classifies all cases correctly.

    All /proc reads are mocked; no real /proc is accessed.
    """

    def _make_identity(self, starttime: int = 12345, boot_id: str = None) -> ProcessIdentity:
        return ProcessIdentity(
            pid=1234, platform="posix", start_time=starttime, boot_id=boot_id
        )

    def _mock_proc_stat(self, content: str) -> "unittest.mock.MagicMock":
        """Return a Path.read_text mock that returns `content`."""
        from unittest.mock import mock_open, patch, MagicMock
        return content

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text")
    def test_match_when_starttime_equals(self, mock_read, _mock_boot) -> None:
        """MATCH when /proc/<pid>/stat starttime matches the stored baseline."""
        mock_read.return_value = _make_stat_line(pid=1234, starttime=12345)
        identity = self._make_identity(starttime=12345)
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MATCH)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text")
    def test_mismatch_when_starttime_differs(self, mock_read, _mock_boot) -> None:
        """MISMATCH when starttime differs (PID recycled)."""
        mock_read.return_value = _make_stat_line(pid=1234, starttime=99999)
        identity = self._make_identity(starttime=12345)
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MISMATCH)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text", side_effect=FileNotFoundError)
    def test_exited_when_proc_stat_missing(self, _mock_read, _mock_boot) -> None:
        """EXITED when /proc/<pid>/stat is not found."""
        result = runner._verify_identity_posix(self._make_identity())
        self.assertEqual(result, IdentityVerificationResult.EXITED)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text")
    def test_exited_when_zombie(self, mock_read, _mock_boot) -> None:
        """EXITED when /proc/<pid>/stat reports state 'Z' (zombie)."""
        mock_read.return_value = _make_stat_line(state="Z", starttime=12345)
        result = runner._verify_identity_posix(self._make_identity(starttime=12345))
        self.assertEqual(result, IdentityVerificationResult.EXITED)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text", side_effect=PermissionError)
    def test_inconclusive_on_permission_error(self, _mock_read, _mock_boot) -> None:
        """INCONCLUSIVE when /proc read raises PermissionError."""
        result = runner._verify_identity_posix(self._make_identity())
        self.assertEqual(result, IdentityVerificationResult.INCONCLUSIVE)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text", return_value="this is not valid stat content")
    def test_inconclusive_on_malformed_stat(self, _mock_read, _mock_boot) -> None:
        """INCONCLUSIVE when /proc/<pid>/stat content cannot be parsed."""
        result = runner._verify_identity_posix(self._make_identity())
        self.assertEqual(result, IdentityVerificationResult.INCONCLUSIVE)

    @patch("core.service_manager.runner._read_boot_id_posix",
           return_value="boot-id-NEW")
    def test_boot_id_mismatch_returns_mismatch_immediately(self, _mock_boot) -> None:
        """Boot-ID mismatch → MISMATCH without even reading /proc/<pid>/stat."""
        # No Path.read_text mock - if it's called, the test would raise.
        identity = self._make_identity(starttime=12345, boot_id="boot-id-OLD")
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MISMATCH)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text")
    def test_boot_id_none_stored_still_checks_starttime(self, mock_read, _mock_boot) -> None:
        """When stored boot_id is None, we skip the boot check and fall through to starttime."""
        mock_read.return_value = _make_stat_line(starttime=12345)
        identity = self._make_identity(starttime=12345, boot_id=None)
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MATCH)


# ===========================================================================
# TestVerifyIdentityWindows
# ===========================================================================
class TestVerifyIdentityWindows(unittest.TestCase):
    """Verify _verify_identity_windows() classifies all cases correctly.

    All Win32 API calls are mocked so these tests run on any platform.
    """

    # A plausible 64-bit Windows FILETIME split into hi/lo 32-bit halves.
    _START_TIME_LO = 0xDEADBEEF
    _START_TIME_HI = 0x00000001
    _START_TIME = (_START_TIME_HI << 32) | _START_TIME_LO

    def _make_identity(self, start_time: int = None) -> ProcessIdentity:
        return ProcessIdentity(
            pid=42,
            platform="nt",
            start_time=start_time if start_time is not None else self._START_TIME,
            boot_id=None,
        )

    def _make_kernel32(self, open_handle, get_times_success: bool,
                       creation_lo: int = None, creation_hi: int = None):
        """Return a minimal kernel32 mock for _verify_identity_windows."""
        from unittest.mock import MagicMock
        import ctypes

        k32 = MagicMock()
        k32.OpenProcess.return_value = open_handle

        def fake_get_times(handle, pCreation, pExit, pKernel, pUser):
            if not get_times_success:
                return 0
            # Write into the ctypes.byref(creation) argument.
            pCreation._obj.dwLowDateTime = creation_lo or self._START_TIME_LO
            pCreation._obj.dwHighDateTime = creation_hi or self._START_TIME_HI
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times
        k32.CloseHandle.return_value = True
        return k32

    @patch("ctypes.windll")
    def test_match_when_creation_time_equals(self, mock_windll) -> None:
        """MATCH when GetProcessTimes creation time matches stored start_time and process is running."""
        from core.service_manager.runner import _FILETIME

        k32 = MagicMock()
        k32.OpenProcess.return_value = 99

        def fake_get_times(handle, pCreation, pExit, pKernel, pUser):
            pCreation._obj.dwLowDateTime = self._START_TIME_LO
            pCreation._obj.dwHighDateTime = self._START_TIME_HI
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times
        k32.WaitForSingleObject.return_value = runner._WIN_WAIT_TIMEOUT
        mock_windll.kernel32 = k32

        identity = self._make_identity(start_time=self._START_TIME)
        result = runner._verify_identity_windows(identity)
        self.assertEqual(result, IdentityVerificationResult.MATCH)
        k32.CloseHandle.assert_called_once_with(99)

    @patch("ctypes.windll")
    def test_signalled_process_handle_recognized_as_exited(self, mock_windll) -> None:
        """A genuinely signalled process handle (WAIT_OBJECT_0) is recognized as EXITED."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 66

        def fake_get_times(handle, pCreation, pExit, pKernel, pUser):
            pCreation._obj.dwLowDateTime = self._START_TIME_LO
            pCreation._obj.dwHighDateTime = self._START_TIME_HI
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times
        k32.WaitForSingleObject.return_value = runner._WIN_WAIT_OBJECT_0
        mock_windll.kernel32 = k32

        identity = self._make_identity(start_time=self._START_TIME)
        result = runner._verify_identity_windows(identity)
        self.assertEqual(result, IdentityVerificationResult.EXITED)
        k32.CloseHandle.assert_called_once_with(66)

    @patch("ctypes.windll")
    def test_mismatch_when_creation_time_differs(self, mock_windll) -> None:
        """MISMATCH when GetProcessTimes returns a different creation time."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 88

        def fake_get_times(handle, pCreation, pExit, pKernel, pUser):
            pCreation._obj.dwLowDateTime = 0x11111111
            pCreation._obj.dwHighDateTime = 0x22222222
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times
        mock_windll.kernel32 = k32

        identity = self._make_identity(start_time=self._START_TIME)
        result = runner._verify_identity_windows(identity)
        self.assertEqual(result, IdentityVerificationResult.MISMATCH)
        k32.CloseHandle.assert_called_once_with(88)

    @patch("ctypes.windll")
    def test_exited_when_open_process_null(self, mock_windll) -> None:
        """EXITED when OpenProcess returns NULL with ERROR_INVALID_PARAMETER (process doesn't exist)."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0  # NULL
        k32.GetLastError.return_value = 87  # ERROR_INVALID_PARAMETER
        mock_windll.kernel32 = k32

        result = runner._verify_identity_windows(self._make_identity())
        self.assertEqual(result, IdentityVerificationResult.EXITED)
        k32.CloseHandle.assert_not_called()

    @patch("ctypes.windll")
    def test_inconclusive_when_get_process_times_fails(self, mock_windll) -> None:
        """INCONCLUSIVE when GetProcessTimes returns 0 (API failure)."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 77
        k32.GetProcessTimes.return_value = 0  # failure
        mock_windll.kernel32 = k32

        result = runner._verify_identity_windows(self._make_identity())
        self.assertEqual(result, IdentityVerificationResult.INCONCLUSIVE)
        # Handle must be closed even on API failure.
        k32.CloseHandle.assert_called_once_with(77)

    @patch("ctypes.windll")
    def test_handle_closed_on_match(self, mock_windll) -> None:
        """CloseHandle is called exactly once when verification succeeds."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 55

        def fake_get_times(handle, pCreation, pExit, pKernel, pUser):
            pCreation._obj.dwLowDateTime = self._START_TIME_LO
            pCreation._obj.dwHighDateTime = self._START_TIME_HI
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times
        k32.WaitForSingleObject.return_value = runner._WIN_WAIT_TIMEOUT
        mock_windll.kernel32 = k32

        runner._verify_identity_windows(self._make_identity(start_time=self._START_TIME))
        k32.CloseHandle.assert_called_once_with(55)


# ===========================================================================
# TestStopIdentityRefusal
# ===========================================================================
class TestStopIdentityRefusal(unittest.TestCase):
    """Verify stop() refuses to signal without a verified identity baseline.

    stop() now dispatches to _stop_posix or _stop_windows after the
    input-consistency checks.  These tests patch the dispatcher to
    avoid touching real OS primitives while still exercising the
    refusal logic in stop() itself.
    """

    def _make_identity(self, pid: int = 1234) -> ProcessIdentity:
        """Build a ProcessIdentity with the platform that matches the running OS."""
        platform = "nt" if runner._IS_WINDOWS else "posix"
        return ProcessIdentity(pid=pid, platform=platform, start_time=12345)

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._stop_posix")
    def test_stop_proceeds_on_match(self, mock_stop_posix, mock_stop_windows) -> None:
        """After passing input checks, stop() dispatches to the platform helper."""
        from core.service_manager.models import ServiceOperationResult
        expected = ServiceOperationResult(success=True, message="Stopped.")
        if runner._IS_WINDOWS:
            mock_stop_windows.return_value = expected
        else:
            mock_stop_posix.return_value = expected

        result = runner.stop(1234, self._make_identity(), timeout=0.0)
        self.assertTrue(result.success)
        if runner._IS_WINDOWS:
            mock_stop_windows.assert_called_once()
            mock_stop_posix.assert_not_called()
        else:
            mock_stop_posix.assert_called_once()
            mock_stop_windows.assert_not_called()

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._stop_posix")
    def test_stop_treats_exited_as_already_stopped(
        self, mock_stop_posix, mock_stop_windows
    ) -> None:
        """EXITED result → success=True propagated from platform helper."""
        from core.service_manager.models import ServiceOperationResult
        exited_result = ServiceOperationResult(success=True, message="Already stopped.")
        mock_stop_posix.return_value = exited_result
        mock_stop_windows.return_value = exited_result

        result = runner.stop(1234, self._make_identity())
        self.assertTrue(result.success)

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._stop_posix")
    def test_stop_refuses_on_mismatch(
        self, mock_stop_posix, mock_stop_windows
    ) -> None:
        """MISMATCH propagated from platform helper."""
        from core.service_manager.models import ServiceOperationResult
        mismatch_result = ServiceOperationResult(
            success=False, message="Stop refused.",
            error="identity MISMATCH — PID was recycled.",
        )
        mock_stop_posix.return_value = mismatch_result
        mock_stop_windows.return_value = mismatch_result

        result = runner.stop(1234, self._make_identity())
        self.assertFalse(result.success)
        self.assertIn("MISMATCH", result.error)

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._stop_posix")
    def test_stop_refuses_on_inconclusive(
        self, mock_stop_posix, mock_stop_windows
    ) -> None:
        """INCONCLUSIVE propagated from platform helper."""
        from core.service_manager.models import ServiceOperationResult
        inc_result = ServiceOperationResult(
            success=False, message="Stop refused.",
            error="identity verification was INCONCLUSIVE.",
        )
        mock_stop_posix.return_value = inc_result
        mock_stop_windows.return_value = inc_result

        result = runner.stop(1234, self._make_identity())
        self.assertFalse(result.success)
        self.assertIn("INCONCLUSIVE", result.error)

    def test_stop_refuses_when_identity_is_none(self) -> None:
        """stop() with identity=None refuses immediately before any dispatch."""
        with patch("core.service_manager.runner._stop_posix") as mock_posix:
            with patch("core.service_manager.runner._stop_windows") as mock_win:
                result = runner.stop(1234, None)
        mock_posix.assert_not_called()
        mock_win.assert_not_called()
        self.assertFalse(result.success)
        self.assertIn("no process identity baseline", result.error)

    def test_stop_refuses_when_pid_does_not_match_identity_pid(self) -> None:
        """stop(pid=99, identity.pid=1234) is rejected before any OS call."""
        identity = self._make_identity(pid=1234)
        with patch("core.service_manager.runner._stop_posix") as mock_posix:
            with patch("core.service_manager.runner._stop_windows") as mock_win:
                result = runner.stop(99, identity)
        mock_posix.assert_not_called()
        mock_win.assert_not_called()
        self.assertFalse(result.success)
        self.assertIn("99", result.error)
        self.assertIn("1234", result.error)

    def test_stop_refuses_when_platform_does_not_match(self) -> None:
        """stop() refuses when identity.platform does not match the active OS."""
        wrong_platform = "posix" if runner._IS_WINDOWS else "nt"
        identity = ProcessIdentity(pid=1234, platform=wrong_platform, start_time=12345)
        with patch("core.service_manager.runner._stop_posix") as mock_posix:
            with patch("core.service_manager.runner._stop_windows") as mock_win:
                result = runner.stop(1234, identity)
        mock_posix.assert_not_called()
        mock_win.assert_not_called()
        self.assertFalse(result.success)
        self.assertIn(wrong_platform, result.error)


# ===========================================================================
# TestStartIdentityCapture
# ===========================================================================
class TestStartIdentityCapture(unittest.TestCase):
    """Verify that runner.start() captures identity immediately after Popen."""

    def _make_definition(self, tmp_dir: Path) -> ServiceDefinition:
        return ServiceDefinition(
            id="id_test_svc",
            name="ID Test Service",
            command=["python", "-c", "import time; time.sleep(10)"],
            working_directory=tmp_dir,
        )

    def _make_running_process_mock(self, pid: int = 555) -> MagicMock:
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # stays running
        mock_process.pid = pid
        return mock_process

    def _make_failed_process_mock(self, pid: int = 556, exit_code: int = 1) -> MagicMock:
        mock_process = MagicMock()
        mock_process.poll.return_value = exit_code
        mock_process.pid = pid
        return mock_process

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity")
    def test_running_state_contains_captured_identity(
        self, mock_capture, mock_popen, _mock_sleep
    ) -> None:
        """RUNNING ServiceRuntimeState.identity equals the return of _capture_identity."""
        fake_identity = ProcessIdentity(pid=555, platform="posix", start_time=9999)
        mock_capture.return_value = fake_identity
        mock_popen.return_value = self._make_running_process_mock(pid=555)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            defn = self._make_definition(tmp_path)
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(defn)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.state)
        self.assertEqual(result.state.identity, fake_identity)

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity", return_value=None)
    def test_running_state_identity_none_when_capture_fails(
        self, _mock_capture, mock_popen, _mock_sleep
    ) -> None:
        """When _capture_identity returns None, identity=None in the RUNNING state."""
        mock_popen.return_value = self._make_running_process_mock(pid=555)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            defn = self._make_definition(tmp_path)
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(defn)

        self.assertTrue(result.success)
        self.assertIsNone(result.state.identity)

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity",
           return_value=ProcessIdentity(pid=556, platform="posix", start_time=1))
    def test_failed_state_has_none_identity(
        self, _mock_capture, mock_popen, _mock_sleep
    ) -> None:
        """FAILED state (grace-period exit) always has identity=None regardless of capture."""
        mock_popen.return_value = self._make_failed_process_mock(exit_code=2)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            defn = self._make_definition(tmp_path)
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                result = runner.start(defn)

        self.assertFalse(result.success)
        self.assertIsNotNone(result.state)
        self.assertIsNone(result.state.identity)
        self.assertIsNone(result.state.pid)

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner.subprocess.Popen")
    @patch("core.service_manager.runner._capture_identity")
    def test_capture_is_called_with_process_pid(
        self, mock_capture, mock_popen, _mock_sleep
    ) -> None:
        """_capture_identity is called with the PID returned by Popen."""
        mock_capture.return_value = None
        mock_process = self._make_running_process_mock(pid=7777)
        mock_popen.return_value = mock_process

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "logs"
            log_dir.mkdir()
            defn = self._make_definition(tmp_path)
            with patch.object(runner.config, "SERVICE_LOG_DIR", log_dir):
                runner.start(defn)

        mock_capture.assert_called_once_with(7777)


# ===========================================================================
# TestSendSignalPosix
# ===========================================================================
class TestSendSignalPosix(unittest.TestCase):
    """Verify _send_signal_posix enforces pgid==pid and never falls back to bare kill.

    os.getpgid, os.killpg, and os.kill are patched with create=True so these
    tests also run on Windows where those POSIX attributes don't exist on the
    os module.
    """

    @patch("core.service_manager.runner.os.killpg", create=True)
    @patch("core.service_manager.runner.os.getpgid", return_value=1234, create=True)
    def test_kills_group_when_pgid_equals_pid(
        self, _mock_getpgid, mock_killpg
    ) -> None:
        """When pgid == pid, os.killpg is called with the pgid."""
        import signal as _signal
        runner._send_signal_posix(1234, _signal.SIGTERM)
        mock_killpg.assert_called_once_with(1234, _signal.SIGTERM)

    @patch("core.service_manager.runner.os.kill", create=True)
    @patch("core.service_manager.runner.os.killpg", create=True)
    @patch("core.service_manager.runner.os.getpgid", return_value=9999, create=True)
    def test_refuses_when_pgid_differs_from_pid(
        self, _mock_getpgid, mock_killpg, mock_kill
    ) -> None:
        """When pgid != pid, neither killpg nor kill is called (refused, no fallback)."""
        import signal as _signal
        runner._send_signal_posix(1234, _signal.SIGTERM)
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()

    @patch("core.service_manager.runner.os.kill", create=True)
    @patch("core.service_manager.runner.os.killpg", create=True)
    @patch("core.service_manager.runner.os.getpgid",
           side_effect=ProcessLookupError, create=True)
    def test_process_lookup_error_on_getpgid_is_silent(
        self, _mock_getpgid, mock_killpg, mock_kill
    ) -> None:
        """ProcessLookupError from getpgid (process gone) → return quietly, no signal."""
        import signal as _signal
        runner._send_signal_posix(1234, _signal.SIGTERM)
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()

    @patch("core.service_manager.runner.os.kill", create=True)
    @patch("core.service_manager.runner.os.killpg", create=True)
    @patch("core.service_manager.runner.os.getpgid",
           side_effect=OSError("mock error"), create=True)
    def test_generic_oserror_on_getpgid_is_silent(
        self, _mock_getpgid, mock_killpg, mock_kill
    ) -> None:
        """Generic OSError from getpgid → return quietly, no signal."""
        import signal as _signal
        # Use SIGTERM: SIGKILL doesn't exist on Windows, but the signal
        # value is irrelevant here - we're only testing the OSError path.
        runner._send_signal_posix(1234, _signal.SIGTERM)
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()


# ===========================================================================
# Process identity safety correction tests
# ===========================================================================

# ---------------------------------------------------------------------------
# TestVerifyIdentityPosixBootIdCorrections
# ---------------------------------------------------------------------------
class TestVerifyIdentityPosixBootIdCorrections(unittest.TestCase):
    """Verify the corrected boot_id pre-check in _verify_identity_posix.

    The original implementation silently skipped the boot_id check when the
    stored boot_id was set but _read_boot_id_posix() returned None.  The
    corrected implementation returns INCONCLUSIVE in that case.
    """

    def _make_identity(self, boot_id: str) -> ProcessIdentity:
        return ProcessIdentity(
            pid=1234, platform="posix", start_time=12345, boot_id=boot_id
        )

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    def test_stored_boot_id_unreadable_current_returns_inconclusive(
        self, _mock_boot
    ) -> None:
        """INCONCLUSIVE when stored boot_id exists but current boot_id is unreadable.

        The corrected code must NOT silently skip the boot check and proceed
        to the /proc/stat check.  Skipping would allow a post-reboot recycled
        PID to pass verification by matching starttime (which restarts from 0).
        """
        identity = self._make_identity(boot_id="stored-boot-id")
        # _read_boot_id_posix returns None → cannot confirm same boot session.
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.INCONCLUSIVE)

    @patch("core.service_manager.runner._read_boot_id_posix",
           return_value="stored-boot-id")
    @patch("pathlib.Path.read_text")
    def test_stored_boot_id_readable_match_proceeds_to_starttime(
        self, mock_read, _mock_boot
    ) -> None:
        """When stored and current boot_ids match, verification proceeds to starttime."""
        mock_read.return_value = _make_stat_line(starttime=12345)
        identity = self._make_identity(boot_id="stored-boot-id")
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MATCH)

    @patch("core.service_manager.runner._read_boot_id_posix",
           return_value="different-boot-id")
    def test_stored_boot_id_mismatch_returns_mismatch(self, _mock_boot) -> None:
        """MISMATCH when stored boot_id and current boot_id differ (system rebooted)."""
        identity = self._make_identity(boot_id="stored-boot-id")
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MISMATCH)

    @patch("core.service_manager.runner._read_boot_id_posix", return_value=None)
    @patch("pathlib.Path.read_text")
    def test_no_stored_boot_id_still_checks_starttime(
        self, mock_read, _mock_boot
    ) -> None:
        """When stored boot_id is None (not captured), falls through to starttime check."""
        mock_read.return_value = _make_stat_line(starttime=12345)
        # boot_id=None means we never captured it at start time.
        identity = ProcessIdentity(pid=1234, platform="posix", start_time=12345, boot_id=None)
        result = runner._verify_identity_posix(identity)
        self.assertEqual(result, IdentityVerificationResult.MATCH)


# ---------------------------------------------------------------------------
# TestVerifyIdentityWindowsAccessDenied
# ---------------------------------------------------------------------------
class TestVerifyIdentityWindowsAccessDenied(unittest.TestCase):
    """Verify _verify_identity_windows distinguishes EXITED from INCONCLUSIVE.

    OpenProcess returning NULL can mean:
      - ERROR_ACCESS_DENIED (5): process exists but we lack permission → INCONCLUSIVE
      - Any other error: process is gone → EXITED
    """

    _WIN_ERROR_ACCESS_DENIED = 0x5

    @patch("ctypes.windll")
    def test_access_denied_returns_inconclusive(self, mock_windll) -> None:
        """OpenProcess NULL + GetLastError==5 (ACCESS_DENIED) → INCONCLUSIVE."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0  # NULL
        k32.GetLastError.return_value = self._WIN_ERROR_ACCESS_DENIED
        mock_windll.kernel32 = k32

        identity = ProcessIdentity(pid=42, platform="nt", start_time=12345, boot_id=None)
        result = runner._verify_identity_windows(identity)
        self.assertEqual(result, IdentityVerificationResult.INCONCLUSIVE)
        # Must NOT call CloseHandle when no handle was opened.
        k32.CloseHandle.assert_not_called()

    @patch("ctypes.windll")
    def test_invalid_parameter_returns_exited(self, mock_windll) -> None:
        """OpenProcess NULL + ERROR_INVALID_PARAMETER (87, no such PID) → EXITED."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0  # NULL
        k32.GetLastError.return_value = runner._WIN_ERROR_INVALID_PARAMETER  # 87
        mock_windll.kernel32 = k32

        identity = ProcessIdentity(pid=42, platform="nt", start_time=12345, boot_id=None)
        result = runner._verify_identity_windows(identity)
        self.assertEqual(result, IdentityVerificationResult.EXITED)
        k32.CloseHandle.assert_not_called()

    @patch("ctypes.windll")
    def test_unexpected_win32_error_returns_inconclusive(self, mock_windll) -> None:
        """OpenProcess NULL + unexpected Win32 error (e.g. 31/ERROR_GEN_FAILURE) → INCONCLUSIVE."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0  # NULL
        k32.GetLastError.return_value = 31  # ERROR_GEN_FAILURE (unexpected error)
        mock_windll.kernel32 = k32

        identity = ProcessIdentity(pid=42, platform="nt", start_time=12345, boot_id=None)
        result = runner._verify_identity_windows(identity)
        self.assertEqual(result, IdentityVerificationResult.INCONCLUSIVE)
        k32.CloseHandle.assert_not_called()

    @patch("ctypes.windll")
    def test_access_denied_does_not_signal(self, mock_windll) -> None:
        """INCONCLUSIVE from OpenProcess ACCESS_DENIED: _stop_windows must refuse."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0  # NULL - cannot open even with full rights
        k32.GetLastError.return_value = self._WIN_ERROR_ACCESS_DENIED
        mock_windll.kernel32 = k32

        identity = ProcessIdentity(pid=42, platform="nt", start_time=99999, boot_id=None)
        result = runner._stop_windows(42, identity, timeout=0.1)
        self.assertFalse(result.success)
        self.assertIn("INCONCLUSIVE", result.error)
        k32.GetProcessTimes.assert_not_called()
        k32.TerminateProcess.assert_not_called()

    @patch("ctypes.windll")
    def test_unexpected_error_in_stop_windows_refuses(self, mock_windll) -> None:
        """Unexpected Win32 error on OpenProcess in _stop_windows → refused (INCONCLUSIVE)."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = 0
        k32.GetLastError.return_value = 31
        mock_windll.kernel32 = k32

        identity = ProcessIdentity(pid=42, platform="nt", start_time=99999, boot_id=None)
        result = runner._stop_windows(42, identity, timeout=0.1)
        self.assertFalse(result.success)
        self.assertIn("INCONCLUSIVE", result.error)
        k32.GetProcessTimes.assert_not_called()
        k32.TerminateProcess.assert_not_called()


# ---------------------------------------------------------------------------
# TestStopPosixNoReverify — PID recycled between SIGTERM and SIGKILL
# ---------------------------------------------------------------------------
class TestStopPosixNoReverify(unittest.TestCase):
    """Prove that _stop_posix re-verifies before escalating to SIGKILL.

    Scenario: the service exits and a different process acquires the same
    PID between the graceful SIGTERM and the escalation window.  The second
    identity check must return MISMATCH and abort the SIGKILL.
    """

    def _make_identity(self, starttime: int = 12345) -> ProcessIdentity:
        return ProcessIdentity(pid=500, platform="posix", start_time=starttime)

    @patch("core.service_manager.runner.is_running", return_value=True)
    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner._send_signal_posix")
    @patch("core.service_manager.runner.verify_process_identity")
    def test_sigkill_refused_when_pid_recycled_between_graceful_and_escalation(
        self,
        mock_verify,
        mock_signal,
        _mock_sleep,
        _mock_running,
    ) -> None:
        """SIGKILL is never sent when re-verification shows MISMATCH."""
        import signal as _signal

        # First call: MATCH (allows SIGTERM).
        # Second call: MISMATCH (PID was recycled before SIGKILL).
        mock_verify.side_effect = [
            IdentityVerificationResult.MATCH,
            IdentityVerificationResult.MISMATCH,
        ]

        identity = self._make_identity()
        result = runner._stop_posix(500, identity, timeout=0.0)

        # First SIGTERM should have been sent.
        sigterm_calls = [
            c for c in mock_signal.call_args_list if c.args[1] == _signal.SIGTERM
        ]
        self.assertEqual(len(sigterm_calls), 1)

        # SIGKILL must NOT have been sent.
        sigkill_calls = [
            c for c in mock_signal.call_args_list
            if hasattr(_signal, "SIGKILL") and c.args[1] == _signal.SIGKILL
        ]
        self.assertEqual(len(sigkill_calls), 0)

        # The operation must fail-safe (not claim success after refusing).
        self.assertFalse(result.success)
        self.assertIn("MISMATCH", result.error)

    @patch("core.service_manager.runner.is_running", return_value=True)
    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner._send_signal_posix")
    @patch("core.service_manager.runner.verify_process_identity")
    def test_sigkill_refused_when_reverification_inconclusive(
        self,
        mock_verify,
        mock_signal,
        _mock_sleep,
        _mock_running,
    ) -> None:
        """SIGKILL is never sent when re-verification returns INCONCLUSIVE."""
        import signal as _signal

        mock_verify.side_effect = [
            IdentityVerificationResult.MATCH,
            IdentityVerificationResult.INCONCLUSIVE,
        ]

        result = runner._stop_posix(500, self._make_identity(), timeout=0.0)

        sigkill_calls = [
            c for c in mock_signal.call_args_list
            if hasattr(_signal, "SIGKILL") and c.args[1] == _signal.SIGKILL
        ]
        self.assertEqual(len(sigkill_calls), 0)
        self.assertFalse(result.success)
        self.assertIn("INCONCLUSIVE", result.error)

    @patch("core.service_manager.runner.is_running", return_value=False)
    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("core.service_manager.runner._send_signal_posix")
    @patch("core.service_manager.runner.verify_process_identity",
           return_value=IdentityVerificationResult.MATCH)
    def test_no_sigkill_when_process_exits_gracefully(
        self, _mock_verify, mock_signal, _mock_sleep, _mock_running
    ) -> None:
        """If the process exits within the graceful window, SIGKILL is never sent."""
        import signal as _signal

        result = runner._stop_posix(500, self._make_identity(), timeout=5.0)

        self.assertTrue(result.success)
        sigkill_calls = [
            c for c in mock_signal.call_args_list
            if hasattr(_signal, "SIGKILL") and c.args[1] == _signal.SIGKILL
        ]
        self.assertEqual(len(sigkill_calls), 0)


# ---------------------------------------------------------------------------
# TestStopWindowsRetainedHandle
# ---------------------------------------------------------------------------
class TestStopWindowsRetainedHandle(unittest.TestCase):
    """Prove that _stop_windows uses the verified handle for TerminateProcess.

    A single handle is opened once.  TerminateProcess receives that SAME
    handle value (not a fresh one).  No second OpenProcess call is made
    during the stop operation.
    """

    _START_TIME_LO = 0xDEADBEEF
    _START_TIME_HI = 0x00000001
    _START_TIME = (_START_TIME_HI << 32) | _START_TIME_LO

    def _make_identity(self) -> ProcessIdentity:
        return ProcessIdentity(
            pid=42, platform="nt", start_time=self._START_TIME, boot_id=None
        )

    def _make_k32_match(self, handle_value: int, process_alive_polls: int = 1) -> MagicMock:
        """kernel32 mock: OpenProcess succeeds, identity matches, process eventually exits."""
        k32 = MagicMock()
        k32.OpenProcess.return_value = handle_value

        def fake_get_times(h, pCreation, pExit, pKernel, pUser):
            pCreation._obj.dwLowDateTime = self._START_TIME_LO
            pCreation._obj.dwHighDateTime = self._START_TIME_HI
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times

        # WaitForSingleObject: alive for N polls, then terminated.
        _calls = [0]
        def fake_wait(h, ms):
            _calls[0] += 1
            if _calls[0] > process_alive_polls:
                return runner._WIN_WAIT_OBJECT_0  # terminated
            return runner._WIN_WAIT_TIMEOUT       # still running

        k32.WaitForSingleObject.side_effect = fake_wait
        k32.TerminateProcess.return_value = 1
        k32.CloseHandle.return_value = True
        return k32

    @patch("core.service_manager.runner.os.kill", create=True)
    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("ctypes.windll")
    def test_terminate_process_uses_retained_handle(
        self, mock_windll, _mock_sleep, mock_kill
    ) -> None:
        """TerminateProcess is called with the SAME handle value as OpenProcess returned; no os.kill."""
        handle_value = 77
        k32 = self._make_k32_match(handle_value, process_alive_polls=999)
        # Force timeout immediately (all polls report alive).
        mock_windll.kernel32 = k32

        result = runner._stop_windows(42, self._make_identity(), timeout=0.0)

        # TerminateProcess must be called exactly once with the retained handle.
        k32.TerminateProcess.assert_called_once()
        actual_handle = k32.TerminateProcess.call_args[0][0]
        self.assertEqual(actual_handle, handle_value)

        # OpenProcess must be called exactly ONCE in the whole stop operation.
        self.assertEqual(k32.OpenProcess.call_count, 1)

        # Handle must be closed in finally.
        k32.CloseHandle.assert_called_with(handle_value)

        # Graceful os.kill is refused — never called!
        mock_kill.assert_not_called()

    @patch("core.service_manager.runner.os.kill", create=True)
    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("ctypes.windll")
    def test_handle_closed_even_after_mismatch(
        self, mock_windll, _mock_sleep, mock_kill
    ) -> None:
        """CloseHandle is always called when OpenProcess succeeded, even on MISMATCH."""
        handle_value = 88
        k32 = MagicMock()
        k32.OpenProcess.return_value = handle_value

        def fake_get_times(h, pCreation, pExit, pKernel, pUser):
            # Return a DIFFERENT creation time → MISMATCH.
            pCreation._obj.dwLowDateTime = 0x11111111
            pCreation._obj.dwHighDateTime = 0x22222222
            return 1

        k32.GetProcessTimes.side_effect = fake_get_times
        mock_windll.kernel32 = k32

        result = runner._stop_windows(42, self._make_identity(), timeout=0.0)

        self.assertFalse(result.success)
        self.assertIn("MISMATCH", result.error)
        # Handle MUST be closed even though we never tried to terminate.
        k32.CloseHandle.assert_called_with(handle_value)
        k32.TerminateProcess.assert_not_called()
        mock_kill.assert_not_called()

    @patch("core.service_manager.runner.os.kill", create=True)
    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("ctypes.windll")
    def test_graceful_exit_no_terminate_process_called(
        self, mock_windll, _mock_sleep, mock_kill
    ) -> None:
        """TerminateProcess is NOT called when the process exits gracefully; no os.kill."""
        handle_value = 55
        k32 = self._make_k32_match(handle_value, process_alive_polls=0)
        mock_windll.kernel32 = k32

        result = runner._stop_windows(42, self._make_identity(), timeout=5.0)

        self.assertTrue(result.success)
        k32.TerminateProcess.assert_not_called()
        k32.CloseHandle.assert_called_with(handle_value)
        mock_kill.assert_not_called()

    @patch("core.service_manager.runner.os.kill", create=True)
    def test_unsafe_graceful_signal_refused_no_pid_kill(self, mock_kill) -> None:
        """_send_terminate_windows refuses bare-PID console event without calling os.kill."""
        runner._send_terminate_windows(42)
        mock_kill.assert_not_called()

    @patch("core.service_manager.runner.time.sleep", return_value=None)
    @patch("ctypes.windll")
    def test_handle_closed_on_exception(self, mock_windll, _mock_sleep) -> None:
        """Handle is closed in finally even if an unexpected exception occurs during termination."""
        handle_value = 99
        k32 = MagicMock()
        k32.OpenProcess.return_value = handle_value
        k32.GetProcessTimes.side_effect = RuntimeError("unexpected Win32 error")
        mock_windll.kernel32 = k32

        with self.assertRaises(RuntimeError):
            runner._stop_windows(42, self._make_identity(), timeout=0.0)

        k32.CloseHandle.assert_called_with(handle_value)


# ---------------------------------------------------------------------------
# TestStopVerificationNeverSignals
# ---------------------------------------------------------------------------
class TestStopVerificationNeverSignals(unittest.TestCase):
    """Cross-cutting: no verification failure path should ever signal a process.

    Each sub-test patches _send_signal_posix and _stop_windows to prove
    that MISMATCH, INCONCLUSIVE, pid/identity mismatch, platform mismatch,
    and no-identity cases do NOT invoke any OS signal primitive.
    """

    def _make_identity(self, pid: int = 1234) -> ProcessIdentity:
        platform = "nt" if runner._IS_WINDOWS else "posix"
        return ProcessIdentity(pid=pid, platform=platform, start_time=12345)

    def _assert_no_signal_sent(self, result, mock_signal, mock_windows):
        """Assert no signal was sent and the result is a refusal."""
        mock_signal.assert_not_called()
        if runner._IS_WINDOWS:
            mock_windows.assert_not_called()
        self.assertFalse(result.success)

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._send_signal_posix", create=True)
    def test_no_identity_never_signals(self, mock_signal, mock_win) -> None:
        """identity=None: no signal, no platform dispatch."""
        result = runner.stop(1234, None)
        mock_signal.assert_not_called()
        mock_win.assert_not_called()
        self.assertFalse(result.success)

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._send_signal_posix", create=True)
    def test_pid_mismatch_never_signals(self, mock_signal, mock_win) -> None:
        """pid != identity.pid: no signal, no platform dispatch."""
        result = runner.stop(999, self._make_identity(pid=1234))
        mock_signal.assert_not_called()
        mock_win.assert_not_called()
        self.assertFalse(result.success)

    @patch("core.service_manager.runner._stop_windows")
    @patch("core.service_manager.runner._send_signal_posix", create=True)
    def test_wrong_platform_never_signals(self, mock_signal, mock_win) -> None:
        """identity.platform mismatch: no signal, no platform dispatch."""
        wrong = "posix" if runner._IS_WINDOWS else "nt"
        identity = ProcessIdentity(pid=1234, platform=wrong, start_time=12345)
        result = runner.stop(1234, identity)
        mock_signal.assert_not_called()
        mock_win.assert_not_called()
        self.assertFalse(result.success)

    @patch("core.service_manager.runner._send_signal_posix", create=True)
    @patch("core.service_manager.runner.verify_process_identity",
           return_value=IdentityVerificationResult.MISMATCH)
    def test_posix_mismatch_never_sends_sigterm(
        self, _mock_verify, mock_signal
    ) -> None:
        """_stop_posix: MISMATCH on first verify → no _send_signal_posix call."""
        identity = ProcessIdentity(pid=1234, platform="posix", start_time=12345)
        result = runner._stop_posix(1234, identity, timeout=0.0)
        mock_signal.assert_not_called()
        self.assertFalse(result.success)

    @patch("core.service_manager.runner._send_signal_posix", create=True)
    @patch("core.service_manager.runner.verify_process_identity",
           return_value=IdentityVerificationResult.INCONCLUSIVE)
    def test_posix_inconclusive_never_sends_sigterm(
        self, _mock_verify, mock_signal
    ) -> None:
        """_stop_posix: INCONCLUSIVE on first verify → no _send_signal_posix call."""
        identity = ProcessIdentity(pid=1234, platform="posix", start_time=12345)
        result = runner._stop_posix(1234, identity, timeout=0.0)
        mock_signal.assert_not_called()
        self.assertFalse(result.success)


# ===========================================================================
# TestWindowsCtypesSignatures
# ===========================================================================
class TestWindowsCtypesSignatures(unittest.TestCase):
    """Verify Win32 function signatures and pointer-sized handle declarations."""

    def test_configure_kernel32_declares_correct_signatures(self) -> None:
        """_configure_kernel32 declares exact argtypes and restype on Win32 functions."""
        import ctypes
        from core.service_manager.runner import _FILETIME

        fake_k32 = MagicMock()
        _configure_kernel32(fake_k32)

        # OpenProcess: argtypes=[DWORD, BOOL, DWORD], restype=HANDLE (c_void_p)
        self.assertEqual(fake_k32.OpenProcess.argtypes, [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong])
        self.assertEqual(fake_k32.OpenProcess.restype, ctypes.c_void_p)

        # CloseHandle: argtypes=[HANDLE], restype=BOOL (c_int)
        self.assertEqual(fake_k32.CloseHandle.argtypes, [ctypes.c_void_p])
        self.assertEqual(fake_k32.CloseHandle.restype, ctypes.c_int)

        # WaitForSingleObject: argtypes=[HANDLE, DWORD], restype=DWORD (c_ulong)
        self.assertEqual(fake_k32.WaitForSingleObject.argtypes, [ctypes.c_void_p, ctypes.c_ulong])
        self.assertEqual(fake_k32.WaitForSingleObject.restype, ctypes.c_ulong)

        # GetProcessTimes: argtypes=[HANDLE, *4], restype=BOOL (c_int)
        self.assertEqual(fake_k32.GetProcessTimes.argtypes[0], ctypes.c_void_p)
        self.assertEqual(fake_k32.GetProcessTimes.restype, ctypes.c_int)

        # TerminateProcess: argtypes=[HANDLE, UINT], restype=BOOL (c_int)
        self.assertEqual(fake_k32.TerminateProcess.argtypes, [ctypes.c_void_p, ctypes.c_uint])
        self.assertEqual(fake_k32.TerminateProcess.restype, ctypes.c_int)

        # GetLastError: argtypes=[], restype=DWORD (c_ulong)
        self.assertEqual(fake_k32.GetLastError.argtypes, [])
        self.assertEqual(fake_k32.GetLastError.restype, ctypes.c_ulong)

    def test_c_void_p_preserves_64bit_pointer_handle(self) -> None:
        """c_void_p retains full 64-bit handle values without 32-bit truncation."""
        import ctypes

        handle_64bit = 0x123456789ABCDEF0
        # If treated as signed 32-bit int, this would truncate/overflow:
        truncated_32bit = ctypes.c_int(handle_64bit).value
        self.assertNotEqual(truncated_32bit, handle_64bit)

        # But c_void_p preserves pointer-sized value:
        p = ctypes.c_void_p(handle_64bit)
        self.assertEqual(p.value, handle_64bit)

    def test_c_void_p_null_handle_is_falsy(self) -> None:
        """c_void_p NULL (0) translates to None / falsy in Python."""
        import ctypes

        p = ctypes.c_void_p(0)
        self.assertIsNone(p.value)
        self.assertFalse(p.value)


if __name__ == "__main__":
    unittest.main()
