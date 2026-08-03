"""
runtime.py
==========
Collects runtime information about the Citrus process itself:
application version, current wall-clock time, and system uptime.
"""

from datetime import datetime
from typing import Dict, Optional

from core.helpers import run_command
from core.logging.logger import get_logger
from core.version import VERSION

logger = get_logger(__name__)


def _format_uptime_seconds(seconds: float) -> str:
    """Convert a duration in seconds into a short "1d 2h 3m" string.

    Args:
        seconds: Duration in seconds.

    Returns:
        A compact human-readable uptime string.
    """
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = [f"{days}d"] if days else []
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _parse_uptime_command_output(output: str) -> Optional[str]:
    """Extract a short, readable uptime phrase from `uptime` output.

    Handles both GNU coreutils and BusyBox/Toybox styles, e.g.:
    "03:22:01 up 1 day, 2:34, 0 users, load average: 0.10, 0.05, 0.01"
    "up 1 day, 2:34"

    Args:
        output: Raw stdout from the "uptime" command.

    Returns:
        A cleaned phrase such as "1 day, 2:34", or None if the output
        doesn't look like a recognizable uptime line.
    """
    if "up" not in output:
        return None

    after_up = output.split("up", 1)[1]
    cleaned_chunks = []
    for chunk in (part.strip() for part in after_up.split(",")):
        if not chunk:
            continue
        if "load average" in chunk:
            break
        stripped = chunk.replace("users", "").replace("user", "").strip()
        if stripped.isdigit():
            continue
        cleaned_chunks.append(chunk)

    return ", ".join(cleaned_chunks) if cleaned_chunks else None


def _get_uptime() -> Optional[str]:
    """Return system uptime as a short, human-readable string.

    Tries /proc/uptime first (fast and precise), then falls back to
    the "uptime" command, since /proc/uptime has been observed to
    raise "Permission denied" on some Android/Termux devices
    (including the Galaxy J7 Max).

    Returns:
        A compact uptime string, or None if every method failed.
    """
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            seconds = float(handle.read().split()[0])
        return _format_uptime_seconds(seconds)
    except (OSError, ValueError, IndexError) as exc:
        logger.debug("/proc/uptime unavailable (%s); trying `uptime` command", exc)

    output = run_command(["uptime"])
    if output:
        parsed = _parse_uptime_command_output(output)
        if parsed:
            return parsed
        logger.warning("`uptime` command output not recognized: %r", output)
    else:
        logger.warning("Both /proc/uptime and `uptime` command failed")

    return None


def collect() -> Dict[str, Optional[str]]:
    """Collect runtime information into a structured dictionary.

    Returns:
        A dict with keys: version, current_time, uptime.
    """
    return {
        "version": VERSION,
        "current_time": datetime.now().strftime("%I:%M %p"),
        "uptime": _get_uptime(),
    }
