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
    UserStory,
    CreatedIssue,
    CreateIssueResult,
    check_gh_cli,
    fetch_ready_issues,
    main,
    issue_to_prompt,
    issues_to_prompts,
    invoke_planner,
    process_ready_issues,
    create_draft_issue,
    create_draft_issues,
    update_issue_labels,
    mark_issue_processed,
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


class TestUserStory(unittest.TestCase):
    """Tests for UserStory dataclass."""

    def test_user_story_creation(self):
        """Test UserStory dataclass creation with all fields."""
        story = UserStory(
            title="Add user authentication",
            body="Implement OAuth2 login flow for users"
        )
        self.assertEqual(story.title, "Add user authentication")
        self.assertEqual(story.body, "Implement OAuth2 login flow for users")

    def test_user_story_empty_body(self):
        """Test UserStory with empty body."""
        story = UserStory(title="Test", body="")
        self.assertEqual(story.body, "")


class TestCreatedIssue(unittest.TestCase):
    """Tests for CreatedIssue dataclass."""

    def test_created_issue_creation(self):
        """Test CreatedIssue dataclass creation."""
        issue = CreatedIssue(
            number=42,
            url="https://github.com/owner/repo/issues/42",
            title="Test Issue"
        )
        self.assertEqual(issue.number, 42)
        self.assertEqual(issue.url, "https://github.com/owner/repo/issues/42")
        self.assertEqual(issue.title, "Test Issue")


class TestCreateIssueResult(unittest.TestCase):
    """Tests for CreateIssueResult dataclass."""

    def test_successful_result(self):
        """Test CreateIssueResult for successful creation."""
        created = CreatedIssue(number=1, url="http://url", title="Test")
        result = CreateIssueResult(
            title="Test",
            success=True,
            issue=created
        )
        self.assertEqual(result.title, "Test")
        self.assertTrue(result.success)
        self.assertIsNotNone(result.issue)
        self.assertIsNone(result.error)

    def test_failed_result(self):
        """Test CreateIssueResult for failed creation."""
        result = CreateIssueResult(
            title="Test",
            success=False,
            error="gh CLI failed"
        )
        self.assertEqual(result.title, "Test")
        self.assertFalse(result.success)
        self.assertIsNone(result.issue)
        self.assertEqual(result.error, "gh CLI failed")


class TestCreateDraftIssue(unittest.TestCase):
    """Tests for create_draft_issue function."""

    def test_create_draft_issue_success(self):
        """Test create_draft_issue returns CreatedIssue on success."""
        story = UserStory(title="Test Issue", body="Test body")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/42\n",
                stderr=""
            )
            result = create_draft_issue(story)

        self.assertIsInstance(result, CreatedIssue)
        self.assertEqual(result.number, 42)
        self.assertEqual(result.url, "https://github.com/owner/repo/issues/42")
        self.assertEqual(result.title, "Test Issue")

    def test_create_draft_issue_correct_command(self):
        """Test create_draft_issue calls gh with correct arguments."""
        story = UserStory(title="My Title", body="My Body")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/1\n",
                stderr=""
            )
            create_draft_issue(story)

        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "gh")
        self.assertEqual(call_args[1], "issue")
        self.assertEqual(call_args[2], "create")
        self.assertIn("--title", call_args)
        self.assertIn("My Title", call_args)
        self.assertIn("--body", call_args)
        self.assertIn("My Body", call_args)
        self.assertIn("--label", call_args)
        self.assertIn("draft", call_args)

    def test_create_draft_issue_cli_error(self):
        """Test create_draft_issue raises GitHubCLIError on CLI failure."""
        story = UserStory(title="Test", body="Body")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: label 'draft' not found"
            )
            with self.assertRaises(GitHubCLIError) as context:
                create_draft_issue(story)
        self.assertIn("gh CLI failed", str(context.exception))

    def test_create_draft_issue_empty_output(self):
        """Test create_draft_issue raises GitHubCLIError on empty output."""
        story = UserStory(title="Test", body="Body")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr=""
            )
            with self.assertRaises(GitHubCLIError) as context:
                create_draft_issue(story)
        self.assertIn("empty output", str(context.exception))

    def test_create_draft_issue_invalid_url(self):
        """Test create_draft_issue raises GitHubCLIError on invalid URL format."""
        story = UserStory(title="Test", body="Body")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="not-a-valid-url",
                stderr=""
            )
            with self.assertRaises(GitHubCLIError) as context:
                create_draft_issue(story)
        self.assertIn("Failed to parse issue number", str(context.exception))

    def test_create_draft_issue_timeout(self):
        """Test create_draft_issue raises GitHubCLIError on timeout."""
        story = UserStory(title="Test", body="Body")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)
            with self.assertRaises(GitHubCLIError) as context:
                create_draft_issue(story)
        self.assertIn("timed out", str(context.exception))

    def test_create_draft_issue_trailing_slash_url(self):
        """Test create_draft_issue handles URL with trailing slash."""
        story = UserStory(title="Test", body="Body")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/99/\n",
                stderr=""
            )
            result = create_draft_issue(story)

        self.assertEqual(result.number, 99)


class TestCreateDraftIssues(unittest.TestCase):
    """Tests for create_draft_issues function."""

    def test_empty_stories_list(self):
        """Test create_draft_issues with empty list."""
        results, success, failure = create_draft_issues([])
        self.assertEqual(results, [])
        self.assertEqual(success, 0)
        self.assertEqual(failure, 0)

    def test_single_successful_story(self):
        """Test create_draft_issues with one successful story."""
        stories = [UserStory(title="Test", body="Body")]

        with patch('fetch_ready_issues.create_draft_issue') as mock_create:
            mock_create.return_value = CreatedIssue(
                number=1,
                url="http://url/1",
                title="Test"
            )
            results, success, failure = create_draft_issues(stories)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Test")
        self.assertTrue(results[0].success)
        self.assertIsNotNone(results[0].issue)
        self.assertIsNone(results[0].error)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 0)

    def test_single_failed_story(self):
        """Test create_draft_issues with one failed story."""
        stories = [UserStory(title="Test", body="Body")]

        with patch('fetch_ready_issues.create_draft_issue') as mock_create:
            mock_create.side_effect = GitHubCLIError("Failed to create")
            results, success, failure = create_draft_issues(stories)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Test")
        self.assertFalse(results[0].success)
        self.assertIsNone(results[0].issue)
        self.assertEqual(results[0].error, "Failed to create")
        self.assertEqual(success, 0)
        self.assertEqual(failure, 1)

    def test_multiple_stories_all_success(self):
        """Test create_draft_issues with multiple successful stories."""
        stories = [
            UserStory(title="First", body="Body 1"),
            UserStory(title="Second", body="Body 2"),
            UserStory(title="Third", body="Body 3"),
        ]

        call_count = [0]
        def mock_create(story):
            call_count[0] += 1
            return CreatedIssue(
                number=call_count[0],
                url=f"http://url/{call_count[0]}",
                title=story.title
            )

        with patch('fetch_ready_issues.create_draft_issue', side_effect=mock_create):
            results, success, failure = create_draft_issues(stories)

        self.assertEqual(len(results), 3)
        self.assertEqual(success, 3)
        self.assertEqual(failure, 0)

    def test_multiple_stories_mixed_results(self):
        """Test create_draft_issues with mixed success/failure results."""
        stories = [
            UserStory(title="First", body="Body 1"),
            UserStory(title="Second", body="Body 2"),
            UserStory(title="Third", body="Body 3"),
        ]

        def mock_create(story):
            if story.title == "Second":
                raise GitHubCLIError("Failed to create Second")
            return CreatedIssue(
                number=1,
                url="http://url/1",
                title=story.title
            )

        with patch('fetch_ready_issues.create_draft_issue', side_effect=mock_create):
            results, success, failure = create_draft_issues(stories)

        self.assertEqual(len(results), 3)
        self.assertEqual(success, 2)
        self.assertEqual(failure, 1)

        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertTrue(results[2].success)

    def test_continues_after_failure(self):
        """Test create_draft_issues continues processing after a failure."""
        stories = [
            UserStory(title="First", body="Body 1"),
            UserStory(title="Second", body="Body 2"),
        ]

        call_count = [0]
        def mock_create(story):
            call_count[0] += 1
            if call_count[0] == 1:
                raise GitHubCLIError("First failed")
            return CreatedIssue(number=2, url="http://url/2", title=story.title)

        with patch('fetch_ready_issues.create_draft_issue', side_effect=mock_create):
            results, success, failure = create_draft_issues(stories)

        # Both stories were processed
        self.assertEqual(len(results), 2)
        self.assertEqual(call_count[0], 2)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 1)

    def test_preserves_order(self):
        """Test create_draft_issues preserves order of results."""
        stories = [
            UserStory(title="A", body="Body"),
            UserStory(title="B", body="Body"),
            UserStory(title="C", body="Body"),
        ]

        call_count = [0]
        def mock_create(story):
            call_count[0] += 1
            return CreatedIssue(
                number=call_count[0],
                url=f"http://url/{call_count[0]}",
                title=story.title
            )

        with patch('fetch_ready_issues.create_draft_issue', side_effect=mock_create):
            results, _, _ = create_draft_issues(stories)

        self.assertEqual(results[0].title, "A")
        self.assertEqual(results[1].title, "B")
        self.assertEqual(results[2].title, "C")


class TestUpdateIssueLabels(unittest.TestCase):
    """Tests for update_issue_labels function."""

    def test_update_issue_labels_add_only(self):
        """Test update_issue_labels with only add_labels."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = update_issue_labels(42, add_labels=["processed"])

        self.assertTrue(result)
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "gh")
        self.assertEqual(call_args[1], "issue")
        self.assertEqual(call_args[2], "edit")
        self.assertEqual(call_args[3], "42")
        self.assertIn("--add-label", call_args)
        self.assertIn("processed", call_args)

    def test_update_issue_labels_remove_only(self):
        """Test update_issue_labels with only remove_labels."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = update_issue_labels(42, remove_labels=["ready"])

        self.assertTrue(result)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--remove-label", call_args)
        self.assertIn("ready", call_args)

    def test_update_issue_labels_add_and_remove(self):
        """Test update_issue_labels with both add and remove labels."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = update_issue_labels(
                42,
                add_labels=["processed", "done"],
                remove_labels=["ready", "pending"]
            )

        self.assertTrue(result)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--add-label", call_args)
        self.assertIn("processed,done", call_args)
        self.assertIn("--remove-label", call_args)
        self.assertIn("ready,pending", call_args)

    def test_update_issue_labels_no_labels(self):
        """Test update_issue_labels with no labels returns True without calling gh."""
        with patch("subprocess.run") as mock_run:
            result = update_issue_labels(42)

        self.assertTrue(result)
        mock_run.assert_not_called()

    def test_update_issue_labels_empty_lists(self):
        """Test update_issue_labels with empty lists returns True without calling gh."""
        with patch("subprocess.run") as mock_run:
            result = update_issue_labels(42, add_labels=[], remove_labels=[])

        self.assertTrue(result)
        mock_run.assert_not_called()

    def test_update_issue_labels_cli_error(self):
        """Test update_issue_labels raises GitHubCLIError on CLI failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: issue not found"
            )
            with self.assertRaises(GitHubCLIError) as context:
                update_issue_labels(42, add_labels=["processed"])

        self.assertIn("gh CLI failed to update labels", str(context.exception))
        self.assertIn("#42", str(context.exception))

    def test_update_issue_labels_timeout(self):
        """Test update_issue_labels raises GitHubCLIError on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)
            with self.assertRaises(GitHubCLIError) as context:
                update_issue_labels(42, add_labels=["processed"])

        self.assertIn("timed out", str(context.exception))
        self.assertIn("#42", str(context.exception))

    def test_update_issue_labels_correct_command_structure(self):
        """Test update_issue_labels builds correct command structure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            update_issue_labels(123, add_labels=["a"], remove_labels=["b"])

        call_args = mock_run.call_args[0][0]
        # Command should be: gh issue edit 123 --add-label a --remove-label b
        self.assertEqual(call_args[:4], ["gh", "issue", "edit", "123"])


class TestMarkIssueProcessed(unittest.TestCase):
    """Tests for mark_issue_processed function."""

    def test_mark_issue_processed_success(self):
        """Test mark_issue_processed returns True on success."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = mark_issue_processed(42)

        self.assertTrue(result)

    def test_mark_issue_processed_correct_labels(self):
        """Test mark_issue_processed uses correct labels."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mark_issue_processed(42)

        call_args = mock_run.call_args[0][0]
        self.assertIn("--add-label", call_args)
        self.assertIn("processed", call_args)
        self.assertIn("--remove-label", call_args)
        self.assertIn("ready", call_args)

    def test_mark_issue_processed_cli_error(self):
        """Test mark_issue_processed raises GitHubCLIError on CLI failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: issue not found"
            )
            with self.assertRaises(GitHubCLIError):
                mark_issue_processed(42)

    def test_mark_issue_processed_timeout(self):
        """Test mark_issue_processed raises GitHubCLIError on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)
            with self.assertRaises(GitHubCLIError):
                mark_issue_processed(42)

    def test_mark_issue_processed_different_issue_numbers(self):
        """Test mark_issue_processed with various issue numbers."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            for issue_num in [1, 99, 1234]:
                mark_issue_processed(issue_num)
                call_args = mock_run.call_args[0][0]
                self.assertEqual(call_args[3], str(issue_num))


if __name__ == "__main__":
    unittest.main()
