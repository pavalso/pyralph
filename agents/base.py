"""Base Agent interface for Ralph orchestrator."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple
import subprocess, traceback

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
    def __init__(self, timeout_seconds: int = 600):
        self.timeout_seconds = timeout_seconds
        self._logger = None
        self._config = None

    def set_logger(self, logger): self._logger = logger
    def set_config(self, config): self._config = config

    @abstractmethod
    def check_dependencies(self) -> bool: pass
    @abstractmethod
    def get_name(self) -> str: pass
    @abstractmethod
    def _build_command(self, prompt: str) -> List[str]: pass
    @abstractmethod
    def _prepare_input(self, prompt: str) -> Optional[str]: pass

    def _log(self, content: str, log_type: str, tag: str, color: str = "RESET"):
        if not self._logger: return
        self._logger.file_log(content, log_type, tag)
        if self._logger.verbose:
            self._logger.debug(f"=== {self.get_name().upper()} {log_type} [{tag}] ===", color)
            self._logger.debug(content, color)

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        self._log(prompt, "PROMPT", tag, "CYAN")
        try:
            result = subprocess.run(self._build_command(prompt), input=self._prepare_input(prompt),
                capture_output=True, text=True, encoding='utf-8', timeout=self.timeout_seconds)
            log_content = result.stdout + (f"\n\n--- [CLI STDERR] ---\n{result.stderr}" if result.stderr.strip() else "")
            if result.returncode != 0:
                self._log(log_content, "ERROR", tag, "RED")
                error = AgentError("CLIError", f"{self.get_name()} CLI exited with code {result.returncode}",
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}", datetime.now().isoformat(), self.get_name(), tag)
                return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}", error
            self._log(log_content, "RESPONSE", tag, "GREEN")
            return True, result.stdout, None
        except Exception as e:
            error = AgentError.from_exception(e, self.get_name(), tag)
            self._log(error.format_log_entry(), "SYSTEM_EXCEPTION", tag, "RED")
            return False, error.format_log_entry(), error
