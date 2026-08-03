"""
state.py
========
Mutable runtime state for the current Citrus process.

Distinct from config.py (static, file-derived settings) and
version.py (fixed version identity) - this module tracks values that
only exist once the app is actually running, such as when the
current session started and which page is currently open. Has zero
internal dependencies, so it can be imported from any layer.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AppState:
    """Runtime state for the current Citrus session.

    Attributes:
        session_id: Unique identifier for this run of the app.
        started_at: When the application process started.
        current_page: Name of the page currently being shown, if any.
        config_loaded: Whether configuration has been initialized.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=datetime.now)
    current_page: Optional[str] = None
    config_loaded: bool = False

    def set_page(self, page_name: str) -> None:
        """Record which page is currently active.

        Args:
            page_name: Human-readable name of the page being opened.
        """
        self.current_page = page_name

    def uptime_seconds(self) -> float:
        """Return how many seconds this session has been running.

        Returns:
            Elapsed seconds since `started_at`.
        """
        return (datetime.now() - self.started_at).total_seconds()


# A single shared instance - Citrus is a single-process, single-session
# terminal app, so a module-level singleton is the simplest honest
# representation of "the current run's state" without needing DI.
state = AppState()
