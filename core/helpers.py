"""
helpers.py
==========
Generic, domain-agnostic helper functions with no UI and no
system-info knowledge. Anything here could be reused by a completely
different project: running a subprocess safely, checking whether a
binary exists, formatting a byte count.
"""

import shutil
import subprocess
from typing import List, Optional

from core.logging.logger import get_logger

logger = get_logger(__name__)


def run_command(command: List[str], timeout: float = 3.0) -> Optional[str]:
    """Run a command and return its trimmed stdout, or None on failure.

    Every failure path is logged with its cause rather than silently
    swallowed, so problems are diagnosable from logs/debug.log or
    logs/errors.log without needing to reproduce them interactively.

    Args:
        command: The command and arguments to execute, e.g.
            ``["git", "--version"]``.
        timeout: Maximum time in seconds to wait for the command.

    Returns:
        The command's stdout, stripped, or None if it failed, was
        missing, timed out, or exited non-zero.
    """
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        logger.debug("Command not found: %s", command[0])
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %.1fs: %s", timeout, command)
        return None
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Command failed to execute: %s (%s)", command, exc)
        return None

    if result.returncode != 0:
        logger.debug(
            "Command exited %s: %s (stderr=%s)",
            result.returncode,
            command,
            result.stderr.strip(),
        )
        return None

    return result.stdout.strip()


def command_exists(command: str) -> bool:
    """Check whether a command is available on the system PATH.

    Args:
        command: The executable name to look for.

    Returns:
        True if the command can be found, False otherwise.
    """
    return shutil.which(command) is not None


def process_running(name: str) -> bool:
    """Check whether a process matching `name` is currently running.

    Args:
        name: Substring to match against running process names.

    Returns:
        True if a matching process is found, False otherwise.
    """
    return run_command(["pgrep", "-f", name]) is not None


def format_bytes(num_bytes: float) -> str:
    """Convert a byte count into a human-readable string.

    Args:
        num_bytes: The number of bytes to format.

    Returns:
        A string such as "1.6 GB".
    """
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"
