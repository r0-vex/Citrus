"""
runner.py
=========
Actual OS-process execution for the Service Manager: starting a
service as a subprocess, stopping it safely, checking whether a
previously-started process is still alive, and verifying that a
stored PID still belongs to the same process Citrus started.

Cross-platform note:
    POSIX (Termux/Android, Linux) and Windows have genuinely
    different process-control primitives - os.kill(pid, 0), signal 0
    probing, process groups (killpg/getpgid), and os.waitpid() are
    POSIX-only; some don't exist on Windows at all, and os.kill()'s
    behavior for a nonzero signal on Windows is NOT a safe no-op (it
    can terminate the process). Every platform-sensitive operation
    below is split into an explicit POSIX path and an explicit
    Windows path, selected once via _IS_WINDOWS, so neither platform
    ever executes the other's code. The Windows path uses ctypes to
    call the Win32 API directly (OpenProcess / WaitForSingleObject /
    GetProcessTimes / TerminateProcess) - the standard, safe way to
    do this from the standard library alone.

Process identity and PID-reuse safety:
    A PID stored in the state file can be recycled by the OS after
    the original service exits.  Before sending any OS signal, Citrus
    verifies the identity of the process currently holding that PID
    against a fingerprint captured at start time (ProcessIdentity in
    models.py).  If the fingerprint does not match, Citrus refuses the
    operation rather than signalling an unrelated process.

    POSIX TOCTOU race (unavoidable without pidfd):
    Even with identity verification against /proc/<pid>/stat, there is
    an unavoidable race between the read-and-check and the subsequent
    os.killpg() call.  A process could exit and a new process could
    acquire the same PID and the same starttime tick within that window.
    Linux 5.3+ has pidfd_open() which closes this race, but Android 8.1's
    kernel (3.18 / 4.4) predates it.  The implementation narrows the risk
    window by verifying immediately before signalling, but cannot eliminate
    it entirely on the target deployment platform.
"""

import ctypes
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from core import config
from core.logging.logger import get_logger
from core.service_manager.models import (
    ProcessIdentity,
    ServiceDefinition,
    ServiceOperationResult,
    ServiceRuntimeState,
    ServiceStatus,
)

logger = get_logger(__name__)

_STOP_POLL_INTERVAL_SECONDS: float = 0.2
_DEFAULT_STOP_TIMEOUT_SECONDS: float = 5.0
_START_GRACE_PERIOD_SECONDS: float = 0.3

_IS_WINDOWS: bool = os.name == "nt"

# Win32 constants used by the Windows-only functions below (plain
# ctypes primitives are used instead of ctypes.wintypes so this
# module never needs a Windows-only import at load time).
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_SYNCHRONIZE = 0x00100000
_WIN_PROCESS_TERMINATE = 0x0001
# WaitForSingleObject return values
_WIN_WAIT_OBJECT_0 = 0x00000000   # process has terminated (handle is signalled)
_WIN_WAIT_TIMEOUT = 0x00000102    # process is still running
_WIN_WAIT_FAILED = 0xFFFFFFFF     # API call failed
# Win32 error codes
_WIN_ERROR_ACCESS_DENIED = 0x00000005        # ERROR_ACCESS_DENIED (5): process exists, access denied
_WIN_ERROR_INVALID_PARAMETER = 0x00000057    # ERROR_INVALID_PARAMETER (87): PID does not exist

# GetProcessTimes needs PROCESS_QUERY_LIMITED_INFORMATION only (0x1000).
# WaitForSingleObject additionally needs SYNCHRONIZE (0x00100000).
# TerminateProcess needs PROCESS_TERMINATE (0x0001).
# For the Windows stop path we open with all three so the single handle can
# be used for identity verification, liveness polling, and forced termination
# without ever reopening (and thus racing on) the PID.
_WIN_PROCESS_VERIFY_AND_TERMINATE = (
    _WIN_PROCESS_QUERY_LIMITED_INFORMATION | _WIN_SYNCHRONIZE | _WIN_PROCESS_TERMINATE
)

# ctypes structure for FILETIME (used by GetProcessTimes).
class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


def _configure_kernel32(k32: object) -> None:
    """Configure explicit ctypes argument and return types on a kernel32 library.

    By default, CPython's ctypes assumes a 32-bit c_int return type and
    passes unadorned integer arguments as 32-bit values.  On 64-bit Windows,
    process handles (HANDLE) are pointer-sized (64-bit / c_void_p).  Failing
    to declare restype and argtypes causes handle truncation to 32 bits and
    sign-extension errors, resulting in invalid handles or memory corruption.

    This function configures:
      - OpenProcess: argtypes=[DWORD, BOOL, DWORD], restype=HANDLE (c_void_p)
      - CloseHandle: argtypes=[HANDLE], restype=BOOL (c_int)
      - WaitForSingleObject: argtypes=[HANDLE, DWORD], restype=DWORD (c_ulong)
      - GetProcessTimes: argtypes=[HANDLE, *4], restype=BOOL (c_int)
      - TerminateProcess: argtypes=[HANDLE, UINT], restype=BOOL (c_int)
      - GetLastError: argtypes=[], restype=DWORD (c_ulong)
    """
    try:
        k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k32.OpenProcess.restype = ctypes.c_void_p

        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_int

        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        k32.WaitForSingleObject.restype = ctypes.c_ulong

        k32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        k32.GetProcessTimes.restype = ctypes.c_int

        k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.TerminateProcess.restype = ctypes.c_int

        k32.GetLastError.argtypes = []
        k32.GetLastError.restype = ctypes.c_ulong
    except (AttributeError, TypeError):
        pass


def _get_kernel32() -> object:
    """Return the Win32 kernel32 library with explicit ctypes signatures.

    Only invoked on Windows (_IS_WINDOWS == True); never executed on POSIX.
    """
    k32 = ctypes.windll.kernel32
    _configure_kernel32(k32)
    return k32


if _IS_WINDOWS:
    try:
        _configure_kernel32(ctypes.windll.kernel32)
    except (AttributeError, OSError):
        pass


class IdentityVerificationResult(Enum):
    """Classification returned by verify_process_identity().

    Attributes:
        MATCH: The process currently holding the PID has the same
            creation-time fingerprint as the stored baseline.  OS
            signals are permitted (subject to the unavoidable POSIX
            TOCTOU race documented in the module docstring).
        MISMATCH: A process exists at that PID but its fingerprint
            differs - the PID was recycled.  Signals must be refused;
            the stored PID should be cleared and status set to FAILED.
        EXITED: The PID no longer exists (ESRCH on POSIX; OpenProcess
            returns NULL on Windows; zombie with state 'Z' on POSIX).
            No signal needed; status set to FAILED, last_exit_at stamped.
        INCONCLUSIVE: The check could not be completed (permission
            denied, /proc read error, GetProcessTimes failure, etc.).
            Status must be set to UNKNOWN; PID preserved for triage.
            Signals must be refused.
    """

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    EXITED = "EXITED"
    INCONCLUSIVE = "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# POSIX identity helpers
# ---------------------------------------------------------------------------

_POSIX_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


def _read_boot_id_posix() -> Optional[str]:
    """Read the system boot UUID from /proc/sys/kernel/random/boot_id.

    Returns:
        The stripped UUID string (e.g. ``"6fd5a44b-35f4-4ad4-a9b9-..."``),
        or None if the file is absent or unreadable.  The boot_id changes
        on every reboot, so a mismatch immediately identifies stale state.
    """
    try:
        return Path(_POSIX_BOOT_ID_PATH).read_text(encoding="ascii").strip()
    except OSError:
        return None


def _parse_proc_stat(stat_content: str) -> Optional[Tuple[str, int]]:
    """Parse /proc/<pid>/stat and extract the process state and starttime.

    The comm field (process name, field 2) is enclosed in parentheses but
    may itself contain spaces, parentheses, or both - for example:
        1234 (my (weird) proc) S 1 1234 ...
    Naive ``split()`` on the entire line would misplace all subsequent
    fields.  We instead find the LAST closing parenthesis with ``rfind``
    and split the remainder, which is always a fixed-format sequence of
    whitespace-separated values.

    Field layout of the right-hand side tokens (0-indexed after rfind):
        [0]  state  (single char: R/S/D/Z/T/...)
        [1]  ppid
        ...
        [19] starttime (clock ticks since boot, used as identity token)

    Args:
        stat_content: The full text of /proc/<pid>/stat.

    Returns:
        A (state, starttime) tuple, or None if the content is malformed.
    """
    end_paren = stat_content.rfind(")")
    if end_paren == -1:
        return None
    remainder = stat_content[end_paren + 1:].split()
    # We need at least 20 tokens to reach starttime (index 19).
    if len(remainder) < 20:
        return None
    try:
        state: str = remainder[0]
        starttime: int = int(remainder[19])
    except (ValueError, IndexError):
        return None
    return state, starttime


def _get_process_identity_posix(pid: int) -> Optional[ProcessIdentity]:
    """POSIX: read /proc/<pid>/stat and capture a ProcessIdentity.

    Args:
        pid: The process ID of the newly-started service.

    Returns:
        A ProcessIdentity with the starttime clock-tick and boot_id, or
        None if /proc/<pid>/stat is unreadable (process already exited
        before we could read it, or permission denied).
    """
    try:
        stat_content = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None

    parsed = _parse_proc_stat(stat_content)
    if parsed is None:
        return None

    _state, starttime = parsed
    boot_id = _read_boot_id_posix()
    return ProcessIdentity(
        pid=pid,
        platform="posix",
        start_time=starttime,
        boot_id=boot_id,
    )


def _get_process_identity_windows(
    pid: int,
) -> Tuple[Optional[ProcessIdentity], Optional[int]]:
    """Windows: open a process handle and capture a ProcessIdentity baseline.

    This function is used during start() by _capture_identity() to obtain
    a startup creation-time fingerprint. The handle returned here is
    temporary and closed immediately by the caller (_capture_identity);
    it is NOT held open across the service's lifetime.

    Handle retention across verification, polling, and termination is
    implemented separately in _stop_windows(), where a single verified
    handle is opened and retained through TerminateProcess to eliminate
    PID-reuse TOCTOU races during the stop operation.

    Args:
        pid: The process ID of the newly-started service.

    Returns:
        A (ProcessIdentity, handle) pair where both are non-None on
        success.  If OpenProcess fails or GetProcessTimes fails, returns
        (None, None).  The caller MUST close the handle in a finally
        block (or immediately after capture) if non-None.
    """
    kernel32 = _get_kernel32()
    handle = kernel32.OpenProcess(_WIN_PROCESS_VERIFY_AND_TERMINATE, False, pid)
    if not handle:
        return None, None

    creation = _FILETIME()
    exit_ft = _FILETIME()
    kernel_ft = _FILETIME()
    user_ft = _FILETIME()

    success = kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_ft),
        ctypes.byref(kernel_ft),
        ctypes.byref(user_ft),
    )
    if not success:
        kernel32.CloseHandle(handle)
        return None, None

    start_time = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    identity = ProcessIdentity(
        pid=pid,
        platform="nt",
        start_time=start_time,
        boot_id=None,  # Windows uses creation FILETIME; no boot_id needed
    )
    return identity, handle


def _capture_identity(pid: int) -> Optional[ProcessIdentity]:
    """Capture a ProcessIdentity for a freshly-started process.

    On Windows, the handle acquired for identity capture is immediately
    closed here - it is NOT retained after start() because the service
    may run for hours and we cannot keep a PROCESS_TERMINATE handle open
    indefinitely (resource leak).  A new handle is acquired at stop() time
    and the identity is re-verified then.

    On POSIX, _get_process_identity_posix reads /proc/<pid>/stat, which
    closes atomically and leaves no resources open.

    Args:
        pid: The process ID of the freshly-started subprocess.

    Returns:
        A ProcessIdentity, or None if capture failed (the process exited
        before we could read its metadata).
    """
    if _IS_WINDOWS:
        identity, handle = _get_process_identity_windows(pid)
        if handle is not None:
            _get_kernel32().CloseHandle(handle)
        return identity
    return _get_process_identity_posix(pid)


def verify_process_identity(
    identity: ProcessIdentity,
) -> IdentityVerificationResult:
    """Verify that the process currently holding identity.pid matches identity.

    This is the authoritative gatekeeper before any OS signal.  The caller
    (manager.py) must never send a signal when the result is anything other
    than MATCH.

    POSIX implementation notes:
        - Checks boot_id first if both stored and current are available.
          A mismatch means the system rebooted; the PID is definitely stale.
        - Reads /proc/<pid>/stat and parses starttime using _parse_proc_stat.
        - Zombie state ('Z') is treated as EXITED, not MISMATCH.
        - Permission errors and parse failures are INCONCLUSIVE.

    Windows implementation notes:
        - Opens a fresh handle with QUERY_LIMITED_INFORMATION.
          (We do NOT reuse a handle from capture time because minutes or
          hours may have passed; the old handle is already closed.)
        - Calls GetProcessTimes and compares the 64-bit FILETIME.
        - OpenProcess returning NULL (no access or no such PID) is EXITED.
        - GetProcessTimes failure is INCONCLUSIVE.
        - A creation-time match is MATCH; mismatch is MISMATCH.

    Args:
        identity: The baseline ProcessIdentity captured at start() time.

    Returns:
        An IdentityVerificationResult.
    """
    if _IS_WINDOWS:
        return _verify_identity_windows(identity)
    return _verify_identity_posix(identity)


def _verify_identity_posix(identity: ProcessIdentity) -> IdentityVerificationResult:
    """POSIX-side implementation of identity verification."""
    pid = identity.pid

    # ---- boot_id pre-check -----------------------------------------------
    # If we stored a boot_id at start time, we MUST check it now.
    # A mismatch means the system rebooted and the PID is definitely stale.
    # If the current boot_id is unreadable (None), we cannot confirm the
    # system is still on the same boot session - this is INCONCLUSIVE, not
    # safe to ignore: we refuse rather than risk signalling a recycled PID.
    if identity.boot_id is not None:
        current_boot_id = _read_boot_id_posix()
        if current_boot_id is None:
            logger.debug(
                "Cannot read current boot_id to verify PID %s "
                "(stored boot_id=%s); returning INCONCLUSIVE",
                pid, identity.boot_id,
            )
            return IdentityVerificationResult.INCONCLUSIVE
        if current_boot_id != identity.boot_id:
            logger.debug(
                "Boot ID mismatch for PID %s: stored=%s current=%s",
                pid, identity.boot_id, current_boot_id,
            )
            return IdentityVerificationResult.MISMATCH

    # ---- /proc/<pid>/stat check ------------------------------------------
    try:
        stat_content = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return IdentityVerificationResult.EXITED
    except PermissionError:
        logger.debug("Permission denied reading /proc/%s/stat", pid)
        return IdentityVerificationResult.INCONCLUSIVE
    except OSError as exc:
        logger.debug("OSError reading /proc/%s/stat: %s", pid, exc)
        return IdentityVerificationResult.INCONCLUSIVE

    parsed = _parse_proc_stat(stat_content)
    if parsed is None:
        logger.debug("Failed to parse /proc/%s/stat", pid)
        return IdentityVerificationResult.INCONCLUSIVE

    state, starttime = parsed

    # Zombie: the process has exited but hasn't been reaped yet.
    if state == "Z":
        return IdentityVerificationResult.EXITED

    if starttime == identity.start_time:
        return IdentityVerificationResult.MATCH

    logger.debug(
        "Starttime mismatch for PID %s: stored=%d current=%d",
        pid, identity.start_time, starttime,
    )
    return IdentityVerificationResult.MISMATCH


def _verify_identity_windows(identity: ProcessIdentity) -> IdentityVerificationResult:
    """Windows-side implementation of identity verification."""
    kernel32 = _get_kernel32()
    pid = identity.pid

    # Request QUERY_LIMITED_INFORMATION for GetProcessTimes and
    # SYNCHRONIZE for WaitForSingleObject (to check if handle is signalled).
    access = _WIN_PROCESS_QUERY_LIMITED_INFORMATION | _WIN_SYNCHRONIZE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        last_error = kernel32.GetLastError()
        if last_error == _WIN_ERROR_ACCESS_DENIED:
            logger.debug(
                "OpenProcess for PID %s returned ACCESS_DENIED; "
                "process exists but is inaccessible → INCONCLUSIVE",
                pid,
            )
            return IdentityVerificationResult.INCONCLUSIVE
        if last_error == _WIN_ERROR_INVALID_PARAMETER:
            logger.debug(
                "OpenProcess for PID %s failed with INVALID_PARAMETER "
                "(no such process) → EXITED",
                pid,
            )
            return IdentityVerificationResult.EXITED
        logger.warning(
            "OpenProcess for PID %s failed with unexpected error %d → INCONCLUSIVE",
            pid, last_error,
        )
        return IdentityVerificationResult.INCONCLUSIVE

    try:
        creation = _FILETIME()
        exit_ft = _FILETIME()
        kernel_ft = _FILETIME()
        user_ft = _FILETIME()

        success = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_ft),
            ctypes.byref(kernel_ft),
            ctypes.byref(user_ft),
        )
        if not success:
            last_error = kernel32.GetLastError()
            logger.debug("GetProcessTimes failed for PID %s (GetLastError=%d)", pid, last_error)
            return IdentityVerificationResult.INCONCLUSIVE

        current_start_time = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        if current_start_time != identity.start_time:
            logger.debug(
                "Creation-time mismatch for PID %s: stored=%d current=%d",
                pid, identity.start_time, current_start_time,
            )
            return IdentityVerificationResult.MISMATCH

        # Creation time matches. Check whether the process is alive or already terminated.
        # A process object handle is signalled (WAIT_OBJECT_0) when the process terminates.
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result == _WIN_WAIT_OBJECT_0:
            logger.debug("Process handle for PID %s is signalled (WAIT_OBJECT_0) → EXITED", pid)
            return IdentityVerificationResult.EXITED
        if wait_result == _WIN_WAIT_TIMEOUT:
            return IdentityVerificationResult.MATCH

        logger.debug(
            "WaitForSingleObject returned unexpected value 0x%X for PID %s → INCONCLUSIVE",
            wait_result, pid,
        )
        return IdentityVerificationResult.INCONCLUSIVE
    finally:
        kernel32.CloseHandle(handle)


def _reap_if_our_child_posix(pid: int) -> None:
    """POSIX only: best-effort, non-blocking reap of a process.

    A subprocess Citrus started remains a zombie entry in the process
    table on POSIX systems - still visible to os.kill(pid, 0) - until
    its parent collects its exit status via wait(). Since Citrus is
    the direct parent of every service process within the same
    running session, this must be done explicitly, or a service that
    has already exited would incorrectly appear to still be running
    forever. This has no Windows equivalent - Windows exit status is
    retained on the process object itself and doesn't require the
    parent to "reap" it, so this function must never be called when
    _IS_WINDOWS is True (os.WNOHANG doesn't exist there).

    Args:
        pid: The process ID to attempt to reap.
    """
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass  # Not our child (e.g. Citrus was restarted since it started) - nothing to reap.
    except OSError as exc:
        logger.debug("Non-blocking reap probe failed for PID %s: %s", pid, exc)


def _is_running_posix(pid: int) -> bool:
    """POSIX: check whether a PID is alive via signal-0 probing.

    Reaps the PID first if it's our own child (see
    _reap_if_our_child_posix), then sends signal 0, which performs
    existence/permission checks without delivering an actual signal -
    the standard, portable POSIX way to probe a PID.

    Args:
        pid: The process ID to check.

    Returns:
        True if a process with that PID exists and is reachable.
    """
    _reap_if_our_child_posix(pid)

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours to signal - shouldn't happen
        # for something we started ourselves, but "it exists" is
        # still the honest answer here.
        return True
    except OSError as exc:
        logger.debug("Unexpected error probing PID %s: %s", pid, exc)
        return False


def _is_running_windows(pid: int) -> bool:
    """Windows: check whether a PID is alive via WaitForSingleObject.

    Uses ctypes to call OpenProcess/WaitForSingleObject directly.
    os.kill(pid, 0) must NOT be used for this on Windows: CPython
    implements any nonzero os.kill() signal on Windows by opening the
    process and calling TerminateProcess, so a naive "signal 0" probe
    ported from POSIX would actually risk killing the process instead
    of just checking it.

    WaitForSingleObject with a zero timeout is the correct, unambiguous
    API for this check: it returns WAIT_TIMEOUT (0x102) when the
    process is still running, and WAIT_OBJECT_0 (0x0) when it has
    terminated - without any dependence on the process's actual exit
    code. This avoids the GetExitCodeProcess == 259 false-positive
    (exit code 259 is numerically equal to STILL_ACTIVE but is a
    legitimate exit code a process can use).

    SYNCHRONIZE (0x00100000) is requested in addition to
    PROCESS_QUERY_LIMITED_INFORMATION because WaitForSingleObject
    requires it; PROCESS_QUERY_LIMITED_INFORMATION alone is sufficient
    for GetExitCodeProcess but not for WaitForSingleObject.

    Args:
        pid: The process ID to check.

    Returns:
        True if the process exists and is still running (WAIT_TIMEOUT).
        False if the process has terminated (WAIT_OBJECT_0) or cannot
        be queried (OpenProcess failure or WAIT_FAILED).
    """
    kernel32 = _get_kernel32()
    access = _WIN_PROCESS_QUERY_LIMITED_INFORMATION | _WIN_SYNCHRONIZE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        # No such process, or it exists but we can't open it (e.g. a
        # different-user process reusing this PID after our service
        # exited) - treat as "not our service running".
        return False

    try:
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result == _WIN_WAIT_TIMEOUT:
            return True
        if wait_result == _WIN_WAIT_OBJECT_0:
            return False
        # WAIT_FAILED or any other unexpected value: the API call
        # itself failed.  Log it and report "not running" rather than
        # silently treating an API error as proof of either state.
        logger.debug(
            "WaitForSingleObject returned unexpected value 0x%X for PID %s", wait_result, pid
        )
        return False
    finally:
        kernel32.CloseHandle(handle)



def is_running(pid: Optional[int]) -> bool:
    """Check whether a process with the given PID is actually alive.

    Dispatches to the POSIX or Windows implementation based on
    _IS_WINDOWS, determined once at import time - each platform only
    ever executes its own code path, never the other's. Never trusts
    a stored PID at face value; this is always the source of truth.

    Args:
        pid: The process ID to check, or None.

    Returns:
        True if a process with that PID exists and is running.
    """
    if pid is None:
        return False

    if _IS_WINDOWS:
        return _is_running_windows(pid)
    return _is_running_posix(pid)


def _validate_executable(command: List[str]) -> Optional[str]:
    """Check that a service's executable can plausibly be run.

    Args:
        command: The argv-style command list.

    Returns:
        None if the executable looks runnable, otherwise a short,
        user-facing reason it isn't.
    """
    if not command:
        return "Command is empty."

    executable = command[0]
    if shutil.which(executable) is not None:
        return None

    candidate = Path(executable)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return None

    return f"Executable not found: {executable}"


def start(definition: ServiceDefinition) -> ServiceOperationResult:
    """Start a service as a tracked, detached subprocess.

    The process is started in its own session (start_new_session=True)
    so it survives Citrus exiting or its controlling terminal closing
    - services are meant to keep running independently of Citrus.
    stdout/stderr are redirected to the service's own dedicated log
    file; nothing from the child process ever reaches Citrus's own
    logs.

    Args:
        definition: The service to start.

    Returns:
        A ServiceOperationResult. On success, result.state holds the
        new RUNNING runtime state (with PID). On failure - including
        a process that exits immediately - result.error explains why
        and result.state (if present) reflects a FAILED state.
    """
    validation_error = _validate_executable(definition.command)
    if validation_error:
        logger.warning("Refusing to start '%s': %s", definition.id, validation_error)
        return ServiceOperationResult(success=False, message="Failed to start.", error=validation_error)

    if not definition.working_directory.is_dir():
        error = f"Working directory does not exist: {definition.working_directory}"
        logger.warning("Refusing to start '%s': %s", definition.id, error)
        return ServiceOperationResult(success=False, message="Failed to start.", error=error)

    config.ensure_service_runtime_directories()
    log_path = config.SERVICE_LOG_DIR / f"{definition.id}.log"
    merged_env = {**os.environ, **definition.environment}

    # POSIX: start_new_session (setsid) makes the child its own
    # process group leader, so stop() can signal the whole group.
    # Windows has no equivalent of setsid; CREATE_NEW_PROCESS_GROUP is
    # its closest analog and isolates the child from console control
    # signals delivered to the parent Citrus console.
    # subprocess.CREATE_NEW_PROCESS_GROUP only exists as an attribute on
    # Windows builds of the subprocess module, so it must never be
    # referenced outside this platform branch.
    if _IS_WINDOWS:
        popen_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        popen_kwargs = {"start_new_session": True}

    try:
        log_file = open(log_path, "a", encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not open log file for '%s': %s", definition.id, exc)
        return ServiceOperationResult(success=False, message="Failed to start.", error=f"Could not open log file: {exc}")

    try:
        log_file.write(f"\n--- Citrus starting '{definition.id}' at {datetime.now().isoformat(timespec='seconds')} ---\n")
        log_file.flush()
        process = subprocess.Popen(
            definition.command,
            cwd=str(definition.working_directory),
            env=merged_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **popen_kwargs,
        )
    except OSError as exc:
        logger.warning("Failed to start '%s': %s", definition.id, exc)
        return ServiceOperationResult(success=False, message="Failed to start.", error=str(exc))
    finally:
        log_file.close()

    # Capture identity immediately after Popen, before the grace-period
    # sleep.  The process is most likely still running at this point; if it
    # has already exited, _capture_identity returns None (e.g. /proc/<pid>/stat
    # is already gone on POSIX).  That's fine: the grace-period check below
    # will catch the early exit and report FAILED with identity=None, which is
    # correct - there's nothing meaningful to re-verify for a failed start.
    identity = _capture_identity(process.pid)
    if identity is None:
        logger.debug(
            "Identity capture failed for '%s' PID %s "
            "(process may have already exited before /proc was read)",
            definition.id, process.pid,
        )

    # A process that dies within this short grace window almost
    # always indicates a bad command or misconfiguration rather than
    # a real, working service - report that as a failure instead of
    # a misleading "RUNNING" that flips to STOPPED a moment later.
    time.sleep(_START_GRACE_PERIOD_SECONDS)
    exit_code = process.poll()
    if exit_code is not None:
        error = f"Process exited immediately with code {exit_code}. See {log_path}."
        logger.warning("Service '%s' failed to stay running: %s", definition.id, error)
        state = ServiceRuntimeState(
            status=ServiceStatus.FAILED,
            pid=None,
            identity=None,
            started_at=None,
            last_exit_code=exit_code,
            last_error=error,
            last_exit_at=datetime.now(),
        )
        return ServiceOperationResult(success=False, message="Failed to start.", error=error, state=state)

    state = ServiceRuntimeState(
        status=ServiceStatus.RUNNING,
        pid=process.pid,
        identity=identity,
        started_at=datetime.now(),
        last_exit_code=None,
        last_error=None,
    )
    logger.info(
        "Service '%s' started with PID %s (identity captured: %s)",
        definition.id, process.pid, identity is not None,
    )
    return ServiceOperationResult(success=True, message="Started.", state=state)


def stop(
    pid: int,
    identity: Optional[ProcessIdentity],
    timeout: float = _DEFAULT_STOP_TIMEOUT_SECONDS,
) -> ServiceOperationResult:
    """Stop a tracked process gracefully, escalating only if needed.

    A ProcessIdentity MUST be provided.  If identity is None (legacy
    state or a process whose identity capture failed), the operation is
    refused immediately rather than signalling a potentially-recycled PID.

    Input consistency checks:
        - pid must equal identity.pid (caller passed the wrong identity).
        - identity.platform must match the active process-control backend
          (``"nt"`` on Windows, ``"posix"`` on POSIX/Linux/Android).
          A mismatch indicates corrupted state; we refuse rather than
          applying the wrong OS primitives.

    Windows stop path (handle-retained):
        A single handle opened with
        QUERY_LIMITED_INFORMATION | SYNCHRONIZE | TERMINATE
        is used for identity verification, exit-state checking (via
        WaitForSingleObject), and forced termination (via TerminateProcess).
        The handle is closed in a finally block.  No second OpenProcess
        call is ever made for the same stop operation, so the process
        instance that was verified is also the one that is terminated.

        Graceful console event safety:
            Windows does not expose a handle-based console event API;
            console control events (such as CTRL_BREAK_EVENT) operate on bare
            PIDs or process group IDs.  Because a PID-based signal cannot be
            guaranteed to target the verified process instance, Citrus
            conservatively refuses the unsafe graceful signal on Windows to
            avoid risking an unrelated process, and proceeds directly to
            verified handle-based forced termination.

    POSIX stop path:
        Identity is verified before the graceful signal.  Before escalating
        to SIGKILL, identity is verified a SECOND time: if the PID was
        recycled between SIGTERM and SIGKILL, the second check catches it
        and the escalation is refused.

        POSIX TOCTOU note: even after a MATCH result, there is an
        unavoidable race between the /proc read and the subsequent
        os.killpg() call.  This race cannot be closed on Android
        8.1 (kernel 3.18/4.4) without pidfd_open() (Linux 5.3+).

    Args:
        pid: The process ID to stop.
        identity: The ProcessIdentity baseline captured at start() time.
            Must not be None; callers without an identity baseline must
            refuse the stop operation before calling this function.
        timeout: Maximum seconds to wait for graceful termination.

    Returns:
        A ServiceOperationResult with the outcome.
        result.error includes the verification result name when the
        operation is refused so the caller can set state correctly.
    """
    # ---- No identity → refuse -------------------------------------------
    if identity is None:
        error = (
            f"Refusing to stop PID {pid}: no process identity baseline is available. "
            "Start the service fresh to establish a new baseline."
        )
        logger.warning(error)
        return ServiceOperationResult(success=False, message="Stop refused.", error=error)

    # ---- Input consistency checks ----------------------------------------
    if pid != identity.pid:
        error = (
            f"Refusing to stop PID {pid}: identity baseline is for PID {identity.pid}. "
            "The pid argument and identity.pid must match."
        )
        logger.warning(error)
        return ServiceOperationResult(success=False, message="Stop refused.", error=error)

    expected_platform = "nt" if _IS_WINDOWS else "posix"
    if identity.platform != expected_platform:
        error = (
            f"Refusing to stop PID {pid}: identity platform '{identity.platform}' "
            f"does not match the active OS platform '{expected_platform}'. "
            "State may be from a different boot environment."
        )
        logger.warning(error)
        return ServiceOperationResult(success=False, message="Stop refused.", error=error)

    # ---- Dispatch to platform-specific implementation --------------------
    if _IS_WINDOWS:
        return _stop_windows(pid, identity, timeout)
    return _stop_posix(pid, identity, timeout)


def _stop_windows(
    pid: int,
    identity: ProcessIdentity,
    timeout: float,
) -> ServiceOperationResult:
    """Windows stop: verify identity and terminate using a single retained handle.

    Opens ONE handle with QUERY_LIMITED_INFORMATION | SYNCHRONIZE | TERMINATE.
    That handle is used for:
      1. GetProcessTimes → identity verification (creation-time fingerprint)
      2. WaitForSingleObject → check if process has already terminated
      3. TerminateProcess → forced termination on the verified handle

    The handle is closed in a finally block regardless of outcome.  Because
    the same handle is used for both verification and forced termination, it
    is not possible to verify one process instance and terminate a different
    one (no PID can be recycled while a handle to it is held open).

    Graceful signal safety:
        Windows does not support handle-targeted console signals.  CPython's
        os.kill(pid, CTRL_BREAK_EVENT) operates on bare process group IDs /
        PIDs and cannot be targeted through a process handle.  To eliminate
        the risk of signalling an unrelated process on PID reuse, Citrus
        conservatively refuses the unsafe PID-based graceful signal and
        proceeds directly to verified handle-based forced termination.
    """
    kernel32 = _get_kernel32()

    # Open with all access rights needed for verify + poll + terminate.
    handle = kernel32.OpenProcess(_WIN_PROCESS_VERIFY_AND_TERMINATE, False, pid)
    if not handle:
        last_error = kernel32.GetLastError()
        if last_error == _WIN_ERROR_ACCESS_DENIED:
            error = (
                f"Refusing to stop PID {pid}: OpenProcess returned ACCESS_DENIED. "
                "The process exists but is inaccessible → INCONCLUSIVE."
            )
            logger.warning(error)
            return ServiceOperationResult(success=False, message="Stop refused.", error=error)
        if last_error == _WIN_ERROR_INVALID_PARAMETER:
            logger.info(
                "OpenProcess for PID %s returned INVALID_PARAMETER (no such process); "
                "treating as EXITED.", pid
            )
            return ServiceOperationResult(success=True, message="Already stopped.")
        error = (
            f"Refusing to stop PID {pid}: OpenProcess failed with unexpected error "
            f"{last_error} → INCONCLUSIVE."
        )
        logger.warning(error)
        return ServiceOperationResult(success=False, message="Stop refused.", error=error)

    try:
        # ---- Identity verification ----------------------------------------
        creation = _FILETIME()
        exit_ft = _FILETIME()
        kernel_ft = _FILETIME()
        user_ft = _FILETIME()

        success = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_ft),
            ctypes.byref(kernel_ft),
            ctypes.byref(user_ft),
        )
        if not success:
            last_err = kernel32.GetLastError()
            error = (
                f"Refusing to stop PID {pid}: GetProcessTimes failed (GetLastError={last_err}) "
                "(identity INCONCLUSIVE). Manual inspection required."
            )
            logger.warning(error)
            return ServiceOperationResult(success=False, message="Stop refused.", error=error)

        current_start_time = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        if current_start_time != identity.start_time:
            error = (
                f"Refusing to stop PID {pid}: identity MISMATCH "
                f"(stored start_time={identity.start_time}, "
                f"current={current_start_time}). PID was recycled."
            )
            logger.warning(error)
            return ServiceOperationResult(success=False, message="Stop refused.", error=error)

        # ---- Check if process has already terminated (handle is signalled) -
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result == _WIN_WAIT_OBJECT_0:
            logger.info("Process %s is already terminated (handle is signalled); treating as stopped.", pid)
            return ServiceOperationResult(success=True, message="Already stopped.")
        if wait_result != _WIN_WAIT_TIMEOUT:
            error = (
                f"Refusing to stop PID {pid}: WaitForSingleObject returned unexpected result "
                f"0x{wait_result:X} → INCONCLUSIVE."
            )
            logger.warning(error)
            return ServiceOperationResult(success=False, message="Stop refused.", error=error)

        # ---- Graceful signal refusal & verified handle-based termination ---
        # Windows does not provide handle-targeted console signals.  Sending a
        # PID-based console event is unsafe because it cannot be verified against
        # the handle.  We refuse the unsafe graceful signal and escalate directly
        # to verified handle-based TerminateProcess.
        logger.info(
            "Graceful console signal refused for PID %s (PID-based console signals "
            "are not identity-safe on Windows); escalating directly to verified "
            "handle-based TerminateProcess.",
            pid,
        )

        if not kernel32.TerminateProcess(handle, 1):
            last_err = kernel32.GetLastError()
            error = f"TerminateProcess failed for PID {pid} (GetLastError={last_err})."
            logger.error(error)
            return ServiceOperationResult(success=False, message="Failed to stop.", error=error)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            wait_result = kernel32.WaitForSingleObject(handle, 0)
            if wait_result == _WIN_WAIT_OBJECT_0:
                logger.info("Process %s stopped after TerminateProcess", pid)
                return ServiceOperationResult(success=True, message="Stopped (forced).")
            time.sleep(_STOP_POLL_INTERVAL_SECONDS)

        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result != _WIN_WAIT_OBJECT_0:
            error = f"Process {pid} is still alive after TerminateProcess."
            logger.error(error)
            return ServiceOperationResult(success=False, message="Failed to stop.", error=error)

        logger.info("Process %s stopped after TerminateProcess", pid)
        return ServiceOperationResult(success=True, message="Stopped (forced).")

    finally:
        kernel32.CloseHandle(handle)


def _stop_posix(
    pid: int,
    identity: ProcessIdentity,
    timeout: float,
) -> ServiceOperationResult:
    """POSIX stop: verify identity twice — before SIGTERM and before SIGKILL.

    The first verification gates the graceful signal.  If the process
    does not exit within ``timeout``, a SECOND verification is performed
    before escalating to SIGKILL.  If the PID was recycled between SIGTERM
    and SIGKILL, the second check catches it and the escalation is refused.

    POSIX TOCTOU note (documented in stop() docstring):
        Each verification → signal step has an unavoidable race on the
        target platform.  Two separate checks reduce the window but do
        not eliminate it.
    """
    # ---- First identity verification: gate graceful signal ----------------
    verification = verify_process_identity(identity)

    if verification == IdentityVerificationResult.EXITED:
        logger.info("PID %s verified as already exited (EXITED).", pid)
        return ServiceOperationResult(success=True, message="Already stopped.")

    if verification != IdentityVerificationResult.MATCH:
        error = (
            f"Refusing to stop PID {pid}: identity verification returned "
            f"{verification.value} before graceful signal. "
            "No signal sent."
        )
        logger.warning(error)
        return ServiceOperationResult(success=False, message="Stop refused.", error=error)

    # verification == MATCH: graceful signal permitted.
    # POSIX TOCTOU race window opens here (unavoidable).
    logger.debug("Identity MATCH for PID %s; sending SIGTERM.", pid)
    _send_signal_posix(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(pid):
            logger.info("Process %s stopped gracefully", pid)
            return ServiceOperationResult(success=True, message="Stopped.")
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)

    # ---- Second identity verification: gate forced kill -------------------
    # The process did not exit gracefully.  Before escalating, we re-verify
    # that the PID still belongs to our service.  If it was recycled in the
    # interval between SIGTERM and now, we refuse SIGKILL.
    logger.warning(
        "Process %s did not stop within %.1fs; re-verifying identity before SIGKILL",
        pid, timeout,
    )
    reverification = verify_process_identity(identity)

    if reverification == IdentityVerificationResult.EXITED:
        logger.info("PID %s exited during graceful window (caught on re-verify).", pid)
        return ServiceOperationResult(success=True, message="Stopped.")

    if reverification != IdentityVerificationResult.MATCH:
        error = (
            f"Refusing to escalate SIGKILL to PID {pid}: re-verification returned "
            f"{reverification.value} after graceful timeout. "
            "PID may have been recycled. No forced kill sent."
        )
        logger.warning(error)
        return ServiceOperationResult(success=False, message="Stop refused (escalation).", error=error)

    # Second MATCH: escalate safely.
    # POSIX TOCTOU race window opens again here (unavoidable).
    logger.warning("Escalating to SIGKILL for PID %s", pid)
    _send_signal_posix(pid, signal.SIGKILL)
    time.sleep(_STOP_POLL_INTERVAL_SECONDS)

    if is_running(pid):
        error = f"Process {pid} is still alive after SIGKILL."
        logger.error(error)
        return ServiceOperationResult(success=False, message="Failed to stop.", error=error)

    logger.info("Process %s stopped after SIGKILL", pid)
    return ServiceOperationResult(success=True, message="Stopped (forced).")


def _send_terminate(pid: int) -> None:
    """Ask a process (and its group) to exit gracefully, per platform.

    NOTE: This function is used by callers that have ALREADY verified
    process identity. It does not re-verify. Use stop() for the full
    identity-verified stop sequence.

    Args:
        pid: The target process ID.
    """
    if _IS_WINDOWS:
        _send_terminate_windows(pid)
    else:
        _send_signal_posix(pid, signal.SIGTERM)


def _send_kill(pid: int) -> None:
    """Force a process (and its group) to exit immediately, per platform.

    NOTE: On Windows this function is no longer called from stop(), which
    uses the handle-retained _stop_windows() instead. It is kept for
    any external callers that need a best-effort forced kill.

    POSIX: re-verify identity before calling this if called outside the
    stop() flow, to avoid SIGKILL racing on a recycled PID.

    Args:
        pid: The target process ID.
    """
    if _IS_WINDOWS:
        _send_kill_windows(pid)
    else:
        _send_signal_posix(pid, signal.SIGKILL)


def _send_signal_posix(pid: int, sig: "signal.Signals") -> None:
    """POSIX: send a signal to the process group led by pid.

    Citrus starts services with start_new_session=True (setsid), which
    makes each service the leader of its own process group.  The process
    group ID therefore equals the PID immediately after start.

    Safety invariants:
      1. We signal the PROCESS GROUP, not the bare PID.  This ensures
         any child processes spawned by the service also receive the
         signal.
      2. We verify that pgid == pid BEFORE sending the signal.  If
         os.getpgid() returns a value different from pid, the process is
         no longer the group leader it was when it started - it has either
         exited or joined a different group.  In that case we REFUSE to
         signal rather than touching an unrelated group.
      3. There is NO fallback to os.kill(pid, sig) when group ownership
         cannot be established.  Signalling an unverified bare PID after
         identity verification is explicitly prohibited: it reintroduces
         the PID-reuse risk (a new process may have acquired the same PID
         without being the original service).

    POSIX TOCTOU note: as documented in the module docstring, there is
    still an unavoidable race between pgid verification here and
    os.killpg() below.  This is unavoidable on the target platform
    (Android 8.1, kernel 3.18/4.4) without pidfd_open() (Linux 5.3+).

    Args:
        pid: The target process ID (expected to be a process group leader).
        sig: The signal to send (SIGTERM or SIGKILL).
    """
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    except OSError as exc:
        logger.debug("getpgid failed for PID %s (%s); refusing to signal", pid, exc)
        return

    if pgid != pid:
        logger.debug(
            "PID %s is no longer its own group leader (pgid=%s); "
            "refusing to send %s (no bare-PID fallback)",
            pid, pgid, sig.name,
        )
        return

    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError as exc:
        logger.warning("killpg failed for PID %s (pgid=%s): %s", pid, pgid, exc)


def _send_terminate_windows(pid: int) -> None:
    """Windows: graceful termination via bare PID is refused for safety.

    CPython's os.kill(pid, CTRL_BREAK_EVENT) uses GenerateConsoleCtrlEvent,
    which operates on bare process group IDs rather than process handles.
    Because Windows does not provide a handle-based console event API,
    a PID-based console event cannot be guaranteed to target the verified
    process instance and risks delivering signals to an unrelated recycled
    process.  Citrus therefore refuses bare-PID console events on Windows;
    use stop(pid, identity) for verified handle-based process termination.

    Args:
        pid: The target process ID.
    """
    logger.warning(
        "Refusing _send_terminate_windows on bare PID %s: Windows PID-based "
        "console events cannot be verified against a process handle.",
        pid,
    )


def _send_kill_windows(pid: int) -> None:
    """Windows: forcefully terminate a process via the Win32 API.

    Uses TerminateProcess through ctypes - the standard, reliable way
    to force-kill an arbitrary PID on Windows from the standard
    library alone, without shelling out to taskkill.

    NOTE: This function opens a new handle each time it is called and
    does NOT retain the verified handle from identity checking.  It is
    kept for external callers (_send_kill) but is no longer called from
    the stop() path, which uses _stop_windows() for handle retention.

    Args:
        pid: The target process ID.
    """
    kernel32 = _get_kernel32()
    handle = kernel32.OpenProcess(_WIN_PROCESS_TERMINATE, False, pid)
    if not handle:
        logger.debug("OpenProcess for termination failed for PID %s (already gone?)", pid)
        return

    try:
        if not kernel32.TerminateProcess(handle, 1):
            logger.warning("TerminateProcess failed for PID %s", pid)
    finally:
        kernel32.CloseHandle(handle)
