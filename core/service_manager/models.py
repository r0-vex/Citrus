"""
models.py
=========
Data structures for the Service Manager: a service's static
definition (what to run), its runtime state (what's happening right
now), and structured results from manager/runner operations. No
process-control behavior lives here - only data and small, obvious
helpers.

Process identity
----------------
ProcessIdentity captures a platform-specific fingerprint of a running
process at the moment Citrus starts it.  A bare PID stored in the
state file can be recycled by an unrelated process after the original
service exits; ProcessIdentity lets runner.py detect that mismatch
before issuing any OS signals.

See runner.verify_process_identity() for the verification logic.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ProcessIdentity:
    """Immutable OS-level fingerprint of one specific process instance.

    A PID alone cannot prove that the process currently holding it is
    the same process Citrus started, because PIDs are recycled by the
    OS once a process exits.  ProcessIdentity adds a creation-time
    token that lets runner.py reject a recycled PID before issuing any
    signal.

    Attributes:
        pid: The process ID, mirrored here for cross-checking.
        platform: ``"nt"`` on Windows, ``"posix"`` on Linux/Android/Termux.
        start_time: Platform-specific creation-time token:
            - POSIX: the ``starttime`` field (field 22) from
              ``/proc/<pid>/stat``, measured in clock ticks since
              system boot.  Monotonically increasing within a boot and
              unique per PID within the same boot.
            - Windows: the process creation time as a 64-bit Windows
              FILETIME integer (100-nanosecond intervals since
              1601-01-01).  Obtained via ``GetProcessTimes``.
        boot_id: On Linux, the UUID from
            ``/proc/sys/kernel/random/boot_id`` recorded at the moment
            the service was started.  Used to immediately reject any
            persisted PID after a system reboot, even before reading
            ``/proc/<pid>/stat``.  ``None`` on Windows or when the
            file was unreadable.
    """

    pid: int
    platform: str          # "nt" or "posix"
    start_time: int        # POSIX: clock ticks; Windows: 64-bit FILETIME
    boot_id: Optional[str] = None  # Linux only; None on Windows


class ServiceStatus(str, Enum):
    """Lifecycle states a managed service can be in."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ServiceDefinition:
    """A service's static configuration - what to run and how.

    Attributes:
        id: Unique, filesystem/JSON-safe identifier. Also used as the
            config filename stem and the per-service log filename stem.
        name: Human-readable display name.
        command: Argv-style command, e.g. ["python3", "bots/atlas.py"].
        working_directory: Absolute directory the process runs from.
        environment: Extra environment variables merged over the
            current process's environment when the service starts.
        enabled: Whether this service should be offered for starting.
        auto_restart: Reserved for a future auto-restart supervisor;
            stored but not yet acted upon in v1.1.
    """

    id: str
    name: str
    command: List[str]
    working_directory: Path
    environment: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    auto_restart: bool = False


@dataclass
class ServiceRuntimeState:
    """A service's current runtime state - separate from its definition.

    Attributes:
        status: Current lifecycle state.
        pid: Process ID, if currently running (or last known before a
            stop/failure was recorded).
        identity: Process identity fingerprint captured immediately after
            the service was started.  None for stopped/failed services,
            legacy state files written before process identity support,
            or the rare case where identity capture fails because the
            process exited before /proc could be read (on POSIX) or
            GetProcessTimes returned an error (on Windows).

            A non-None identity must be verified by runner.verify_process_identity()
            before any OS signal is sent.  A None identity disables
            signalling - the service must be started fresh.
        started_at: When the process was last started, if known for
            this session (not reconstructed across restarts).
        last_exit_code: Exit code from the most recent run, if known.
            Only ever populated when the OS actually gave us one (a
            process that died within its start grace period); left
            None for exits detected later via a status check, since
            we have no reliable way to retrieve a real exit code then.
        last_error: Human-readable reason for the most recent failure.
        last_exit_at: When this service was last found to have exited
            unexpectedly (i.e. Citrus believed it was RUNNING and then
            discovered the process was gone). None if it has never
            exited unexpectedly, or if its most recent halt was an
            intentional stop instead - see last_stopped_at.
        last_stopped_at: When this service was last stopped
            intentionally via stop_service(). None if it has never
            been stopped, or if its most recent halt was an
            unexpected exit instead - see last_exit_at.
        restart_count: How many times restart_service() has completed
            successfully for this service. Never incremented by
            start_service()/stop_service() called independently -
            only by a full, successful restart.
    """

    status: ServiceStatus = ServiceStatus.STOPPED
    pid: Optional[int] = None
    identity: Optional[ProcessIdentity] = None
    started_at: Optional[datetime] = None
    last_exit_code: Optional[int] = None
    last_error: Optional[str] = None
    last_exit_at: Optional[datetime] = None
    last_stopped_at: Optional[datetime] = None
    restart_count: int = 0

    def runtime_seconds(self) -> Optional[float]:
        """Return how long the service has been running, if it is.

        Returns:
            Elapsed seconds since `started_at`, or None if the service
            isn't running or its start time isn't known.
        """
        if self.status != ServiceStatus.RUNNING or self.started_at is None:
            return None
        return (datetime.now() - self.started_at).total_seconds()

    def to_persisted_dict(self) -> Dict[str, object]:
        """Serialize this state for the JSON runtime-state file.

        Datetimes are stored as ISO 8601 strings, since JSON has no
        native datetime type. started_at is intentionally NOT
        persisted - it reflects an in-memory session's start time and
        is never meant to be reconstructed across a Citrus restart.

        identity is serialized as a nested dict when present so it
        survives a Citrus restart and can be re-verified next session.
        A missing or null identity in the stored dict is treated as
        "no baseline" on load, which disables signalling for that entry
        until the service is re-started.

        Returns:
            A plain, JSON-serializable dict.
        """
        identity_dict: Optional[Dict[str, object]] = None
        if self.identity is not None:
            identity_dict = {
                "pid": self.identity.pid,
                "platform": self.identity.platform,
                "start_time": self.identity.start_time,
                "boot_id": self.identity.boot_id,
            }
        return {
            "status": self.status.value,
            "pid": self.pid,
            "identity": identity_dict,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "last_exit_at": self.last_exit_at.isoformat() if self.last_exit_at else None,
            "last_stopped_at": self.last_stopped_at.isoformat() if self.last_stopped_at else None,
            "restart_count": self.restart_count,
        }

    @classmethod
    def from_persisted_dict(cls, entry: Dict[str, object]) -> "ServiceRuntimeState":
        """Deserialize a persisted dict back into a ServiceRuntimeState.

        Backward compatible with state files written before
        last_exit_at/last_stopped_at/restart_count/identity existed -
        missing fields fall back to sensible defaults rather than
        raising.  started_at is never restored from disk (see
        to_persisted_dict).

        Identity deserialisation notes
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        A missing or null ``"identity"`` key yields ``identity=None``,
        which is safe and backward-compatible.  A present identity dict
        with invalid field values (wrong types, negative start_time, etc.)
        or whose PID does not match the outer ``"pid"`` is treated the
        same as a missing identity: set to None and the state is still loaded.
        This ensures an inconsistent state cannot authorize process signalling.
        This is deliberately more lenient than pid/status parsing because a damaged
        identity baseline is recoverable (the service will be marked UNKNOWN and
        re-started), whereas a bad status value indicates a fundamentally corrupt
        entry.

        Args:
            entry: One service's raw dict, as loaded from the state
                file.

        Returns:
            A ServiceRuntimeState reconstructed from the dict.

        Raises:
            ValueError: If ``"status"`` isn't a recognized ServiceStatus
                value, or if ``"pid"`` is present but not a valid positive
                integer or null. Callers should catch this per-entry so
                one corrupt service's state doesn't discard every other
                service's state too.
        """
        pid = _parse_pid(entry.get("pid"))
        identity = _parse_process_identity(entry.get("identity"))
        if pid is None or (identity is not None and identity.pid != pid):
            identity = None

        return cls(
            status=ServiceStatus(entry.get("status", ServiceStatus.UNKNOWN.value)),
            pid=pid,
            identity=identity,
            started_at=None,
            last_exit_code=entry.get("last_exit_code"),
            last_error=entry.get("last_error"),
            last_exit_at=_parse_optional_datetime(entry.get("last_exit_at")),
            last_stopped_at=_parse_optional_datetime(entry.get("last_stopped_at")),
            restart_count=_parse_restart_count(entry["restart_count"]) if "restart_count" in entry else 0,
        )


def _parse_restart_count(value: object) -> int:
    """Strictly validate a persisted restart_count.

    Only a real, non-negative integer is accepted. The caller
    handles a missing key (defaulting to 0); a value that is present
    - including an explicit null - must be valid. None, booleans (an
    int subclass in Python), floats, strings and negative numbers are
    rejected rather than coerced.

    Args:
        value: The raw persisted value.

    Returns:
        The validated restart count.

    Raises:
        ValueError: If the value is present but not a non-negative int.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid restart_count {value!r} (must be a non-negative integer)")
    return value


def _parse_pid(value: object) -> Optional[int]:
    """Strictly validate a persisted pid.

    None is accepted - it means no process is associated.  A positive
    integer is accepted.  Anything else (bool, float, string, zero,
    or a negative number) is rejected so a corrupt state entry can be
    cleanly discarded by _load_state() without crashing the runner.

    Booleans are tested before integers because bool is a subclass of
    int in Python (isinstance(True, int) is True), matching the same
    pattern used by _parse_restart_count.

    Args:
        value: The raw persisted value.

    Returns:
        A positive int, or None.

    Raises:
        ValueError: If the value is present but not None or a positive
            integer.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid pid {value!r} (must be a positive integer or null)")
    return value


def _parse_process_identity(value: object) -> Optional["ProcessIdentity"]:
    """Parse a persisted identity dict back into a ProcessIdentity, tolerating bad input.

    Unlike _parse_pid/_parse_restart_count, a corrupted or unrecognised
    identity block yields None rather than raising.  A damaged identity
    baseline is recoverable - the service will be flagged UNKNOWN and
    restarted - whereas a bad pid or status value makes the whole entry
    unsalvageable.

    Args:
        value: The raw persisted value - expected to be a dict with
            ``pid``, ``platform``, ``start_time``, and optional ``boot_id``
            keys, or None.

    Returns:
        A ProcessIdentity, or None if the value is absent or invalid.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    try:
        pid_raw = value.get("pid")
        platform_raw = value.get("platform")
        start_time_raw = value.get("start_time")
        boot_id_raw = value.get("boot_id")

        # pid: positive integer, same rules as _parse_pid
        if (
            isinstance(pid_raw, bool)
            or not isinstance(pid_raw, int)
            or pid_raw <= 0
        ):
            return None

        # platform: must be "nt" or "posix"
        if platform_raw not in ("nt", "posix"):
            return None

        # start_time: non-negative integer (0 is a valid FILETIME on Windows
        # only in theory; in practice both platforms return positive values,
        # but we allow 0 rather than incorrectly rejecting a valid Windows
        # FILETIME from early January 1601)
        if (
            isinstance(start_time_raw, bool)
            or not isinstance(start_time_raw, int)
            or start_time_raw < 0
        ):
            return None

        # boot_id: None or a non-empty string
        if boot_id_raw is not None and not isinstance(boot_id_raw, str):
            return None

        return ProcessIdentity(
            pid=pid_raw,
            platform=platform_raw,
            start_time=start_time_raw,
            boot_id=boot_id_raw if boot_id_raw else None,
        )
    except Exception:  # pragma: no cover — defensive belt-and-suspenders
        return None


def _parse_optional_datetime(value: object) -> Optional[datetime]:
    """Parse an ISO 8601 string into a datetime, tolerating bad input.

    A malformed individual timestamp must never crash state loading -
    it's treated the same as if the field were simply absent.

    Args:
        value: The raw persisted value - expected to be an ISO 8601
            string or None.

    Returns:
        The parsed datetime, or None if `value` is None/empty/unparsable.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class ServiceOperationResult:
    """Structured outcome of a start/stop/restart/status operation.

    Attributes:
        success: Whether the operation achieved its intended effect.
        message: Short, user-facing summary - never a raw traceback.
        error: Technical detail suitable for logs; None on success.
        state: Updated runtime state, when the operation produced one.
    """

    success: bool
    message: str
    error: Optional[str] = None
    state: Optional[ServiceRuntimeState] = None