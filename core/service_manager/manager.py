"""
manager.py
==========
Orchestrates the Service Manager: combines registry.py (what's
configured) with runner.py (how to actually run it) and a small
persisted runtime-state file (so PIDs and status survive across
Citrus restarts). pages/services.py is the only expected caller; it
never talks to the OS directly - that's runner.py's job.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import config
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.service_manager import registry, runner
from core.service_manager.models import ServiceDefinition, ServiceOperationResult, ServiceRuntimeState, ServiceStatus

logger = get_logger(__name__)


class ServiceManager:
    """Coordinates service discovery, process control, and state."""

    def __init__(self) -> None:
        self._runtime_state: Dict[str, ServiceRuntimeState] = {}
        self._load_state()

    # --- Persistence ---------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted runtime state from disk, if present.

        A missing or corrupt state file is treated as "no known
        services yet" rather than raised - it must never prevent
        Citrus from starting.
        """
        state_file = config.SERVICE_STATE_FILE
        if not state_file.is_file():
            return

        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read service state file %s: %s", state_file, exc)
            return

        for service_id, entry in raw.items():
            try:
                self._runtime_state[service_id] = ServiceRuntimeState(
                    status=ServiceStatus(entry.get("status", ServiceStatus.UNKNOWN.value)),
                    pid=entry.get("pid"),
                    started_at=None,
                    last_exit_code=entry.get("last_exit_code"),
                    last_error=entry.get("last_error"),
                )
            except ValueError as exc:
                logger.warning("Discarding malformed saved state for '%s': %s", service_id, exc)

    def _save_state(self) -> None:
        """Persist runtime state to disk, best-effort.

        Written atomically (temp file + os.replace) so a crash
        mid-write can't corrupt the state file for the next launch.
        A failure to save is logged but never raised - it must never
        take down a start/stop operation that otherwise succeeded.
        """
        config.ensure_service_runtime_directories()
        state_file = config.SERVICE_STATE_FILE
        payload = {
            service_id: {
                "status": state.status.value,
                "pid": state.pid,
                "last_exit_code": state.last_exit_code,
                "last_error": state.last_error,
            }
            for service_id, state in self._runtime_state.items()
        }

        try:
            tmp_path = state_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, state_file)
        except OSError as exc:
            logger.warning("Could not save service state file %s: %s", state_file, exc)

    # --- Queries ---------------------------------------------------------

    def list_services(self) -> List[Tuple[ServiceDefinition, ServiceRuntimeState]]:
        """List every configured service with its freshly-checked status.

        Returns:
            (definition, runtime_state) pairs, in discovery order.
        """
        return [(definition, self.get_status(definition)) for definition in registry.discover()]

    def get_status(self, definition: ServiceDefinition) -> ServiceRuntimeState:
        """Return a service's current status, verified against the OS.

        Never trusts a stored PID at face value - always confirms the
        process is actually alive via runner.is_running(), and
        corrects stale state (e.g. a service that exited since Citrus
        last checked, or crossed a Citrus restart) accordingly.

        Args:
            definition: The service to check.

        Returns:
            The (possibly corrected) ServiceRuntimeState.
        """
        state = self._runtime_state.get(definition.id, ServiceRuntimeState())

        if state.pid is not None:
            if runner.is_running(state.pid):
                if state.status != ServiceStatus.RUNNING:
                    state.status = ServiceStatus.RUNNING
                    self._runtime_state[definition.id] = state
                    self._save_state()
            else:
                logger.info("Service '%s' PID %s is no longer alive; marking STOPPED", definition.id, state.pid)
                state = ServiceRuntimeState(
                    status=ServiceStatus.STOPPED,
                    last_exit_code=state.last_exit_code,
                    last_error=state.last_error,
                )
                self._runtime_state[definition.id] = state
                self._save_state()

        emit(EventNames.SERVICE_STATUS_CHECKED, service_id=definition.id, status=state.status.value)
        return state

    def get_log_path(self, definition: ServiceDefinition) -> Path:
        """Return the path to a service's dedicated log file.

        Args:
            definition: The service whose log path is requested.

        Returns:
            The absolute path to logs/services/<id>.log.
        """
        return config.SERVICE_LOG_DIR / f"{definition.id}.log"

    # --- Operations --------------------------------------------------------

    def start_service(self, definition: ServiceDefinition) -> ServiceOperationResult:
        """Start a service, updating and persisting its runtime state.

        Args:
            definition: The service to start.

        Returns:
            A ServiceOperationResult describing the outcome.
        """
        if self.get_status(definition).status == ServiceStatus.RUNNING:
            return ServiceOperationResult(success=False, message=f"{definition.name} is already running.")

        if not definition.enabled:
            return ServiceOperationResult(success=False, message=f"{definition.name} is disabled.")

        emit(EventNames.SERVICE_START_REQUESTED, service_id=definition.id)
        logger.info("Start requested for service '%s'", definition.id)

        result = runner.start(definition)

        if result.state is not None:
            self._runtime_state[definition.id] = result.state
            self._save_state()

        if result.success:
            emit(EventNames.SERVICE_STARTED, service_id=definition.id, pid=result.state.pid if result.state else None)
            logger.info("Service '%s' started (PID %s)", definition.id, result.state.pid if result.state else None)
        else:
            emit(EventNames.SERVICE_START_FAILED, service_id=definition.id, error=result.error)
            logger.warning("Service '%s' failed to start: %s", definition.id, result.error)

        return result

    def stop_service(self, definition: ServiceDefinition) -> ServiceOperationResult:
        """Stop a running service, updating and persisting its state.

        Args:
            definition: The service to stop.

        Returns:
            A ServiceOperationResult describing the outcome.
        """
        current = self.get_status(definition)
        if current.status != ServiceStatus.RUNNING or current.pid is None:
            return ServiceOperationResult(success=True, message=f"{definition.name} is not running.")

        emit(EventNames.SERVICE_STOP_REQUESTED, service_id=definition.id, pid=current.pid)
        logger.info("Stop requested for service '%s' (PID %s)", definition.id, current.pid)

        result = runner.stop(current.pid)

        if result.success:
            self._runtime_state[definition.id] = ServiceRuntimeState(status=ServiceStatus.STOPPED)
            self._save_state()
            emit(EventNames.SERVICE_STOPPED, service_id=definition.id)
            logger.info("Service '%s' stopped", definition.id)
        else:
            emit(EventNames.SERVICE_STOP_FAILED, service_id=definition.id, error=result.error)
            logger.warning("Service '%s' failed to stop: %s", definition.id, result.error)

        return result

    def restart_service(self, definition: ServiceDefinition) -> ServiceOperationResult:
        """Restart a service: stop, verify stopped, start, verify running.

        Args:
            definition: The service to restart.

        Returns:
            A ServiceOperationResult describing the outcome.
        """
        emit(EventNames.SERVICE_RESTART_REQUESTED, service_id=definition.id)
        logger.info("Restart requested for service '%s'", definition.id)

        stop_result = self.stop_service(definition)
        if not stop_result.success:
            return ServiceOperationResult(
                success=False,
                message=f"Failed to restart {definition.name}.",
                error=f"Could not stop it first: {stop_result.error}",
            )

        start_result = self.start_service(definition)
        if not start_result.success:
            return ServiceOperationResult(
                success=False,
                message=f"Failed to restart {definition.name}.",
                error=f"Stopped, but failed to start again: {start_result.error}",
            )

        emit(EventNames.SERVICE_RESTARTED, service_id=definition.id)
        logger.info("Service '%s' restarted", definition.id)
        return ServiceOperationResult(success=True, message=f"{definition.name} restarted.", state=start_result.state)


_manager: Optional[ServiceManager] = None


def get_manager() -> ServiceManager:
    """Return the process-wide ServiceManager singleton.

    Returns:
        The shared ServiceManager instance, created on first call.
    """
    global _manager
    if _manager is None:
        _manager = ServiceManager()
    return _manager
