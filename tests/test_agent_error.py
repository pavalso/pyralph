from datetime import datetime as dt
from unittest.mock import MagicMock, patch

from pyralph.agents.base import AgentError
from pyralph.agents.claude import ClaudeAgent
from pyralph.agents.copilot import GithubAgent


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
