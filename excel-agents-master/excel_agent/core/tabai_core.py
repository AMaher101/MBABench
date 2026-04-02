"""
TabAI core interaction logic.

Handles TabAI-specific implementation extending AIAgentCore.
"""

import logging

from .ai_agent_base import AIAgentCore

logger = logging.getLogger(__name__)


class TabAICore(AIAgentCore):
    """TabAI-specific implementation of AI agent automation."""

    def get_agent_type(self) -> str:
        """Return agent type for logging."""
        return "tabai"

    def get_addon_name(self) -> str:
        """Return addon name for installation."""
        return "TabAI"

    def get_open_button_text(self) -> str:
        """Return open button text."""
        return "Open TabAI"


# Legacy compatibility - preserve TabAIState for backward compatibility
from .ai_agent_base import AIAgentState as TabAIState  # noqa: F401, E402
