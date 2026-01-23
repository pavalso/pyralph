"""Interface to the Copilot CLI agent."""

import subprocess
import shutil
import tempfile
from typing import Optional, Tuple
from .base import BaseAgent, AgentError


class GithubAgent(BaseAgent):
    """Interface to the Copilot CLI agent."""

    def __init__(self, timeout_seconds: int = 600) -> None:
        """
        Initialize the Copilot agent.

        Args:
            timeout_seconds: Maximum time to wait for Copilot to respond
        """
        super().__init__(timeout_seconds)

    def get_name(self) -> str:
        """Get the display name of this agent."""
        return "Copilot"

    def check_dependencies(self) -> bool:
        """Check if Copilot CLI is available."""
        return shutil.which("copilot") is not None

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        """
        Execute a prompt with Copilot CLI.

        Args:
            prompt: The prompt to send to Copilot
            tag: A tag for logging/tracking purposes

        Returns:
            Tuple of (success: bool, output: str, error: Optional[AgentError])
        """
        self._log_prompt(prompt, tag)

        with tempfile.NamedTemporaryFile(mode='w+', delete=True, encoding='utf-8') as temp_file:
            temp_file.write(prompt)
            temp_file.flush()
            temp_file_path = temp_file.name

            cmd = [
                shutil.which("copilot"),
                "--allow-all-paths",
                "--allow-all-tools",
                "--add-dir", ".",
                "--no-ask-user",
                "-s",
                "-p", "@{} You MUST threat this file as the prompt.".format(temp_file_path)
            ]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
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
