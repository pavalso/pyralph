"""Interface to the Copilot CLI agent."""

import subprocess
import shutil
from typing import Tuple
from .base import BaseAgent

class GithubAgent(BaseAgent):
    """Interface to the Copilot CLI agent."""

    def __init__(self, timeout_seconds: int = 600):
        """
        Initialize the Copilot agent.

        Args:
            timeout_seconds: Maximum time to wait for Copilot to respond
        """
        self.timeout_seconds = timeout_seconds
        self._logger = None
        self._config = None

    def set_logger(self, logger):
        """Set the logger instance for this agent."""
        self._logger = logger

    def set_config(self, config):
        """Set the config instance for this agent."""
        self._config = config

    def get_name(self) -> str:
        """Get the display name of this agent."""
        return "Copilot"

    def check_dependencies(self) -> bool:
        """Check if Claude CLI is available."""
        return shutil.which("copilot") is not None

    def run(self, prompt: str, tag: str) -> Tuple[bool, str]:
        """
        Execute a prompt with Copilot CLI.

        Args:
            prompt: The prompt to send to Copilot
            tag: A tag for logging/tracking purposes

        Returns:
            Tuple of (success: bool, output: str)
        """
        if self._logger:
            self._logger.file_log(prompt, "PROMPT", tag)

            # Display prompt in verbose mode
            if self._logger.verbose:
                self._logger.debug(f"=== COPILOT PROMPT [{tag}] ===", "CYAN")
                self._logger.debug(prompt, "CYAN")
                self._logger.debug("=" * 40, "CYAN")
        
        prompt = prompt.replace("\n", " ")
        prompt = prompt.replace('"', '\\"')
        prompt = prompt.strip()
        prompt += "\n"

        cmd = [
            shutil.which("copilot"),
            "--allow-all-tools",
            "--add-dir", ".",
            "--no-ask-user",
            "-s",
            "-p", prompt
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', timeout=self.timeout_seconds
            )

            log_content = result.stdout
            if result.stderr.strip():
                log_content += f"\n\n--- [CLI STDERR] ---\n{result.stderr}"

            if result.returncode != 0:
                if self._logger:
                    self._logger.file_log(log_content, "ERROR", tag)
                    # Display error in verbose mode
                    if self._logger.verbose:
                        self._logger.debug(f"=== COPILOT ERROR [{tag}] ===", "RED")
                        self._logger.debug(f"STDOUT:\n{result.stdout}", "RED")
                        self._logger.debug(f"STDERR:\n{result.stderr}", "RED")
                        self._logger.debug("=" * 40, "RED")
                return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

            if self._logger:
                self._logger.file_log(log_content, "RESPONSE", tag)

                # Display response in verbose mode
                if self._logger.verbose:
                    self._logger.debug(f"=== COPILOT RESPONSE [{tag}] ===", "GREEN")
                    self._logger.debug(result.stdout, "GREEN")
                    self._logger.debug("=" * 40, "GREEN")

            return True, result.stdout

        except Exception as e:
            if self._logger:
                self._logger.file_log(str(e), "SYSTEM_EXCEPTION", tag)
                if self._logger.verbose:
                    self._logger.debug(f"=== COPILOT EXCEPTION [{tag}] ===", "RED")
                    self._logger.debug(str(e), "RED")
                    self._logger.debug("=" * 40, "RED")
            return False, str(e)

