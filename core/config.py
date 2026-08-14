"""
config.py
=========
Central runtime configuration: filesystem paths and formatting
strings that every other module agrees on. Values here are derived
from the actual location of this file, never from the current
working directory, so Citrus behaves the same no matter how or from
where it's launched.
"""

from pathlib import Path
from typing import Dict, Final

from core.constants import DEFAULT_LABEL_WIDTH, DEFAULT_MENU_WIDTH
from core.exceptions import ConfigurationError
from core.version import VERSION

# --- Filesystem layout ---------------------------------------------------

CORE_DIR: Final[Path] = Path(__file__).resolve().parent
PROJECT_ROOT: Final[Path] = CORE_DIR.parent
LOG_DIR: Final[Path] = PROJECT_ROOT / "logs"

LOG_FILES: Final[Dict[str, Path]] = {
    "system": LOG_DIR / "system.log",
    "events": LOG_DIR / "events.log",
    "errors": LOG_DIR / "errors.log",
    "git": LOG_DIR / "git.log",
    "debug": LOG_DIR / "debug.log",
}

# --- Service Manager layout ------------------------------------------------

SERVICES_DIR: Final[Path] = PROJECT_ROOT / "services"
SERVICE_LOG_DIR: Final[Path] = LOG_DIR / "services"
SERVICE_STATE_FILE: Final[Path] = PROJECT_ROOT / "temp" / "service_manager_state.json"

# --- Logging format --------------------------------------------------------

LOG_FORMAT: Final[str] = "[%(asctime)s]\n%(levelname)s\n%(name)s\n%(message)s\n"
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

# --- UI configuration --------------------------------------------------------

MENU_WIDTH: Final[int] = DEFAULT_MENU_WIDTH
LABEL_WIDTH: Final[int] = DEFAULT_LABEL_WIDTH

# --- App metadata passthrough ----------------------------------------------

APP_VERSION: Final[str] = VERSION


def ensure_log_directory() -> None:
    """Create the log directory (and parents) if it doesn't exist yet.

    Safe to call multiple times; does nothing if the directory is
    already present.

    Raises:
        ConfigurationError: If the directory can't be created, e.g.
            due to a permissions issue.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(f"Could not create log directory {LOG_DIR}: {exc}") from exc


def ensure_service_runtime_directories() -> None:
    """Create service-manager runtime directories if they don't exist.

    Covers the per-service log directory (logs/services/) and the
    parent directory of the service runtime-state file. Safe to call
    multiple times.

    Raises:
        ConfigurationError: If a directory can't be created, e.g.
            due to a permissions issue.
    """
    try:
        SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        SERVICE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(f"Could not create service runtime directories: {exc}") from exc
