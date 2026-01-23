"""Base Agent interface for Ralph orchestrator."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Tuple
import subprocess
import traceback


@dataclass
class AgentError:
    exception_type: str
    message: str
    stack_trace: str
    timestamp: str
    agent_name: str
    task_id: str

    def format_log_entry(self) -> str:
        return f"[{self.timestamp}] AGENT ERROR\nAgent: {self.agent_name}\nTask ID: {self.task_id}\nException Type: {self.exception_type}\nMessage: {self.message}\nStack Trace:\n{self.stack_trace}"

    @classmethod
    def from_exception(cls, exc: Exception, agent_name: str, task_id: str) -> "AgentError":
        return cls(type(exc).__name__, str(exc), traceback.format_exc(), datetime.now().isoformat(), agent_name, task_id)


class BaseAgent(ABC):
    def __init__(self, timeout_seconds: int = 600, model: Optional[str] = None,
                 temperature: Optional[float] = None, max_tokens: Optional[int] = None,
                 seed: Optional[int] = None) -> None:
        """
        Initialize the agent.

        Args:
            timeout_seconds: Maximum time to wait for the agent to respond
            model: Model identifier to use for LLM requests
            temperature: Sampling temperature for response generation (0.0-1.0)
            max_tokens: Maximum number of tokens in the response
            seed: Random seed for reproducible outputs
        """
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
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

    def _log(self, content: str, log_type: str, tag: str, color: str = "RESET"):
        if not self._logger:
            return
        self._logger.file_log(content, log_type, tag)
        if self._logger.verbose:
            self._logger.debug(f"=== {self.get_name().upper()} {log_type} [{tag}] ===", color)
            self._logger.debug(content, color)

    @abstractmethod
    def check_dependencies(self) -> bool:
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass

    @abstractmethod
    def _build_command(self, prompt: str) -> List[str]:
        pass

    @abstractmethod
    def _prepare_input(self, prompt: str) -> Optional[str]:
        pass

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        self._log(prompt, "PROMPT", tag, "CYAN")
        try:
            result = subprocess.run(
                self._build_command(prompt),
                input=self._prepare_input(prompt),
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=self.timeout_seconds
            )
            log_content = result.stdout + (f"\n\n--- [CLI STDERR] ---\n{result.stderr}" if result.stderr.strip() else "")
            if result.returncode != 0:
                self._log(log_content, "ERROR", tag, "RED")
                error = AgentError(
                    "CLIError",
                    f"{self.get_name()} CLI exited with code {result.returncode}",
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
                    datetime.now().isoformat(),
                    self.get_name(),
                    tag
                )
                return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}", error
            self._log(log_content, "RESPONSE", tag, "GREEN")
            return True, result.stdout, None
        except Exception as e:
            error = AgentError.from_exception(e, self.get_name(), tag)
            self._log(error.format_log_entry(), "SYSTEM_EXCEPTION", tag, "RED")
            return False, error.format_log_entry(), error
