"""
constants.py
============
Static display constants: ASCII art, icons, labels, menu ids, and
default sizing/color values. Nothing here changes at runtime - for
runtime-computed configuration (paths, log format), see config.py.
Has zero internal dependencies.
"""

from typing import Dict, Final

APP_NAME: Final[str] = "Citrus"
AUTHOR: Final[str] = "You"

ASCII_LOGO: Final[str] = r"""
   _____ _ _
  / ____(_) |
 | |    _| |_ _ __ _   _ ___
 | |   | | __| '__| | | / __|
 | |___| | |_| |  | |_| \__ \
  \_____|_|\__|_|   \__,_|___/

        🍋 Citrus Platform 🍋
"""

ICON_LEMON: Final[str] = "🍋"
ICON_OK: Final[str] = "✅"
ICON_FAIL: Final[str] = "❌"

DEFAULT_MENU_WIDTH: Final[int] = 40
DEFAULT_LABEL_WIDTH: Final[int] = 18


class MenuID:
    """Namespaced constants identifying each selectable menu page.

    Using named constants instead of raw string literals keeps menu
    routing (menu.py) and menu content (constants.py) from drifting
    out of sync as pages are added.
    """

    DASHBOARD: Final[str] = "1"
    SERVICES: Final[str] = "2"
    BOTS: Final[str] = "3"
    MUSIC: Final[str] = "4"
    GIT: Final[str] = "5"
    SYSTEM: Final[str] = "6"
    EXIT: Final[str] = "0"


MAIN_MENU_LABELS: Final[Dict[str, str]] = {
    MenuID.DASHBOARD: "Dashboard",
    MenuID.SERVICES: "Services",
    MenuID.BOTS: "Bots",
    MenuID.MUSIC: "Music",
    MenuID.GIT: "Git",
    MenuID.SYSTEM: "System",
    MenuID.EXIT: "Exit",
}


class Color:
    """ANSI color escape codes used sparingly by ui.py."""

    RESET: Final[str] = "\033[0m"
    GREEN: Final[str] = "\033[32m"
    RED: Final[str] = "\033[31m"
    YELLOW: Final[str] = "\033[33m"
    CYAN: Final[str] = "\033[36m"
    DIM: Final[str] = "\033[2m"
