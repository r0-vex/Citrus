"""
models.py
=========
Data structures for the Service Manager: a service's static
definition (what to run), its runtime state (what's happening right
now), and structured results from manager/runner operations. No
process-control behavior lives here - only data and small, obvious
helpers.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


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

        Returns:
            A plain, JSON-serializable dict.
        """
        return {
            "status": self.status.value,
            "pid": self.pid,
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
        last_exit_at/last_stopped_at/restart_count existed - missing
        fields fall back to sensible defaults rather than raising.
        started_at is never restored from disk (see to_persisted_dict).

        Args:
            entry: One service's raw dict, as loaded from the state
                file.

        Returns:
            A ServiceRuntimeState reconstructed from the dict.

        Raises:
            ValueError: If "status" isn't a recognized ServiceStatus
                value. Callers should catch this per-entry so one
                corrupt service's state doesn't discard every other
                service's state too.
        """
        return cls(
            status=ServiceStatus(entry.get("status", ServiceStatus.UNKNOWN.value)),
            pid=entry.get("pid"),
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