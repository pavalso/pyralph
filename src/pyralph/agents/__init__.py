"""Agent implementations for Ralph orchestrator."""

from typing import Dict, List, Type

from .base import BaseAgent, AgentError
from .claude import ClaudeAgent
from .copilot import GithubAgent


class UnregisteredAgentError(KeyError):
    """Raised when attempting to create an agent that is not registered."""

    def __init__(self, name: str, available: List[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(
            f"Unknown agent: {name}. Available agents: {', '.join(available)}"
        )


class AgentFactory:
    """Factory for creating agent instances with explicit registration.

    This factory allows agent types to be registered dynamically without
    modifying factory code. New agents can be added by calling the
    register() classmethod.

    Example:
        >>> AgentFactory.register("custom", CustomAgent)
        >>> agent = AgentFactory.create("custom", timeout_seconds=300)
    """

    _agents: Dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register(cls, name: str, agent_class: Type[BaseAgent]) -> None:
        """Register an agent class with the factory.

        Args:
            name: The name to register the agent under (case-insensitive).
            agent_class: The agent class to register. Must be a subclass of BaseAgent.

        Raises:
            TypeError: If agent_class is not a subclass of BaseAgent.
        """
        if not isinstance(agent_class, type) or not issubclass(agent_class, BaseAgent):
            raise TypeError(
                f"agent_class must be a subclass of BaseAgent, got {agent_class}"
            )
        cls._agents[name.lower()] = agent_class

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseAgent:
        """Create an agent instance by name.

        Args:
            name: The registered name of the agent (case-insensitive).
            **kwargs: Arguments to pass to the agent constructor.

        Returns:
            An instance of the registered agent class.

        Raises:
            UnregisteredAgentError: If no agent is registered under the given name.
        """
        name_lower = name.lower()
        if name_lower not in cls._agents:
            raise UnregisteredAgentError(name_lower, list(cls._agents.keys()))
        return cls._agents[name_lower](**kwargs)

    @classmethod
    def list_registered(cls) -> List[str]:
        """Get a list of registered agent names.

        Returns:
            List of registered agent names.
        """
        return list(cls._agents.keys())

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if an agent is registered.

        Args:
            name: The name to check (case-insensitive).

        Returns:
            True if an agent is registered under that name, False otherwise.
        """
        return name.lower() in cls._agents


# Register built-in agents
AgentFactory.register("claude", ClaudeAgent)
AgentFactory.register("copilot", GithubAgent)

# Backward compatibility: AVAILABLE_AGENTS now references the factory's registry
AVAILABLE_AGENTS = AgentFactory._agents


def get_agent(agent_name: str, **kwargs) -> BaseAgent:
    """Get an agent instance by name.

    This function is provided for backward compatibility. New code should
    use AgentFactory.create() instead.

    Args:
        agent_name: The name of the agent (case-insensitive).
        **kwargs: Arguments to pass to the agent constructor.

    Returns:
        An instance of the requested agent.

    Raises:
        ValueError: If the agent name is not registered.
    """
    try:
        return AgentFactory.create(agent_name, **kwargs)
    except UnregisteredAgentError as e:
        # Maintain backward compatibility by raising ValueError
        raise ValueError(str(e)) from e


def list_agents() -> List[str]:
    """Get a list of available agent names.

    This function is provided for backward compatibility. New code should
    use AgentFactory.list_registered() instead.

    Returns:
        List of available agent names.
    """
    return AgentFactory.list_registered()


__all__ = [
    "BaseAgent",
    "AgentError",
    "ClaudeAgent",
    "AgentFactory",
    "UnregisteredAgentError",
    "get_agent",
    "list_agents",
    "AVAILABLE_AGENTS",
]
