"""
runner.py
=========
Actual OS-process execution for the Service Manager: starting a
service as a subprocess, stopping it safely, and checking whether a
previously-started process is still alive. Uses only the Python
standard library.

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
    call the Win32 API directly (OpenProcess / GetExitCodeProcess /
    TerminateProcess) - the standard, safe way to do this from the
    standard library alone.
"""

import ctypes
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core import config
from core.logging.logger import get_logger
from core.service_manager.models import ServiceDefinition, ServiceOperationResult, ServiceRuntimeState, ServiceStatus

logger = get_logger(__name__)

_STOP_POLL_INTERVAL_SECONDS: float = 0.2
_DEFAULT_STOP_TIMEOUT_SECONDS: float = 5.0
_START_GRACE_PERIOD_SECONDS: float = 0.3

_IS_WINDOWS: bool = os.name == "nt"

# Win32 constants used by the Windows-only functions below (plain
# ctypes primitives are used instead of ctypes.wintypes so this
# module never needs a Windows-only import at load time).
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_PROCESS_TERMINATE = 0x0001
_WIN_STILL_ACTIVE = 259


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
    """Windows: check whether a PID is alive via the Win32 API.

    Uses ctypes to call OpenProcess/GetExitCodeProcess directly.
    os.kill(pid, 0) must NOT be used for this on Windows: CPython
    implements any nonzero os.kill() signal on Windows by opening the
    process and calling TerminateProcess, so a naive "signal 0" probe
    ported from POSIX would actually risk killing the process instead
    of just checking it. GetExitCodeProcess distinguishes "process is
    still running" (STILL_ACTIVE) from "process exited with code X",
    which is a real status check, not just a PID-existence check.

    Args:
        pid: The process ID to check.

    Returns:
        True if the process exists and its exit code is STILL_ACTIVE.
    """
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # No such process, or it exists but we can't query it (e.g.
        # a different user's process reusing this PID) - treat as
        # "not our service running" rather than guessing.
        return False

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            logger.debug("GetExitCodeProcess failed for PID %s", pid)
            return False
        return exit_code.value == _WIN_STILL_ACTIVE
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
    # its closest analog and is what lets stop() send a CTRL_BREAK
    # event to the child (and anything it spawned) instead of the
    # whole console. subprocess.CREATE_NEW_PROCESS_GROUP only exists
    # as an attribute on Windows builds of the subprocess module, so
    # it must never be referenced outside this platform branch.
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
            started_at=None,
            last_exit_code=exit_code,
            last_error=error,
        )
        return ServiceOperationResult(success=False, message="Failed to start.", error=error, state=state)

    state = ServiceRuntimeState(
        status=ServiceStatus.RUNNING,
        pid=process.pid,
        started_at=datetime.now(),
        last_exit_code=None,
        last_error=None,
    )
    logger.info("Service '%s' started with PID %s", definition.id, process.pid)
    return ServiceOperationResult(success=True, message="Started.", state=state)


def stop(pid: int, timeout: float = _DEFAULT_STOP_TIMEOUT_SECONDS) -> ServiceOperationResult:
    """Stop a tracked process gracefully, escalating only if needed.

    Asks the process (and its group, on both platforms - see start())
    to exit gracefully first and waits up to `timeout` seconds,
    polling periodically. Only escalates to a forceful kill if the
    process is still alive once the timeout elapses. Never touches
    any PID other than the one given.

    Args:
        pid: The process ID to stop.
        timeout: Maximum seconds to wait for graceful termination.

    Returns:
        A ServiceOperationResult confirming whether the process is
        actually gone.
    """
    if not is_running(pid):
        return ServiceOperationResult(success=True, message="Already stopped.")

    _send_terminate(pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(pid):
            logger.info("Process %s stopped gracefully", pid)
            return ServiceOperationResult(success=True, message="Stopped.")
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)

    logger.warning("Process %s did not stop within %.1fs; escalating to a forced kill", pid, timeout)
    _send_kill(pid)
    time.sleep(_STOP_POLL_INTERVAL_SECONDS)

    if is_running(pid):
        error = f"Process {pid} is still alive after a forced kill."
        logger.error(error)
        return ServiceOperationResult(success=False, message="Failed to stop.", error=error)

    logger.info("Process %s stopped after a forced kill", pid)
    return ServiceOperationResult(success=True, message="Stopped (forced).")


def _send_terminate(pid: int) -> None:
    """Ask a process (and its group) to exit gracefully, per platform.

    Args:
        pid: The target process ID.
    """
    if _IS_WINDOWS:
        _send_terminate_windows(pid)
    else:
        _send_signal_posix(pid, signal.SIGTERM)


def _send_kill(pid: int) -> None:
    """Force a process (and its group) to exit immediately, per platform.

    Args:
        pid: The target process ID.
    """
    if _IS_WINDOWS:
        _send_kill_windows(pid)
    else:
        _send_signal_posix(pid, signal.SIGKILL)


def _send_signal_posix(pid: int, sig: "signal.Signals") -> None:
    """POSIX: send a signal to a process's group, falling back to the PID.

    Args:
        pid: The target process ID (also its process group id, since
            services are started with start_new_session=True).
        sig: The signal to send (SIGTERM or SIGKILL).
    """
    try:
        os.killpg(os.getpgid(pid), sig)
        return
    except (ProcessLookupError, PermissionError):
        return
    except OSError as exc:
        logger.debug("killpg failed for PID %s (%s); falling back to kill()", pid, exc)

    try:
        os.kill(pid, sig)
    except OSError as exc:
        logger.warning("Failed to send %s to PID %s: %s", sig.name, pid, exc)


def _send_terminate_windows(pid: int) -> None:
    """Windows: ask a process group to exit gracefully.

    Sends CTRL_BREAK_EVENT, which only reaches processes started with
    CREATE_NEW_PROCESS_GROUP (see start()) - the closest Windows
    analog to POSIX's SIGTERM-to-process-group. A process with no
    handler for this event terminates by default, which is the
    behavior our test service and any simple service exhibit. This is
    best-effort: if it fails or is ignored, stop()'s timeout/escalate
    logic still forces the process closed via _send_kill_windows.

    Args:
        pid: The target process ID.
    """
    try:
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    except OSError as exc:
        logger.debug("CTRL_BREAK_EVENT failed for PID %s (%s); will rely on forced kill if needed", pid, exc)


def _send_kill_windows(pid: int) -> None:
    """Windows: forcefully terminate a process via the Win32 API.

    Uses TerminateProcess through ctypes - the standard, reliable way
    to force-kill an arbitrary PID on Windows from the standard
    library alone, without shelling out to taskkill.

    Args:
        pid: The target process ID.
    """
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_WIN_PROCESS_TERMINATE, False, pid)
    if not handle:
        logger.debug("OpenProcess for termination failed for PID %s (already gone?)", pid)
        return

    try:
        if not kernel32.TerminateProcess(handle, 1):
            logger.warning("TerminateProcess failed for PID %s", pid)
    finally:
        kernel32.CloseHandle(handle)
