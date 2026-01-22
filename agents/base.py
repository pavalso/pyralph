"""Base Agent interface for Ralph orchestrator."""

from abc import ABC, abstractmethod
from typing import Tuple


class BaseAgent(ABC):
    """Abstract base class for all Ralph agents."""

    @abstractmethod
    def run(self, prompt: str, tag: str) -> Tuple[bool, str]:
        """
        Execute a prompt with the agent.

        Args:
            prompt: The prompt to send to the agent
            tag: A tag for logging/tracking purposes

        Returns:
            Tuple of (success: bool, output: str)
            - success: True if the agent executed successfully, False otherwise
            - output: The agent's response or error message
        """
        pass

    @abstractmethod
    def check_dependencies(self) -> bool:
        """
        Check if all required dependencies for this agent are available.

        Returns:
            True if all dependencies are satisfied, False otherwise
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """
        Get the display name of this agent.

        Returns:
            The agent's display name
        """
        pass
