import os
import shutil
import subprocess

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
