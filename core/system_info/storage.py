"""
storage.py
==========
Collects filesystem storage information for the Termux-writable home
directory - deliberately NOT the Android system partition mounted at
"/", which reports as nearly full and read-only, and isn't
representative of the space actually available to Citrus.
"""

import shutil
from pathlib import Path
from typing import Dict, Optional

from core.logging.logger import get_logger

logger = get_logger(__name__)


def _get_disk_usage(path: Optional[Path] = None) -> Optional[Dict[str, int]]:
    """Return raw {"used": bytes, "total": bytes} disk figures.

    Args:
        path: Filesystem path to measure. Defaults to Path.home().

    Returns:
        A dict with "used" and "total" byte counts, or None if the
        path couldn't be measured.
    """
    target = path if path is not None else Path.home()
    try:
        usage = shutil.disk_usage(target)
        return {"used": usage.used, "total": usage.total}
    except OSError as exc:
        logger.warning("Failed to read disk usage for %s: %s", target, exc)
        return None


def collect() -> Dict[str, Optional[Dict[str, int]]]:
    """Collect storage information into a structured dictionary.

    Returns:
        A dict with key: disk (a nested {"used", "total"} dict, or
        None if it couldn't be determined).
    """
    return {"disk": _get_disk_usage()}
