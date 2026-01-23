import unittest
from datetime import datetime

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


if __name__ == "__main__":
    unittest.main()
