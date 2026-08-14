"""
services.py (pages)
====================
User-facing Service Manager page. Pure UI: lists configured services,
shows a selected service's detail/actions screen, and delegates every
actual start/stop/restart/status operation to
core.service_manager.manager. Contains no subprocess or OS-process
logic of its own - see core/service_manager/ for that.
"""

from typing import List, Optional, Tuple

from core import config
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.service_manager.manager import get_manager
from core.service_manager.models import ServiceDefinition, ServiceRuntimeState
from core.ui import clear_screen, pause, print_divider, print_header, print_row

logger = get_logger(__name__)

_LOG_TAIL_LINES = 20
_ServicePair = Tuple[ServiceDefinition, ServiceRuntimeState]


def _print_service_list(services: List[_ServicePair]) -> None:
    """Render the numbered list of configured services.

    Args:
        services: (definition, runtime_state) pairs, in display order.
    """
    print("\nConfigured Services")
    print_divider(config.MENU_WIDTH)
    for index, (definition, state) in enumerate(services, start=1):
        print(f"{index}. {definition.name.ljust(24)} {state.status.value}")
    print("\n0. Back")


def _select_service(services: List[_ServicePair], choice: str) -> Optional[_ServicePair]:
    """Resolve a menu choice to a listed service, if valid.

    Args:
        services: The currently-listed (definition, state) pairs.
        choice: Raw user input.

    Returns:
        The matching (definition, state) pair, or None if the choice
        doesn't correspond to a listed service.
    """
    if not choice.isdigit():
        return None
    index = int(choice)
    if 1 <= index <= len(services):
        return services[index - 1]
    return None


def _print_service_detail(definition: ServiceDefinition, state: ServiceRuntimeState) -> None:
    """Render a single service's detail/status screen.

    Args:
        definition: The service's static configuration.
        state: The service's current runtime state.
    """
    clear_screen()
    print_header(definition.name, config.MENU_WIDTH)
    print()
    print_row("Status", state.status.value)
    print_row("PID", str(state.pid) if state.pid is not None else "-")
    print_row("Started", state.started_at.strftime("%Y-%m-%d %H:%M:%S") if state.started_at else "-")

    runtime = state.runtime_seconds()
    print_row("Runtime", f"{int(runtime)}s" if runtime is not None else "-")

    if state.last_error:
        print_row("Last error", state.last_error)

    print()
    print("1. Start")
    print("2. Stop")
    print("3. Restart")
    print("4. Refresh")
    print("5. View Logs")
    print("0. Back")


def _view_logs(definition: ServiceDefinition) -> None:
    """Display the tail of a service's dedicated log file.

    Args:
        definition: The service whose logs should be shown.
    """
    manager = get_manager()
    log_path = manager.get_log_path(definition)

    clear_screen()
    print_header(f"{definition.name} - Logs", config.MENU_WIDTH)

    if not log_path.is_file():
        print("\nNo logs yet.\n")
    else:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            print()
            for line in lines[-_LOG_TAIL_LINES:]:
                print(line)
            print()
        except OSError as exc:
            logger.warning("Failed to read log file %s: %s", log_path, exc)
            print("\nCould not read log file.\n")

    pause()


def _report_result(message: str, error: Optional[str]) -> None:
    """Print a user-facing operation result and wait for Enter.

    Args:
        message: Short, friendly summary of what happened.
        error: Technical reason, shown only if the operation failed.
    """
    print(f"\n{message}")
    if error:
        print(f"\nReason:\n{error}")
    pause()


def _show_service_detail(definition: ServiceDefinition) -> None:
    """Run the detail/actions loop for a single service until Back.

    Args:
        definition: The service to show and act on.
    """
    manager = get_manager()

    while True:
        state = manager.get_status(definition)
        _print_service_detail(definition, state)
        choice = input("\nSelect an option: ").strip()

        if choice == "0":
            return
        if choice == "1":
            result = manager.start_service(definition)
            _report_result(result.message, result.error)
        elif choice == "2":
            result = manager.stop_service(definition)
            _report_result(result.message, result.error)
        elif choice == "3":
            result = manager.restart_service(definition)
            _report_result(result.message, result.error)
        elif choice == "4":
            continue
        elif choice == "5":
            _view_logs(definition)
        else:
            print("\nInvalid option.")
            pause()


def show() -> None:
    """Display the Service Manager: list, select, and act on services."""
    emit(EventNames.PAGE_OPENED, page="services")
    logger.info("Services page opened")

    manager = get_manager()

    while True:
        services = manager.list_services()

        clear_screen()
        print_header("🍋 SERVICE MANAGER", config.MENU_WIDTH)

        if not services:
            print(
                "\nNo services configured.\n\n"
                "Add a service definition to the services/\n"
                "directory.\n"
            )
            pause()
            break

        _print_service_list(services)
        choice = input("\nSelect an option: ").strip()

        if choice == "0":
            break

        selected = _select_service(services, choice)
        if selected is None:
            print("\nInvalid option.")
            pause()
            continue

        _show_service_detail(selected[0])

    emit(EventNames.PAGE_CLOSED, page="services")
