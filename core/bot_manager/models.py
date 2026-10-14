"""
models.py
=========
Data structures for the Bot Manager: static bot definitions (what to run),
runtime state (lifecycle status, PID, ProcessIdentity, timestamps),
and structured operation results.

Reuses the proven ProcessIdentity and ServiceStatus models from
core.service_manager.models to ensure consistent OS-level process
tracking and PID-reuse safety without duplicating low-level logic.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.service_manager.models import (
    ProcessIdentity,
    ServiceStatus,
    _parse_optional_datetime,
    _parse_pid,
    _parse_process_identity,
    _parse_restart_count,
)

# Alias ServiceStatus as BotStatus for semantic clarity in bot contexts.
BotStatus = ServiceStatus


@dataclass(frozen=True)
class BotDefinition:
    """A bot's static configuration - what to run and how.

    Attributes:
        id: Unique filesystem-safe identifier (matches ^[A-Za-z0-9_-]+$).
            Used as manifest stem and log filename stem.
        name: Human-readable display name.
        command: Argv-style command list, e.g. ["python", "bots/atlas.py"].
        working_directory: Absolute directory the process executes from.
        environment: Extra environment variables for the bot process.
            NOTE: Credentials or tokens must never be logged or rendered in UI.
        enabled: Whether this bot is allowed to be started.
        auto_restart: Reserved for future restart supervisor (deferred).
    """

    id: str
    name: str
    command: List[str]
    working_directory: Path
    environment: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    auto_restart: bool = False


@dataclass
class BotRuntimeState:
    """A bot's current runtime state - separate from its static definition.

    Attributes:
        status: Current lifecycle state (STOPPED, STARTING, RUNNING, STOPPING, FAILED, UNKNOWN).
        pid: Process ID if running or last known PID.
        identity: OS-level fingerprint captured immediately upon spawn.
            Required for safe process verification and signalling.
        started_at: When the verified process instance was started.
        last_exit_code: Exit code from early exit within grace period, if known.
        last_error: Human-readable error description of the most recent failure.
        last_exit_at: When the bot was last detected to have exited unexpectedly.
        last_stopped_at: When the bot was last intentionally stopped.
        restart_count: How many times restart_bot() has succeeded.
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
        """Return how long the bot has been running in seconds, if active."""
        if self.status != ServiceStatus.RUNNING or self.started_at is None:
            return None
        return (datetime.now() - self.started_at).total_seconds()

    def to_persisted_dict(self) -> Dict[str, object]:
        """Serialize runtime state for atomic JSON persistence.

        Returns:
            A JSON-serializable dictionary.
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
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "last_exit_at": self.last_exit_at.isoformat() if self.last_exit_at else None,
            "last_stopped_at": self.last_stopped_at.isoformat() if self.last_stopped_at else None,
            "restart_count": self.restart_count,
        }

    @classmethod
    def from_persisted_dict(cls, entry: Dict[str, object]) -> "BotRuntimeState":
        """Deserialize a persisted dictionary into a BotRuntimeState.

        Applies strict validation:
        - Rejects invalid PID or invalid status values by raising ValueError.
        - Tolerates corrupted or mismatched ProcessIdentity by setting it to None.
        - If PID is None or identity is None/mismatched, started_at is cleared.
        - Strictly validates restart_count (must be non-negative integer).
        - Tolerates invalid timestamps by setting them to None.
        """
        pid = _parse_pid(entry.get("pid"))
        identity = _parse_process_identity(entry.get("identity"))
        if pid is None or (identity is not None and identity.pid != pid):
            identity = None

        started_at = _parse_optional_datetime(entry.get("started_at"))
        if pid is None or identity is None:
            started_at = None

        return cls(
            status=ServiceStatus(entry.get("status", ServiceStatus.UNKNOWN.value)),
            pid=pid,
            identity=identity,
            started_at=started_at,
            last_exit_code=entry.get("last_exit_code"),
            last_error=entry.get("last_error"),
            last_exit_at=_parse_optional_datetime(entry.get("last_exit_at")),
            last_stopped_at=_parse_optional_datetime(entry.get("last_stopped_at")),
            restart_count=_parse_restart_count(entry["restart_count"]) if "restart_count" in entry else 0,
        )


@dataclass
class BotOperationResult:
    """Structured result of a bot lifecycle operation."""

    success: bool
    message: str
    error: Optional[str] = None
    state: Optional[BotRuntimeState] = None
