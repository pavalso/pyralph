import unittest
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.base import AgentError


class TestAlwaysPass(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)


class TestAgentError(unittest.TestCase):
    """Tests for AgentError dataclass and error logging functionality."""

    def test_agent_error_from_exception_captures_exception_type(self):
        """Verify exception type is captured correctly."""
        try:
            raise ValueError("test error message")
        except ValueError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-001")

        self.assertEqual(error.exception_type, "ValueError")

    def test_agent_error_from_exception_captures_message(self):
        """Verify exception message is captured correctly."""
        try:
            raise RuntimeError("specific error details")
        except RuntimeError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-002")

        self.assertEqual(error.message, "specific error details")

    def test_agent_error_from_exception_captures_stack_trace(self):
        """Verify stack trace is captured and non-empty."""
        try:
            raise KeyError("missing key")
        except KeyError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-003")

        self.assertIn("Traceback", error.stack_trace)
        self.assertIn("KeyError", error.stack_trace)

    def test_agent_error_from_exception_captures_timestamp(self):
        """Verify timestamp is captured in ISO format."""
        try:
            raise Exception("test")
        except Exception as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-004")

        # Verify timestamp can be parsed as ISO format
        parsed = datetime.fromisoformat(error.timestamp)
        self.assertIsInstance(parsed, datetime)

    def test_agent_error_from_exception_captures_agent_name(self):
        """Verify agent name is captured correctly."""
        try:
            raise Exception("test")
        except Exception as e:
            error = AgentError.from_exception(e, "Claude", "TASK-005")

        self.assertEqual(error.agent_name, "Claude")

    def test_agent_error_from_exception_captures_task_id(self):
        """Verify task ID is captured correctly."""
        try:
            raise Exception("test")
        except Exception as e:
            error = AgentError.from_exception(e, "TestAgent", "WORKER-TASK-006")

        self.assertEqual(error.task_id, "WORKER-TASK-006")

    def test_format_log_entry_contains_all_fields(self):
        """Verify formatted log entry contains all required context."""
        try:
            raise TypeError("type mismatch error")
        except TypeError as e:
            error = AgentError.from_exception(e, "Copilot", "TASK-007")

        log_entry = error.format_log_entry()

        self.assertIn("AGENT ERROR", log_entry)
        self.assertIn("Agent: Copilot", log_entry)
        self.assertIn("Task ID: TASK-007", log_entry)
        self.assertIn("Exception Type: TypeError", log_entry)
        self.assertIn("Message: type mismatch error", log_entry)
        self.assertIn("Stack Trace:", log_entry)
        self.assertIn("Traceback", log_entry)


class TestAgentStructuredErrorReturn(unittest.TestCase):
    """Tests for agent implementations returning structured error information."""

    def test_claude_agent_returns_agent_error_on_cli_failure(self):
        """Verify ClaudeAgent returns AgentError when CLI fails."""
        from agents.claude import ClaudeAgent

        agent = ClaudeAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="some output",
                stderr="some error"
            )
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test prompt", "TEST-TAG")

        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "CLIError")
        self.assertIn("exited with code 1", error.message)
        self.assertEqual(error.agent_name, "Claude")
        self.assertEqual(error.task_id, "TEST-TAG")

    def test_claude_agent_returns_none_error_on_success(self):
        """Verify ClaudeAgent returns None for error on success."""
        from agents.claude import ClaudeAgent

        agent = ClaudeAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="success output",
                stderr=""
            )
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test prompt", "TEST-TAG")

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(output, "success output")

    def test_claude_agent_returns_agent_error_on_exception(self):
        """Verify ClaudeAgent returns AgentError when exception occurs."""
        from agents.claude import ClaudeAgent

        agent = ClaudeAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = TimeoutError("Process timed out")
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test prompt", "TEST-TAG")

        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "TimeoutError")
        self.assertEqual(error.agent_name, "Claude")

    def test_copilot_agent_returns_agent_error_on_cli_failure(self):
        """Verify GithubAgent returns AgentError when CLI fails."""
        from agents.copilot import GithubAgent

        agent = GithubAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="copilot output",
                stderr="copilot error"
            )
            with patch('shutil.which', return_value='/usr/bin/copilot'):
                success, output, error = agent.run("test prompt", "COPILOT-TAG")

        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "CLIError")
        self.assertIn("exited with code 1", error.message)
        self.assertEqual(error.agent_name, "Copilot")
        self.assertEqual(error.task_id, "COPILOT-TAG")

    def test_copilot_agent_returns_none_error_on_success(self):
        """Verify GithubAgent returns None for error on success."""
        from agents.copilot import GithubAgent

        agent = GithubAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="copilot success",
                stderr=""
            )
            with patch('shutil.which', return_value='/usr/bin/copilot'):
                success, output, error = agent.run("test prompt", "COPILOT-TAG")

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(output, "copilot success")


class TestProgressRetryContext(unittest.TestCase):
    """Tests for structured failure information in progress.txt retry context."""

    def setUp(self):
        """Create a temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.progress_file = Path(self.test_dir) / "progress.txt"

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_record_failure_with_agent_error_includes_structured_context(self):
        """Verify _record_failure writes structured error context when AgentError provided."""
        from ralph import RalphOrchestrator, CONF, Logger

        # Suppress logging output
        Logger.set_verbose(False)

        # Create a mock agent
        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "TestAgent"
        mock_agent.check_dependencies.return_value = True

        # Temporarily redirect CONF paths
        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    error = AgentError(
                        exception_type="VerificationError",
                        message="Test command failed with exit code 1",
                        stack_trace="STDOUT:\ntest output\nSTDERR:\nerror output",
                        timestamp="2024-01-01T12:00:00",
                        agent_name="TestAgent",
                        task_id="TASK-001",
                    )

                    orchestrator._record_failure(0, "Verification Failed", "agent output", agent_error=error)

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("Structured Error Context", content)
                    self.assertIn("VerificationError", content)
                    self.assertIn("Test command failed with exit code 1", content)
                    self.assertIn("TestAgent", content)
                    self.assertIn("TASK-001", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file

    def test_record_failure_without_agent_error_uses_simple_format(self):
        """Verify _record_failure uses simple format when no AgentError provided."""
        from ralph import RalphOrchestrator, CONF, Logger

        Logger.set_verbose(False)

        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "TestAgent"
        mock_agent.check_dependencies.return_value = True

        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    orchestrator._record_failure(0, "CLI Crash", "simple error detail")

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("Attempt 1 Failed: CLI Crash", content)
                    self.assertIn("simple error detail", content)
                    self.assertNotIn("Structured Error Context", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file

    def test_structured_failure_context_includes_agent_output_section(self):
        """Verify structured failure includes the agent output section."""
        from ralph import RalphOrchestrator, CONF, Logger

        Logger.set_verbose(False)

        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "TestAgent"
        mock_agent.check_dependencies.return_value = True

        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    error = AgentError(
                        exception_type="AgentReportedFailure",
                        message="Agent did not report STATUS: SUCCESS",
                        stack_trace="Agent output trace",
                        timestamp="2024-01-01T12:00:00",
                        agent_name="Claude",
                        task_id="TASK-002",
                    )

                    orchestrator._record_failure(1, "Agent Reported Failure", "last 1000 chars of output", agent_error=error)

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("Agent Output (last 1000 chars)", content)
                    self.assertIn("last 1000 chars of output", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file


class TestOrchestratorUsesAgentError(unittest.TestCase):
    """Tests for orchestrator using structured error from agent return value."""

    def setUp(self):
        """Create a temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.progress_file = Path(self.test_dir) / "progress.txt"

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_orchestrator_uses_agent_returned_error(self):
        """Verify orchestrator uses structured error returned by agent."""
        from ralph import RalphOrchestrator, CONF, Logger

        Logger.set_verbose(False)

        agent_error = AgentError(
            exception_type="CLIError",
            message="Claude CLI exited with code 1",
            stack_trace="STDOUT:\ntest\nSTDERR:\nerror",
            timestamp="2024-01-01T12:00:00",
            agent_name="Claude",
            task_id="WORKER-TASK-001",
        )

        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "Claude"
        mock_agent.check_dependencies.return_value = True
        mock_agent.run.return_value = (False, "CLI output", agent_error)

        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    orchestrator._record_failure(0, "CLI Crash", "CLI output", agent_error=agent_error)

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("CLIError", content)
                    self.assertIn("Claude CLI exited with code 1", content)
                    self.assertIn("Claude", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file


if __name__ == "__main__":
    unittest.main()
