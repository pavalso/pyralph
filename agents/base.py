"""Base Agent interface for Ralph orchestrator."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
import traceback


@dataclass
class AgentError:
    """Encapsulates full error context for agent failures."""
    exception_type: str
    message: str
    stack_trace: str
    timestamp: str
    agent_name: str
    task_id: str

    def format_log_entry(self) -> str:
        """Format the error as a detailed log entry."""
        return (
            f"[{self.timestamp}] AGENT ERROR\n"
            f"Agent: {self.agent_name}\n"
            f"Task ID: {self.task_id}\n"
            f"Exception Type: {self.exception_type}\n"
            f"Message: {self.message}\n"
            f"Stack Trace:\n{self.stack_trace}"
        )

    @classmethod
    def from_exception(
        cls, exc: Exception, agent_name: str, task_id: str
    ) -> "AgentError":
        """Create an AgentError from an exception with full context."""
        return cls(
            exception_type=type(exc).__name__,
            message=str(exc),
            stack_trace=traceback.format_exc(),
            timestamp=datetime.now().isoformat(),
            agent_name=agent_name,
            task_id=task_id,
        )


class BaseAgent(ABC):
    """Abstract base class for all Ralph agents."""

    @abstractmethod
    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        """
        Execute a prompt with the agent.

        Args:
            prompt: The prompt to send to the agent
            tag: A tag for logging/tracking purposes

        Returns:
            Tuple of (success: bool, output: str, error: Optional[AgentError])
            - success: True if the agent executed successfully, False otherwise
            - output: The agent's response or error message
            - error: Structured AgentError if execution failed, None on success
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
