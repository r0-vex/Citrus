"""
bots.py (pages)
=================
Bots page. Placeholder for now - future home for managing Python and
Telegram bots.
"""

from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.ui import show_placeholder

logger = get_logger(__name__)


def show() -> None:
    """Display the bots screen (placeholder for now)."""
    emit(EventNames.PAGE_OPENED, page="bots")
    logger.info("Bots page opened")

    show_placeholder("Bots", "No bots configured yet.")

    emit(EventNames.PAGE_CLOSED, page="bots")
