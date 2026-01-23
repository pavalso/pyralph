"""Claude CLI Agent implementation."""
import shutil
from typing import List, Optional
from .base import BaseAgent


class ClaudeAgent(BaseAgent):
    """Interface to the Claude CLI agent."""

    def get_name(self) -> str:
        """Get the display name of this agent."""
        return "Claude"

    def check_dependencies(self) -> bool:
        """Check if Claude CLI is available."""
        return shutil.which("claude") is not None

    def _build_command(self, prompt: str) -> List[str]:
        return [shutil.which("claude"), "-p", "--dangerously-skip-permissions"]

    def _prepare_input(self, prompt: str) -> Optional[str]:
        return prompt
