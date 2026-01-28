from datetime import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from pyralph.agents import AgentFactory, UnregisteredAgentError
from pyralph.agents.base import AgentError, BaseAgent
from pyralph.agents.claude import ClaudeAgent
from pyralph.agents.copilot import GithubAgent


class TestAgentFactory:
    """Tests for the AgentFactory class."""

    def test_agents_dict_exists(self):
        """Given AgentFactory class, when reviewed, then it has _agents dict."""
        assert hasattr(AgentFactory, "_agents")
        assert isinstance(AgentFactory._agents, dict)

    def test_register_classmethod_exists(self):
        """Given AgentFactory class, when reviewed, then it has register() classmethod."""
        assert hasattr(AgentFactory, "register")
        assert callable(AgentFactory.register)

    def test_create_classmethod_exists(self):
        """Given AgentFactory class, when reviewed, then it has create() classmethod."""
        assert hasattr(AgentFactory, "create")
        assert callable(AgentFactory.create)

    def test_register_stores_agent(self):
        """Given AgentFactory.register() is called with name and class, when executed, then agent is stored in _agents."""

        class TestAgent(BaseAgent):
            def check_dependencies(self) -> bool:
                return True

            def get_name(self) -> str:
                return "test"

            def _build_command(self, prompt: str):
                return ["echo", prompt]

            def _prepare_input(self, prompt: str):
                return None

        AgentFactory.register("test_agent", TestAgent)
        assert "test_agent" in AgentFactory._agents
        assert AgentFactory._agents["test_agent"] is TestAgent
        # Cleanup
        del AgentFactory._agents["test_agent"]

    def test_register_case_insensitive(self):
        """Given register() is called with mixed-case name, when stored, then name is lowercase."""

        class TestAgent2(BaseAgent):
            def check_dependencies(self) -> bool:
                return True

            def get_name(self) -> str:
                return "test2"

            def _build_command(self, prompt: str):
                return ["echo", prompt]

            def _prepare_input(self, prompt: str):
                return None

        AgentFactory.register("TestAgent2", TestAgent2)
        assert "testagent2" in AgentFactory._agents
        assert "TestAgent2" not in AgentFactory._agents
        # Cleanup
        del AgentFactory._agents["testagent2"]

    def test_register_rejects_non_baseagent(self):
        """Given register() is called with non-BaseAgent class, when executed, then TypeError is raised."""

        class NotAnAgent:
            pass

        with pytest.raises(TypeError, match="must be a subclass of BaseAgent"):
            AgentFactory.register("invalid", NotAnAgent)

    def test_create_returns_correct_instance(self):
        """Given AgentFactory.create() is called with registered name, when executed, then correct agent instance is returned."""
        agent = AgentFactory.create("claude", timeout_seconds=100)
        assert isinstance(agent, ClaudeAgent)
        assert agent.timeout_seconds == 100

    def test_create_case_insensitive(self):
        """Given create() is called with mixed-case name, when executed, then correct agent is returned."""
        agent = AgentFactory.create("CLAUDE")
        assert isinstance(agent, ClaudeAgent)

        agent2 = AgentFactory.create("Copilot")
        assert isinstance(agent2, GithubAgent)

    def test_create_unregistered_raises_error(self):
        """Given AgentFactory.create() is called with unregistered name, when executed, then UnregisteredAgentError is raised."""
        with pytest.raises(UnregisteredAgentError) as exc_info:
            AgentFactory.create("nonexistent_agent")
        assert exc_info.value.name == "nonexistent_agent"
        assert "claude" in exc_info.value.available
        assert "copilot" in exc_info.value.available

    def test_unregistered_agent_error_is_key_error(self):
        """Given UnregisteredAgentError is raised, when checked, then it is a KeyError subclass."""
        with pytest.raises(KeyError):
            AgentFactory.create("unknown")

    def test_list_registered(self):
        """Given registered agents, when list_registered() is called, then all registered names are returned."""
        registered = AgentFactory.list_registered()
        assert "claude" in registered
        assert "copilot" in registered

    def test_is_registered(self):
        """Given an agent name, when is_registered() is called, then correct boolean is returned."""
        assert AgentFactory.is_registered("claude") is True
        assert AgentFactory.is_registered("CLAUDE") is True  # case-insensitive
        assert AgentFactory.is_registered("nonexistent") is False

    def test_new_agent_no_factory_code_change(self):
        """Given a new agent type is added, when registering, then no factory code changes are required."""

        class CustomAgent(BaseAgent):
            def check_dependencies(self) -> bool:
                return True

            def get_name(self) -> str:
                return "custom"

            def _build_command(self, prompt: str):
                return ["echo", prompt]

            def _prepare_input(self, prompt: str):
                return None

        # Register new agent without modifying factory code
        AgentFactory.register("custom", CustomAgent)

        # Verify it works
        agent = AgentFactory.create("custom")
        assert isinstance(agent, CustomAgent)
        assert AgentFactory.is_registered("custom")
        assert "custom" in AgentFactory.list_registered()

        # Cleanup
        del AgentFactory._agents["custom"]


class TestAgentError:
    def test_from_exception(self):
        try:
            raise ValueError("test error")
        except ValueError as e:
            error = AgentError.from_exception(e, "TestAgent", "T-001")
        assert error.exception_type == "ValueError"
        assert error.message == "test error"
        assert "Traceback" in error.stack_trace
        dt.fromisoformat(error.timestamp)

    def test_format_log_entry(self):
        try:
            raise TypeError("type error")
        except TypeError as e:
            error = AgentError.from_exception(e, "Copilot", "T-007")
        log = error.format_log_entry()
        for exp in ["AGENT ERROR", "Copilot", "T-007", "TypeError"]:
            assert exp in log

    def test_agent_error_handling(self):
        for AgentClass in [ClaudeAgent, GithubAgent]:
            agent = AgentClass(timeout_seconds=5)
            with patch('subprocess.run', return_value=MagicMock(returncode=1, stdout="", stderr="")):
                with patch('shutil.which', return_value='/usr/bin/agent'):
                    success, output, error = agent.run("test", "TAG")
            assert not success
            assert isinstance(error, AgentError)
