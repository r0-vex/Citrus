"""
menu.py
=======
Main navigation loop. Draws the main menu, reads the user's choice,
and dispatches to the matching page's show() function. Contains no
business logic or page content of its own - only routing. Startup/
shutdown lifecycle (sessions, top-level error handling) lives one
layer up, in app.py.
"""

from typing import Callable, Dict

from core import constants, state
from core.logging.logger import get_logger
from core.pages import bots, dashboard, git, music, services, system
from core.ui import clear_screen, pause, print_header

logger = get_logger(__name__)

_PAGE_ACTIONS: Dict[str, Callable[[], None]] = {
    constants.MenuID.DASHBOARD: dashboard.show,
    constants.MenuID.SERVICES: services.show,
    constants.MenuID.BOTS: bots.show,
    constants.MenuID.MUSIC: music.show,
    constants.MenuID.GIT: git.show,
    constants.MenuID.SYSTEM: system.show,
}


def _print_main_menu() -> None:
    """Render the main menu screen."""
    clear_screen()
    print(constants.ASCII_LOGO)
    print_header(f"{constants.ICON_LEMON} CITRUS CONTROL CENTER", constants.DEFAULT_MENU_WIDTH)
    print()
    for key, label in constants.MAIN_MENU_LABELS.items():
        print(f"{key}. {label}")
    print()


def _exit_app() -> None:
    """Print a farewell message before the app closes."""
    clear_screen()
    print(f"\nGoodbye from {constants.APP_NAME}! {constants.ICON_LEMON}\n")


def run() -> None:
    """Run the main menu loop until the user chooses to exit.

    Reads one choice at a time, dispatches to the matching page, and
    re-draws the menu on return, so navigation feels like Main Menu
    -> Page -> Back -> Main Menu without ever restarting the process.
    Returns normally on exit; app.py is responsible for anything that
    needs to happen around the whole session's lifecycle.
    """
    while True:
        state.state.set_page("main_menu")
        _print_main_menu()
        choice = input("Select an option: ").strip()

        if choice == constants.MenuID.EXIT:
            _exit_app()
            return

        action = _PAGE_ACTIONS.get(choice)
        if action is not None:
            action()
        else:
            logger.debug("Invalid menu choice entered: %r", choice)
            print("\nInvalid option.")
            pause()
