"""
manager.py
==========
Orchestrates the Bot Manager subsystem: combines registry discovery with
runner process management and atomic runtime-state persistence in
temp/bot_manager_state.json.

Guarantees:
- Never signals a bare PID without process-identity verification.
- Never signals on identity mismatch (recycled PID) or inconclusive checks.
- Keeps bot runtime state strictly isolated from Service Manager state.
- Emits structured lifecycle events for all operations.
- Provides bounded log retrieval.
"""

import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import config
from core.bot_manager import registry
from core.bot_manager.models import (
    BotDefinition,
    BotOperationResult,
    BotRuntimeState,
    BotStatus,
)
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.service_manager import runner
from core.service_manager.runner import IdentityVerificationResult

logger = get_logger(__name__)


class BotManager:
    """Coordinates bot discovery, process lifecycle, and persisted runtime state."""

    def __init__(self) -> None:
        self._runtime_state: Dict[str, BotRuntimeState] = {}
        self._load_state()

    # --- Persistence ---------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted runtime state from temp/bot_manager_state.json.

        Tolerates missing or corrupt files without crashing Citrus. Individual
        corrupt entries are discarded without losing other valid entries.
        """
        state_file = config.BOT_STATE_FILE
        if not state_file.is_file():
            return

        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read bot state file %s: %s", state_file, exc)
            return

        if not isinstance(raw, dict):
            logger.warning("Ignoring bot state file %s: top-level JSON must be an object", state_file)
            return

        for bot_id, entry in raw.items():
            if not isinstance(entry, dict):
                logger.warning("Discarding malformed saved state for '%s': entry is not an object", bot_id)
                continue
            try:
                self._runtime_state[bot_id] = BotRuntimeState.from_persisted_dict(entry)
            except (ValueError, TypeError) as exc:
                logger.warning("Discarding malformed saved state for '%s': %s", bot_id, exc)

    def _save_state(self) -> None:
        """Persist runtime state to disk atomically via a temporary file."""
        config.ensure_bot_runtime_directories()
        state_file = config.BOT_STATE_FILE
        payload = {bot_id: state.to_persisted_dict() for bot_id, state in self._runtime_state.items()}

        try:
            tmp_path = state_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, state_file)
        except OSError as exc:
            logger.warning("Could not save bot state file %s: %s", state_file, exc)

    @staticmethod
    def _carry_history(new: BotRuntimeState, previous: BotRuntimeState) -> None:
        """Preserve historical metadata from earlier runs onto a newly produced state."""
        new.restart_count = previous.restart_count
        if new.last_stopped_at is None:
            new.last_stopped_at = previous.last_stopped_at
        if new.last_exit_at is None:
            new.last_exit_at = previous.last_exit_at

    def _mark_transient_status(self, bot_id: str, status: BotStatus) -> None:
        """Record an in-memory transient status (STARTING/STOPPING) before an operation."""
        current = self._runtime_state.get(bot_id, BotRuntimeState())
        current.status = status
        self._runtime_state[bot_id] = current

    # --- Queries ---------------------------------------------------------

    def list_bots(self) -> List[Tuple[BotDefinition, BotRuntimeState]]:
        """List every configured bot with its freshly verified OS status.

        Returns:
            List of (BotDefinition, BotRuntimeState) tuples in discovery order.
        """
        return [(definition, self.get_status(definition)) for definition in registry.discover()]

    def get_status(self, definition: BotDefinition) -> BotRuntimeState:
        """Return a bot's current status, verified against the OS process table.

        Transitions:
        - No PID: returns stored state unchanged.
        - Legacy state (PID present, identity=None): marks UNKNOWN and clears PID.
          Never allows signalling a legacy bare PID.
        - MATCH: process verified alive; marks RUNNING.
        - EXITED: process has died; marks FAILED with last_exit_at.
        - MISMATCH: PID was recycled; marks FAILED and clears PID.
        - INCONCLUSIVE: verification check failed; marks UNKNOWN and preserves PID for triage.
        """
        state = self._runtime_state.get(definition.id, BotRuntimeState())

        if state.pid is None:
            return state

        # ---- Legacy state: PID present but no process identity -----------
        if state.identity is None:
            if state.status == BotStatus.UNKNOWN:
                # Already in UNKNOWN status (e.g. unconfirmed launch cleanup or triage);
                # preserve PID so start_bot refuses duplicate spawns and stop_bot refuses bare-PID signals.
                return state

            logger.warning(
                "Bot '%s' PID %s has no identity baseline (legacy state); "
                "clearing PID and marking UNKNOWN.",
                definition.id,
                state.pid,
            )
            state = BotRuntimeState(
                status=BotStatus.UNKNOWN,
                pid=None,
                identity=None,
                started_at=None,
                last_error="Legacy state: PID cleared (no process identity baseline). Restart the bot.",
                restart_count=state.restart_count,
                last_stopped_at=state.last_stopped_at,
                last_exit_at=state.last_exit_at,
            )
            self._runtime_state[definition.id] = state
            self._save_state()
            emit(EventNames.BOT_STATUS_CHECKED, bot_id=definition.id, status=state.status.value)
            return state

        # ---- Process Identity Verification -------------------------------
        verification = runner.verify_process_identity(state.identity)

        if verification == IdentityVerificationResult.MATCH:
            status_changed = state.status != BotStatus.RUNNING
            if status_changed:
                state.status = BotStatus.RUNNING
                self._runtime_state[definition.id] = state
                self._save_state()
                emit(EventNames.BOT_STATUS_CHECKED, bot_id=definition.id, status=state.status.value)
            return state

        if verification == IdentityVerificationResult.EXITED:
            logger.warning(
                "Bot '%s' PID %s is no longer alive (EXITED); treating as an unexpected exit.",
                definition.id,
                state.pid,
            )
            state = BotRuntimeState(
                status=BotStatus.FAILED,
                pid=None,
                identity=None,
                started_at=None,
                last_exit_code=None,
                last_error="Process exited unexpectedly (no longer running).",
                last_exit_at=datetime.now(),
                restart_count=state.restart_count,
            )
            self._runtime_state[definition.id] = state
            self._save_state()
            emit(EventNames.BOT_STATUS_CHECKED, bot_id=definition.id, status=state.status.value)
            return state

        if verification == IdentityVerificationResult.MISMATCH:
            logger.warning(
                "Bot '%s' PID %s identity MISMATCH (PID recycled); clearing PID and marking FAILED.",
                definition.id,
                state.pid,
            )
            state = BotRuntimeState(
                status=BotStatus.FAILED,
                pid=None,
                identity=None,
                started_at=None,
                last_exit_code=None,
                last_error="PID recycled by a different process (identity mismatch).",
                last_exit_at=datetime.now(),
                restart_count=state.restart_count,
            )
            self._runtime_state[definition.id] = state
            self._save_state()
            emit(EventNames.BOT_STATUS_CHECKED, bot_id=definition.id, status=state.status.value)
            return state

        # INCONCLUSIVE: permission denied, read error, or OS limitation
        logger.warning(
            "Bot '%s' PID %s identity verification INCONCLUSIVE; marking UNKNOWN.",
            definition.id,
            state.pid,
        )
        state = BotRuntimeState(
            status=BotStatus.UNKNOWN,
            pid=state.pid,
            identity=state.identity,  # Preserved for triage/retry
            started_at=state.started_at,
            last_error="Process identity verification inconclusive (permission denied or read error).",
            restart_count=state.restart_count,
            last_stopped_at=state.last_stopped_at,
            last_exit_at=state.last_exit_at,
        )
        self._runtime_state[definition.id] = state
        self._save_state()
        emit(EventNames.BOT_STATUS_CHECKED, bot_id=definition.id, status=state.status.value)
        return state

    def get_log_path(self, definition: BotDefinition) -> Path:
        """Return the absolute path to a bot's dedicated log file."""
        return config.BOT_LOG_DIR / f"{definition.id}.log"

    def get_log_tail(
        self,
        definition: BotDefinition,
        max_lines: int = 20,
        max_bytes: int = 65536,
    ) -> List[str]:
        """Read trailing lines from a bot's log file with bounded memory usage."""
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
            logger.warning("Could not read bot log file %s: %s", log_path, exc)
            return []

    # --- Operations --------------------------------------------------------

    def start_bot(self, definition: BotDefinition) -> BotOperationResult:
        """Start a bot process, capturing identity and persisting state.

        Args:
            definition: Static bot definition.

        Returns:
            Structured BotOperationResult.
        """
        current = replace(self.get_status(definition))
        if current.status == BotStatus.RUNNING:
            return BotOperationResult(success=False, message=f"{definition.name} is already running.")

        if current.status == BotStatus.UNKNOWN and current.pid is not None:
            logger.warning(
                "Refusing to start bot '%s': status is UNKNOWN with active PID %s.",
                definition.id,
                current.pid,
            )
            return BotOperationResult(
                success=False,
                message=f"Cannot start {definition.name}: status is UNKNOWN with active PID {current.pid}.",
                error=f"Process identity verification inconclusive for PID {current.pid}; start refused to prevent duplicate process.",
                state=current,
            )

        if not definition.enabled:
            return BotOperationResult(success=False, message=f"{definition.name} is disabled.")

        emit(EventNames.BOT_START_REQUESTED, bot_id=definition.id)
        logger.info("Start requested for bot '%s'", definition.id)

        self._mark_transient_status(definition.id, BotStatus.STARTING)

        log_path = self.get_log_path(definition)
        runner_result = runner.start(definition, log_path=log_path, require_identity=True)

        if runner_result.state is not None:
            bot_state = BotRuntimeState(
                status=runner_result.state.status,
                pid=runner_result.state.pid,
                identity=runner_result.state.identity,
                started_at=runner_result.state.started_at,
                last_exit_code=runner_result.state.last_exit_code,
                last_error=runner_result.state.last_error,
                last_exit_at=runner_result.state.last_exit_at,
            )
            self._carry_history(bot_state, current)
            self._runtime_state[definition.id] = bot_state
        elif not runner_result.success:
            failed = BotRuntimeState(status=BotStatus.FAILED, last_error=runner_result.error)
            self._carry_history(failed, current)
            self._runtime_state[definition.id] = failed

        self._save_state()

        if runner_result.success:
            state = self._runtime_state[definition.id]
            emit(EventNames.BOT_STARTED, bot_id=definition.id, pid=state.pid)
            logger.info("Bot '%s' started (PID %s)", definition.id, state.pid)
            return BotOperationResult(success=True, message="Started.", state=state)
        else:
            emit(EventNames.BOT_START_FAILED, bot_id=definition.id, error=runner_result.error)
            logger.warning("Bot '%s' failed to start: %s", definition.id, runner_result.error)
            return BotOperationResult(
                success=False,
                message="Failed to start.",
                error=runner_result.error,
                state=self._runtime_state[definition.id],
            )

    def stop_bot(self, definition: BotDefinition) -> BotOperationResult:
        """Stop a running bot gracefully, verifying process identity before signalling.

        Args:
            definition: Bot definition to stop.

        Returns:
            Structured BotOperationResult.
        """
        current = replace(self.get_status(definition))
        if current.status == BotStatus.UNKNOWN and current.pid is not None:
            logger.warning(
                "Refusing to stop bot '%s': status is UNKNOWN with active PID %s "
                "(identity verification inconclusive; refusing to signal).",
                definition.id,
                current.pid,
            )
            return BotOperationResult(
                success=False,
                message=f"Cannot stop {definition.name}: status is UNKNOWN (identity verification inconclusive).",
                error="Identity verification inconclusive; refusing to signal unverified PID.",
                state=current,
            )

        if current.status != BotStatus.RUNNING or current.pid is None:
            return BotOperationResult(success=True, message=f"{definition.name} is not running.")

        emit(EventNames.BOT_STOP_REQUESTED, bot_id=definition.id, pid=current.pid)
        logger.info("Stop requested for bot '%s' (PID %s)", definition.id, current.pid)

        self._mark_transient_status(definition.id, BotStatus.STOPPING)

        result = runner.stop(current.pid, current.identity)

        if result.success:
            self._runtime_state[definition.id] = BotRuntimeState(
                status=BotStatus.STOPPED,
                restart_count=current.restart_count,
                last_stopped_at=datetime.now(),
            )
            self._save_state()
            emit(EventNames.BOT_STOPPED, bot_id=definition.id)
            logger.info("Bot '%s' stopped", definition.id)
            return BotOperationResult(success=True, message=f"{definition.name} stopped.")
        else:
            # Stop failed: restore the previous valid running state
            self._runtime_state[definition.id] = replace(current)
            self._save_state()
            emit(EventNames.BOT_STOP_FAILED, bot_id=definition.id, error=result.error)
            logger.warning("Bot '%s' failed to stop: %s", definition.id, result.error)
            return BotOperationResult(
                success=False,
                message=f"Failed to stop {definition.name}.",
                error=result.error,
            )

    def restart_bot(self, definition: BotDefinition) -> BotOperationResult:
        """Restart a bot: stop, verify stopped, start, verify running.

        restart_count is incremented by exactly 1 only when BOTH stop and start succeed.
        If stopping fails, restart is aborted immediately without incrementing.
        """
        emit(EventNames.BOT_RESTART_REQUESTED, bot_id=definition.id)
        logger.info("Restart requested for bot '%s'", definition.id)

        previous_restart_count = self.get_status(definition).restart_count

        stop_result = self.stop_bot(definition)
        if not stop_result.success:
            logger.warning("Restart of '%s' aborted: could not stop it first: %s", definition.id, stop_result.error)
            return BotOperationResult(
                success=False,
                message=f"Failed to restart {definition.name}.",
                error=f"Could not stop it first: {stop_result.error}",
            )

        start_result = self.start_bot(definition)
        if not start_result.success:
            logger.warning("Restart of '%s' failed: stopped, but could not start again: %s", definition.id, start_result.error)
            return BotOperationResult(
                success=False,
                message=f"Failed to restart {definition.name}.",
                error=f"Stopped, but failed to start again: {start_result.error}",
            )

        new_state = self._runtime_state.get(definition.id)
        if new_state is not None:
            new_state.restart_count = previous_restart_count + 1
            self._save_state()

        emit(EventNames.BOT_RESTARTED, bot_id=definition.id)
        logger.info("Bot '%s' restarted (restart_count=%s)", definition.id, new_state.restart_count if new_state else "?")
        return BotOperationResult(success=True, message=f"{definition.name} restarted.", state=new_state)


_manager: Optional[BotManager] = None


def get_manager() -> BotManager:
    """Return the process-wide BotManager singleton instance."""
    global _manager
    if _manager is None:
        _manager = BotManager()
    return _manager
