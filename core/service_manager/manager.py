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
from core.service_manager import health as health_evaluator, registry, runner
from core.service_manager.models import (
    ServiceDefinition,
    ServiceHealth,
    ServiceOperationResult,
    ServiceRuntimeState,
    ServiceStatus,
)
from core.service_manager.runner import IdentityVerificationResult

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

    def get_status(self, definition: ServiceDefinition, force_probe: bool = False) -> ServiceRuntimeState:
        """Return a service's current status, verified against the OS.

        Never trusts a stored PID at face value.  Uses
        runner.verify_process_identity() to confirm the process currently
        holding the PID is the same process Citrus started.  Each
        verification result maps to a distinct state transition:

        - No PID stored: return stored state unchanged (nothing to check).
        - Legacy state (PID stored, identity=None): immediately transition
          to UNKNOWN and clear the PID.  A legacy bare PID will NEVER be
          used to send any OS signal.
        - MATCH: process is alive and is our service; mark RUNNING if not
          already, and evaluate application health if configured.
        - EXITED: PID is gone; transition to FAILED with last_exit_at.
        - MISMATCH: PID was recycled; transition to FAILED, clear PID.
        - INCONCLUSIVE: check could not be completed; transition to UNKNOWN,
          preserve PID for triage, block all signals.

        Only logs/emits when the status actually changes - repeatedly
        calling this for an unchanged, healthy service (e.g. the UI
        refreshing the detail screen) produces no log noise.

        Args:
            definition: The service to check.
            force_probe: If True, bypass the short probe cache TTL and evaluate
                health checks immediately.

        Returns:
            The (possibly corrected) ServiceRuntimeState.
        """
        state = self._runtime_state.get(definition.id, ServiceRuntimeState())

        if state.pid is None:
            return state

        # ---- Legacy state: PID present but no identity baseline ----------
        # State files written before process identity support have no identity field.
        # We cannot safely signal such a PID; transition to UNKNOWN and
        # clear the PID so the service must be restarted to get a baseline.
        if state.identity is None:
            logger.warning(
                "Service '%s' PID %s has no identity baseline (legacy state); "
                "clearing PID and marking UNKNOWN.",
                definition.id, state.pid,
            )
            state = ServiceRuntimeState(
                status=ServiceStatus.UNKNOWN,
                pid=None,
                identity=None,
                started_at=None,
                health=ServiceHealth.NONE,
                health_message=None,
                last_health_check_at=None,
                last_error="Legacy state: PID cleared (no process identity baseline). Restart the service.",
                restart_count=state.restart_count,
                last_stopped_at=state.last_stopped_at,
                last_exit_at=state.last_exit_at,
            )
            self._runtime_state[definition.id] = state
            self._save_state()
            emit(EventNames.SERVICE_STATUS_CHECKED, service_id=definition.id, status=state.status.value)
            return state

        # ---- Identity verification ----------------------------------------
        verification = runner.verify_process_identity(state.identity)

        if verification == IdentityVerificationResult.MATCH:
            status_changed = state.status != ServiceStatus.RUNNING
            if status_changed:
                state.status = ServiceStatus.RUNNING

            # Evaluate application health ONLY after verified MATCH
            if definition.health_check is not None:
                now = datetime.now()
                should_probe = (
                    force_probe
                    or state.last_health_check_at is None
                    or (now - state.last_health_check_at).total_seconds() > 2.0
                )
                if should_probe:
                    health, health_message = health_evaluator.evaluate_health(definition)
                    health_changed = (state.health != health or state.health_message != health_message)
                    state.health = health
                    state.health_message = health_message
                    state.last_health_check_at = now
                    if health_changed or status_changed:
                        self._save_state()
                    emit(
                        EventNames.SERVICE_HEALTH_CHECKED,
                        service_id=definition.id,
                        health=state.health.value,
                        message=state.health_message,
                    )
            else:
                state.health = ServiceHealth.NONE
                state.health_message = None

            if status_changed:
                self._runtime_state[definition.id] = state
                self._save_state()
                emit(EventNames.SERVICE_STATUS_CHECKED, service_id=definition.id, status=state.status.value)
            else:
                self._runtime_state[definition.id] = state

            return state

        if verification == IdentityVerificationResult.EXITED:
            logger.warning(
                "Service '%s' PID %s is no longer alive (EXITED); treating as an unexpected exit.",
                definition.id, state.pid,
            )
            state = ServiceRuntimeState(
                status=ServiceStatus.FAILED,
                pid=None,
                identity=None,
                started_at=None,
                health=ServiceHealth.NONE,
                health_message=None,
                last_health_check_at=None,
                last_exit_code=None,
                last_error="Process exited unexpectedly (no longer running).",
                last_exit_at=datetime.now(),
                restart_count=state.restart_count,
            )
            self._runtime_state[definition.id] = state
            self._save_state()
            emit(EventNames.SERVICE_STATUS_CHECKED, service_id=definition.id, status=state.status.value)
            return state

        if verification == IdentityVerificationResult.MISMATCH:
            logger.warning(
                "Service '%s' PID %s identity MISMATCH (PID recycled); clearing PID and marking FAILED.",
                definition.id, state.pid,
            )
            state = ServiceRuntimeState(
                status=ServiceStatus.FAILED,
                pid=None,
                identity=None,
                started_at=None,
                health=ServiceHealth.NONE,
                health_message=None,
                last_health_check_at=None,
                last_exit_code=None,
                last_error="PID recycled by a different process (identity mismatch).",
                last_exit_at=datetime.now(),
                restart_count=state.restart_count,
            )
            self._runtime_state[definition.id] = state
            self._save_state()
            emit(EventNames.SERVICE_STATUS_CHECKED, service_id=definition.id, status=state.status.value)
            return state

        # INCONCLUSIVE: verification failed (permission error, /proc error, etc.)
        # Preserve the PID for triage; mark UNKNOWN; block all signals.
        logger.warning(
            "Service '%s' PID %s identity verification INCONCLUSIVE; marking UNKNOWN.",
            definition.id, state.pid,
        )
        state = ServiceRuntimeState(
            status=ServiceStatus.UNKNOWN,
            pid=state.pid,
            identity=state.identity,  # Preserve for retry
            started_at=state.started_at,
            health=ServiceHealth.UNKNOWN,
            health_message="Process identity verification inconclusive; health probe bypassed.",
            last_health_check_at=state.last_health_check_at,
            last_error="Process identity verification inconclusive (permission denied or /proc read error).",
            restart_count=state.restart_count,
            last_stopped_at=state.last_stopped_at,
            last_exit_at=state.last_exit_at,
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

    def get_log_tail(
        self,
        definition: ServiceDefinition,
        max_lines: int = 20,
        max_bytes: int = 65536,
    ) -> List[str]:
        """Read the trailing lines of a service's log file with bounded memory usage.

        Safely seeks to near the end of the file if it exceeds max_bytes to
        avoid loading arbitrarily large files on memory-constrained devices.

        Args:
            definition: The service whose log tail to retrieve.
            max_lines: Maximum number of trailing lines to return.
            max_bytes: Maximum number of bytes to read from the end of the file.

        Returns:
            A list of string lines (without trailing newlines), or an empty list
            if the log file is missing or unreadable.
        """
        log_path = self.get_log_path(definition)
        if not log_path.is_file():
            return []

        try:
            with open(log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                read_size = min(size, max_bytes)
                f.seek(size - read_size, os.SEEK_SET)
                data = f.read(read_size)
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            return lines[-max_lines:] if max_lines > 0 else []
        except OSError as exc:
            logger.warning("Could not read log file %s: %s", log_path, exc)
            return []

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

        result = runner.stop(current.pid, current.identity)

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
