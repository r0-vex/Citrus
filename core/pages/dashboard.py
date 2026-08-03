"""
dashboard.py (pages)
=====================
Renders the Citrus dashboard. Performs zero subprocess calls or file
reads itself - it only asks core.system_info.collect() for a
dictionary and formats that dictionary for display. See
core/system_info/ for where the actual data collection happens.
"""

from typing import Dict, Optional

from core import config, system_info
from core.constants import ICON_FAIL, ICON_OK
from core.helpers import format_bytes
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.ui import clear_screen, pause, print_divider, print_header, print_row

logger = get_logger(__name__)


def _fmt(value: Optional[object]) -> str:
    """Render any collector value as display text, defaulting sensibly.

    Args:
        value: A raw value from a system_info collector dict.

    Returns:
        "Unavailable" for None, otherwise str(value).
    """
    return "Unavailable" if value is None else str(value)


def _fmt_bytes_pair(pair: Optional[Dict[str, int]]) -> str:
    """Render a {"used", "total"} byte-count dict as "X / Y".

    Args:
        pair: A dict with "used" and "total" byte counts, or None.

    Returns:
        A human-readable "used / total" string, or "Unavailable".
    """
    if not pair:
        return "Unavailable"
    return f"{format_bytes(pair['used'])} / {format_bytes(pair['total'])}"


def _render_identity_section(identity: Dict[str, Optional[str]]) -> None:
    """Render the System (identity) section.

    Args:
        identity: The dict returned by system_info.identity.collect().
    """
    print("\nSystem")
    print_divider(config.MENU_WIDTH)
    print_row("Hostname", _fmt(identity.get("hostname")))
    print_row("User", _fmt(identity.get("user")))
    print_row("OS", _fmt(identity.get("os")))
    print_row("Kernel", _fmt(identity.get("kernel")))
    print_row("Python", _fmt(identity.get("python")))
    print_row("Git", _fmt(identity.get("git")))
    print_row("Directory", _fmt(identity.get("directory")))


def _render_network_section(network: Dict[str, Optional[object]]) -> None:
    """Render the Server (network) section.

    Args:
        network: The dict returned by system_info.network.collect().
    """
    print("\nServer")
    print_divider(config.MENU_WIDTH)

    ssh_running = bool(network.get("ssh_running"))
    ssh_icon = ICON_OK if ssh_running else ICON_FAIL
    print_row("SSH", f"{ssh_icon} {'Running' if ssh_running else 'Not running'}")

    connected = bool(network.get("internet_connected"))
    net_icon = ICON_OK if connected else ICON_FAIL
    print_row("Internet", f"{net_icon} {'Connected' if connected else 'Disconnected'}")

    print_row("IP", _fmt(network.get("ip_address")))


def _render_hardware_section(
    hardware: Dict[str, Optional[object]], storage: Dict[str, Optional[dict]]
) -> None:
    """Render the Hardware section.

    Args:
        hardware: The dict returned by system_info.hardware.collect().
        storage: The dict returned by system_info.storage.collect().
    """
    print("\nHardware")
    print_divider(config.MENU_WIDTH)
    print_row("CPU", _fmt(hardware.get("cpu_architecture")))
    print_row("Cores", _fmt(hardware.get("cpu_count")))
    print_row("RAM", _fmt_bytes_pair(hardware.get("ram")))
    print_row("Storage", _fmt_bytes_pair(storage.get("disk")))

    battery = hardware.get("battery_percent")
    print_row("Battery", f"{battery}%" if battery is not None else "Unavailable")


def _render_runtime_section(runtime: Dict[str, Optional[str]]) -> None:
    """Render the Runtime section.

    Args:
        runtime: The dict returned by system_info.runtime.collect().
    """
    print("\nRuntime")
    print_divider(config.MENU_WIDTH)
    print_row("Version", _fmt(runtime.get("version")))
    print_row("Time", _fmt(runtime.get("current_time")))
    print_row("Uptime", _fmt(runtime.get("uptime")))


def show() -> None:
    """Display the full Citrus dashboard and wait for the user.

    Collects a fresh snapshot of system data, renders it section by
    section, then blocks on Enter before returning to the menu.
    """
    emit(EventNames.PAGE_OPENED, page="dashboard")
    logger.info("Dashboard opened")

    data = system_info.collect()

    clear_screen()
    print_header("🍋 CITRUS DASHBOARD", config.MENU_WIDTH)

    _render_identity_section(data["identity"])
    _render_network_section(data["network"])
    _render_hardware_section(data["hardware"], data["storage"])
    _render_runtime_section(data["runtime"])

    print()
    print("=" * config.MENU_WIDTH)
    pause("\nPress ENTER to return...")

    emit(EventNames.PAGE_CLOSED, page="dashboard")
