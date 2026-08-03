"""
identity.py
===========
Collects basic machine/software identity information: hostname,
current user, OS, kernel, Python version, git version, and working
directory. Returns a plain dictionary of raw values - formatting for
display is the caller's job (see pages/dashboard.py).
"""

import getpass
import os
import platform
import socket
import sys
from pathlib import Path
from typing import Dict, Optional

from core.helpers import run_command
from core.logging.logger import get_logger

logger = get_logger(__name__)


def _get_hostname() -> Optional[str]:
    """Return the device hostname, or None if it can't be read."""
    try:
        return socket.gethostname() or None
    except OSError as exc:
        logger.warning("Failed to read hostname: %s", exc)
        return None


def _get_current_user() -> Optional[str]:
    """Return the current username, or None if it can't be read."""
    try:
        return getpass.getuser()
    except (KeyError, OSError) as exc:
        logger.debug("getpass.getuser() failed (%s); trying USER env var", exc)
        return os.environ.get("USER")


def _get_os_info() -> Optional[str]:
    """Return a short OS description, flagging Android/Termux when detected."""
    try:
        system = platform.system() or None
        if system is None:
            logger.warning("platform.system() returned an empty value")
            return None
        is_android = "ANDROID_ROOT" in os.environ or "com.termux" in sys.prefix
        return f"Android / {system}" if is_android else system
    except OSError as exc:
        logger.warning("Failed to detect OS info: %s", exc)
        return None


def _get_kernel_version() -> Optional[str]:
    """Return the kernel release string, or None if unknown."""
    try:
        return platform.release() or None
    except OSError as exc:
        logger.warning("Failed to read kernel version: %s", exc)
        return None


def _get_python_version() -> Optional[str]:
    """Return the running Python interpreter version."""
    try:
        return platform.python_version()
    except OSError as exc:
        logger.warning("Failed to read Python version: %s", exc)
        return None


def _get_git_version() -> Optional[str]:
    """Return the installed Git version, or None if git is missing."""
    output = run_command(["git", "--version"])
    if output is None:
        logger.debug("git --version unavailable; git may not be installed")
        return None
    return output.replace("git version", "").strip()


def _get_working_directory() -> Optional[str]:
    """Return the current working directory."""
    try:
        return str(Path.cwd())
    except OSError as exc:
        logger.warning("Failed to read working directory: %s", exc)
        return None


def collect() -> Dict[str, Optional[str]]:
    """Collect identity information into a structured dictionary.

    Returns:
        A dict with keys: hostname, user, os, kernel, python, git,
        directory. Any value that couldn't be determined is None.
    """
    return {
        "hostname": _get_hostname(),
        "user": _get_current_user(),
        "os": _get_os_info(),
        "kernel": _get_kernel_version(),
        "python": _get_python_version(),
        "git": _get_git_version(),
        "directory": _get_working_directory(),
    }
