"""
dashboard.py
============
Real-time system monitoring screen.

Pulls system, server, hardware, and runtime information together
into one status screen. Every value is fetched through a dedicated
helper in utils.py, each of which degrades to "Unavailable" instead
of raising, so this screen can never crash the app.
"""

from datetime import datetime

from config import MENU_WIDTH, VERSION
from utils import (
    check_internet_connection,
    clear_screen,
    get_cpu_architecture,
    get_cpu_count,
    get_current_user,
    get_disk_usage,
    get_git_version,
    get_hostname,
    get_ip_address,
    get_kernel_version,
    get_os_info,
    get_python_version,
    get_ram_usage,
    get_uptime,
    get_working_directory,
    is_ssh_running,
    get_battery_percentage,
    pause,
    print_divider,
    print_header,
)

_LABEL_WIDTH: int = 18


def _print_row(label: str, value: str) -> None:
    """Print a single aligned "label   value" row.

    Args:
        label: Short field name, e.g. "Hostname".
        value: The value to display next to it.
    """
    print(f"{label.ljust(_LABEL_WIDTH)}{value}")


def _print_system_section() -> None:
    """Print the System section of the dashboard."""
    print("\nSystem")
    print_divider(MENU_WIDTH)
    _print_row("Hostname", get_hostname())
    _print_row("User", get_current_user())
    _print_row("OS", get_os_info())
    _print_row("Kernel", get_kernel_version())
    _print_row("Python", get_python_version())
    _print_row("Git", get_git_version())
    _print_row("Directory", get_working_directory())


def _print_server_section() -> None:
    """Print the Server Status section of the dashboard."""
    print("\nServer")
    print_divider(MENU_WIDTH)
    _print_row("SSH", "Running" if is_ssh_running() else "Not running")
    _print_row("Internet", "Connected" if check_internet_connection() else "Disconnected")
    _print_row("IP", get_ip_address())


def _print_hardware_section() -> None:
    """Print the Hardware section of the dashboard."""
    print("\nHardware")
    print_divider(MENU_WIDTH)
    _print_row("CPU", get_cpu_architecture())
    _print_row("Cores", get_cpu_count())
    _print_row("RAM", get_ram_usage())
    _print_row("Storage", get_disk_usage())
    _print_row("Battery", get_battery_percentage())


def _print_runtime_section() -> None:
    """Print the Runtime section of the dashboard."""
    print("\nRuntime")
    print_divider(MENU_WIDTH)
    _print_row("Version", VERSION)
    _print_row("Time", datetime.now().strftime("%I:%M %p"))
    _print_row("Uptime", get_uptime())


def dashboard() -> None:
    """Display the full Citrus dashboard and wait for the user.

    Renders System, Server, Hardware, and Runtime sections, then
    blocks on Enter before returning control to the main menu.
    """
    clear_screen()
    print_header("🍋 CITRUS DASHBOARD", MENU_WIDTH)

    _print_system_section()
    _print_server_section()
    _print_hardware_section()
    _print_runtime_section()

    print()
    print("=" * MENU_WIDTH)
    pause("\nPress ENTER to return...")
