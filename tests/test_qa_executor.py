"""Tests for QA executor module."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyralph.config import CONF
from pyralph.qa.executor import (
    QAExecutor,
    QAResult,
    QAViolation,
    AgentTimeoutError,
    AgentUnavailableError,
    MalformedResponseError,
)
from pyralph.qa.rules import QARule


class QAExecutorTestCase(unittest.TestCase):
    """Base test case for QAExecutor tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.qa_dir = self.temp_path / ".ralph" / "qa"
        self._original_qa_rules_dir = CONF.QA_RULES_DIR
        CONF.QA_RULES_DIR = self.qa_dir

    def tearDown(self):
        CONF.QA_RULES_DIR = self._original_qa_rules_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_rule_file(self, name: str, content: str) -> Path:
        """Create a rule file in the QA directory."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.qa_dir / name
        file_path.write_text(content, encoding="utf-8")
        return file_path


class TestQAViolation(unittest.TestCase):
    """Tests for QAViolation dataclass."""

    def test_violation_to_dict(self):
        violation = QAViolation(
            rule_id="test-rule",
            rule_name="Test Rule",
            severity="major",
            message="Test message",
            file_path="test.py",
            line_number=42,
            details="Additional details",
        )
        result = violation.to_dict()
        assert result["rule_id"] == "test-rule"
        assert result["rule_name"] == "Test Rule"
        assert result["severity"] == "major"
        assert result["message"] == "Test message"
        assert result["file_path"] == "test.py"
        assert result["line_number"] == 42
        assert result["details"] == "Additional details"

    def test_violation_defaults(self):
        violation = QAViolation(
            rule_id="test-rule",
            rule_name="Test Rule",
            severity="minor",
            message="Test",
        )
        assert violation.file_path is None
        assert violation.line_number is None
        assert violation.details == ""


class TestQAResult(unittest.TestCase):
    """Tests for QAResult dataclass."""

    def test_result_to_dict(self):
        violations = [
            QAViolation(
                rule_id="rule-1",
                rule_name="Rule 1",
                severity="critical",
                message="Violation 1",
            )
        ]
        result = QAResult(
            success=False,
            rules_checked=5,
            violations=violations,
            summary="Found violations",
        )
        data = result.to_dict()
        assert data["success"] is False
        assert data["rules_checked"] == 5
        assert len(data["violations"]) == 1
        assert data["violations"][0]["rule_id"] == "rule-1"
        assert data["summary"] == "Found violations"

    def test_result_defaults(self):
        result = QAResult(success=True, rules_checked=0)
        assert result.violations == []
        assert result.summary == ""
        assert result.raw_output == ""


class TestQAExecutorValidation(QAExecutorTestCase):
    """Tests for QAExecutor validation logic."""

    def test_invalid_agent_raises_error(self):
        executor = QAExecutor(agent_name="invalid_agent")
        with self.assertRaises(AgentUnavailableError) as context:
            executor.run()
        assert "invalid_agent" in str(context.exception)
        assert "Available agents" in str(context.exception)
        assert "Retry" in str(context.exception)

    def test_no_rules_returns_success(self):
        """Test that no rules configured returns success with informative message."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)  # Empty directory
        with patch("pyralph.qa.executor.Logger"):
            executor = QAExecutor(agent_name="claude")
            result = executor.run()
        assert result.success is True
        assert result.rules_checked == 0
        assert "No QA rules" in result.summary


class TestQAExecutorAgentIntegration(QAExecutorTestCase):
    """Tests for QAExecutor agent integration."""

    def test_agent_timeout_raises_error(self):
        """Test that agent timeout raises AgentTimeoutError with retry guidance."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        # Simulate timeout error
        from pyralph.agents.base import AgentError
        timeout_error = AgentError(
            exception_type="TimeoutExpired",
            message="timeout after 600 seconds",
            stack_trace="",
            timestamp="2024-01-01T00:00:00",
            agent_name="Claude",
            task_id="qa-validation",
        )
        mock_agent.run.return_value = (False, "", timeout_error)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude", timeout=600)
                with self.assertRaises(AgentTimeoutError) as context:
                    executor.run()
                assert "timed out" in str(context.exception)
                assert "Retry" in str(context.exception)

    def test_agent_unavailable_raises_error(self):
        """Test that agent failure raises AgentUnavailableError with retry guidance."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        # Simulate general error
        from pyralph.agents.base import AgentError
        agent_error = AgentError(
            exception_type="CLIError",
            message="Agent failed to respond",
            stack_trace="",
            timestamp="2024-01-01T00:00:00",
            agent_name="Claude",
            task_id="qa-validation",
        )
        mock_agent.run.return_value = (False, "", agent_error)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                with self.assertRaises(AgentUnavailableError) as context:
                    executor.run()
                assert "failed to respond" in str(context.exception)
                assert "Retry" in str(context.exception)

    def test_agent_dependencies_not_met(self):
        """Test that missing agent dependencies raises AgentUnavailableError."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = False

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                with self.assertRaises(AgentUnavailableError) as context:
                    executor.run()
                assert "dependencies" in str(context.exception)


class TestQAExecutorResponseParsing(QAExecutorTestCase):
    """Tests for QAExecutor response parsing."""

    def test_parse_valid_json_response(self):
        """Test parsing a valid JSON response from agent."""
        yaml_content = """
id: no-print
name: No Print Statements
severity: major
"""
        self.create_rule_file("test.yaml", yaml_content)

        valid_response = json.dumps({
            "success": False,
            "violations": [
                {
                    "rule_id": "no-print",
                    "file_path": "test.py",
                    "line_number": 10,
                    "message": "Found print statement",
                }
            ],
            "summary": "1 violation found",
        })

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, valid_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                result = executor.run()

        assert result.success is False
        assert result.rules_checked == 1
        assert len(result.violations) == 1
        assert result.violations[0].rule_id == "no-print"
        assert result.violations[0].file_path == "test.py"
        assert result.violations[0].line_number == 10

    def test_parse_json_in_markdown_code_block(self):
        """Test parsing JSON embedded in markdown code block."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        markdown_response = '''Here's the analysis:

```json
{
  "success": true,
  "violations": [],
  "summary": "All rules passed"
}
```

That's all!'''

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, markdown_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                result = executor.run()

        assert result.success is True
        assert len(result.violations) == 0

    def test_malformed_response_no_json(self):
        """Test that response without JSON raises MalformedResponseError."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        invalid_response = "This is just plain text without any JSON"

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, invalid_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                with self.assertRaises(MalformedResponseError) as context:
                    executor.run()
                assert "does not contain valid JSON" in str(context.exception)

    def test_malformed_response_invalid_json(self):
        """Test that invalid JSON raises MalformedResponseError."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        invalid_json = '{"success": true, "violations": [}'  # Invalid JSON

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, invalid_json, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                with self.assertRaises(MalformedResponseError) as context:
                    executor.run()
                assert "Failed to parse" in str(context.exception)

    def test_malformed_response_missing_success_field(self):
        """Test that response missing 'success' field raises MalformedResponseError."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        missing_field_response = json.dumps({
            "violations": [],
            "summary": "No violations",
        })

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, missing_field_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                with self.assertRaises(MalformedResponseError) as context:
                    executor.run()
                assert "missing required 'success' field" in str(context.exception)

    def test_malformed_response_not_object(self):
        """Test that non-object JSON raises MalformedResponseError."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        array_response = '["not", "an", "object"]'

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, array_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                with self.assertRaises(MalformedResponseError) as context:
                    executor.run()
                # Array response doesn't have { so it's treated as no valid JSON object
                assert "does not contain valid JSON" in str(context.exception)


class TestQAExecutorGitDiff(QAExecutorTestCase):
    """Tests for QAExecutor git diff handling."""

    def test_handles_git_not_found(self):
        """Test that executor handles missing git gracefully."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        valid_response = json.dumps({
            "success": True,
            "violations": [],
            "summary": "All passed",
        })

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, valid_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                with patch("pyralph.qa.executor.subprocess.run", side_effect=FileNotFoundError("git not found")):
                    executor = QAExecutor(agent_name="claude")
                    result = executor.run()

        assert result.success is True


class TestQAExecutorSuccess(QAExecutorTestCase):
    """Tests for successful QA validation scenarios."""

    def test_successful_validation_with_rules(self):
        """Test successful validation when rules exist and pass."""
        yaml_content = """
id: no-print
name: No Print Statements
description: Disallow print statements
severity: major
"""
        self.create_rule_file("test.yaml", yaml_content)

        valid_response = json.dumps({
            "success": True,
            "violations": [],
            "summary": "All QA rules passed. No violations found.",
        })

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "Claude"
        mock_agent.run.return_value = (True, valid_response, None)

        with patch("pyralph.qa.executor.get_agent", return_value=mock_agent):
            with patch("pyralph.qa.executor.Logger"):
                executor = QAExecutor(agent_name="claude")
                result = executor.run()

        assert result.success is True
        assert result.rules_checked == 1
        assert len(result.violations) == 0


if __name__ == "__main__":
    unittest.main()
