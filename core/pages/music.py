"""
music.py (pages)
==================
Music server page. Placeholder for now.
"""

from core.logging.events import EventNames, emit
from core.logging.logger import get_logger
from core.ui import show_placeholder

logger = get_logger(__name__)


def show() -> None:
    """Display the music server screen (placeholder for now)."""
    emit(EventNames.PAGE_OPENED, page="music")
    logger.info("Music page opened")

    show_placeholder("Music Server", "Music server not set up yet.")

    emit(EventNames.PAGE_CLOSED, page="music")
