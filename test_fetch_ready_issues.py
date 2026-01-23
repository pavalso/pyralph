"""Tests for fetch_ready_issues.py module."""
import json
import subprocess
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from fetch_ready_issues import (
    Issue,
    GitHubCLIError,
    PlannerError,
    PlannerResult,
    check_gh_cli,
    fetch_ready_issues,
    main,
    issue_to_prompt,
    issues_to_prompts,
    invoke_planner,
    process_ready_issues,
)


class TestIssue(unittest.TestCase):
    """Tests for Issue dataclass."""

    def test_issue_creation(self):
        """Test Issue dataclass creation with all fields."""
        issue = Issue(
            number=42,
            title="Test Issue",
            body="Issue body content",
            url="https://github.com/owner/repo/issues/42",
            labels=["ready", "bug"]
        )
        self.assertEqual(issue.number, 42)
        self.assertEqual(issue.title, "Test Issue")
        self.assertEqual(issue.body, "Issue body content")
        self.assertEqual(issue.url, "https://github.com/owner/repo/issues/42")
        self.assertEqual(issue.labels, ["ready", "bug"])

    def test_issue_with_none_body(self):
        """Test Issue dataclass with None body."""
        issue = Issue(
            number=1,
            title="No Body",
            body=None,
            url="https://github.com/owner/repo/issues/1",
            labels=["ready"]
        )
        self.assertIsNone(issue.body)

    def test_issue_to_dict(self):
        """Test Issue.to_dict() method."""
        issue = Issue(
            number=10,
            title="Dict Test",
            body="Body text",
            url="https://github.com/owner/repo/issues/10",
            labels=["ready", "enhancement"]
        )
        result = issue.to_dict()
        self.assertEqual(result, {
            "number": 10,
            "title": "Dict Test",
            "body": "Body text",
            "url": "https://github.com/owner/repo/issues/10",
            "labels": ["ready", "enhancement"]
        })

    def test_issue_to_dict_with_none_body(self):
        """Test Issue.to_dict() with None body."""
        issue = Issue(number=1, title="Test", body=None, url="http://test", labels=[])
        result = issue.to_dict()
        self.assertIsNone(result["body"])


class TestCheckGhCli(unittest.TestCase):
    """Tests for check_gh_cli function."""

    def test_check_gh_cli_success(self):
        """Test check_gh_cli returns True when gh is authenticated."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = check_gh_cli()
        self.assertTrue(result)
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        self.assertEqual(call_args[0][0], ["gh", "auth", "status"])

    def test_check_gh_cli_not_authenticated(self):
        """Test check_gh_cli returns False when gh is not authenticated."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = check_gh_cli()
        self.assertFalse(result)

    def test_check_gh_cli_not_installed(self):
        """Test check_gh_cli returns False when gh is not installed."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = check_gh_cli()
        self.assertFalse(result)

    def test_check_gh_cli_timeout(self):
        """Test check_gh_cli returns False on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
            result = check_gh_cli()
        self.assertFalse(result)


class TestFetchReadyIssues(unittest.TestCase):
    """Tests for fetch_ready_issues function."""

    def test_fetch_ready_issues_success(self):
        """Test fetch_ready_issues returns issues on success."""
        mock_output = json.dumps([
            {
                "number": 1,
                "title": "First Issue",
                "body": "Description 1",
                "url": "https://github.com/owner/repo/issues/1",
                "labels": [{"name": "ready"}, {"name": "bug"}]
            },
            {
                "number": 2,
                "title": "Second Issue",
                "body": None,
                "url": "https://github.com/owner/repo/issues/2",
                "labels": [{"name": "ready"}]
            }
        ])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            issues = fetch_ready_issues()

        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].number, 1)
        self.assertEqual(issues[0].title, "First Issue")
        self.assertEqual(issues[0].labels, ["ready", "bug"])
        self.assertEqual(issues[1].number, 2)
        self.assertIsNone(issues[1].body)

    def test_fetch_ready_issues_empty_result(self):
        """Test fetch_ready_issues returns empty list when no issues found."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
            issues = fetch_ready_issues()
        self.assertEqual(issues, [])

    def test_fetch_ready_issues_empty_stdout(self):
        """Test fetch_ready_issues handles empty stdout."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            issues = fetch_ready_issues()
        self.assertEqual(issues, [])

    def test_fetch_ready_issues_cli_error(self):
        """Test fetch_ready_issues raises GitHubCLIError on CLI failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error message")
            with self.assertRaises(GitHubCLIError) as context:
                fetch_ready_issues()
        self.assertIn("gh CLI failed", str(context.exception))

    def test_fetch_ready_issues_timeout(self):
        """Test fetch_ready_issues raises GitHubCLIError on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)
            with self.assertRaises(GitHubCLIError) as context:
                fetch_ready_issues()
        self.assertIn("timed out", str(context.exception))

    def test_fetch_ready_issues_invalid_json(self):
        """Test fetch_ready_issues raises GitHubCLIError on invalid JSON."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not valid json", stderr="")
            with self.assertRaises(GitHubCLIError) as context:
                fetch_ready_issues()
        self.assertIn("Failed to parse", str(context.exception))

    def test_fetch_ready_issues_correct_command(self):
        """Test fetch_ready_issues calls gh with correct arguments."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
            fetch_ready_issues()

        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "gh")
        self.assertEqual(call_args[1], "issue")
        self.assertEqual(call_args[2], "list")
        self.assertIn("--label", call_args)
        self.assertIn("ready", call_args)
        self.assertIn("--state", call_args)
        self.assertIn("open", call_args)
        self.assertIn("--json", call_args)

    def test_fetch_ready_issues_handles_empty_labels(self):
        """Test fetch_ready_issues handles issues with empty labels array."""
        mock_output = json.dumps([
            {
                "number": 1,
                "title": "Issue",
                "body": "Body",
                "url": "http://url",
                "labels": []
            }
        ])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            issues = fetch_ready_issues()
        self.assertEqual(issues[0].labels, [])


class TestMain(unittest.TestCase):
    """Tests for main function."""

    def test_main_gh_not_available(self):
        """Test main returns 1 when gh CLI is not available."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=False):
            with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                result = main()
        self.assertEqual(result, 1)
        self.assertIn("gh CLI is not installed", mock_stderr.getvalue())

    def test_main_no_issues_found(self):
        """Test main returns 0 and prints message when no issues found."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=[]):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    result = main()
        self.assertEqual(result, 0)
        self.assertIn("No open issues", mock_stdout.getvalue())

    def test_main_issues_found(self):
        """Test main returns 0 and prints JSON when issues found."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=["ready"])
        ]
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=issues):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    result = main()
        self.assertEqual(result, 0)
        output = json.loads(mock_stdout.getvalue())
        self.assertEqual(output["count"], 1)
        self.assertEqual(len(output["issues"]), 1)
        self.assertEqual(output["issues"][0]["number"], 1)

    def test_main_github_cli_error(self):
        """Test main returns 1 and prints error on GitHubCLIError."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues") as mock_fetch:
                mock_fetch.side_effect = GitHubCLIError("Test error")
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    result = main()
        self.assertEqual(result, 1)
        self.assertIn("Test error", mock_stderr.getvalue())


class TestGitHubCLIError(unittest.TestCase):
    """Tests for GitHubCLIError exception."""

    def test_exception_message(self):
        """Test GitHubCLIError stores message correctly."""
        error = GitHubCLIError("Test message")
        self.assertEqual(str(error), "Test message")

    def test_exception_inheritance(self):
        """Test GitHubCLIError inherits from Exception."""
        error = GitHubCLIError("Test")
        self.assertIsInstance(error, Exception)


class TestIssueToPrompt(unittest.TestCase):
    """Tests for issue_to_prompt function."""

    def test_basic_issue_transformation(self):
        """Test basic issue transformation to prompt format."""
        issue = Issue(
            number=42,
            title="Add user authentication",
            body="Implement OAuth2 login flow",
            url="https://github.com/owner/repo/issues/42",
            labels=["ready", "feature"]
        )
        result = issue_to_prompt(issue)
        self.assertIn("TASK-042", result)
        self.assertIn("Add user authentication", result)
        self.assertIn("Implement OAuth2 login flow", result)

    def test_issue_number_padding(self):
        """Test issue number is zero-padded to 3 digits."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            url="http://url",
            labels=[]
        )
        result = issue_to_prompt(issue)
        self.assertIn("TASK-001", result)

    def test_large_issue_number(self):
        """Test large issue numbers are handled correctly."""
        issue = Issue(
            number=1234,
            title="Test",
            body="Body",
            url="http://url",
            labels=[]
        )
        result = issue_to_prompt(issue)
        self.assertIn("TASK-1234", result)

    def test_none_body_uses_placeholder(self):
        """Test None body is replaced with placeholder text."""
        issue = Issue(
            number=5,
            title="No description issue",
            body=None,
            url="http://url",
            labels=["ready"]
        )
        result = issue_to_prompt(issue)
        self.assertIn("TASK-005", result)
        self.assertIn("No description issue", result)
        self.assertIn("No description provided.", result)

    def test_empty_body_uses_placeholder(self):
        """Test empty/whitespace body is replaced with placeholder text."""
        issue = Issue(
            number=6,
            title="Empty body issue",
            body="   ",
            url="http://url",
            labels=[]
        )
        result = issue_to_prompt(issue)
        self.assertIn("No description provided.", result)

    def test_body_whitespace_is_stripped(self):
        """Test body whitespace is trimmed."""
        issue = Issue(
            number=7,
            title="Test",
            body="  Description with whitespace  ",
            url="http://url",
            labels=[]
        )
        result = issue_to_prompt(issue)
        self.assertIn("Description with whitespace", result)
        self.assertNotIn("  Description", result)

    def test_multiline_body_preserved(self):
        """Test multiline body content is preserved."""
        issue = Issue(
            number=8,
            title="Multiline test",
            body="Line 1\nLine 2\nLine 3",
            url="http://url",
            labels=[]
        )
        result = issue_to_prompt(issue)
        self.assertIn("Line 1\nLine 2\nLine 3", result)

    def test_prompt_format_structure(self):
        """Test the overall prompt format structure."""
        issue = Issue(
            number=10,
            title="Test Title",
            body="Test Body",
            url="http://url",
            labels=[]
        )
        result = issue_to_prompt(issue)
        expected = "TASK-010: Test Title\n\nDescription:\nTest Body"
        self.assertEqual(result, expected)


class TestIssuesToPrompts(unittest.TestCase):
    """Tests for issues_to_prompts function."""

    def test_empty_list(self):
        """Test empty list returns empty list."""
        result = issues_to_prompts([])
        self.assertEqual(result, [])

    def test_single_issue(self):
        """Test single issue is transformed correctly."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=[])
        ]
        result = issues_to_prompts(issues)
        self.assertEqual(len(result), 1)
        self.assertIn("TASK-001", result[0])

    def test_multiple_issues(self):
        """Test multiple issues are all transformed."""
        issues = [
            Issue(number=1, title="First", body="Body 1", url="http://url1", labels=[]),
            Issue(number=2, title="Second", body="Body 2", url="http://url2", labels=[]),
            Issue(number=3, title="Third", body="Body 3", url="http://url3", labels=[]),
        ]
        result = issues_to_prompts(issues)
        self.assertEqual(len(result), 3)
        self.assertIn("TASK-001", result[0])
        self.assertIn("First", result[0])
        self.assertIn("TASK-002", result[1])
        self.assertIn("Second", result[1])
        self.assertIn("TASK-003", result[2])
        self.assertIn("Third", result[2])

    def test_preserves_order(self):
        """Test issues order is preserved in output."""
        issues = [
            Issue(number=99, title="Ninety-nine", body="B", url="http://url", labels=[]),
            Issue(number=1, title="One", body="B", url="http://url", labels=[]),
            Issue(number=50, title="Fifty", body="B", url="http://url", labels=[]),
        ]
        result = issues_to_prompts(issues)
        self.assertIn("TASK-099", result[0])
        self.assertIn("TASK-001", result[1])
        self.assertIn("TASK-050", result[2])


class TestPlannerError(unittest.TestCase):
    """Tests for PlannerError exception."""

    def test_exception_message(self):
        """Test PlannerError stores message correctly."""
        error = PlannerError("Test message")
        self.assertEqual(str(error), "Test message")

    def test_exception_inheritance(self):
        """Test PlannerError inherits from Exception."""
        error = PlannerError("Test")
        self.assertIsInstance(error, Exception)


class TestPlannerResult(unittest.TestCase):
    """Tests for PlannerResult dataclass."""

    def test_successful_result(self):
        """Test PlannerResult for successful invocation."""
        result = PlannerResult(issue_number=42, success=True)
        self.assertEqual(result.issue_number, 42)
        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_failed_result_with_error(self):
        """Test PlannerResult for failed invocation with error message."""
        result = PlannerResult(
            issue_number=10,
            success=False,
            error="Memory directory missing"
        )
        self.assertEqual(result.issue_number, 10)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Memory directory missing")


class TestInvokePlanner(unittest.TestCase):
    """Tests for invoke_planner function."""

    def test_invoke_planner_import_error(self):
        """Test invoke_planner raises PlannerError when ralph cannot be imported."""
        with patch.dict('sys.modules', {'ralph': None}):
            with patch('builtins.__import__', side_effect=ImportError("No module")):
                with self.assertRaises(PlannerError) as context:
                    invoke_planner("Test intent")
        self.assertIn("Failed to import", str(context.exception))

    def test_invoke_planner_missing_memory(self):
        """Test invoke_planner raises PlannerError when memory is missing."""
        mock_conf = MagicMock()
        mock_conf.MEMORY_DIR.exists.return_value = False

        mock_ralph_module = MagicMock()
        mock_ralph_module.CONF = mock_conf
        mock_ralph_module.RalphOrchestrator = MagicMock()

        with patch.dict('sys.modules', {'ralph': mock_ralph_module}):
            with self.assertRaises(PlannerError) as context:
                invoke_planner("Test intent")
        self.assertIn("Memory directory is missing", str(context.exception))

    def test_invoke_planner_empty_memory(self):
        """Test invoke_planner raises PlannerError when memory is empty."""
        mock_conf = MagicMock()
        mock_conf.MEMORY_DIR.exists.return_value = True
        mock_conf.MEMORY_DIR.iterdir.return_value = iter([])

        mock_ralph_module = MagicMock()
        mock_ralph_module.CONF = mock_conf
        mock_ralph_module.RalphOrchestrator = MagicMock()

        with patch.dict('sys.modules', {'ralph': mock_ralph_module}):
            with self.assertRaises(PlannerError) as context:
                invoke_planner("Test intent")
        self.assertIn("empty", str(context.exception))

    def test_invoke_planner_success(self):
        """Test invoke_planner returns True on successful planning."""
        mock_conf = MagicMock()
        mock_conf.MEMORY_DIR.exists.return_value = True
        mock_conf.MEMORY_DIR.iterdir.return_value = iter(["file1.md"])

        mock_orchestrator = MagicMock()
        mock_orchestrator_class = MagicMock(return_value=mock_orchestrator)

        mock_ralph_module = MagicMock()
        mock_ralph_module.CONF = mock_conf
        mock_ralph_module.RalphOrchestrator = mock_orchestrator_class

        with patch.dict('sys.modules', {'ralph': mock_ralph_module}):
            result = invoke_planner("Test intent")

        self.assertTrue(result)
        mock_orchestrator.run_planner.assert_called_once_with("Test intent")

    def test_invoke_planner_failure_returns_false(self):
        """Test invoke_planner returns False when planner fails with SystemExit."""
        mock_conf = MagicMock()
        mock_conf.MEMORY_DIR.exists.return_value = True
        mock_conf.MEMORY_DIR.iterdir.return_value = iter(["file1.md"])

        mock_orchestrator = MagicMock()
        mock_orchestrator.run_planner.side_effect = SystemExit(1)
        mock_orchestrator_class = MagicMock(return_value=mock_orchestrator)

        mock_ralph_module = MagicMock()
        mock_ralph_module.CONF = mock_conf
        mock_ralph_module.RalphOrchestrator = mock_orchestrator_class

        with patch.dict('sys.modules', {'ralph': mock_ralph_module}):
            result = invoke_planner("Test intent")

        self.assertFalse(result)

    def test_invoke_planner_uses_custom_agent(self):
        """Test invoke_planner passes agent_name to orchestrator."""
        mock_conf = MagicMock()
        mock_conf.MEMORY_DIR.exists.return_value = True
        mock_conf.MEMORY_DIR.iterdir.return_value = iter(["file1.md"])

        mock_orchestrator = MagicMock()
        mock_orchestrator_class = MagicMock(return_value=mock_orchestrator)

        mock_ralph_module = MagicMock()
        mock_ralph_module.CONF = mock_conf
        mock_ralph_module.RalphOrchestrator = mock_orchestrator_class

        with patch.dict('sys.modules', {'ralph': mock_ralph_module}):
            invoke_planner("Test", agent_name="copilot")

        mock_orchestrator_class.assert_called_once_with(agent_name="copilot", enable_hooks=True)

    def test_invoke_planner_disables_hooks(self):
        """Test invoke_planner passes enable_hooks=False to orchestrator."""
        mock_conf = MagicMock()
        mock_conf.MEMORY_DIR.exists.return_value = True
        mock_conf.MEMORY_DIR.iterdir.return_value = iter(["file1.md"])

        mock_orchestrator = MagicMock()
        mock_orchestrator_class = MagicMock(return_value=mock_orchestrator)

        mock_ralph_module = MagicMock()
        mock_ralph_module.CONF = mock_conf
        mock_ralph_module.RalphOrchestrator = mock_orchestrator_class

        with patch.dict('sys.modules', {'ralph': mock_ralph_module}):
            invoke_planner("Test", enable_hooks=False)

        mock_orchestrator_class.assert_called_once_with(agent_name="claude", enable_hooks=False)


class TestProcessReadyIssues(unittest.TestCase):
    """Tests for process_ready_issues function."""

    def test_empty_issues_list(self):
        """Test process_ready_issues with empty list."""
        results, success, failure = process_ready_issues([])
        self.assertEqual(results, [])
        self.assertEqual(success, 0)
        self.assertEqual(failure, 0)

    def test_single_successful_issue(self):
        """Test process_ready_issues with one successful issue."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=[])
        ]

        with patch('fetch_ready_issues.invoke_planner', return_value=True):
            results, success, failure = process_ready_issues(issues)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].issue_number, 1)
        self.assertTrue(results[0].success)
        self.assertIsNone(results[0].error)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 0)

    def test_single_failed_issue(self):
        """Test process_ready_issues with one failed issue."""
        issues = [
            Issue(number=2, title="Test", body="Body", url="http://url", labels=[])
        ]

        with patch('fetch_ready_issues.invoke_planner', return_value=False):
            results, success, failure = process_ready_issues(issues)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].issue_number, 2)
        self.assertFalse(results[0].success)
        self.assertIn("did not complete successfully", results[0].error)
        self.assertEqual(success, 0)
        self.assertEqual(failure, 1)

    def test_planner_error_is_caught(self):
        """Test process_ready_issues catches PlannerError."""
        issues = [
            Issue(number=3, title="Test", body="Body", url="http://url", labels=[])
        ]

        with patch('fetch_ready_issues.invoke_planner') as mock_invoke:
            mock_invoke.side_effect = PlannerError("Memory missing")
            results, success, failure = process_ready_issues(issues)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].issue_number, 3)
        self.assertFalse(results[0].success)
        self.assertEqual(results[0].error, "Memory missing")
        self.assertEqual(success, 0)
        self.assertEqual(failure, 1)

    def test_multiple_issues_mixed_results(self):
        """Test process_ready_issues with mixed success/failure results."""
        issues = [
            Issue(number=1, title="First", body="Body", url="http://url", labels=[]),
            Issue(number=2, title="Second", body="Body", url="http://url", labels=[]),
            Issue(number=3, title="Third", body="Body", url="http://url", labels=[]),
        ]

        # First succeeds, second fails, third has error
        def mock_invoke(user_intent, agent_name="claude", enable_hooks=True):
            if "TASK-001" in user_intent:
                return True
            elif "TASK-002" in user_intent:
                return False
            else:
                raise PlannerError("Simulated error")

        with patch('fetch_ready_issues.invoke_planner', side_effect=mock_invoke):
            results, success, failure = process_ready_issues(issues)

        self.assertEqual(len(results), 3)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 2)

        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertFalse(results[2].success)

    def test_passes_agent_name(self):
        """Test process_ready_issues passes agent_name to invoke_planner."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=[])
        ]

        with patch('fetch_ready_issues.invoke_planner', return_value=True) as mock_invoke:
            process_ready_issues(issues, agent_name="copilot")

        mock_invoke.assert_called_once()
        call_kwargs = mock_invoke.call_args[1]
        self.assertEqual(call_kwargs["agent_name"], "copilot")

    def test_passes_enable_hooks(self):
        """Test process_ready_issues passes enable_hooks to invoke_planner."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=[])
        ]

        with patch('fetch_ready_issues.invoke_planner', return_value=True) as mock_invoke:
            process_ready_issues(issues, enable_hooks=False)

        mock_invoke.assert_called_once()
        call_kwargs = mock_invoke.call_args[1]
        self.assertFalse(call_kwargs["enable_hooks"])

    def test_continues_after_failure(self):
        """Test process_ready_issues continues processing after a failure."""
        issues = [
            Issue(number=1, title="First", body="Body", url="http://url", labels=[]),
            Issue(number=2, title="Second", body="Body", url="http://url", labels=[]),
        ]

        # First fails, second succeeds
        call_count = [0]
        def mock_invoke(user_intent, agent_name="claude", enable_hooks=True):
            call_count[0] += 1
            if call_count[0] == 1:
                raise PlannerError("First failed")
            return True

        with patch('fetch_ready_issues.invoke_planner', side_effect=mock_invoke):
            results, success, failure = process_ready_issues(issues)

        # Both issues were processed
        self.assertEqual(len(results), 2)
        self.assertEqual(call_count[0], 2)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 1)


if __name__ == "__main__":
    unittest.main()
