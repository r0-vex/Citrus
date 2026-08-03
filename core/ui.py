"""
ui.py
=====
Every terminal-drawing primitive Citrus uses: clearing the screen,
headers, dividers, colored output, pausing for input, and generic
menu/status rendering. Nothing here knows about hostnames, CPUs,
git, or any other domain data - it only knows how to draw text.
"""

import os
import shutil
from typing import Dict

from core import config
from core.constants import Color, ICON_FAIL, ICON_OK
from core.logging.logger import get_logger

logger = get_logger(__name__)


def clear_screen() -> None:
    """Clear the terminal screen (Termux/Linux, with Windows fallback)."""
    os.system("cls" if os.name == "nt" else "clear")


def pause(message: str = "\nPress Enter to return...") -> None:
    """Block until the user presses Enter.

    Args:
        message: Prompt shown while waiting for input.
    """
    input(message)


def terminal_width(default: int = config.MENU_WIDTH) -> int:
    """Return the current terminal width, falling back to a default.

    Args:
        default: Width to use if the terminal size can't be detected.

    Returns:
        The detected terminal column count, or `default`.
    """
    try:
        return shutil.get_terminal_size(fallback=(default, 24)).columns
    except OSError as exc:
        logger.debug("Failed to detect terminal width: %s", exc)
        return default


def colorize(text: str, color: str, enabled: bool = True) -> str:
    """Wrap text in an ANSI color code, if enabled.

    Args:
        text: The text to colorize.
        color: One of the escape codes from constants.Color.
        enabled: Whether to actually apply color (callers can disable
            this for logs or unsupported terminals).

    Returns:
        The colorized string, or the original text if disabled.
    """
    if not enabled:
        return text
    return f"{color}{text}{Color.RESET}"


def print_header(title: str, width: int = config.MENU_WIDTH) -> None:
    """Print a centered, boxed section header.

    Args:
        title: The text to display inside the header.
        width: Total width of the header line, in characters.
    """
    print("=" * width)
    print(title.center(width))
    print("=" * width)


def print_divider(width: int = config.MENU_WIDTH) -> None:
    """Print a thin divider line, useful to separate sections.

    Args:
        width: Width of the divider line, in characters.
    """
    print("-" * width)


def print_row(label: str, value: str, label_width: int = config.LABEL_WIDTH) -> None:
    """Print a single aligned "label   value" row.

    Args:
        label: Short field name, e.g. "Hostname".
        value: The value to display next to it.
        label_width: Fixed width the label column is padded to.
    """
    print(f"{label.ljust(label_width)}{value}")


def print_status(label: str, is_ok: bool, label_width: int = config.LABEL_WIDTH) -> None:
    """Print a status row with an OK/FAIL icon.

    Args:
        label: Short field name.
        is_ok: Whether to show the OK icon (True) or FAIL icon (False).
        label_width: Fixed width the label column is padded to.
    """
    icon = ICON_OK if is_ok else ICON_FAIL
    print(f"  {icon} {label.ljust(label_width)}")


def render_menu(title: str, options: Dict[str, str], width: int = config.MENU_WIDTH) -> None:
    """Render a full-screen menu: header plus numbered options.

    Args:
        title: Menu title shown in the header.
        options: Ordered mapping of choice key -> display label.
        width: Header/box width.
    """
    print_header(title, width)
    print()
    for key, label in options.items():
        print(f"{key}. {label}")
    print()


def show_placeholder(title: str, message: str = "Future feature.") -> None:
    """Display a generic "coming soon" screen and wait for Enter.

    Args:
        title: Header text for the screen.
        message: Body text explaining the current state of the page.
    """
    clear_screen()
    print_header(title)
    print(f"\n{message}\n")
    pause()
