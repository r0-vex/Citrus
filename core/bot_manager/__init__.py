"""
core.bot_manager
================
Citrus Bot Manager subsystem: lifecycle controls, runtime status,
and discovery for Python and other continuous bot processes.
"""

from core.bot_manager.manager import BotManager, get_manager
from core.bot_manager.models import (
    BotDefinition,
    BotOperationResult,
    BotRuntimeState,
    BotStatus,
)

__all__ = [
    "BotDefinition",
    "BotManager",
    "BotOperationResult",
    "BotRuntimeState",
    "BotStatus",
    "get_manager",
]
