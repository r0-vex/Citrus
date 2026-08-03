"""
app.py
======
Application bootstrap: initializes configuration and logging, starts
a tracked session, runs the menu loop, and guarantees a clean,
logged shutdown no matter how the app stops. Owns the full startup/
shutdown lifecycle so citrus.py can stay a near-empty entry point.

Startup flow:
    config -> logger -> event bus -> session -> state -> menu

Shutdown flow (always runs, via finally):
    save session -> emit APP_STOPPED -> return control to citrus.py
"""

import platform
import sys
from datetime import datetime

from core import config, menu, state
from core.exceptions import ConfigurationError
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.system_info import identity
from core.version import VERSION

logger = get_logger(__name__)


def _log_session_start() -> None:
    """Log and emit details identifying this Citrus run.

    Captures session id, hostname, current user, Python version,
    platform, and app version - everything needed to reconstruct
    "what was running, where, and as whom" from the logs alone.
    """
    session = state.state
    ident = identity.collect()

    logger.info(
        "Session started | id=%s | hostname=%s | user=%s | python=%s | platform=%s | version=%s",
        session.session_id,
        ident.get("hostname"),
        ident.get("user"),
        platform.python_version(),
        platform.platform(),
        VERSION,
    )
    emit(
        EventNames.APP_STARTED,
        session_id=session.session_id,
        hostname=ident.get("hostname"),
        user=ident.get("user"),
        python_version=platform.python_version(),
        platform=platform.platform(),
        version=VERSION,
    )


def _log_session_end(reason: str) -> None:
    """Log and emit the end of this Citrus run.

    Args:
        reason: Why the session ended - "normal", "crash", or
            "keyboard_interrupt".
    """
    session = state.state
    runtime_seconds = session.uptime_seconds()

    logger.info(
        "Session ended | id=%s | reason=%s | runtime=%.1fs",
        session.session_id,
        reason,
        runtime_seconds,
    )
    emit(
        EventNames.APP_STOPPED,
        session_id=session.session_id,
        reason=reason,
        runtime_seconds=round(runtime_seconds, 1),
        end_time=datetime.now().isoformat(timespec="seconds"),
    )


def run() -> None:
    """Run the full Citrus application lifecycle.

    Initializes logging/config, starts a tracked session, runs the
    menu loop until the user exits, and guarantees the session end is
    logged and emitted no matter how the app stops (normal exit,
    crash, or Ctrl+C). Unhandled exceptions are logged with a full
    traceback and reported to the user as a short, friendly message
    rather than a raw traceback.
    """
    try:
        config.ensure_log_directory()
    except ConfigurationError as exc:
        print(f"Fatal: {exc}", file=sys.stderr)
        return

    state.state.config_loaded = True
    _log_session_start()

    reason = "normal"
    try:
        menu.run()
    except KeyboardInterrupt:
        reason = "keyboard_interrupt"
        print("\n\nInterrupted. Shutting down Citrus...")
    except Exception:
        reason = "crash"
        logger.exception("Citrus crashed with an unhandled exception")
        print("\nSomething went wrong. Check logs/errors.log for details.")
    finally:
        _log_session_end(reason)
