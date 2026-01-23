"""Interface to the Copilot CLI agent."""
import shutil
import tempfile
from typing import List, Optional, Tuple
from .base import BaseAgent, AgentError


class GithubAgent(BaseAgent):
    """Interface to the Copilot CLI agent."""

    def __init__(self, timeout_seconds: int = 600, model: Optional[str] = None,
                 temperature: Optional[float] = None, max_tokens: Optional[int] = None,
                 seed: Optional[int] = None) -> None:
        """
        Initialize the Copilot agent.

        Args:
            timeout_seconds: Maximum time to wait for Copilot to respond
            model: Model identifier to use for LLM requests
            temperature: Sampling temperature for response generation (0.0-1.0)
            max_tokens: Maximum number of tokens in the response
            seed: Random seed for reproducible outputs
        """
        super().__init__(timeout_seconds, model, temperature, max_tokens, seed)
        self._temp_file_path: Optional[str] = None

    def get_name(self) -> str:
        """Get the display name of this agent."""
        return "Copilot"

    def check_dependencies(self) -> bool:
        """Check if Copilot CLI is available."""
        return shutil.which("copilot") is not None

    def _build_command(self, prompt: str) -> List[str]:
        cmd = [
            shutil.which("copilot"),
            "--allow-all-paths",
            "--allow-all-tools",
            "--add-dir", ".",
            "--no-ask-user",
            "-s",
            "-p", f"@{self._temp_file_path} You MUST threat this file as the prompt."
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        # Note: Copilot CLI does not support --temperature, --max-tokens, or --seed flags directly
        # These are stored for potential future use or custom implementations
        return cmd

    def _prepare_input(self, prompt: str) -> Optional[str]:
        return None

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        with tempfile.NamedTemporaryFile(mode='w+', delete=True, encoding='utf-8') as f:
            f.write(prompt)
            f.flush()
            self._temp_file_path = f.name
            return super().run(prompt, tag)
