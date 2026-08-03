"""
logger.py
=========
Central logger factory for Citrus.

Every module calls get_logger(__name__) instead of using print().
This module owns all handler/formatter setup and decides which
physical log file each logger's records are written to.

See core/logging/__init__.py for an important note about why
"import logging" below is safe despite this package's own name.
"""

import logging
from pathlib import Path
from typing import Dict

from core import config

_configured_loggers: Dict[str, logging.Logger] = {}
_root_configured: bool = False


def build_file_handler(log_path: Path, level: int) -> logging.Handler:
    """Create a file handler using Citrus's standard formatter.

    Shared by this module and core.logging.events, so every log file
    Citrus writes uses one consistent timestamp/level/module/message
    format.

    Args:
        log_path: Absolute path to the log file to write to.
        level: Minimum logging level this handler should accept.

    Returns:
        A configured logging.FileHandler.
    """
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(fmt=config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT)
    )
    return handler


def _configure_root() -> None:
    """Attach Citrus's shared handlers to the root "citrus" logger.

    Runs once per process. Routes:
        * errors.log <- WARNING and above, from every logger.
        * system.log <- INFO and above, from every logger.
        * debug.log  <- DEBUG and above, from every logger.

    Module-specific files (events.log, git.log) are attached
    separately by core.logging.events and get_git_logger(), so they
    stay free of general application noise.
    """
    global _root_configured
    if _root_configured:
        return

    config.ensure_log_directory()

    root = logging.getLogger("citrus")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    root.addHandler(build_file_handler(config.LOG_FILES["errors"], logging.WARNING))
    root.addHandler(build_file_handler(config.LOG_FILES["system"], logging.INFO))
    root.addHandler(build_file_handler(config.LOG_FILES["debug"], logging.DEBUG))

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given module name.

    Args:
        name: Usually the caller's __name__, used to identify the
            source module in every log line.

    Returns:
        A logging.Logger that writes to Citrus's shared log files.
    """
    _configure_root()

    qualified_name = f"citrus.{name}"
    if qualified_name in _configured_loggers:
        return _configured_loggers[qualified_name]

    logger = logging.getLogger(qualified_name)
    _configured_loggers[qualified_name] = logger
    return logger


def get_git_logger() -> logging.Logger:
    """Return a logger dedicated to git.log, for git-related pages only.

    Returns:
        A logging.Logger that writes to git.log in addition to
        bubbling up (via propagation) to the shared errors/system/
        debug logs, so git activity is both centrally visible and
        separately reviewable.
    """
    _configure_root()

    qualified_name = "citrus.git"
    if qualified_name in _configured_loggers:
        return _configured_loggers[qualified_name]

    logger = logging.getLogger(qualified_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    logger.addHandler(build_file_handler(config.LOG_FILES["git"], logging.DEBUG))

    _configured_loggers[qualified_name] = logger
    return logger
