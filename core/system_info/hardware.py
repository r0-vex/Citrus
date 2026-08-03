"""
hardware.py
===========
Collects hardware information: CPU architecture and core count, RAM
usage, and battery percentage. Returns a plain dictionary of raw
values - formatting for display is the caller's job.
"""

import json
import os
import platform
from pathlib import Path
from typing import Dict, Optional

from core.helpers import run_command
from core.logging.logger import get_logger

logger = get_logger(__name__)


def _get_cpu_architecture() -> Optional[str]:
    """Return the CPU architecture string, e.g. "aarch64"."""
    try:
        return platform.machine() or None
    except OSError as exc:
        logger.warning("Failed to read CPU architecture: %s", exc)
        return None


def _count_processors_from_cpuinfo() -> Optional[int]:
    """Count "processor" entries in /proc/cpuinfo.

    Matches only lines whose key (the text before the first colon,
    stripped) is exactly "processor" - a naive prefix/substring match
    would also count unrelated "Processor" model-name fields present
    on some Android/ARM kernels, which previously caused undercounting
    on the Galaxy J7 Max.

    Returns:
        The processor count if at least one entry was found,
        otherwise None.
    """
    try:
        count = 0
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key = line.split(":", 1)[0].strip()
                if key == "processor":
                    count += 1

        if count > 0:
            return count

        logger.warning("/proc/cpuinfo parsed but found 0 'processor' entries")
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Failed to parse /proc/cpuinfo: %s", exc)
        return None


def _count_processors_from_nproc() -> Optional[int]:
    """Return CPU count via the `nproc` command, or None on failure."""
    output = run_command(["nproc"])
    if output is None or not output.isdigit():
        logger.debug("nproc unavailable or returned non-numeric output: %r", output)
        return None
    return int(output)


def _count_processors_from_os() -> Optional[int]:
    """Return CPU count via os.cpu_count(), or None if unknown."""
    count = os.cpu_count()
    if count is None:
        logger.warning("os.cpu_count() returned None")
    return count


def _get_cpu_count() -> Optional[int]:
    """Determine logical CPU core count using a robust fallback chain.

    Priority order: /proc/cpuinfo, then `nproc`, then os.cpu_count().
    Each method's failure is logged with its cause before falling
    through to the next, since os.cpu_count() has been observed to
    under-report the real core count on some Android/Termux devices
    (including the Galaxy J7 Max).

    Returns:
        The detected core count, or None if every method failed.
    """
    for method in (
        _count_processors_from_cpuinfo,
        _count_processors_from_nproc,
        _count_processors_from_os,
    ):
        result = method()
        if result is not None:
            return result

    logger.error("All CPU count methods failed")
    return None


def _get_ram_usage_bytes() -> Optional[Dict[str, int]]:
    """Return raw {"used": bytes, "total": bytes} RAM figures.

    Reads /proc/meminfo directly (available on Termux/Linux).

    Returns:
        A dict with "used" and "total" byte counts, or None if
        /proc/meminfo couldn't be read or parsed.
    """
    try:
        meminfo: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                fields = rest.strip().split()
                if not fields:
                    continue
                meminfo[key.strip()] = int(fields[0]) * 1024  # kB -> bytes

        total = meminfo.get("MemTotal")
        available = meminfo.get("MemAvailable")
        if total is None or available is None:
            logger.warning("MemTotal/MemAvailable missing from /proc/meminfo")
            return None

        return {"used": total - available, "total": total}
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read /proc/meminfo: %s", exc)
        return None


def _get_battery_percentage() -> Optional[int]:
    """Return battery charge percentage, if available on this device.

    Tries the Linux power-supply sysfs entry first, then falls back
    to the termux-api package's `termux-battery-status` command.

    Returns:
        An integer percentage, or None if neither method works -
        which is expected on devices/environments without battery
        access (e.g. this development sandbox).
    """
    capacity_path = Path("/sys/class/power_supply/battery/capacity")
    try:
        if capacity_path.exists():
            raw = capacity_path.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                return int(raw)
    except OSError as exc:
        logger.debug("Battery sysfs read failed: %s", exc)

    output = run_command(["termux-battery-status"])
    if output:
        try:
            data = json.loads(output)
            percentage = data.get("percentage")
            if percentage is not None:
                return int(percentage)
        except (json.JSONDecodeError, AttributeError, ValueError) as exc:
            logger.debug("Failed to parse termux-battery-status output: %s", exc)

    logger.debug("Battery percentage unavailable on this device")
    return None


def collect() -> Dict[str, Optional[object]]:
    """Collect hardware information into a structured dictionary.

    Returns:
        A dict with keys: cpu_architecture, cpu_count, ram (a nested
        {"used", "total"} dict or None), battery_percent. Any value
        that couldn't be determined is None.
    """
    return {
        "cpu_architecture": _get_cpu_architecture(),
        "cpu_count": _get_cpu_count(),
        "ram": _get_ram_usage_bytes(),
        "battery_percent": _get_battery_percentage(),
    }
