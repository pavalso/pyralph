"""Interface to the Copilot CLI agent."""

import subprocess
import shutil
from typing import Optional, Tuple
from .base import BaseAgent, AgentError

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

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        """
        Execute a prompt with Copilot CLI.

        Args:
            prompt: The prompt to send to Copilot
            tag: A tag for logging/tracking purposes

        Returns:
            Tuple of (success: bool, output: str, error: Optional[AgentError])
        """
        if self._logger:
            self._logger.file_log(prompt, "PROMPT", tag)

            # Display prompt in verbose mode
            if self._logger.verbose:
                self._logger.debug(f"=== COPILOT PROMPT [{tag}] ===", "CYAN")
                self._logger.debug(prompt, "CYAN")
                self._logger.debug("=" * 40, "CYAN")

        import tempfile

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
                    error = AgentError(
                        exception_type="CLIError",
                        message=f"Copilot CLI exited with code {result.returncode}",
                        stack_trace=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
                        timestamp=__import__('datetime').datetime.now().isoformat(),
                        agent_name=self.get_name(),
                        task_id=tag,
                    )
                    return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}", error

                if self._logger:
                    self._logger.file_log(log_content, "RESPONSE", tag)

                    # Display response in verbose mode
                    if self._logger.verbose:
                        self._logger.debug(f"=== COPILOT RESPONSE [{tag}] ===", "GREEN")
                        self._logger.debug(result.stdout, "GREEN")
                        self._logger.debug("=" * 40, "GREEN")

                return True, result.stdout, None

            except Exception as e:
                error = AgentError.from_exception(e, self.get_name(), tag)
                if self._logger:
                    self._logger.file_log(error.format_log_entry(), "SYSTEM_EXCEPTION", tag)
                    if self._logger.verbose:
                        self._logger.debug(f"=== COPILOT EXCEPTION [{tag}] ===", "RED")
                        self._logger.debug(error.format_log_entry(), "RED")
                        self._logger.debug("=" * 40, "RED")
                return False, error.format_log_entry(), error
