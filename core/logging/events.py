"""
events.py
=========
Citrus's internal event bus.

Modules emit structured Event objects instead of printing status
messages. Every event is written to events.log and, if any callback
has subscribed to that event name, delivered to it immediately
in-process. No subscribers exist yet in v1.0 - this exists so future
modules (a web dashboard, automation engine, REST API, etc.) can
react to what's happening without any producer module needing to
change.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Final, List

from core import config
from core.logging.logger import build_file_handler

EventCallback = Callable[["Event"], None]


class EventNames:
    """Canonical event name constants, to avoid typos across modules."""

    APP_STARTED: Final[str] = "APP_STARTED"
    APP_STOPPED: Final[str] = "APP_STOPPED"
    PAGE_OPENED: Final[str] = "PAGE_OPENED"
    PAGE_CLOSED: Final[str] = "PAGE_CLOSED"
    SSH_CONNECTED: Final[str] = "SSH_CONNECTED"
    SSH_DISCONNECTED: Final[str] = "SSH_DISCONNECTED"
    SERVICE_DISCOVERED: Final[str] = "SERVICE_DISCOVERED"
    SERVICE_START_REQUESTED: Final[str] = "SERVICE_START_REQUESTED"
    SERVICE_STARTED: Final[str] = "SERVICE_STARTED"
    SERVICE_START_FAILED: Final[str] = "SERVICE_START_FAILED"
    SERVICE_STOP_REQUESTED: Final[str] = "SERVICE_STOP_REQUESTED"
    SERVICE_STOPPED: Final[str] = "SERVICE_STOPPED"
    SERVICE_STOP_FAILED: Final[str] = "SERVICE_STOP_FAILED"
    SERVICE_RESTART_REQUESTED: Final[str] = "SERVICE_RESTART_REQUESTED"
    SERVICE_RESTARTED: Final[str] = "SERVICE_RESTARTED"
    SERVICE_STATUS_CHECKED: Final[str] = "SERVICE_STATUS_CHECKED"
    GIT_PULL: Final[str] = "GIT_PULL"
    GIT_PUSH: Final[str] = "GIT_PUSH"
    BOT_STARTED: Final[str] = "BOT_STARTED"
    BOT_STOPPED: Final[str] = "BOT_STOPPED"


@dataclass(frozen=True)
class Event:
    """A single structured event emitted somewhere in Citrus.

    Attributes:
        name: Short, upper-snake-case event identifier, e.g.
            "APP_STARTED" or "SERVICE_STARTED". See EventNames.
        timestamp: When the event was created.
        data: Arbitrary key/value context for the event.
    """

    name: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, object] = field(default_factory=dict)

    def format(self) -> str:
        """Render this event as a single human-readable log line.

        Returns:
            The event name, followed by "(key=value, ...)" context
            if any data was attached.
        """
        if self.data:
            context = ", ".join(f"{key}={value}" for key, value in self.data.items())
            return f"{self.name} ({context})"
        return self.name


class EventBus:
    """In-process publish/subscribe bus, plus events.log persistence."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventCallback]] = {}
        self._logger: logging.Logger = self._build_events_logger()

    @staticmethod
    def _build_events_logger() -> logging.Logger:
        """Build a dedicated logger that writes only to events.log.

        Returns:
            A logging.Logger configured with a single file handler
            pointed at events.log, with propagation disabled so
            events never leak into system.log/debug.log.
        """
        config.ensure_log_directory()
        logger = logging.getLogger("citrus.events")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(build_file_handler(config.LOG_FILES["events"], logging.INFO))
        return logger

    def subscribe(self, event_name: str, callback: EventCallback) -> None:
        """Register a callback to run whenever `event_name` is emitted.

        Args:
            event_name: The event name to listen for (see EventNames).
            callback: A function accepting a single Event argument.
        """
        self._subscribers.setdefault(event_name, []).append(callback)

    def emit(self, name: str, **data: object) -> Event:
        """Create, log, and dispatch an event.

        Args:
            name: Upper-snake-case event identifier (see EventNames).
            **data: Arbitrary context fields attached to the event.

        Returns:
            The Event instance that was emitted.
        """
        event = Event(name=name, data=data)
        self._logger.info(event.format())

        for callback in self._subscribers.get(name, []):
            try:
                callback(event)
            except Exception:  # A subscriber must never break emit() for others.
                self._logger.exception("Subscriber for %s raised an exception", name)

        return event


_bus = EventBus()


def subscribe(event_name: str, callback: EventCallback) -> None:
    """Module-level convenience wrapper around the shared EventBus.

    Args:
        event_name: The event name to listen for (see EventNames).
        callback: A function accepting a single Event argument.
    """
    _bus.subscribe(event_name, callback)


def emit(name: str, **data: object) -> Event:
    """Module-level convenience wrapper around the shared EventBus.

    Args:
        name: Upper-snake-case event identifier (see EventNames).
        **data: Arbitrary context fields attached to the event.

    Returns:
        The Event instance that was emitted.
    """
    return _bus.emit(name, **data)
