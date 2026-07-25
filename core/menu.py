"""
menu.py
=======
Main application loop and navigation.

This is the "traffic controller" of Citrus: it draws the main menu,
reads the user's choice, and hands control to the matching module.
Every screen returns here when it's done, so the app never restarts
between actions - it just loops.
"""

from typing import Callable, Dict

from config import APP_NAME, ASCII_LOGO, MENU_WIDTH
from utils import clear_screen, print_header, pause, show_placeholder
from dashboard import dashboard
from services import services_menu
from git_tools import git_menu
from system import system_menu


def _bots_menu() -> None:
    """Placeholder entry point for the Bots module."""
    show_placeholder("Bots", "No bots configured yet.")


def _music_menu() -> None:
    """Placeholder entry point for the Music module."""
    show_placeholder("Music Server", "Music server not set up yet.")


def _print_menu() -> None:
    """Render the main menu screen."""
    clear_screen()
    print(ASCII_LOGO)
    print_header("🍋 CITRUS CONTROL CENTER", MENU_WIDTH)
    print(
        "\n"
        "1. Dashboard\n"
        "2. Services\n"
        "3. Bots\n"
        "4. Music\n"
        "5. Git\n"
        "6. System\n"
        "0. Exit\n"
    )


def _exit_app() -> None:
    """Print a farewell message before the app closes."""
    clear_screen()
    print(f"\nGoodbye from {APP_NAME}! 🍋\n")


def main_loop() -> None:
    """Run the main menu loop until the user chooses to exit.

    Reads a single keypress-style choice at a time, dispatches to the
    matching module function, and re-draws the menu on return - this
    is what makes navigation feel like Main Menu -> Page -> Back ->
    Main Menu without ever restarting the process.
    """
    actions: Dict[str, Callable[[], None]] = {
        "1": dashboard,
        "2": services_menu,
        "3": _bots_menu,
        "4": _music_menu,
        "5": git_menu,
        "6": system_menu,
    }

    while True:
        _print_menu()
        choice = input("Select an option: ").strip()

        if choice == "0":
            _exit_app()
            break

        action = actions.get(choice)
        if action is not None:
            action()
        else:
            print("\nInvalid option.")
            pause()
