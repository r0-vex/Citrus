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
        last_exit_code: Exit code from the most recent run, if any.
        last_error: Human-readable reason for the most recent failure.
    """

    status: ServiceStatus = ServiceStatus.STOPPED
    pid: Optional[int] = None
    started_at: Optional[datetime] = None
    last_exit_code: Optional[int] = None
    last_error: Optional[str] = None

    def runtime_seconds(self) -> Optional[float]:
        """Return how long the service has been running, if it is.

        Returns:
            Elapsed seconds since `started_at`, or None if the service
            isn't running or its start time isn't known.
        """
        if self.status != ServiceStatus.RUNNING or self.started_at is None:
            return None
        return (datetime.now() - self.started_at).total_seconds()


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
