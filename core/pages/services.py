"""
services.py (pages)
====================
Service manager page. Placeholder for now - no new features are
being added in this rewrite, only the architecture around it.
"""

from core import config
from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.ui import clear_screen, pause, print_header

logger = get_logger(__name__)


def show() -> None:
    """Display the service manager screen and wait for Enter."""
    emit(EventNames.PAGE_OPENED, page="services")
    logger.info("Services page opened")

    clear_screen()
    print_header("Service Manager", config.MENU_WIDTH)
    print("\nCurrently no services available.\n")
    pause()

    emit(EventNames.PAGE_CLOSED, page="services")
