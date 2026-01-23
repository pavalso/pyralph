"""Base Agent interface for Ralph orchestrator."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Tuple
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

    def __init__(self, timeout_seconds: int = 600) -> None:
        """
        Initialize the agent.

        Args:
            timeout_seconds: Maximum time to wait for the agent to respond
        """
        self.timeout_seconds = timeout_seconds
        self._logger: Any = None
        self._config: Any = None

    def set_logger(self, logger: Any) -> None:
        """Set the logger instance for this agent."""
        self._logger = logger

    def set_config(self, config: Any) -> None:
        """Set the config instance for this agent."""
        self._config = config

    def _log_prompt(self, prompt: str, tag: str) -> None:
        """Log the prompt being sent to the agent."""
        if self._logger:
            self._logger.file_log(prompt, "PROMPT", tag)
            if self._logger.verbose:
                self._logger.debug(f"=== {self.get_name().upper()} PROMPT [{tag}] ===", "CYAN")
                self._logger.debug(prompt, "CYAN")
                self._logger.debug("=" * 40, "CYAN")

    def _log_response(self, log_content: str, stdout: str, tag: str) -> None:
        """Log a successful response from the agent."""
        if self._logger:
            self._logger.file_log(log_content, "RESPONSE", tag)
            if self._logger.verbose:
                self._logger.debug(f"=== {self.get_name().upper()} RESPONSE [{tag}] ===", "GREEN")
                self._logger.debug(stdout, "GREEN")
                self._logger.debug("=" * 40, "GREEN")

    def _log_cli_error(self, log_content: str, stdout: str, stderr: str, tag: str) -> None:
        """Log a CLI error from the agent."""
        if self._logger:
            self._logger.file_log(log_content, "ERROR", tag)
            if self._logger.verbose:
                self._logger.debug(f"=== {self.get_name().upper()} ERROR [{tag}] ===", "RED")
                self._logger.debug(f"STDOUT:\n{stdout}", "RED")
                self._logger.debug(f"STDERR:\n{stderr}", "RED")
                self._logger.debug("=" * 40, "RED")

    def _log_exception(self, error: AgentError, tag: str) -> None:
        """Log an exception that occurred during agent execution."""
        if self._logger:
            self._logger.file_log(error.format_log_entry(), "SYSTEM_EXCEPTION", tag)
            if self._logger.verbose:
                self._logger.debug(f"=== {self.get_name().upper()} EXCEPTION [{tag}] ===", "RED")
                self._logger.debug(error.format_log_entry(), "RED")
                self._logger.debug("=" * 40, "RED")

    def _build_log_content(self, stdout: str, stderr: str) -> str:
        """Build log content from stdout and stderr."""
        log_content = stdout
        if stderr.strip():
            log_content += f"\n\n--- [CLI STDERR] ---\n{stderr}"
        return log_content

    def _create_cli_error(self, returncode: int, stdout: str, stderr: str, tag: str) -> AgentError:
        """Create an AgentError for CLI failures."""
        return AgentError(
            exception_type="CLIError",
            message=f"{self.get_name()} CLI exited with code {returncode}",
            stack_trace=f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            timestamp=datetime.now().isoformat(),
            agent_name=self.get_name(),
            task_id=tag,
        )

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
