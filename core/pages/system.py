"""
system.py (pages)
===================
System information page (menu placeholder). Not to be confused with
core/system_info/, which is the data-collection package this page
will eventually consume for a richer view.
"""

from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.ui import show_placeholder

logger = get_logger(__name__)


def show() -> None:
    """Display the system information screen (placeholder for now)."""
    emit(EventNames.PAGE_OPENED, page="system")
    logger.info("System page opened")

    show_placeholder("System Information")

    emit(EventNames.PAGE_CLOSED, page="system")
