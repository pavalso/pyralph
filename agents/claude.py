"""Claude CLI Agent implementation."""

import subprocess
import shutil
from typing import Optional, Tuple
from .base import BaseAgent, AgentError


class ClaudeAgent(BaseAgent):
    """Interface to the Claude CLI agent."""

    def __init__(self, timeout_seconds: int = 600) -> None:
        """
        Initialize the Claude agent.

        Args:
            timeout_seconds: Maximum time to wait for Claude to respond
        """
        super().__init__(timeout_seconds)

    def get_name(self) -> str:
        """Get the display name of this agent."""
        return "Claude"

    def check_dependencies(self) -> bool:
        """Check if Claude CLI is available."""
        return shutil.which("claude") is not None

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        """
        Execute a prompt with Claude CLI.

        Args:
            prompt: The prompt to send to Claude
            tag: A tag for logging/tracking purposes

        Returns:
            Tuple of (success: bool, output: str, error: Optional[AgentError])
        """
        self._log_prompt(prompt, tag)

        cmd = [
            shutil.which("claude"),
            "-p",
            "--dangerously-skip-permissions"
        ]

        try:
            result = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                encoding='utf-8', timeout=self.timeout_seconds
            )

            log_content = self._build_log_content(result.stdout, result.stderr)

            if result.returncode != 0:
                self._log_cli_error(log_content, result.stdout, result.stderr, tag)
                error = self._create_cli_error(result.returncode, result.stdout, result.stderr, tag)
                return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}", error

            self._log_response(log_content, result.stdout, tag)
            return True, result.stdout, None

        except Exception as e:
            error = AgentError.from_exception(e, self.get_name(), tag)
            self._log_exception(error, tag)
            return False, error.format_log_entry(), error
