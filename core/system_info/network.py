"""
network.py
==========
Collects networking information: local IP address, internet
reachability, and whether an SSH daemon is currently running.
Returns a plain dictionary of raw values.
"""

import socket
from typing import Dict, Optional

from core.helpers import process_running
from core.logging.logger import get_logger

logger = get_logger(__name__)


def _check_internet_connection(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Check outbound internet connectivity via a quick TCP probe.

    Attempts a plain TCP connection rather than sending real DNS
    traffic, so it only tests reachability.

    Args:
        host: Address to attempt to connect to.
        port: Port to attempt to connect to.
        timeout: Maximum time in seconds to wait for the connection.

    Returns:
        True if the connection succeeds, False otherwise.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        logger.debug("Internet connectivity check failed: %s", exc)
        return False


def _get_ip_address() -> Optional[str]:
    """Return the device's local IP address, or None if unknown.

    Uses a UDP "connect" (no packets are actually sent) purely to ask
    the OS which local interface/address would be used to reach the
    internet.

    Returns:
        The local IP address as a string, or None on failure.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError as exc:
        logger.debug("Failed to determine local IP address: %s", exc)
        return None


def _is_ssh_running() -> bool:
    """Return True if an sshd process is currently running."""
    return process_running("sshd")


def collect() -> Dict[str, Optional[object]]:
    """Collect network information into a structured dictionary.

    Returns:
        A dict with keys: ip_address, internet_connected, ssh_running.
    """
    return {
        "ip_address": _get_ip_address(),
        "internet_connected": _check_internet_connection(),
        "ssh_running": _is_ssh_running(),
    }
