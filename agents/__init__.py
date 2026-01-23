"""Agent implementations for Ralph orchestrator."""

from typing import List

from .base import BaseAgent, AgentError
from .claude import ClaudeAgent
from .copilot import GithubAgent

AVAILABLE_AGENTS = {"claude": ClaudeAgent, "copilot": GithubAgent}

def get_agent(agent_name: str, **kwargs) -> BaseAgent:
    agent_name = agent_name.lower()
    if agent_name not in AVAILABLE_AGENTS:
        available = ", ".join(AVAILABLE_AGENTS.keys())
        raise ValueError(f"Unknown agent: {agent_name}. Available agents: {available}")

    agent_class = AVAILABLE_AGENTS[agent_name]
    return agent_class(**kwargs)


def list_agents() -> List[str]:
    """
    Get a list of available agent names.

    Returns:
        List of available agent names
    """
    return list(AVAILABLE_AGENTS.keys())

__all__ = ["BaseAgent", "AgentError", "ClaudeAgent", "get_agent", "list_agents", "AVAILABLE_AGENTS"]
