"""Agent implementations for Ralph orchestrator."""
from .base import BaseAgent, AgentError
from .claude import ClaudeAgent
from .copilot import GithubAgent

AVAILABLE_AGENTS = {"claude": ClaudeAgent, "copilot": GithubAgent}

def get_agent(agent_name: str, **kwargs) -> BaseAgent:
    agent_name = agent_name.lower()
    if agent_name not in AVAILABLE_AGENTS:
        raise ValueError(f"Unknown agent: {agent_name}. Available: {', '.join(AVAILABLE_AGENTS.keys())}")
    return AVAILABLE_AGENTS[agent_name](**kwargs)

def list_agents(): return list(AVAILABLE_AGENTS.keys())

__all__ = ["BaseAgent", "AgentError", "ClaudeAgent", "get_agent", "list_agents", "AVAILABLE_AGENTS"]
