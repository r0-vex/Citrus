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
from dataclasses import replace
from datetime import datetime
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

        A missing or corrupt state file - at the top level, or for an
        individual service's entry - is treated as "nothing usable
        there" rather than raised. It must never prevent Citrus from
        starting, and one service's corrupt entry must never discard
        every other service's valid state.
        """
        state_file = config.SERVICE_STATE_FILE
        if not state_file.is_file():
            return

        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read service state file %s: %s", state_file, exc)
            return

        if not isinstance(raw, dict):
            logger.warning("Ignoring service state file %s: top-level JSON must be an object", state_file)
            return

        for service_id, entry in raw.items():
            if not isinstance(entry, dict):
                logger.warning("Discarding malformed saved state for '%s': entry is not an object", service_id)
                continue
            try:
                self._runtime_state[service_id] = ServiceRuntimeState.from_persisted_dict(entry)
            except (ValueError, TypeError) as exc:
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
        payload = {service_id: state.to_persisted_dict() for service_id, state in self._runtime_state.items()}

        try:
            tmp_path = state_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, state_file)
        except OSError as exc:
            logger.warning("Could not save service state file %s: %s", state_file, exc)

    @staticmethod
    def _carry_history(new: ServiceRuntimeState, previous: ServiceRuntimeState) -> None:
        """Copy historical metadata from a previous state onto a new one.

        A fresh runner-produced state knows nothing about earlier runs,
        so restart_count, last_stopped_at and last_exit_at are carried
        over (never overwriting a value the new state already has).

        Args:
            new: The newly produced state, updated in place.
            previous: A snapshot of the state before the operation.
        """
        new.restart_count = previous.restart_count
        if new.last_stopped_at is None:
            new.last_stopped_at = previous.last_stopped_at
        if new.last_exit_at is None:
            new.last_exit_at = previous.last_exit_at

    def _mark_transient_status(self, service_id: str, status: ServiceStatus) -> None:
        """Briefly record a transient in-memory status before an operation.

        Not persisted to disk on its own - the operation that follows
        always immediately overwrites this with its real, settled
        outcome (RUNNING/FAILED after start, STOPPED after stop, or
        the pre-operation state restored if the operation itself
        fails). Exists so STARTING/STOPPING are genuinely used at the
        point they're semantically meaningful, without introducing
        asynchronous complexity for a runner that's fully synchronous.

        Args:
            service_id: The service whose in-memory status to update.
            status: The transient status to record (STARTING or STOPPING).
        """
        current = self._runtime_state.get(service_id, ServiceRuntimeState())
        current.status = status
        self._runtime_state[service_id] = current

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
        corrects stale state accordingly. A PID that's gone while
        Citrus still believed the service was RUNNING is, by
        construction, always an unexpected exit - an intentional stop
        always clears the PID itself (see stop_service()), so this
        code path can never be reached as a result of one.

        Only logs/emits when the status actually changes - repeatedly
        calling this for an unchanged, healthy service (e.g. the UI
        refreshing the detail screen) produces no log noise.

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
                    emit(EventNames.SERVICE_STATUS_CHECKED, service_id=definition.id, status=state.status.value)
            else:
                logger.warning(
                    "Service '%s' PID %s is no longer alive; treating as an unexpected exit",
                    definition.id,
                    state.pid,
                )
                state = ServiceRuntimeState(
                    status=ServiceStatus.FAILED,
                    last_exit_code=None,  # We only learn about this after the fact - the
                    # OS gives us no reliable exit code this way, so we stay honest
                    # rather than inventing one.
                    last_error="Process exited unexpectedly (no longer running).",
                    last_exit_at=datetime.now(),
                    restart_count=state.restart_count,
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

        Does not itself touch restart_count - only restart_service()
        increments that, exactly once per successful full restart.

        Args:
            definition: The service to start.

        Returns:
            A ServiceOperationResult describing the outcome.
        """
        current = replace(self.get_status(definition))
        if current.status == ServiceStatus.RUNNING:
            return ServiceOperationResult(success=False, message=f"{definition.name} is already running.")

        if not definition.enabled:
            return ServiceOperationResult(success=False, message=f"{definition.name} is disabled.")

        emit(EventNames.SERVICE_START_REQUESTED, service_id=definition.id)
        logger.info("Start requested for service '%s'", definition.id)

        self._mark_transient_status(definition.id, ServiceStatus.STARTING)

        result = runner.start(definition)

        if result.state is not None:
            # runner.start() already produced a full, correct RUNNING
            # or FAILED state (e.g. a real exit code for a process
            # that died within its start grace period).
            self._carry_history(result.state, current)
            self._runtime_state[definition.id] = result.state
        elif not result.success:
            # runner.start() failed before any process (and therefore
            # any state) existed - e.g. bad executable, missing
            # working directory. Never leave the transient STARTING
            # marker in place; record an explicit, honest FAILED state.
            failed = ServiceRuntimeState(status=ServiceStatus.FAILED, last_error=result.error)
            self._carry_history(failed, current)
            self._runtime_state[definition.id] = failed

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

        Does not itself touch restart_count - only restart_service()
        increments that.

        Args:
            definition: The service to stop.

        Returns:
            A ServiceOperationResult describing the outcome.
        """
        # get_status() returns the live stored object, which
        # _mark_transient_status() mutates in place - so keep an
        # independent copy to restore from if the stop fails.
        current = replace(self.get_status(definition))
        if current.status != ServiceStatus.RUNNING or current.pid is None:
            return ServiceOperationResult(success=True, message=f"{definition.name} is not running.")

        emit(EventNames.SERVICE_STOP_REQUESTED, service_id=definition.id, pid=current.pid)
        logger.info("Stop requested for service '%s' (PID %s)", definition.id, current.pid)

        self._mark_transient_status(definition.id, ServiceStatus.STOPPING)

        result = runner.stop(current.pid)

        if result.success:
            self._runtime_state[definition.id] = ServiceRuntimeState(
                status=ServiceStatus.STOPPED,
                restart_count=current.restart_count,
                last_stopped_at=datetime.now(),
            )
            self._save_state()
            emit(EventNames.SERVICE_STOPPED, service_id=definition.id)
            logger.info("Service '%s' stopped", definition.id)
        else:
            # Stop failed - restore the (still-running) state we had
            # before attempting it, rather than leaving the transient
            # STOPPING marker stuck in place.
            self._runtime_state[definition.id] = replace(current)
            self._save_state()
            emit(EventNames.SERVICE_STOP_FAILED, service_id=definition.id, error=result.error)
            logger.warning("Service '%s' failed to stop: %s", definition.id, result.error)

        return result

    def restart_service(self, definition: ServiceDefinition) -> ServiceOperationResult:
        """Restart a service: stop, verify stopped, start, verify running.

        restart_count is incremented exactly once here, only when both
        the stop and the start fully succeed. Calling stop_service()
        and start_service() independently (not through this method)
        never increments it.

        Args:
            definition: The service to restart.

        Returns:
            A ServiceOperationResult describing the outcome.
        """
        emit(EventNames.SERVICE_RESTART_REQUESTED, service_id=definition.id)
        logger.info("Restart requested for service '%s'", definition.id)

        previous_restart_count = self.get_status(definition).restart_count

        stop_result = self.stop_service(definition)
        if not stop_result.success:
            logger.warning("Restart of '%s' aborted: could not stop it first: %s", definition.id, stop_result.error)
            return ServiceOperationResult(
                success=False,
                message=f"Failed to restart {definition.name}.",
                error=f"Could not stop it first: {stop_result.error}",
            )

        start_result = self.start_service(definition)
        if not start_result.success:
            logger.warning(
                "Restart of '%s' failed: stopped, but could not start again: %s",
                definition.id,
                start_result.error,
            )
            return ServiceOperationResult(
                success=False,
                message=f"Failed to restart {definition.name}.",
                error=f"Stopped, but failed to start again: {start_result.error}",
            )

        new_state = self._runtime_state.get(definition.id)
        if new_state is not None:
            new_state.restart_count = previous_restart_count + 1
            self._save_state()

        emit(EventNames.SERVICE_RESTARTED, service_id=definition.id)
        logger.info("Service '%s' restarted (restart_count=%s)", definition.id, new_state.restart_count if new_state else "?")
        return ServiceOperationResult(success=True, message=f"{definition.name} restarted.", state=new_state)


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
