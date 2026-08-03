"""
git.py (pages)
===============
Git utilities page. Placeholder for now - future home for repo
status checks, commit/push helpers, and backup-to-git workflows.
Uses the dedicated git logger so git activity is reviewable in its
own logs/git.log file.
"""

from core.logging.events import EventNames, emit
from core.logging.logger import get_git_logger
from core.ui import show_placeholder

logger = get_git_logger()


def show() -> None:
    """Display the Git utilities screen (placeholder for now)."""
    emit(EventNames.PAGE_OPENED, page="git")
    logger.info("Git page opened")

    show_placeholder("Git Utilities")

    emit(EventNames.PAGE_CLOSED, page="git")
