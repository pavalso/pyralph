"""Tests for QA CLI module."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyralph.config import CONF
from pyralph.qa.cli import main, format_violation, format_result_json
from pyralph.qa.executor import QAResult, QAViolation


class QACLITestCase(unittest.TestCase):
    """Base test case for QA CLI tests."""

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


class TestFormatViolation(unittest.TestCase):
    """Tests for violation formatting."""

    def test_format_violation_with_location(self):
        violation = QAViolation(
            rule_id="test-rule",
            rule_name="Test Rule",
            severity="critical",
            message="Test message",
            file_path="test.py",
            line_number=42,
        )
        result = format_violation(violation)
        assert "[CRITICAL]" in result
        assert "Test Rule" in result
        assert "test.py:42" in result
        assert "Test message" in result

    def test_format_violation_without_location(self):
        violation = QAViolation(
            rule_id="test-rule",
            rule_name="Test Rule",
            severity="minor",
            message="Test message",
        )
        result = format_violation(violation)
        assert "[MINOR]" in result
        assert "Test Rule" in result
        assert "Test message" in result


class TestFormatResultJson(unittest.TestCase):
    """Tests for JSON result formatting."""

    def test_format_result_json(self):
        violations = [
            QAViolation(
                rule_id="rule-1",
                rule_name="Rule 1",
                severity="major",
                message="Violation",
            )
        ]
        result = QAResult(
            success=False,
            rules_checked=1,
            violations=violations,
            summary="Failed",
        )
        json_str = format_result_json(result)
        data = json.loads(json_str)
        assert data["success"] is False
        assert data["rules_checked"] == 1
        assert len(data["violations"]) == 1


class TestCLIAgentRequired(QACLITestCase):
    """Tests for CLI --agent argument requirement."""

    def test_agent_is_required(self):
        """Test that --agent argument is required."""
        with patch("pyralph.qa.cli.Logger"):
            # argparse should raise SystemExit for missing required arg
            with self.assertRaises(SystemExit) as context:
                main([])
            # Exit code 2 is argparse error
            assert context.exception.code == 2


class TestCLINoRulesScenario(QACLITestCase):
    """Tests for CLI when no rules exist."""

    def test_no_rules_exits_zero(self):
        """Test that no rules configured exits with code 0."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)  # Empty directory
        with patch("pyralph.qa.cli.Logger"):
            exit_code = main(["--agent", "claude"])
        assert exit_code == 0

    def test_no_rules_json_output(self):
        """Test JSON output when no rules configured."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)  # Empty directory
        with patch("pyralph.qa.cli.Logger"):
            with patch("builtins.print") as mock_print:
                exit_code = main(["--agent", "claude", "--json"])
        assert exit_code == 0
        # Check JSON was printed
        mock_print.assert_called()
        printed_json = mock_print.call_args[0][0]
        data = json.loads(printed_json)
        assert data["success"] is True
        assert data["rules_checked"] == 0


class TestCLISuccessScenario(QACLITestCase):
    """Tests for CLI success scenarios."""

    def test_successful_validation(self):
        """Test CLI with successful validation."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        mock_result = QAResult(
            success=True,
            rules_checked=1,
            violations=[],
            summary="All passed",
        )

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.return_value = mock_result
            with patch("pyralph.qa.cli.Logger"):
                exit_code = main(["--agent", "claude"])

        assert exit_code == 0

    def test_validation_with_violations(self):
        """Test CLI returns exit code 1 when violations found."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        mock_result = QAResult(
            success=False,
            rules_checked=1,
            violations=[
                QAViolation(
                    rule_id="test-rule",
                    rule_name="Test Rule",
                    severity="major",
                    message="Violation found",
                )
            ],
            summary="1 violation",
        )

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.return_value = mock_result
            with patch("pyralph.qa.cli.Logger"):
                exit_code = main(["--agent", "claude"])

        assert exit_code == 1


class TestCLIErrorScenarios(QACLITestCase):
    """Tests for CLI error scenarios."""

    def test_agent_timeout_error(self):
        """Test CLI handles agent timeout with exit code 2."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        from pyralph.qa.executor import AgentTimeoutError

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.side_effect = AgentTimeoutError(
                "Agent timed out. Retry with --timeout."
            )
            with patch("pyralph.qa.cli.Logger"):
                exit_code = main(["--agent", "claude"])

        assert exit_code == 2

    def test_agent_timeout_json_output(self):
        """Test JSON output on timeout error."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        from pyralph.qa.executor import AgentTimeoutError

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.side_effect = AgentTimeoutError(
                "Agent timed out."
            )
            with patch("pyralph.qa.cli.Logger"):
                with patch("builtins.print") as mock_print:
                    exit_code = main(["--agent", "claude", "--json"])

        assert exit_code == 2
        printed_json = mock_print.call_args[0][0]
        data = json.loads(printed_json)
        assert data["error"] == "timeout"
        assert "timed out" in data["message"]

    def test_agent_unavailable_error(self):
        """Test CLI handles agent unavailable with exit code 2."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        from pyralph.qa.executor import AgentUnavailableError

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.side_effect = AgentUnavailableError(
                "Agent unavailable. Retry."
            )
            with patch("pyralph.qa.cli.Logger"):
                exit_code = main(["--agent", "claude"])

        assert exit_code == 2

    def test_malformed_response_error(self):
        """Test CLI handles malformed response with exit code 2."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        from pyralph.qa.executor import MalformedResponseError

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.side_effect = MalformedResponseError(
                "Response not valid JSON. Expected format: {}"
            )
            with patch("pyralph.qa.cli.Logger"):
                exit_code = main(["--agent", "claude"])

        assert exit_code == 2

    def test_malformed_response_json_output(self):
        """Test JSON output on malformed response error."""
        yaml_content = """
id: test-rule
name: Test Rule
"""
        self.create_rule_file("test.yaml", yaml_content)

        from pyralph.qa.executor import MalformedResponseError

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.side_effect = MalformedResponseError(
                "Response format issue"
            )
            with patch("pyralph.qa.cli.Logger"):
                with patch("builtins.print") as mock_print:
                    exit_code = main(["--agent", "claude", "--json"])

        assert exit_code == 2
        printed_json = mock_print.call_args[0][0]
        data = json.loads(printed_json)
        assert data["error"] == "malformed_response"


class TestCLIArguments(QACLITestCase):
    """Tests for CLI argument handling."""

    def test_timeout_argument(self):
        """Test --timeout argument is passed to executor."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.return_value = QAResult(
                success=True, rules_checked=0
            )
            with patch("pyralph.qa.cli.Logger"):
                main(["--agent", "claude", "--timeout", "300"])

        mock_executor.assert_called_once()
        call_kwargs = mock_executor.call_args[1]
        assert call_kwargs["timeout"] == 300

    def test_model_argument(self):
        """Test --model argument is passed to executor."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.return_value = QAResult(
                success=True, rules_checked=0
            )
            with patch("pyralph.qa.cli.Logger"):
                main(["--agent", "claude", "--model", "claude-3-opus"])

        mock_executor.assert_called_once()
        call_kwargs = mock_executor.call_args[1]
        assert call_kwargs["model"] == "claude-3-opus"

    def test_rules_dir_argument(self):
        """Test --rules-dir argument is passed to executor."""
        custom_dir = self.temp_path / "custom_qa"
        custom_dir.mkdir(parents=True, exist_ok=True)

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.return_value = QAResult(
                success=True, rules_checked=0
            )
            with patch("pyralph.qa.cli.Logger"):
                main(["--agent", "claude", "--rules-dir", str(custom_dir)])

        mock_executor.assert_called_once()
        call_kwargs = mock_executor.call_args[1]
        assert call_kwargs["rules_dir"] == str(custom_dir)

    def test_quiet_mode(self):
        """Test --quiet mode suppresses output."""
        self.qa_dir.mkdir(parents=True, exist_ok=True)

        with patch("pyralph.qa.cli.QAExecutor") as mock_executor:
            mock_executor.return_value.run.return_value = QAResult(
                success=True, rules_checked=0
            )
            with patch("pyralph.qa.cli.Logger") as mock_logger:
                main(["--agent", "claude", "--quiet"])

        mock_logger.set_quiet.assert_called_with(True)


class TestCLIInvalidAgent(QACLITestCase):
    """Tests for invalid agent handling."""

    def test_invalid_agent_choice(self):
        """Test that invalid agent choice is rejected by argparse."""
        with patch("pyralph.qa.cli.Logger"):
            with self.assertRaises(SystemExit) as context:
                main(["--agent", "invalid_agent"])
        # argparse exits with code 2 for invalid choices
        assert context.exception.code == 2


if __name__ == "__main__":
    unittest.main()
