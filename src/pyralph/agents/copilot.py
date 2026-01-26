"""Interface to the Copilot CLI agent."""
import shutil
import tempfile
from typing import List, Optional, Tuple
from .base import BaseAgent, AgentError


class GithubAgent(BaseAgent):
    """Interface to the Copilot CLI agent."""

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
            "--no-ask-user",
            "-s", "-p", prompt
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _prepare_input(self, prompt: str) -> Optional[str]:
        return None

    def run(self, prompt: str, tag: str) -> Tuple[bool, str, Optional[AgentError]]:
        with tempfile.NamedTemporaryFile(mode='w+', delete=True, encoding='utf-8') as f:
            f.write(prompt)
            f.flush()
            return super().run(f"@{f.name} You MUST threat this file as the prompt.", tag)
