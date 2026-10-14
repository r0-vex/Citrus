"""
bots.py (pages)
=================
User-facing Bot Manager page. Pure UI: lists configured bots from bots/*.json,
displays selected bot detail/actions, and delegates every start/stop/restart/status
operation to core.bot_manager.manager. Contains no subprocess or OS-process logic
of its own.
"""

from typing import List, Optional, Tuple

from core import config
from core.bot_manager.manager import get_manager
from core.bot_manager.models import BotDefinition, BotRuntimeState
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.ui import clear_screen, pause, print_divider, print_header, print_row

logger = get_logger(__name__)

_LOG_TAIL_LINES = 20
_BotPair = Tuple[BotDefinition, BotRuntimeState]


def _format_status_badge(state: BotRuntimeState) -> str:
    """Format the process lifecycle status badge."""
    return state.status.value


def _print_bot_list(bots: List[_BotPair]) -> None:
    """Render the numbered list of configured bots.

    Args:
        bots: (definition, runtime_state) pairs in display order.
    """
    print("\nConfigured Bots")
    print_divider(config.MENU_WIDTH)
    for index, (definition, state) in enumerate(bots, start=1):
        status_display = _format_status_badge(state)
        print(f"{index}. {definition.name.ljust(22)} {status_display}")
    print("\n0. Back")


def _select_bot(bots: List[_BotPair], choice: str) -> Optional[_BotPair]:
    """Resolve a menu choice to a listed bot, if valid.

    Args:
        bots: Currently-listed (definition, state) pairs.
        choice: Raw user input.

    Returns:
        Matching (definition, state) pair, or None if invalid.
    """
    if not choice.isdigit():
        return None
    index = int(choice)
    if 1 <= index <= len(bots):
        return bots[index - 1]
    return None


def _print_bot_detail(definition: BotDefinition, state: BotRuntimeState) -> None:
    """Render a single bot's detail and status screen.

    Args:
        definition: The bot's static configuration.
        state: The bot's current verified runtime state.
    """
    clear_screen()
    print_header(definition.name, config.MENU_WIDTH)
    print()
    print_row("Status", state.status.value)
    print_row("PID", str(state.pid) if state.pid is not None else "-")
    print_row("Started", state.started_at.strftime("%Y-%m-%d %H:%M:%S") if state.started_at else "-")

    runtime = state.runtime_seconds()
    print_row("Runtime", f"{int(runtime)}s" if runtime is not None else "-")

    print_row("Restarts", str(state.restart_count))
    print_row("Last Exit", state.last_exit_at.strftime("%Y-%m-%d %H:%M:%S") if state.last_exit_at else "-")
    print_row("Last Stopped", state.last_stopped_at.strftime("%Y-%m-%d %H:%M:%S") if state.last_stopped_at else "-")
    print_row("Last Error", state.last_error if state.last_error else "-")

    print()
    print("1. Start")
    print("2. Stop")
    print("3. Restart")
    print("4. Refresh")
    print("5. View Logs")
    print("0. Back")


def _view_logs(definition: BotDefinition) -> None:
    """Display the trailing lines of a bot's dedicated log file."""
    manager = get_manager()
    clear_screen()
    print_header(f"{definition.name} - Logs", config.MENU_WIDTH)

    lines = manager.get_log_tail(definition, max_lines=_LOG_TAIL_LINES)
    if not lines:
        print("\nNo logs yet.\n")
    else:
        print()
        for line in lines:
            print(line)
        print()

    pause()


def _report_result(message: str, error: Optional[str]) -> None:
    """Print a user-facing operation result and wait for Enter.

    Args:
        message: Short, friendly summary.
        error: Technical details if operation failed.
    """
    print(f"\n{message}")
    if error:
        print(f"\nReason:\n{error}")
    pause()


def _show_bot_detail(definition: BotDefinition) -> None:
    """Run the detail/actions loop for a single bot until Back."""
    manager = get_manager()

    while True:
        state = manager.get_status(definition)
        _print_bot_detail(definition, state)
        choice = input("\nSelect an option: ").strip()

        if choice == "0":
            return
        if choice == "1":
            result = manager.start_bot(definition)
            _report_result(result.message, result.error)
        elif choice == "2":
            result = manager.stop_bot(definition)
            _report_result(result.message, result.error)
        elif choice == "3":
            result = manager.restart_bot(definition)
            _report_result(result.message, result.error)
        elif choice == "4":
            continue
        elif choice == "5":
            _view_logs(definition)
        else:
            print("\nInvalid option.")
            pause()


def show() -> None:
    """Display the Bot Manager: list, select, and manage bots."""
    emit(EventNames.PAGE_OPENED, page="bots")
    logger.info("Bots page opened")

    manager = get_manager()

    while True:
        bots = manager.list_bots()

        clear_screen()
        print_header("🍋 BOT MANAGER", config.MENU_WIDTH)

        if not bots:
            print(
                "\nNo bots configured.\n\n"
                "Add a bot manifest to the bots/\n"
                "directory.\n"
            )
            pause()
            break

        _print_bot_list(bots)
        choice = input("\nSelect an option: ").strip()

        if choice == "0":
            break

        selected = _select_bot(bots, choice)
        if selected is None:
            print("\nInvalid option.")
            pause()
            continue

        _show_bot_detail(selected[0])

    emit(EventNames.PAGE_CLOSED, page="bots")
