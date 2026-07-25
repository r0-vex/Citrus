"""
utils.py
========
Reusable helper functions shared by every Citrus module.

Keeping these in one place means the rest of the app never has to
re-implement "clear the screen" or "print a header" logic, and any
visual/UX tweak (box characters, colors, timeouts) only needs to
happen here.
"""

import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from config import MENU_WIDTH


def clear_screen() -> None:
    """Clear the terminal screen.

    Works on Termux/Linux (``clear``) and, just in case, on Windows
    (``cls``) so the codebase stays portable.
    """
    os.system("cls" if os.name == "nt" else "clear")


def pause(message: str = "\nPress Enter to return...") -> None:
    """Block until the user presses Enter.

    Args:
        message: Prompt shown while waiting for input.
    """
    input(message)


def print_header(title: str, width: int = MENU_WIDTH) -> None:
    """Print a centered, boxed section header.

    Args:
        title: The text to display inside the header.
        width: Total width of the header line, in characters.
    """
    print("=" * width)
    print(title.center(width))
    print("=" * width)


def print_divider(width: int = MENU_WIDTH) -> None:
    """Print a thin divider line, useful to separate sections."""
    print("-" * width)


def command_exists(command: str) -> bool:
    """Check whether a command is available on the system PATH.

    Args:
        command: The executable name to look for (e.g. ``"git"``).

    Returns:
        True if the command can be found, False otherwise.
    """
    return shutil.which(command) is not None


def process_running(name: str) -> bool:
    """Check whether a process matching ``name`` is currently running.

    Uses ``pgrep`` (available on Termux/Linux) as a lightweight,
    dependency-free way to inspect running processes.

    Args:
        name: Substring to match against running process names.

    Returns:
        True if a matching process is found, False otherwise
        (including if the check itself fails for any reason).
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


# ---------------------------------------------------------------------
# System information helpers
#
# Every function below is defensive by design: system/hardware queries
# can fail for many reasons (missing binary, no permission, not
# available on this device), and the dashboard must never crash
# because of it. Each helper catches its own errors and returns a
# plain "Unavailable" string (or a safe default) instead of raising.
# ---------------------------------------------------------------------


def run_command(command: List[str], timeout: float = 3.0) -> Optional[str]:
    """Run a command and return its trimmed stdout, or None on failure.

    Args:
        command: The command and arguments to execute, e.g. ``["git", "--version"]``.
        timeout: Maximum time in seconds to wait for the command.

    Returns:
        The command's stdout with surrounding whitespace stripped, or
        None if the command is missing, fails, or times out.
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
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def format_bytes(num_bytes: float) -> str:
    """Convert a byte count into a human-readable string.

    Args:
        num_bytes: The number of bytes to format.

    Returns:
        A string such as ``"1.6 GB"``.
    """
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def get_hostname() -> str:
    """Return the device hostname, or "Unavailable" if it can't be read."""
    try:
        return socket.gethostname() or "Unavailable"
    except OSError:
        return "Unavailable"


def get_current_user() -> str:
    """Return the current username, or "Unavailable" if it can't be read."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return os.environ.get("USER", "Unavailable")


def get_os_info() -> str:
    """Return a short OS description, flagging Android/Termux when detected."""
    try:
        system = platform.system() or "Unknown"
        is_android = "ANDROID_ROOT" in os.environ or "com.termux" in sys.prefix
        return f"Android / {system}" if is_android else system
    except OSError:
        return "Unavailable"


def get_kernel_version() -> str:
    """Return the kernel release string, or "Unavailable" if unknown."""
    try:
        return platform.release() or "Unavailable"
    except OSError:
        return "Unavailable"


def get_python_version() -> str:
    """Return the running Python interpreter version, e.g. "3.11.4"."""
    try:
        return platform.python_version()
    except OSError:
        return "Unavailable"


def get_git_version() -> str:
    """Return the installed Git version, or "Unavailable" if git is missing."""
    output = run_command(["git", "--version"])
    if output:
        return output.replace("git version", "").strip()
    return "Unavailable"


def get_working_directory() -> str:
    """Return the current working directory as a string."""
    try:
        return str(Path.cwd())
    except OSError:
        return "Unavailable"


def is_ssh_running() -> bool:
    """Return True if an sshd process is currently running."""
    return process_running("sshd")


def check_internet_connection(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> bool:
    """Check for outbound internet connectivity.

    Attempts a quick TCP connection to a well-known DNS server rather
    than sending real DNS traffic, so it only tests reachability.

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
    except OSError:
        return False


def get_ip_address() -> str:
    """Return the device's local IP address, or "Unavailable" if unknown.

    Uses a UDP "connect" (no packets are actually sent) purely to ask
    the OS which local interface/address would be used to reach the
    internet.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "Unavailable"


def get_cpu_architecture() -> str:
    """Return the CPU architecture string, e.g. "aarch64"."""
    try:
        return platform.machine() or "Unavailable"
    except OSError:
        return "Unavailable"


def get_cpu_count() -> str:
    """Return the number of logical CPU cores as a string."""
    count = os.cpu_count()
    return str(count) if count else "Unavailable"


def get_ram_usage() -> str:
    """Return "used / total" RAM as a human-readable string.

    Reads /proc/meminfo directly (available on Termux/Linux) so no
    third-party system-info library is required.
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
            return "Unavailable"

        used = total - available
        return f"{format_bytes(used)} / {format_bytes(total)}"
    except (OSError, ValueError):
        return "Unavailable"


def get_disk_usage(path: str = "/") -> str:
    """Return "used / total" disk space for the given path.

    Args:
        path: Filesystem path to measure. Defaults to root.
    """
    try:
        usage = shutil.disk_usage(path)
        return f"{format_bytes(usage.used)} / {format_bytes(usage.total)}"
    except OSError:
        return "Unavailable"


def get_battery_percentage() -> str:
    """Return battery charge as a percentage string, if available.

    Tries the Linux power-supply sysfs entry first, then falls back
    to the ``termux-api`` package's ``termux-battery-status`` command
    if it's installed. Returns "Unavailable" if neither works, which
    is expected on devices/environments without battery access.
    """
    capacity_path = Path("/sys/class/power_supply/battery/capacity")
    try:
        if capacity_path.exists():
            capacity = capacity_path.read_text(encoding="utf-8").strip()
            if capacity:
                return f"{capacity}%"
    except OSError:
        pass

    output = run_command(["termux-battery-status"])
    if output:
        try:
            data = json.loads(output)
            percentage = data.get("percentage")
            if percentage is not None:
                return f"{percentage}%"
        except (json.JSONDecodeError, AttributeError):
            pass

    return "Unavailable"


def get_uptime() -> str:
    """Return system uptime as a short "1d 2h 3m" style string.

    Reads /proc/uptime directly (available on Termux/Linux). Returns
    "Unavailable" if the file can't be read, e.g. on non-Linux systems.
    """
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            seconds = float(handle.read().split()[0])

        days, remainder = divmod(int(seconds), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)

        parts = [f"{days}d"] if days else []
        if hours or days:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)
    except (OSError, ValueError, IndexError):
        return "Unavailable"


def show_placeholder(title: str, message: str = "Future feature.") -> None:
    """Display a generic "coming soon" screen and wait for Enter.

    Used by modules that don't have real functionality yet, so every
    stub screen looks and behaves the same way.

    Args:
        title: Header text for the screen.
        message: Body text explaining the current state of the module.
    """
    clear_screen()
    print_header(title)
    print(f"\n{message}\n")
    pause()
