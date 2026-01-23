"""Tests for fetch_ready_issues.py module."""
import json
import subprocess
import threading
import time
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
    HookConfig,
    HookRegistrationError,
    PollerConfig,
    GitHubPoller,
    StoredIssue,
    IssueStoreError,
    IssueStore,
    QueueItem,
    ProcessingQueueError,
    ProcessingQueue,
    PromptTransformerError,
    TransformedPrompt,
    PromptTransformer,
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
    create_argument_parser,
    format_issues_as_text,
    generate_hook_config,
    register_hook,
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

    def test_fetch_ready_issues_custom_label(self):
        """Test fetch_ready_issues uses custom label parameter."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
            fetch_ready_issues(label="bug")

        call_args = mock_run.call_args[0][0]
        self.assertIn("--label", call_args)
        label_idx = call_args.index("--label")
        self.assertEqual(call_args[label_idx + 1], "bug")

    def test_fetch_ready_issues_default_label(self):
        """Test fetch_ready_issues uses 'ready' as default label."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
            fetch_ready_issues()

        call_args = mock_run.call_args[0][0]
        self.assertIn("ready", call_args)


class TestCreateArgumentParser(unittest.TestCase):
    """Tests for create_argument_parser function."""

    def test_parser_default_values(self):
        """Test parser has correct default values."""
        parser = create_argument_parser()
        args = parser.parse_args([])
        self.assertEqual(args.label, "ready")
        self.assertEqual(args.output_format, "json")
        self.assertFalse(args.verbose)
        self.assertFalse(args.no_check)

    def test_parser_label_option(self):
        """Test parser parses --label option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--label", "bug"])
        self.assertEqual(args.label, "bug")

    def test_parser_format_option_json(self):
        """Test parser parses --format json option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--format", "json"])
        self.assertEqual(args.output_format, "json")

    def test_parser_format_option_text(self):
        """Test parser parses --format text option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--format", "text"])
        self.assertEqual(args.output_format, "text")

    def test_parser_verbose_option(self):
        """Test parser parses --verbose option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--verbose"])
        self.assertTrue(args.verbose)

    def test_parser_no_check_option(self):
        """Test parser parses --no-check option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--no-check"])
        self.assertTrue(args.no_check)

    def test_parser_multiple_options(self):
        """Test parser parses multiple options together."""
        parser = create_argument_parser()
        args = parser.parse_args(["--label", "feature", "--format", "text", "--verbose", "--no-check"])
        self.assertEqual(args.label, "feature")
        self.assertEqual(args.output_format, "text")
        self.assertTrue(args.verbose)
        self.assertTrue(args.no_check)

    def test_parser_invalid_format_raises(self):
        """Test parser raises on invalid format option."""
        parser = create_argument_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--format", "invalid"])


class TestFormatIssuesAsText(unittest.TestCase):
    """Tests for format_issues_as_text function."""

    def test_format_empty_list(self):
        """Test format_issues_as_text with empty list."""
        result = format_issues_as_text([])
        self.assertEqual(result, "No issues found.")

    def test_format_single_issue(self):
        """Test format_issues_as_text with single issue."""
        issues = [
            Issue(
                number=42,
                title="Test Issue",
                body="Issue body",
                url="https://github.com/owner/repo/issues/42",
                labels=["ready", "bug"]
            )
        ]
        result = format_issues_as_text(issues)
        self.assertIn("Found 1 issue(s):", result)
        self.assertIn("#42: Test Issue", result)
        self.assertIn("URL: https://github.com/owner/repo/issues/42", result)
        self.assertIn("Labels: ready, bug", result)
        self.assertIn("Body: Issue body", result)

    def test_format_multiple_issues(self):
        """Test format_issues_as_text with multiple issues."""
        issues = [
            Issue(number=1, title="First", body="Body 1", url="http://url1", labels=["ready"]),
            Issue(number=2, title="Second", body="Body 2", url="http://url2", labels=["bug"]),
        ]
        result = format_issues_as_text(issues)
        self.assertIn("Found 2 issue(s):", result)
        self.assertIn("#1: First", result)
        self.assertIn("#2: Second", result)

    def test_format_issue_no_labels(self):
        """Test format_issues_as_text with issue having no labels."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=[])
        ]
        result = format_issues_as_text(issues)
        self.assertNotIn("Labels:", result)

    def test_format_issue_no_body(self):
        """Test format_issues_as_text with issue having no body."""
        issues = [
            Issue(number=1, title="Test", body=None, url="http://url", labels=[])
        ]
        result = format_issues_as_text(issues)
        self.assertNotIn("Body:", result)

    def test_format_long_body_truncated(self):
        """Test format_issues_as_text truncates long body."""
        long_body = "A" * 150
        issues = [
            Issue(number=1, title="Test", body=long_body, url="http://url", labels=[])
        ]
        result = format_issues_as_text(issues)
        self.assertIn("...", result)
        self.assertNotIn("A" * 150, result)

    def test_format_body_newlines_replaced(self):
        """Test format_issues_as_text replaces newlines in body preview."""
        issues = [
            Issue(number=1, title="Test", body="Line1\nLine2\nLine3", url="http://url", labels=[])
        ]
        result = format_issues_as_text(issues)
        self.assertIn("Line1 Line2 Line3", result)


class TestMain(unittest.TestCase):
    """Tests for main function."""

    def test_main_gh_not_available(self):
        """Test main returns 1 when gh CLI is not available."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=False):
            with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                result = main([])
        self.assertEqual(result, 1)
        self.assertIn("gh CLI is not installed", mock_stderr.getvalue())

    def test_main_no_issues_found_json_format(self):
        """Test main returns 0 and prints JSON when no issues found (json format)."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=[]):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    result = main([])
        self.assertEqual(result, 0)
        output = json.loads(mock_stdout.getvalue())
        self.assertEqual(output["count"], 0)
        self.assertEqual(output["issues"], [])

    def test_main_no_issues_found_text_format(self):
        """Test main returns 0 and prints message when no issues found (text format)."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=[]):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    result = main(["--format", "text"])
        self.assertEqual(result, 0)
        self.assertIn("No open issues", mock_stdout.getvalue())

    def test_main_issues_found_json_format(self):
        """Test main returns 0 and prints JSON when issues found."""
        issues = [
            Issue(number=1, title="Test", body="Body", url="http://url", labels=["ready"])
        ]
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=issues):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    result = main([])
        self.assertEqual(result, 0)
        output = json.loads(mock_stdout.getvalue())
        self.assertEqual(output["count"], 1)
        self.assertEqual(len(output["issues"]), 1)
        self.assertEqual(output["issues"][0]["number"], 1)

    def test_main_issues_found_text_format(self):
        """Test main returns 0 and prints text when issues found (text format)."""
        issues = [
            Issue(number=42, title="Test Issue", body="Body", url="http://url", labels=["ready"])
        ]
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=issues):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    result = main(["--format", "text"])
        self.assertEqual(result, 0)
        output = mock_stdout.getvalue()
        self.assertIn("#42: Test Issue", output)
        self.assertIn("Found 1 issue(s):", output)

    def test_main_github_cli_error(self):
        """Test main returns 1 and prints error on GitHubCLIError."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues") as mock_fetch:
                mock_fetch.side_effect = GitHubCLIError("Test error")
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    result = main([])
        self.assertEqual(result, 1)
        self.assertIn("Test error", mock_stderr.getvalue())

    def test_main_custom_label(self):
        """Test main passes custom label to fetch_ready_issues."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=[]) as mock_fetch:
                main(["--label", "bug"])
        mock_fetch.assert_called_once_with(label="bug")

    def test_main_no_check_skips_auth_check(self):
        """Test main with --no-check skips gh CLI authentication check."""
        with patch("fetch_ready_issues.check_gh_cli") as mock_check:
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=[]):
                main(["--no-check"])
        mock_check.assert_not_called()

    def test_main_verbose_output(self):
        """Test main with --verbose prints verbose output."""
        with patch("fetch_ready_issues.check_gh_cli", return_value=True):
            with patch("fetch_ready_issues.fetch_ready_issues", return_value=[]):
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    main(["--verbose", "--label", "test"])
        stderr_output = mock_stderr.getvalue()
        self.assertIn("Label filter: test", stderr_output)
        self.assertIn("Output format: json", stderr_output)
        self.assertIn("Fetching issues", stderr_output)


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


class TestHookConfig(unittest.TestCase):
    """Tests for HookConfig dataclass."""

    def test_hook_config_creation(self):
        """Test HookConfig dataclass creation with all fields."""
        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS", "TASK_SUCCESS"],
            priority=50,
            timeout=10.0
        )
        self.assertEqual(config.name, "test_hook")
        self.assertEqual(config.path, "/path/to/script.py")
        self.assertEqual(config.events, ["PLANNER_SUCCESS", "TASK_SUCCESS"])
        self.assertEqual(config.priority, 50)
        self.assertEqual(config.timeout, 10.0)

    def test_hook_config_default_values(self):
        """Test HookConfig dataclass default values."""
        config = HookConfig(
            name="minimal_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"]
        )
        self.assertEqual(config.priority, 100)
        self.assertEqual(config.timeout, 5.0)

    def test_hook_config_to_dict(self):
        """Test HookConfig.to_dict() method."""
        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"],
            priority=75,
            timeout=15.0
        )
        result = config.to_dict()
        self.assertEqual(result, {
            "name": "test_hook",
            "path": "/path/to/script.py",
            "events": ["PLANNER_SUCCESS"],
            "priority": 75,
            "timeout": 15.0
        })


class TestHookRegistrationError(unittest.TestCase):
    """Tests for HookRegistrationError exception."""

    def test_exception_message(self):
        """Test HookRegistrationError stores message correctly."""
        error = HookRegistrationError("Test message")
        self.assertEqual(str(error), "Test message")

    def test_exception_inheritance(self):
        """Test HookRegistrationError inherits from Exception."""
        error = HookRegistrationError("Test")
        self.assertIsInstance(error, Exception)


class TestGenerateHookConfig(unittest.TestCase):
    """Tests for generate_hook_config function."""

    def test_generate_hook_config_default_path(self):
        """Test generate_hook_config uses default path."""
        config = generate_hook_config()
        self.assertEqual(config.name, "fetch_ready_issues")
        self.assertEqual(config.events, ["PLANNER_SUCCESS"])
        self.assertEqual(config.priority, 100)
        self.assertEqual(config.timeout, 30.0)
        # Path should be absolute
        self.assertTrue(config.path.endswith("fetch_ready_issues.py"))

    def test_generate_hook_config_custom_path(self):
        """Test generate_hook_config with custom script path."""
        custom_path = "/custom/path/to/script.py"
        config = generate_hook_config(script_path=custom_path)
        self.assertEqual(config.path, custom_path)
        self.assertEqual(config.name, "fetch_ready_issues")
        self.assertEqual(config.events, ["PLANNER_SUCCESS"])

    def test_generate_hook_config_events(self):
        """Test generate_hook_config returns correct events."""
        config = generate_hook_config()
        self.assertIn("PLANNER_SUCCESS", config.events)
        self.assertEqual(len(config.events), 1)


class TestRegisterHook(unittest.TestCase):
    """Tests for register_hook function."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        import shutil
        self.temp_dir = tempfile.mkdtemp()
        self.hooks_dir = f"{self.temp_dir}/.ralph/hooks"

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_hook_creates_directory(self):
        """Test register_hook creates hooks directory if it doesn't exist."""
        import os
        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"]
        )

        result = register_hook(self.hooks_dir, config)

        self.assertTrue(os.path.exists(self.hooks_dir))
        self.assertTrue(os.path.exists(result))

    def test_register_hook_creates_yaml_file(self):
        """Test register_hook creates hooks.yaml file."""
        import os
        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"]
        )

        result = register_hook(self.hooks_dir, config)

        self.assertTrue(result.endswith("hooks.yaml"))
        self.assertTrue(os.path.exists(result))

    def test_register_hook_writes_correct_yaml(self):
        """Test register_hook writes correct YAML content."""
        import yaml
        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"],
            priority=50,
            timeout=10.0
        )

        result = register_hook(self.hooks_dir, config)

        with open(result, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        self.assertIn("hooks", content)
        self.assertEqual(len(content["hooks"]), 1)
        hook = content["hooks"][0]
        self.assertEqual(hook["name"], "test_hook")
        self.assertEqual(hook["path"], "/path/to/script.py")
        self.assertEqual(hook["events"], ["PLANNER_SUCCESS"])
        self.assertEqual(hook["priority"], 50)
        self.assertEqual(hook["timeout"], 10.0)

    def test_register_hook_appends_to_existing_config(self):
        """Test register_hook appends to existing hooks.yaml."""
        import os
        import yaml

        # Create initial hooks directory and config
        os.makedirs(self.hooks_dir, exist_ok=True)
        config_path = f"{self.hooks_dir}/hooks.yaml"
        initial_config = {
            "hooks": [
                {"name": "existing_hook", "path": "/existing/path.py", "events": ["TASK_SUCCESS"]}
            ]
        }
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(initial_config, f)

        # Register new hook
        config = HookConfig(
            name="new_hook",
            path="/new/path.py",
            events=["PLANNER_SUCCESS"]
        )

        register_hook(self.hooks_dir, config)

        with open(config_path, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        self.assertEqual(len(content["hooks"]), 2)
        hook_names = [h["name"] for h in content["hooks"]]
        self.assertIn("existing_hook", hook_names)
        self.assertIn("new_hook", hook_names)

    def test_register_hook_replaces_existing_hook_with_same_name(self):
        """Test register_hook replaces hook with same name."""
        import os
        import yaml

        # Create initial hooks directory and config
        os.makedirs(self.hooks_dir, exist_ok=True)
        config_path = f"{self.hooks_dir}/hooks.yaml"
        initial_config = {
            "hooks": [
                {"name": "test_hook", "path": "/old/path.py", "events": ["TASK_SUCCESS"]}
            ]
        }
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(initial_config, f)

        # Register hook with same name but different config
        config = HookConfig(
            name="test_hook",
            path="/new/path.py",
            events=["PLANNER_SUCCESS"]
        )

        register_hook(self.hooks_dir, config)

        with open(config_path, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        self.assertEqual(len(content["hooks"]), 1)
        self.assertEqual(content["hooks"][0]["path"], "/new/path.py")
        self.assertEqual(content["hooks"][0]["events"], ["PLANNER_SUCCESS"])

    def test_register_hook_handles_empty_yaml(self):
        """Test register_hook handles empty existing hooks.yaml."""
        import os
        import yaml

        # Create empty hooks.yaml
        os.makedirs(self.hooks_dir, exist_ok=True)
        config_path = f"{self.hooks_dir}/hooks.yaml"
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write("")

        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"]
        )

        register_hook(self.hooks_dir, config)

        with open(config_path, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        self.assertEqual(len(content["hooks"]), 1)

    def test_register_hook_handles_malformed_hooks_list(self):
        """Test register_hook handles hooks.yaml with non-list hooks value."""
        import os
        import yaml

        # Create hooks.yaml with non-list hooks value
        os.makedirs(self.hooks_dir, exist_ok=True)
        config_path = f"{self.hooks_dir}/hooks.yaml"
        initial_config = {"hooks": "not_a_list"}
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(initial_config, f)

        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"]
        )

        register_hook(self.hooks_dir, config)

        with open(config_path, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        self.assertIsInstance(content["hooks"], list)
        self.assertEqual(len(content["hooks"]), 1)

    def test_register_hook_returns_config_path(self):
        """Test register_hook returns the path to hooks.yaml."""
        config = HookConfig(
            name="test_hook",
            path="/path/to/script.py",
            events=["PLANNER_SUCCESS"]
        )

        result = register_hook(self.hooks_dir, config)

        self.assertTrue(result.endswith("hooks.yaml"))
        self.assertIn(self.temp_dir, result)


class TestMainRegisterHook(unittest.TestCase):
    """Tests for main function with --register-hook option."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.hooks_dir = f"{self.temp_dir}/.ralph/hooks"

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_main_register_hook_success(self):
        """Test main with --register-hook returns 0 on success."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            result = main(["--register-hook", "--hooks-dir", self.hooks_dir])

        self.assertEqual(result, 0)
        output = mock_stdout.getvalue()
        self.assertIn("Hook registered successfully", output)
        self.assertIn("fetch_ready_issues", output)
        self.assertIn("PLANNER_SUCCESS", output)

    def test_main_register_hook_custom_hooks_dir(self):
        """Test main with --register-hook uses custom hooks directory."""
        import os
        custom_hooks_dir = f"{self.temp_dir}/custom/hooks"

        with patch("sys.stdout", new_callable=StringIO):
            result = main(["--register-hook", "--hooks-dir", custom_hooks_dir])

        self.assertEqual(result, 0)
        self.assertTrue(os.path.exists(f"{custom_hooks_dir}/hooks.yaml"))

    def test_main_register_hook_error(self):
        """Test main with --register-hook returns 1 on error."""
        with patch("fetch_ready_issues.register_hook") as mock_register:
            mock_register.side_effect = HookRegistrationError("Test error")
            with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                result = main(["--register-hook", "--hooks-dir", self.hooks_dir])

        self.assertEqual(result, 1)
        self.assertIn("Test error", mock_stderr.getvalue())

    def test_main_register_hook_skips_gh_check(self):
        """Test main with --register-hook does not check gh CLI."""
        with patch("fetch_ready_issues.check_gh_cli") as mock_check:
            with patch("sys.stdout", new_callable=StringIO):
                main(["--register-hook", "--hooks-dir", self.hooks_dir])

        mock_check.assert_not_called()

    def test_main_register_hook_skips_fetch(self):
        """Test main with --register-hook does not fetch issues."""
        with patch("fetch_ready_issues.fetch_ready_issues") as mock_fetch:
            with patch("sys.stdout", new_callable=StringIO):
                main(["--register-hook", "--hooks-dir", self.hooks_dir])

        mock_fetch.assert_not_called()


class TestCreateArgumentParserRegisterHook(unittest.TestCase):
    """Tests for create_argument_parser with register hook options."""

    def test_parser_register_hook_default(self):
        """Test parser has register_hook default to False."""
        parser = create_argument_parser()
        args = parser.parse_args([])
        self.assertFalse(args.register_hook)

    def test_parser_register_hook_option(self):
        """Test parser parses --register-hook option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--register-hook"])
        self.assertTrue(args.register_hook)

    def test_parser_hooks_dir_default(self):
        """Test parser has hooks_dir default value."""
        parser = create_argument_parser()
        args = parser.parse_args([])
        self.assertEqual(args.hooks_dir, ".ralph/hooks")

    def test_parser_hooks_dir_option(self):
        """Test parser parses --hooks-dir option."""
        parser = create_argument_parser()
        args = parser.parse_args(["--hooks-dir", "/custom/path"])
        self.assertEqual(args.hooks_dir, "/custom/path")

    def test_parser_register_hook_with_hooks_dir(self):
        """Test parser parses both --register-hook and --hooks-dir options."""
        parser = create_argument_parser()
        args = parser.parse_args(["--register-hook", "--hooks-dir", "/my/hooks"])
        self.assertTrue(args.register_hook)
        self.assertEqual(args.hooks_dir, "/my/hooks")


class TestPollerConfig(unittest.TestCase):
    """Tests for PollerConfig dataclass."""

    def test_poller_config_default_values(self):
        """Test PollerConfig has correct default values."""
        config = PollerConfig()
        self.assertEqual(config.label, "ready")
        self.assertEqual(config.interval, 60.0)
        self.assertIsNone(config.on_new_issues)
        self.assertIsNone(config.on_error)

    def test_poller_config_custom_values(self):
        """Test PollerConfig with custom values."""
        callback = lambda issues: None
        error_callback = lambda e: None
        config = PollerConfig(
            label="bug",
            interval=30.0,
            on_new_issues=callback,
            on_error=error_callback
        )
        self.assertEqual(config.label, "bug")
        self.assertEqual(config.interval, 30.0)
        self.assertIs(config.on_new_issues, callback)
        self.assertIs(config.on_error, error_callback)

    def test_poller_config_partial_values(self):
        """Test PollerConfig with only some custom values."""
        config = PollerConfig(interval=120.0)
        self.assertEqual(config.label, "ready")
        self.assertEqual(config.interval, 120.0)
        self.assertIsNone(config.on_new_issues)


class TestGitHubPoller(unittest.TestCase):
    """Tests for GitHubPoller class."""

    def test_poller_default_config(self):
        """Test GitHubPoller with default configuration."""
        poller = GitHubPoller()
        self.assertEqual(poller.interval, 60.0)
        self.assertEqual(poller.config.label, "ready")
        self.assertFalse(poller.is_running)

    def test_poller_custom_config(self):
        """Test GitHubPoller with custom configuration."""
        config = PollerConfig(label="feature", interval=45.0)
        poller = GitHubPoller(config)
        self.assertEqual(poller.interval, 45.0)
        self.assertEqual(poller.config.label, "feature")

    def test_poller_interval_setter(self):
        """Test setting polling interval."""
        poller = GitHubPoller()
        poller.interval = 30.0
        self.assertEqual(poller.interval, 30.0)

    def test_poller_interval_setter_invalid(self):
        """Test setting invalid polling interval raises ValueError."""
        poller = GitHubPoller()
        with self.assertRaises(ValueError):
            poller.interval = 0
        with self.assertRaises(ValueError):
            poller.interval = -10

    def test_poller_seen_issues_initially_empty(self):
        """Test seen_issues is initially empty."""
        poller = GitHubPoller()
        self.assertEqual(poller.seen_issues, set())

    def test_poller_seen_issues_returns_copy(self):
        """Test seen_issues returns a copy of the set."""
        poller = GitHubPoller()
        seen = poller.seen_issues
        seen.add(999)  # Modify the copy
        self.assertNotIn(999, poller.seen_issues)  # Original should be unmodified

    def test_poller_start_stop(self):
        """Test starting and stopping the poller."""
        config = PollerConfig(interval=0.1)
        poller = GitHubPoller(config)

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=[]):
            result = poller.start()
            self.assertTrue(result)
            self.assertTrue(poller.is_running)

            # Try starting again - should return False
            result = poller.start()
            self.assertFalse(result)

            # Stop the poller
            result = poller.stop(timeout=1.0)
            self.assertTrue(result)
            self.assertFalse(poller.is_running)

            # Try stopping again - should return False
            result = poller.stop()
            self.assertFalse(result)

    def test_poller_poll_once_returns_new_issues(self):
        """Test poll_once returns new issues."""
        mock_issues = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
            Issue(number=2, title="Second", body="Body", url="http://url/2", labels=["ready"]),
        ]

        poller = GitHubPoller()

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=mock_issues):
            new_issues = poller.poll_once()

        self.assertEqual(len(new_issues), 2)
        self.assertEqual(new_issues[0].number, 1)
        self.assertEqual(new_issues[1].number, 2)
        self.assertEqual(poller.seen_issues, {1, 2})

    def test_poller_poll_once_filters_seen_issues(self):
        """Test poll_once does not return already seen issues."""
        mock_issues = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
            Issue(number=2, title="Second", body="Body", url="http://url/2", labels=["ready"]),
        ]

        poller = GitHubPoller()

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=mock_issues):
            # First poll - all issues are new
            new_issues_1 = poller.poll_once()
            self.assertEqual(len(new_issues_1), 2)

            # Second poll - no new issues
            new_issues_2 = poller.poll_once()
            self.assertEqual(len(new_issues_2), 0)

    def test_poller_poll_once_detects_new_issues_incrementally(self):
        """Test poll_once detects new issues appearing between polls."""
        poller = GitHubPoller()

        # First poll - one issue
        mock_issues_1 = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
        ]

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=mock_issues_1):
            new_issues = poller.poll_once()
            self.assertEqual(len(new_issues), 1)

        # Second poll - same issue plus a new one
        mock_issues_2 = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
            Issue(number=2, title="Second", body="Body", url="http://url/2", labels=["ready"]),
        ]

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=mock_issues_2):
            new_issues = poller.poll_once()
            self.assertEqual(len(new_issues), 1)
            self.assertEqual(new_issues[0].number, 2)

    def test_poller_poll_once_raises_on_error(self):
        """Test poll_once raises GitHubCLIError on failure."""
        poller = GitHubPoller()

        with patch('fetch_ready_issues.fetch_ready_issues') as mock_fetch:
            mock_fetch.side_effect = GitHubCLIError("CLI error")
            with self.assertRaises(GitHubCLIError):
                poller.poll_once()

    def test_poller_reset_seen_issues(self):
        """Test reset_seen_issues clears the seen set."""
        mock_issues = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
        ]

        poller = GitHubPoller()

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=mock_issues):
            poller.poll_once()
            self.assertEqual(poller.seen_issues, {1})

            poller.reset_seen_issues()
            self.assertEqual(poller.seen_issues, set())

            # After reset, issues should be "new" again
            new_issues = poller.poll_once()
            self.assertEqual(len(new_issues), 1)

    def test_poller_callback_invoked_on_new_issues(self):
        """Test on_new_issues callback is invoked when new issues are found."""
        received_issues = []

        def callback(issues):
            received_issues.extend(issues)

        config = PollerConfig(
            interval=0.05,
            on_new_issues=callback
        )
        poller = GitHubPoller(config)

        mock_issues = [
            Issue(number=1, title="Test", body="Body", url="http://url/1", labels=["ready"]),
        ]

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=mock_issues):
            poller.start()
            time.sleep(0.15)  # Let the polling loop run a couple of times
            poller.stop(timeout=1.0)

        self.assertEqual(len(received_issues), 1)
        self.assertEqual(received_issues[0].number, 1)

    def test_poller_callback_not_invoked_when_no_new_issues(self):
        """Test on_new_issues callback is not invoked when no new issues."""
        call_count = [0]

        def callback(issues):
            call_count[0] += 1

        config = PollerConfig(
            interval=0.05,
            on_new_issues=callback
        )
        poller = GitHubPoller(config)

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=[]):
            poller.start()
            time.sleep(0.15)
            poller.stop(timeout=1.0)

        self.assertEqual(call_count[0], 0)

    def test_poller_error_callback_invoked_on_error(self):
        """Test on_error callback is invoked when an error occurs."""
        received_errors = []

        def error_callback(e):
            received_errors.append(e)

        config = PollerConfig(
            interval=0.05,
            on_error=error_callback
        )
        poller = GitHubPoller(config)

        with patch('fetch_ready_issues.fetch_ready_issues') as mock_fetch:
            mock_fetch.side_effect = GitHubCLIError("Test error")
            poller.start()
            time.sleep(0.15)
            poller.stop(timeout=1.0)

        self.assertGreater(len(received_errors), 0)
        self.assertIsInstance(received_errors[0], GitHubCLIError)

    def test_poller_continues_after_error(self):
        """Test poller continues polling after an error."""
        poll_count = [0]
        received_issues = []

        def callback(issues):
            received_issues.extend(issues)

        config = PollerConfig(
            interval=0.05,
            on_new_issues=callback
        )
        poller = GitHubPoller(config)

        def mock_fetch(label="ready"):
            poll_count[0] += 1
            if poll_count[0] == 1:
                raise GitHubCLIError("Temporary error")
            return [Issue(number=1, title="Test", body="Body", url="http://url/1", labels=["ready"])]

        with patch('fetch_ready_issues.fetch_ready_issues', side_effect=mock_fetch):
            poller.start()
            time.sleep(0.2)
            poller.stop(timeout=1.0)

        # Should have polled multiple times
        self.assertGreater(poll_count[0], 1)
        # Should have eventually received issues after the error
        self.assertGreater(len(received_issues), 0)

    def test_poller_uses_configured_label(self):
        """Test poller uses the configured label for fetching."""
        config = PollerConfig(label="custom-label")
        poller = GitHubPoller(config)

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=[]) as mock_fetch:
            poller.poll_once()

        mock_fetch.assert_called_once_with(label="custom-label")

    def test_poller_thread_safety(self):
        """Test poller is thread-safe for concurrent access."""
        poller = GitHubPoller()
        errors = []

        def poll_worker():
            try:
                for _ in range(10):
                    with patch('fetch_ready_issues.fetch_ready_issues', return_value=[
                        Issue(number=1, title="Test", body="Body", url="http://url/1", labels=["ready"])
                    ]):
                        poller.poll_once()
                        _ = poller.seen_issues
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=poll_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)

    def test_poller_stop_with_timeout(self):
        """Test stopping poller with timeout."""
        config = PollerConfig(interval=10.0)  # Long interval
        poller = GitHubPoller(config)

        with patch('fetch_ready_issues.fetch_ready_issues', return_value=[]):
            poller.start()
            # Stop should complete quickly due to stop_event, not wait for interval
            start_time = time.time()
            poller.stop(timeout=1.0)
            elapsed = time.time() - start_time

        self.assertLess(elapsed, 2.0)  # Should complete well within timeout
        self.assertFalse(poller.is_running)


class TestStoredIssue(unittest.TestCase):
    """Tests for StoredIssue dataclass."""

    def test_stored_issue_creation(self):
        """Test StoredIssue dataclass creation with all fields."""
        stored = StoredIssue(
            number=42,
            title="Test Issue",
            body="Issue body content",
            url="https://github.com/owner/repo/issues/42",
            labels=["ready", "bug"],
            stored_at="2024-01-15T10:30:00+00:00",
            status="pending"
        )
        self.assertEqual(stored.number, 42)
        self.assertEqual(stored.title, "Test Issue")
        self.assertEqual(stored.body, "Issue body content")
        self.assertEqual(stored.url, "https://github.com/owner/repo/issues/42")
        self.assertEqual(stored.labels, ["ready", "bug"])
        self.assertEqual(stored.stored_at, "2024-01-15T10:30:00+00:00")
        self.assertEqual(stored.status, "pending")

    def test_stored_issue_default_status(self):
        """Test StoredIssue default status is 'pending'."""
        stored = StoredIssue(
            number=1,
            title="Test",
            body=None,
            url="http://url",
            labels=[],
            stored_at="2024-01-15T10:30:00+00:00"
        )
        self.assertEqual(stored.status, "pending")

    def test_stored_issue_to_dict(self):
        """Test StoredIssue.to_dict() method."""
        stored = StoredIssue(
            number=10,
            title="Dict Test",
            body="Body text",
            url="https://github.com/owner/repo/issues/10",
            labels=["ready"],
            stored_at="2024-01-15T10:30:00+00:00",
            status="completed"
        )
        result = stored.to_dict()
        self.assertEqual(result, {
            "number": 10,
            "title": "Dict Test",
            "body": "Body text",
            "url": "https://github.com/owner/repo/issues/10",
            "labels": ["ready"],
            "stored_at": "2024-01-15T10:30:00+00:00",
            "status": "completed"
        })

    def test_stored_issue_from_dict(self):
        """Test StoredIssue.from_dict() class method."""
        data = {
            "number": 5,
            "title": "From Dict",
            "body": "Test body",
            "url": "http://url",
            "labels": ["bug"],
            "stored_at": "2024-01-15T10:30:00+00:00",
            "status": "processing"
        }
        stored = StoredIssue.from_dict(data)
        self.assertEqual(stored.number, 5)
        self.assertEqual(stored.title, "From Dict")
        self.assertEqual(stored.body, "Test body")
        self.assertEqual(stored.labels, ["bug"])
        self.assertEqual(stored.status, "processing")

    def test_stored_issue_from_dict_missing_optional_fields(self):
        """Test StoredIssue.from_dict() with missing optional fields."""
        data = {
            "number": 1,
            "title": "Test",
            "url": "http://url",
            "stored_at": "2024-01-15T10:30:00+00:00"
        }
        stored = StoredIssue.from_dict(data)
        self.assertIsNone(stored.body)
        self.assertEqual(stored.labels, [])
        self.assertEqual(stored.status, "pending")

    def test_stored_issue_from_issue(self):
        """Test StoredIssue.from_issue() class method."""
        issue = Issue(
            number=42,
            title="Test Issue",
            body="Body",
            url="http://url",
            labels=["ready"]
        )
        stored = StoredIssue.from_issue(issue)
        self.assertEqual(stored.number, 42)
        self.assertEqual(stored.title, "Test Issue")
        self.assertEqual(stored.body, "Body")
        self.assertEqual(stored.labels, ["ready"])
        self.assertEqual(stored.status, "pending")
        self.assertIsNotNone(stored.stored_at)

    def test_stored_issue_from_issue_custom_status(self):
        """Test StoredIssue.from_issue() with custom status."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            url="http://url",
            labels=[]
        )
        stored = StoredIssue.from_issue(issue, status="processing")
        self.assertEqual(stored.status, "processing")


class TestIssueStoreError(unittest.TestCase):
    """Tests for IssueStoreError exception."""

    def test_exception_message(self):
        """Test IssueStoreError stores message correctly."""
        error = IssueStoreError("Test message")
        self.assertEqual(str(error), "Test message")

    def test_exception_inheritance(self):
        """Test IssueStoreError inherits from Exception."""
        error = IssueStoreError("Test")
        self.assertIsInstance(error, Exception)


class TestIssueStore(unittest.TestCase):
    """Tests for IssueStore class."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        import shutil
        self.temp_dir = tempfile.mkdtemp()
        self.store_dir = f"{self.temp_dir}/issues_store"
        self.store = IssueStore(self.store_dir)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_issue(self, number: int, title: str = "Test") -> Issue:
        """Helper to create a test issue."""
        return Issue(
            number=number,
            title=title,
            body=f"Body for issue {number}",
            url=f"http://url/{number}",
            labels=["ready"]
        )

    def test_store_initialization(self):
        """Test IssueStore initialization."""
        import os
        store = IssueStore("/custom/path")
        # Use os.path.normpath to handle platform differences
        self.assertEqual(os.path.normpath(store.store_dir), os.path.normpath("/custom/path"))
        self.assertEqual(os.path.normpath(store.issues_dir), os.path.normpath("/custom/path/issues"))

    def test_store_default_path(self):
        """Test IssueStore default path."""
        import os
        store = IssueStore()
        self.assertEqual(os.path.normpath(store.store_dir), os.path.normpath(".ralph/issues"))

    def test_save_creates_directory(self):
        """Test save creates storage directory if it doesn't exist."""
        import os
        issue = self._create_issue(1)
        self.store.save(issue)
        self.assertTrue(os.path.exists(self.store.issues_dir))

    def test_save_creates_json_file(self):
        """Test save creates a JSON file for the issue."""
        import os
        issue = self._create_issue(42)
        self.store.save(issue)
        expected_path = os.path.join(self.store.issues_dir, "42.json")
        self.assertTrue(os.path.exists(expected_path))

    def test_save_returns_stored_issue(self):
        """Test save returns a StoredIssue."""
        issue = self._create_issue(1)
        stored = self.store.save(issue)
        self.assertIsInstance(stored, StoredIssue)
        self.assertEqual(stored.number, 1)
        self.assertEqual(stored.status, "pending")

    def test_save_with_custom_status(self):
        """Test save with custom status."""
        issue = self._create_issue(1)
        stored = self.store.save(issue, status="processing")
        self.assertEqual(stored.status, "processing")

    def test_save_overwrites_existing(self):
        """Test save overwrites existing issue."""
        issue1 = self._create_issue(1, title="Original")
        issue2 = self._create_issue(1, title="Updated")

        self.store.save(issue1)
        self.store.save(issue2)

        retrieved = self.store.get(1)
        self.assertEqual(retrieved.title, "Updated")

    def test_save_stored(self):
        """Test save_stored saves a StoredIssue."""
        issue = self._create_issue(1)
        stored = self.store.save(issue)

        # Modify and save again
        from dataclasses import replace
        updated = replace(stored, status="completed")
        self.store.save_stored(updated)

        retrieved = self.store.get(1)
        self.assertEqual(retrieved.status, "completed")

    def test_get_returns_stored_issue(self):
        """Test get returns the stored issue."""
        issue = self._create_issue(42)
        self.store.save(issue)

        retrieved = self.store.get(42)
        self.assertIsInstance(retrieved, StoredIssue)
        self.assertEqual(retrieved.number, 42)
        self.assertEqual(retrieved.title, "Test")

    def test_get_returns_none_for_missing(self):
        """Test get returns None for non-existent issue."""
        result = self.store.get(999)
        self.assertIsNone(result)

    def test_get_preserves_all_fields(self):
        """Test get preserves all issue fields."""
        issue = Issue(
            number=1,
            title="Full Test",
            body="Body content",
            url="http://example.com/1",
            labels=["ready", "bug", "high-priority"]
        )
        self.store.save(issue, status="processing")

        retrieved = self.store.get(1)
        self.assertEqual(retrieved.title, "Full Test")
        self.assertEqual(retrieved.body, "Body content")
        self.assertEqual(retrieved.url, "http://example.com/1")
        self.assertEqual(retrieved.labels, ["ready", "bug", "high-priority"])
        self.assertEqual(retrieved.status, "processing")

    def test_exists_returns_true_for_existing(self):
        """Test exists returns True for stored issue."""
        issue = self._create_issue(1)
        self.store.save(issue)
        self.assertTrue(self.store.exists(1))

    def test_exists_returns_false_for_missing(self):
        """Test exists returns False for non-existent issue."""
        self.assertFalse(self.store.exists(999))

    def test_delete_removes_issue(self):
        """Test delete removes the issue file."""
        issue = self._create_issue(1)
        self.store.save(issue)
        self.assertTrue(self.store.exists(1))

        result = self.store.delete(1)
        self.assertTrue(result)
        self.assertFalse(self.store.exists(1))

    def test_delete_returns_false_for_missing(self):
        """Test delete returns False for non-existent issue."""
        result = self.store.delete(999)
        self.assertFalse(result)

    def test_list_issues_empty_store(self):
        """Test list_issues returns empty list for empty store."""
        result = self.store.list_issues()
        self.assertEqual(result, [])

    def test_list_issues_returns_all_issues(self):
        """Test list_issues returns all stored issues."""
        for i in range(1, 4):
            self.store.save(self._create_issue(i))

        issues = self.store.list_issues()
        self.assertEqual(len(issues), 3)
        numbers = [i.number for i in issues]
        self.assertEqual(numbers, [1, 2, 3])

    def test_list_issues_sorted_by_number(self):
        """Test list_issues returns issues sorted by number."""
        # Save in random order
        for i in [5, 2, 8, 1, 3]:
            self.store.save(self._create_issue(i))

        issues = self.store.list_issues()
        numbers = [i.number for i in issues]
        self.assertEqual(numbers, [1, 2, 3, 5, 8])

    def test_list_issues_filter_by_status(self):
        """Test list_issues filters by status."""
        self.store.save(self._create_issue(1), status="pending")
        self.store.save(self._create_issue(2), status="processing")
        self.store.save(self._create_issue(3), status="completed")
        self.store.save(self._create_issue(4), status="pending")

        pending = self.store.list_issues(status="pending")
        self.assertEqual(len(pending), 2)
        self.assertEqual([i.number for i in pending], [1, 4])

        completed = self.store.list_issues(status="completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].number, 3)

    def test_list_issues_skips_malformed_files(self):
        """Test list_issues skips malformed JSON files."""
        import os

        # Create a valid issue
        self.store.save(self._create_issue(1))

        # Create a malformed JSON file
        malformed_path = os.path.join(self.store.issues_dir, "2.json")
        with open(malformed_path, 'w') as f:
            f.write("not valid json")

        # Should only return the valid issue
        issues = self.store.list_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].number, 1)

    def test_update_status(self):
        """Test update_status updates the issue status."""
        self.store.save(self._create_issue(1), status="pending")

        updated = self.store.update_status(1, "completed")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "completed")

        # Verify persisted
        retrieved = self.store.get(1)
        self.assertEqual(retrieved.status, "completed")

    def test_update_status_returns_none_for_missing(self):
        """Test update_status returns None for non-existent issue."""
        result = self.store.update_status(999, "completed")
        self.assertIsNone(result)

    def test_count_empty_store(self):
        """Test count returns 0 for empty store."""
        self.assertEqual(self.store.count(), 0)

    def test_count_all_issues(self):
        """Test count returns total number of issues."""
        for i in range(5):
            self.store.save(self._create_issue(i))
        self.assertEqual(self.store.count(), 5)

    def test_count_by_status(self):
        """Test count filters by status."""
        self.store.save(self._create_issue(1), status="pending")
        self.store.save(self._create_issue(2), status="pending")
        self.store.save(self._create_issue(3), status="completed")

        self.assertEqual(self.store.count(status="pending"), 2)
        self.assertEqual(self.store.count(status="completed"), 1)
        self.assertEqual(self.store.count(status="failed"), 0)

    def test_clear_removes_all_issues(self):
        """Test clear removes all stored issues."""
        for i in range(3):
            self.store.save(self._create_issue(i))
        self.assertEqual(self.store.count(), 3)

        count = self.store.clear()
        self.assertEqual(count, 3)
        self.assertEqual(self.store.count(), 0)

    def test_clear_returns_zero_for_empty_store(self):
        """Test clear returns 0 for empty store."""
        count = self.store.clear()
        self.assertEqual(count, 0)

    def test_save_batch(self):
        """Test save_batch saves multiple issues."""
        issues = [self._create_issue(i) for i in range(1, 4)]
        stored = self.store.save_batch(issues)

        self.assertEqual(len(stored), 3)
        self.assertEqual(self.store.count(), 3)

        for i, s in enumerate(stored, 1):
            self.assertEqual(s.number, i)
            self.assertEqual(s.status, "pending")

    def test_save_batch_with_custom_status(self):
        """Test save_batch with custom status."""
        issues = [self._create_issue(i) for i in range(1, 3)]
        stored = self.store.save_batch(issues, status="processing")

        for s in stored:
            self.assertEqual(s.status, "processing")

    def test_save_batch_empty_list(self):
        """Test save_batch with empty list."""
        stored = self.store.save_batch([])
        self.assertEqual(stored, [])
        self.assertEqual(self.store.count(), 0)

    def test_stored_issue_survives_restart(self):
        """Test stored issues survive creating a new IssueStore instance."""
        # Save with first store instance
        self.store.save(self._create_issue(42), status="processing")

        # Create new store instance pointing to same directory
        new_store = IssueStore(self.store_dir)

        # Issue should be retrievable
        retrieved = new_store.get(42)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.number, 42)
        self.assertEqual(retrieved.status, "processing")

    def test_json_file_format(self):
        """Test the JSON file format is human-readable."""
        import os

        issue = Issue(
            number=1,
            title="Test Issue",
            body="Test body",
            url="http://url",
            labels=["ready"]
        )
        self.store.save(issue)

        file_path = os.path.join(self.store.issues_dir, "1.json")
        with open(file_path, 'r') as f:
            content = f.read()

        # Should be indented JSON
        self.assertIn('"number": 1', content)
        self.assertIn('"title": "Test Issue"', content)
        self.assertIn('\n', content)  # Should have newlines (indented)


class TestIssueStoreIntegration(unittest.TestCase):
    """Integration tests for IssueStore with GitHubPoller workflow."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.store_dir = f"{self.temp_dir}/issues_store"
        self.store = IssueStore(self.store_dir)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_store_issues_from_poller_callback(self):
        """Test storing issues received from a poller callback."""
        mock_issues = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
            Issue(number=2, title="Second", body="Body", url="http://url/2", labels=["ready"]),
        ]

        # Simulate callback storing issues
        def on_new_issues(issues):
            self.store.save_batch(issues)

        on_new_issues(mock_issues)

        self.assertEqual(self.store.count(), 2)
        self.assertIsNotNone(self.store.get(1))
        self.assertIsNotNone(self.store.get(2))

    def test_track_processing_status(self):
        """Test tracking issue processing status through workflow."""
        issue = Issue(number=1, title="Test", body="Body", url="http://url", labels=["ready"])

        # Issue arrives - save as pending
        self.store.save(issue, status="pending")
        self.assertEqual(self.store.get(1).status, "pending")

        # Start processing
        self.store.update_status(1, "processing")
        self.assertEqual(self.store.get(1).status, "processing")

        # Processing complete
        self.store.update_status(1, "completed")
        self.assertEqual(self.store.get(1).status, "completed")

    def test_resume_after_restart(self):
        """Test resuming incomplete processing after restart."""
        # Save issues with various statuses
        for i, status in [(1, "completed"), (2, "processing"), (3, "pending")]:
            issue = Issue(number=i, title=f"Issue {i}", body="Body", url="http://url", labels=[])
            self.store.save(issue, status=status)

        # Simulate restart - create new store instance
        new_store = IssueStore(self.store_dir)

        # Find incomplete issues to resume
        pending = new_store.list_issues(status="pending")
        processing = new_store.list_issues(status="processing")

        # Issues needing attention
        incomplete = pending + processing
        self.assertEqual(len(incomplete), 2)
        incomplete_numbers = {i.number for i in incomplete}
        self.assertEqual(incomplete_numbers, {2, 3})


class TestQueueItem(unittest.TestCase):
    """Tests for QueueItem dataclass."""

    def test_queue_item_creation(self):
        """Test QueueItem dataclass creation with all fields."""
        item = QueueItem(
            issue_number=42,
            priority=1,
            added_at="2024-01-15T10:30:00+00:00",
            status="pending",
            started_at=None,
            completed_at=None,
            error=None,
            retry_count=0
        )
        self.assertEqual(item.issue_number, 42)
        self.assertEqual(item.priority, 1)
        self.assertEqual(item.added_at, "2024-01-15T10:30:00+00:00")
        self.assertEqual(item.status, "pending")
        self.assertIsNone(item.started_at)
        self.assertIsNone(item.completed_at)
        self.assertIsNone(item.error)
        self.assertEqual(item.retry_count, 0)

    def test_queue_item_default_values(self):
        """Test QueueItem default values."""
        item = QueueItem(issue_number=1)
        self.assertEqual(item.issue_number, 1)
        self.assertEqual(item.priority, 0)
        self.assertIsNotNone(item.added_at)  # Auto-generated
        self.assertEqual(item.status, "pending")
        self.assertIsNone(item.started_at)
        self.assertIsNone(item.completed_at)
        self.assertIsNone(item.error)
        self.assertEqual(item.retry_count, 0)

    def test_queue_item_auto_timestamp(self):
        """Test QueueItem auto-generates timestamp if not provided."""
        item = QueueItem(issue_number=1)
        self.assertIsNotNone(item.added_at)
        self.assertIn("T", item.added_at)  # ISO format

    def test_queue_item_to_dict(self):
        """Test QueueItem.to_dict() method."""
        item = QueueItem(
            issue_number=10,
            priority=2,
            added_at="2024-01-15T10:30:00+00:00",
            status="completed",
            started_at="2024-01-15T10:31:00+00:00",
            completed_at="2024-01-15T10:32:00+00:00",
            error=None,
            retry_count=1
        )
        result = item.to_dict()
        self.assertEqual(result, {
            "issue_number": 10,
            "priority": 2,
            "added_at": "2024-01-15T10:30:00+00:00",
            "status": "completed",
            "started_at": "2024-01-15T10:31:00+00:00",
            "completed_at": "2024-01-15T10:32:00+00:00",
            "error": None,
            "retry_count": 1
        })

    def test_queue_item_from_dict(self):
        """Test QueueItem.from_dict() class method."""
        data = {
            "issue_number": 5,
            "priority": 1,
            "added_at": "2024-01-15T10:30:00+00:00",
            "status": "processing",
            "started_at": "2024-01-15T10:31:00+00:00",
            "completed_at": None,
            "error": None,
            "retry_count": 0
        }
        item = QueueItem.from_dict(data)
        self.assertEqual(item.issue_number, 5)
        self.assertEqual(item.priority, 1)
        self.assertEqual(item.status, "processing")
        self.assertEqual(item.started_at, "2024-01-15T10:31:00+00:00")

    def test_queue_item_from_dict_missing_optional_fields(self):
        """Test QueueItem.from_dict() with missing optional fields."""
        data = {
            "issue_number": 1,
        }
        item = QueueItem.from_dict(data)
        self.assertEqual(item.issue_number, 1)
        self.assertEqual(item.priority, 0)
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.retry_count, 0)

    def test_queue_item_with_error(self):
        """Test QueueItem with error message."""
        item = QueueItem(
            issue_number=1,
            status="failed",
            error="Processing failed due to timeout"
        )
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.error, "Processing failed due to timeout")


class TestProcessingQueueError(unittest.TestCase):
    """Tests for ProcessingQueueError exception."""

    def test_exception_message(self):
        """Test ProcessingQueueError stores message correctly."""
        error = ProcessingQueueError("Test message")
        self.assertEqual(str(error), "Test message")

    def test_exception_inheritance(self):
        """Test ProcessingQueueError inherits from Exception."""
        error = ProcessingQueueError("Test")
        self.assertIsInstance(error, Exception)


class TestProcessingQueue(unittest.TestCase):
    """Tests for ProcessingQueue class."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        import shutil
        self.temp_dir = tempfile.mkdtemp()
        self.queue_dir = f"{self.temp_dir}/queue"
        self.queue = ProcessingQueue(self.queue_dir)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_queue_initialization(self):
        """Test ProcessingQueue initialization."""
        import os
        queue = ProcessingQueue("/custom/path")
        self.assertEqual(os.path.normpath(queue.queue_dir), os.path.normpath("/custom/path"))

    def test_queue_default_path(self):
        """Test ProcessingQueue default path."""
        import os
        queue = ProcessingQueue()
        self.assertEqual(os.path.normpath(queue.queue_dir), os.path.normpath(".ralph/queue"))

    def test_enqueue_creates_directory(self):
        """Test enqueue creates queue directory if it doesn't exist."""
        import os
        self.queue.enqueue(1)
        self.assertTrue(os.path.exists(self.queue.queue_dir))

    def test_enqueue_returns_queue_item(self):
        """Test enqueue returns a QueueItem."""
        item = self.queue.enqueue(42)
        self.assertIsInstance(item, QueueItem)
        self.assertEqual(item.issue_number, 42)
        self.assertEqual(item.status, "pending")

    def test_enqueue_with_priority(self):
        """Test enqueue with custom priority."""
        item = self.queue.enqueue(1, priority=5)
        self.assertEqual(item.priority, 5)

    def test_enqueue_idempotent_for_pending(self):
        """Test enqueue is idempotent for pending issues."""
        item1 = self.queue.enqueue(1)
        item2 = self.queue.enqueue(1)
        self.assertEqual(item1.issue_number, item2.issue_number)
        self.assertEqual(self.queue.count(), 1)

    def test_enqueue_idempotent_for_processing(self):
        """Test enqueue is idempotent for processing issues."""
        self.queue.enqueue(1)
        self.queue.dequeue()  # Mark as processing
        item = self.queue.enqueue(1)  # Try to enqueue again
        self.assertEqual(item.status, "processing")
        self.assertEqual(self.queue.count(), 1)

    def test_enqueue_allows_requeue_after_completed(self):
        """Test enqueue allows re-adding completed issues."""
        self.queue.enqueue(1)
        self.queue.dequeue()
        self.queue.mark_completed(1)

        item = self.queue.enqueue(1)  # Re-enqueue
        self.assertEqual(item.status, "pending")
        self.assertEqual(self.queue.count(), 2)  # Both items exist

    def test_dequeue_returns_pending_item(self):
        """Test dequeue returns a pending item."""
        self.queue.enqueue(1)
        item = self.queue.dequeue()
        self.assertIsNotNone(item)
        self.assertEqual(item.issue_number, 1)
        self.assertEqual(item.status, "processing")

    def test_dequeue_sets_started_at(self):
        """Test dequeue sets started_at timestamp."""
        self.queue.enqueue(1)
        item = self.queue.dequeue()
        self.assertIsNotNone(item.started_at)

    def test_dequeue_returns_none_when_empty(self):
        """Test dequeue returns None when queue is empty."""
        item = self.queue.dequeue()
        self.assertIsNone(item)

    def test_dequeue_returns_none_when_all_processing(self):
        """Test dequeue returns None when all items are processing."""
        self.queue.enqueue(1)
        self.queue.dequeue()  # Mark as processing
        item = self.queue.dequeue()  # Try again
        self.assertIsNone(item)

    def test_dequeue_priority_order(self):
        """Test dequeue returns items in priority order."""
        self.queue.enqueue(1, priority=10)
        self.queue.enqueue(2, priority=5)
        self.queue.enqueue(3, priority=15)

        item1 = self.queue.dequeue()
        self.assertEqual(item1.issue_number, 2)  # Priority 5 first

        item2 = self.queue.dequeue()
        self.assertEqual(item2.issue_number, 1)  # Priority 10 second

        item3 = self.queue.dequeue()
        self.assertEqual(item3.issue_number, 3)  # Priority 15 last

    def test_dequeue_fifo_same_priority(self):
        """Test dequeue returns items in FIFO order for same priority."""
        import time
        self.queue.enqueue(1, priority=0)
        time.sleep(0.01)  # Ensure different timestamps
        self.queue.enqueue(2, priority=0)
        time.sleep(0.01)
        self.queue.enqueue(3, priority=0)

        item1 = self.queue.dequeue()
        self.assertEqual(item1.issue_number, 1)  # First added

        item2 = self.queue.dequeue()
        self.assertEqual(item2.issue_number, 2)  # Second added

        item3 = self.queue.dequeue()
        self.assertEqual(item3.issue_number, 3)  # Third added

    def test_peek_returns_next_item(self):
        """Test peek returns next pending item without changing state."""
        self.queue.enqueue(1)
        item = self.queue.peek()
        self.assertIsNotNone(item)
        self.assertEqual(item.issue_number, 1)
        self.assertEqual(item.status, "pending")

    def test_peek_returns_none_when_empty(self):
        """Test peek returns None when queue is empty."""
        item = self.queue.peek()
        self.assertIsNone(item)

    def test_peek_does_not_change_state(self):
        """Test peek does not modify item state."""
        self.queue.enqueue(1)
        self.queue.peek()
        item = self.queue.get(1)
        self.assertEqual(item.status, "pending")

    def test_mark_completed(self):
        """Test mark_completed updates item status."""
        self.queue.enqueue(1)
        self.queue.dequeue()
        item = self.queue.mark_completed(1)
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "completed")
        self.assertIsNotNone(item.completed_at)

    def test_mark_completed_returns_none_for_missing(self):
        """Test mark_completed returns None for non-existent item."""
        item = self.queue.mark_completed(999)
        self.assertIsNone(item)

    def test_mark_failed(self):
        """Test mark_failed updates item status."""
        self.queue.enqueue(1)
        self.queue.dequeue()
        item = self.queue.mark_failed(1, error="Test error")
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.error, "Test error")
        self.assertIsNotNone(item.completed_at)

    def test_mark_failed_without_error(self):
        """Test mark_failed without error message."""
        self.queue.enqueue(1)
        self.queue.dequeue()
        item = self.queue.mark_failed(1)
        self.assertEqual(item.status, "failed")
        self.assertIsNone(item.error)

    def test_mark_failed_returns_none_for_missing(self):
        """Test mark_failed returns None for non-existent item."""
        item = self.queue.mark_failed(999)
        self.assertIsNone(item)

    def test_retry(self):
        """Test retry resets item to pending."""
        self.queue.enqueue(1)
        self.queue.dequeue()
        self.queue.mark_failed(1)

        item = self.queue.retry(1)
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "pending")
        self.assertIsNone(item.started_at)
        self.assertIsNone(item.completed_at)
        self.assertIsNone(item.error)
        self.assertEqual(item.retry_count, 1)

    def test_retry_increments_count(self):
        """Test retry increments retry_count."""
        self.queue.enqueue(1)

        # First retry
        self.queue.dequeue()
        self.queue.mark_failed(1)
        item = self.queue.retry(1)
        self.assertEqual(item.retry_count, 1)

        # Second retry
        self.queue.dequeue()
        self.queue.mark_failed(1)
        item = self.queue.retry(1)
        self.assertEqual(item.retry_count, 2)

    def test_retry_returns_none_for_missing(self):
        """Test retry returns None for non-existent item."""
        item = self.queue.retry(999)
        self.assertIsNone(item)

    def test_get(self):
        """Test get returns item by issue number."""
        self.queue.enqueue(42)
        item = self.queue.get(42)
        self.assertIsNotNone(item)
        self.assertEqual(item.issue_number, 42)

    def test_get_returns_none_for_missing(self):
        """Test get returns None for non-existent item."""
        item = self.queue.get(999)
        self.assertIsNone(item)

    def test_remove(self):
        """Test remove deletes item from queue."""
        self.queue.enqueue(1)
        self.assertTrue(self.queue.remove(1))
        self.assertIsNone(self.queue.get(1))
        self.assertEqual(self.queue.count(), 0)

    def test_remove_returns_false_for_missing(self):
        """Test remove returns False for non-existent item."""
        self.assertFalse(self.queue.remove(999))

    def test_list_items(self):
        """Test list_items returns all items."""
        self.queue.enqueue(1)
        self.queue.enqueue(2)
        self.queue.enqueue(3)

        items = self.queue.list_items()
        self.assertEqual(len(items), 3)
        numbers = [i.issue_number for i in items]
        self.assertEqual(set(numbers), {1, 2, 3})

    def test_list_items_filter_by_status(self):
        """Test list_items filters by status."""
        self.queue.enqueue(1)
        self.queue.enqueue(2)
        self.queue.dequeue()  # 1 is now processing
        self.queue.mark_completed(1)
        self.queue.dequeue()  # 2 is now processing
        self.queue.enqueue(3)  # Still pending

        pending = self.queue.list_items(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].issue_number, 3)

        processing = self.queue.list_items(status="processing")
        self.assertEqual(len(processing), 1)
        self.assertEqual(processing[0].issue_number, 2)

        completed = self.queue.list_items(status="completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].issue_number, 1)

    def test_count(self):
        """Test count returns total item count."""
        self.assertEqual(self.queue.count(), 0)
        self.queue.enqueue(1)
        self.assertEqual(self.queue.count(), 1)
        self.queue.enqueue(2)
        self.assertEqual(self.queue.count(), 2)

    def test_count_by_status(self):
        """Test count filters by status."""
        self.queue.enqueue(1)
        self.queue.enqueue(2)
        self.queue.dequeue()

        self.assertEqual(self.queue.count(status="pending"), 1)
        self.assertEqual(self.queue.count(status="processing"), 1)
        self.assertEqual(self.queue.count(status="completed"), 0)

    def test_clear(self):
        """Test clear removes all items."""
        self.queue.enqueue(1)
        self.queue.enqueue(2)

        count = self.queue.clear()
        self.assertEqual(count, 2)
        self.assertEqual(self.queue.count(), 0)

    def test_clear_by_status(self):
        """Test clear removes only items with specified status."""
        self.queue.enqueue(1)
        self.queue.enqueue(2)
        self.queue.dequeue()  # 1 is processing
        self.queue.mark_completed(1)

        count = self.queue.clear(status="completed")
        self.assertEqual(count, 1)
        self.assertEqual(self.queue.count(), 1)  # 2 (pending) remains

    def test_clear_empty_queue(self):
        """Test clear returns 0 for empty queue."""
        count = self.queue.clear()
        self.assertEqual(count, 0)

    def test_is_empty(self):
        """Test is_empty checks for pending items."""
        self.assertTrue(self.queue.is_empty())

        self.queue.enqueue(1)
        self.assertFalse(self.queue.is_empty())

        self.queue.dequeue()  # Now processing
        self.assertTrue(self.queue.is_empty())  # No pending

    def test_has_processing(self):
        """Test has_processing checks for processing items."""
        self.assertFalse(self.queue.has_processing())

        self.queue.enqueue(1)
        self.assertFalse(self.queue.has_processing())

        self.queue.dequeue()
        self.assertTrue(self.queue.has_processing())

        self.queue.mark_completed(1)
        self.assertFalse(self.queue.has_processing())

    def test_reset_processing(self):
        """Test reset_processing resets processing items to pending."""
        self.queue.enqueue(1)
        self.queue.enqueue(2)
        self.queue.dequeue()  # 1 is processing
        self.queue.dequeue()  # 2 is processing

        count = self.queue.reset_processing()
        self.assertEqual(count, 2)
        self.assertEqual(self.queue.count(status="pending"), 2)
        self.assertEqual(self.queue.count(status="processing"), 0)

    def test_reset_processing_clears_started_at(self):
        """Test reset_processing clears started_at timestamp."""
        self.queue.enqueue(1)
        self.queue.dequeue()

        self.queue.reset_processing()
        item = self.queue.get(1)
        self.assertIsNone(item.started_at)

    def test_reset_processing_empty_queue(self):
        """Test reset_processing returns 0 for queue with no processing items."""
        self.queue.enqueue(1)  # pending only
        count = self.queue.reset_processing()
        self.assertEqual(count, 0)

    def test_enqueue_batch(self):
        """Test enqueue_batch adds multiple items."""
        items = self.queue.enqueue_batch([1, 2, 3])
        self.assertEqual(len(items), 3)
        self.assertEqual(self.queue.count(), 3)

    def test_enqueue_batch_with_priority(self):
        """Test enqueue_batch with custom priority."""
        items = self.queue.enqueue_batch([1, 2], priority=5)
        for item in items:
            self.assertEqual(item.priority, 5)

    def test_enqueue_batch_idempotent(self):
        """Test enqueue_batch is idempotent for existing pending items."""
        self.queue.enqueue(1)
        items = self.queue.enqueue_batch([1, 2, 3])
        self.assertEqual(len(items), 3)
        self.assertEqual(self.queue.count(), 3)  # 1 existed, 2 and 3 new

    def test_enqueue_batch_empty_list(self):
        """Test enqueue_batch with empty list."""
        items = self.queue.enqueue_batch([])
        self.assertEqual(items, [])
        self.assertEqual(self.queue.count(), 0)

    def test_persistence(self):
        """Test queue state persists across instances."""
        self.queue.enqueue(1, priority=5)
        self.queue.enqueue(2, priority=0)
        self.queue.dequeue()  # 2 is processing (lower priority number = higher priority)

        # Create new queue instance
        new_queue = ProcessingQueue(self.queue_dir)

        self.assertEqual(new_queue.count(), 2)
        self.assertEqual(new_queue.count(status="pending"), 1)
        self.assertEqual(new_queue.count(status="processing"), 1)

        item = new_queue.get(2)
        self.assertEqual(item.status, "processing")

    def test_persistence_file_format(self):
        """Test queue persists as readable JSON."""
        import os

        self.queue.enqueue(1)
        self.queue.enqueue(2)

        file_path = os.path.join(self.queue_dir, "queue.json")
        self.assertTrue(os.path.exists(file_path))

        with open(file_path, 'r') as f:
            content = f.read()

        # Should be indented JSON
        self.assertIn('"items"', content)
        self.assertIn('\n', content)

    def test_thread_safety(self):
        """Test queue operations are thread-safe."""
        import threading

        errors = []

        def enqueue_items():
            try:
                for i in range(100):
                    self.queue.enqueue(i)
            except Exception as e:
                errors.append(e)

        def dequeue_items():
            try:
                for _ in range(50):
                    self.queue.dequeue()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=enqueue_items),
            threading.Thread(target=enqueue_items),
            threading.Thread(target=dequeue_items),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)


class TestProcessingQueueIntegration(unittest.TestCase):
    """Integration tests for ProcessingQueue workflow."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.queue_dir = f"{self.temp_dir}/queue"
        self.store_dir = f"{self.temp_dir}/issues"
        self.queue = ProcessingQueue(self.queue_dir)
        self.store = IssueStore(self.store_dir)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_processing_workflow(self):
        """Test complete issue processing workflow."""
        # 1. Issues arrive and get stored + queued
        issues = [
            Issue(number=1, title="First", body="Body", url="http://url/1", labels=["ready"]),
            Issue(number=2, title="Second", body="Body", url="http://url/2", labels=["ready"]),
        ]

        for issue in issues:
            self.store.save(issue)
            self.queue.enqueue(issue.number)

        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.queue.count(), 2)

        # 2. Process first item
        item = self.queue.dequeue()
        self.assertEqual(item.issue_number, 1)
        self.store.update_status(1, "processing")

        # 3. First item completes
        self.queue.mark_completed(1)
        self.store.update_status(1, "completed")

        # 4. Process second item
        item = self.queue.dequeue()
        self.assertEqual(item.issue_number, 2)
        self.store.update_status(2, "processing")

        # 5. Second item fails
        self.queue.mark_failed(2, error="Timeout")
        self.store.update_status(2, "failed")

        # 6. Verify final state
        self.assertEqual(self.queue.count(status="completed"), 1)
        self.assertEqual(self.queue.count(status="failed"), 1)
        self.assertEqual(self.store.get(1).status, "completed")
        self.assertEqual(self.store.get(2).status, "failed")

    def test_recovery_after_crash(self):
        """Test recovering from a simulated crash during processing."""
        # Setup: items in various states
        self.queue.enqueue(1)
        self.queue.enqueue(2)
        self.queue.enqueue(3)
        self.queue.dequeue()  # 1 is processing
        self.queue.dequeue()  # 2 is processing

        # Simulate crash - create new queue instance
        new_queue = ProcessingQueue(self.queue_dir)

        # Recovery: reset all processing items
        reset_count = new_queue.reset_processing()
        self.assertEqual(reset_count, 2)

        # All items should be pending again
        self.assertEqual(new_queue.count(status="pending"), 3)
        self.assertEqual(new_queue.count(status="processing"), 0)

        # Can resume processing
        item = new_queue.dequeue()
        self.assertIsNotNone(item)

    def test_retry_failed_items(self):
        """Test retrying failed items."""
        self.queue.enqueue(1)
        self.queue.dequeue()
        self.queue.mark_failed(1, error="First attempt failed")

        # Verify failed state
        item = self.queue.get(1)
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.retry_count, 0)

        # Retry
        self.queue.retry(1)
        item = self.queue.get(1)
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.retry_count, 1)

        # Process again
        item = self.queue.dequeue()
        self.assertEqual(item.issue_number, 1)
        self.assertEqual(item.status, "processing")

    def test_priority_processing(self):
        """Test high-priority items are processed first."""
        # Add items with different priorities
        self.queue.enqueue(1, priority=10)  # Low priority
        self.queue.enqueue(2, priority=1)   # High priority
        self.queue.enqueue(3, priority=5)   # Medium priority

        # Should process in priority order
        item1 = self.queue.dequeue()
        self.assertEqual(item1.issue_number, 2)

        item2 = self.queue.dequeue()
        self.assertEqual(item2.issue_number, 3)

        item3 = self.queue.dequeue()
        self.assertEqual(item3.issue_number, 1)


class TestPromptTransformerError(unittest.TestCase):
    """Tests for PromptTransformerError exception."""

    def test_exception_message(self):
        """Test PromptTransformerError stores message correctly."""
        error = PromptTransformerError("Test message")
        self.assertEqual(str(error), "Test message")

    def test_exception_inheritance(self):
        """Test PromptTransformerError inherits from Exception."""
        error = PromptTransformerError("Test")
        self.assertIsInstance(error, Exception)


class TestTransformedPrompt(unittest.TestCase):
    """Tests for TransformedPrompt dataclass."""

    def test_creation_with_defaults(self):
        """Test TransformedPrompt creation with default priority."""
        prompt = TransformedPrompt(
            issue_number=42,
            prompt="TASK-042: Test\n\nDescription:\nBody"
        )
        self.assertEqual(prompt.issue_number, 42)
        self.assertEqual(prompt.prompt, "TASK-042: Test\n\nDescription:\nBody")
        self.assertEqual(prompt.priority, 0)

    def test_creation_with_priority(self):
        """Test TransformedPrompt creation with custom priority."""
        prompt = TransformedPrompt(
            issue_number=10,
            prompt="TASK-010: Feature\n\nDescription:\nDetails",
            priority=5
        )
        self.assertEqual(prompt.issue_number, 10)
        self.assertEqual(prompt.priority, 5)

    def test_prompt_content(self):
        """Test TransformedPrompt stores prompt content correctly."""
        content = "TASK-001: Add login\n\nDescription:\nImplement OAuth"
        prompt = TransformedPrompt(issue_number=1, prompt=content)
        self.assertEqual(prompt.prompt, content)


class TestPromptTransformer(unittest.TestCase):
    """Tests for PromptTransformer class."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        import shutil
        self.temp_dir = tempfile.mkdtemp()
        self.store_dir = f"{self.temp_dir}/store"
        self.queue_dir = f"{self.temp_dir}/queue"
        self.store = IssueStore(self.store_dir)
        self.queue = ProcessingQueue(self.queue_dir)
        self.transformer = PromptTransformer(self.queue, self.store)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_issue(self, number, title="Test Issue", body="Test body"):
        """Helper to create an Issue."""
        return Issue(
            number=number,
            title=title,
            body=body,
            url=f"https://github.com/owner/repo/issues/{number}",
            labels=["ready"]
        )

    def test_properties(self):
        """Test queue and store properties."""
        self.assertIs(self.transformer.queue, self.queue)
        self.assertIs(self.transformer.store, self.store)

    def test_transform_single_issue(self):
        """Test transforming a single issue to prompt."""
        issue = self._create_issue(42, "Add feature", "Feature description")
        self.store.save(issue)
        self.queue.enqueue(42, priority=5)

        result = self.transformer.transform(42)

        self.assertIsInstance(result, TransformedPrompt)
        self.assertEqual(result.issue_number, 42)
        self.assertIn("TASK-042", result.prompt)
        self.assertIn("Add feature", result.prompt)
        self.assertIn("Feature description", result.prompt)
        self.assertEqual(result.priority, 5)

    def test_transform_issue_not_in_store(self):
        """Test transform raises error when issue not in store."""
        self.queue.enqueue(999)

        with self.assertRaises(PromptTransformerError) as ctx:
            self.transformer.transform(999)

        self.assertIn("999", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_transform_issue_not_in_queue(self):
        """Test transform works for issue in store but not in queue."""
        issue = self._create_issue(50, "Test", "Body")
        self.store.save(issue)

        result = self.transformer.transform(50)

        self.assertEqual(result.issue_number, 50)
        self.assertEqual(result.priority, 0)  # Default priority

    def test_transform_preserves_none_body(self):
        """Test transform handles None body correctly."""
        issue = Issue(
            number=1,
            title="No body",
            body=None,
            url="http://url",
            labels=[]
        )
        self.store.save(issue)

        result = self.transformer.transform(1)

        self.assertIn("No description provided.", result.prompt)

    def test_transform_batch(self):
        """Test transforming multiple issues."""
        for i in range(1, 4):
            issue = self._create_issue(i, f"Issue {i}", f"Body {i}")
            self.store.save(issue)
            self.queue.enqueue(i)

        results = self.transformer.transform_batch([1, 2, 3])

        self.assertEqual(len(results), 3)
        for i, result in enumerate(results, 1):
            self.assertEqual(result.issue_number, i)
            self.assertIn(f"TASK-00{i}", result.prompt)

    def test_transform_batch_skips_missing(self):
        """Test transform_batch skips issues not in store."""
        issue = self._create_issue(1, "Test", "Body")
        self.store.save(issue)

        results = self.transformer.transform_batch([1, 999, 2])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].issue_number, 1)

    def test_transform_batch_empty_list(self):
        """Test transform_batch with empty list."""
        results = self.transformer.transform_batch([])
        self.assertEqual(results, [])

    def test_get_pending_prompts(self):
        """Test getting all pending prompts."""
        for i in range(1, 4):
            issue = self._create_issue(i, f"Issue {i}")
            self.store.save(issue)
            self.queue.enqueue(i, priority=3 - i)  # Priority: 2, 1, 0

        prompts = self.transformer.get_pending_prompts()

        self.assertEqual(len(prompts), 3)
        # Should be ordered by priority (lower = higher priority)
        self.assertEqual(prompts[0].issue_number, 3)  # priority 0
        self.assertEqual(prompts[1].issue_number, 2)  # priority 1
        self.assertEqual(prompts[2].issue_number, 1)  # priority 2

    def test_get_pending_prompts_empty_queue(self):
        """Test get_pending_prompts with empty queue."""
        prompts = self.transformer.get_pending_prompts()
        self.assertEqual(prompts, [])

    def test_get_pending_prompts_skips_non_pending(self):
        """Test get_pending_prompts only returns pending items."""
        for i in range(1, 4):
            issue = self._create_issue(i)
            self.store.save(issue)
            self.queue.enqueue(i)

        # Mark some as non-pending
        self.queue.dequeue()  # Marks first as processing
        self.queue.mark_completed(1)

        prompts = self.transformer.get_pending_prompts()

        self.assertEqual(len(prompts), 2)
        numbers = [p.issue_number for p in prompts]
        self.assertNotIn(1, numbers)

    def test_get_next_prompt(self):
        """Test getting and dequeuing next prompt."""
        issue = self._create_issue(42, "Test Issue")
        self.store.save(issue)
        self.queue.enqueue(42)

        result = self.transformer.get_next_prompt()

        self.assertIsInstance(result, TransformedPrompt)
        self.assertEqual(result.issue_number, 42)
        # Item should now be marked as processing
        item = self.queue.get(42)
        self.assertEqual(item.status, "processing")

    def test_get_next_prompt_empty_queue(self):
        """Test get_next_prompt returns None for empty queue."""
        result = self.transformer.get_next_prompt()
        self.assertIsNone(result)

    def test_get_next_prompt_issue_not_in_store(self):
        """Test get_next_prompt marks as failed when issue not in store."""
        self.queue.enqueue(999)

        with self.assertRaises(PromptTransformerError):
            self.transformer.get_next_prompt()

        # Issue should be marked as failed
        item = self.queue.get(999)
        self.assertEqual(item.status, "failed")
        self.assertIn("not found", item.error)

    def test_get_next_prompt_respects_priority(self):
        """Test get_next_prompt returns highest priority item."""
        for i, priority in [(1, 10), (2, 1), (3, 5)]:
            issue = self._create_issue(i)
            self.store.save(issue)
            self.queue.enqueue(i, priority=priority)

        result = self.transformer.get_next_prompt()

        self.assertEqual(result.issue_number, 2)  # Priority 1 (highest)

    def test_peek_next_prompt(self):
        """Test peeking at next prompt without dequeuing."""
        issue = self._create_issue(42)
        self.store.save(issue)
        self.queue.enqueue(42)

        result = self.transformer.peek_next_prompt()

        self.assertIsInstance(result, TransformedPrompt)
        self.assertEqual(result.issue_number, 42)
        # Item should still be pending
        item = self.queue.get(42)
        self.assertEqual(item.status, "pending")

    def test_peek_next_prompt_empty_queue(self):
        """Test peek_next_prompt returns None for empty queue."""
        result = self.transformer.peek_next_prompt()
        self.assertIsNone(result)

    def test_peek_next_prompt_issue_not_in_store(self):
        """Test peek_next_prompt returns None when issue not in store."""
        self.queue.enqueue(999)

        result = self.transformer.peek_next_prompt()

        self.assertIsNone(result)
        # Item should still be pending (not marked as failed)
        item = self.queue.get(999)
        self.assertEqual(item.status, "pending")

    def test_count_pending(self):
        """Test counting pending items."""
        for i in range(1, 5):
            issue = self._create_issue(i)
            self.store.save(issue)
            self.queue.enqueue(i)

        self.assertEqual(self.transformer.count_pending(), 4)

        self.queue.dequeue()  # Mark one as processing
        self.assertEqual(self.transformer.count_pending(), 3)

    def test_count_pending_empty(self):
        """Test count_pending returns 0 for empty queue."""
        self.assertEqual(self.transformer.count_pending(), 0)

    def test_count_transformable(self):
        """Test counting transformable items."""
        # Add 3 issues to store and queue
        for i in range(1, 4):
            issue = self._create_issue(i)
            self.store.save(issue)
            self.queue.enqueue(i)

        # Add 2 issues only to queue (not in store)
        self.queue.enqueue(100)
        self.queue.enqueue(101)

        self.assertEqual(self.transformer.count_pending(), 5)
        self.assertEqual(self.transformer.count_transformable(), 3)

    def test_count_transformable_excludes_non_pending(self):
        """Test count_transformable only counts pending items."""
        for i in range(1, 4):
            issue = self._create_issue(i)
            self.store.save(issue)
            self.queue.enqueue(i)

        self.queue.dequeue()  # Mark as processing

        self.assertEqual(self.transformer.count_transformable(), 2)

    def test_mark_completed(self):
        """Test marking issue as completed."""
        issue = self._create_issue(42)
        self.store.save(issue)
        self.queue.enqueue(42)
        self.queue.dequeue()  # Mark as processing

        result = self.transformer.mark_completed(42)

        self.assertTrue(result)
        item = self.queue.get(42)
        self.assertEqual(item.status, "completed")

    def test_mark_completed_not_found(self):
        """Test mark_completed returns False for unknown issue."""
        result = self.transformer.mark_completed(999)
        self.assertFalse(result)

    def test_mark_failed(self):
        """Test marking issue as failed."""
        issue = self._create_issue(42)
        self.store.save(issue)
        self.queue.enqueue(42)
        self.queue.dequeue()

        result = self.transformer.mark_failed(42, error="Test error")

        self.assertTrue(result)
        item = self.queue.get(42)
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.error, "Test error")

    def test_mark_failed_not_found(self):
        """Test mark_failed returns False for unknown issue."""
        result = self.transformer.mark_failed(999)
        self.assertFalse(result)

    def test_mark_failed_without_error_message(self):
        """Test marking issue as failed without error message."""
        issue = self._create_issue(42)
        self.store.save(issue)
        self.queue.enqueue(42)
        self.queue.dequeue()

        result = self.transformer.mark_failed(42)

        self.assertTrue(result)
        item = self.queue.get(42)
        self.assertEqual(item.status, "failed")
        self.assertIsNone(item.error)


class TestPromptTransformerIntegration(unittest.TestCase):
    """Integration tests for PromptTransformer with real queue and store."""

    def setUp(self):
        """Set up test fixtures."""
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.store_dir = f"{self.temp_dir}/store"
        self.queue_dir = f"{self.temp_dir}/queue"
        self.store = IssueStore(self.store_dir)
        self.queue = ProcessingQueue(self.queue_dir)
        self.transformer = PromptTransformer(self.queue, self.store)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_issue(self, number, title="Test", body="Body"):
        """Helper to create an Issue."""
        return Issue(
            number=number,
            title=title,
            body=body,
            url=f"http://url/{number}",
            labels=["ready"]
        )

    def test_full_processing_workflow(self):
        """Test complete workflow from queue to completion."""
        # Store and queue issues
        for i in range(1, 4):
            issue = self._create_issue(i, f"Task {i}", f"Description {i}")
            self.store.save(issue)
            self.queue.enqueue(i)

        # Process all issues
        processed = []
        while True:
            prompt = self.transformer.get_next_prompt()
            if prompt is None:
                break
            processed.append(prompt)
            self.transformer.mark_completed(prompt.issue_number)

        self.assertEqual(len(processed), 3)
        for p in processed:
            item = self.queue.get(p.issue_number)
            self.assertEqual(item.status, "completed")

    def test_retry_failed_workflow(self):
        """Test workflow with retry after failure."""
        issue = self._create_issue(42, "Feature", "Add new feature")
        self.store.save(issue)
        self.queue.enqueue(42)

        # Get and fail the prompt
        prompt = self.transformer.get_next_prompt()
        self.transformer.mark_failed(42, error="First attempt failed")

        # Verify failed status
        item = self.queue.get(42)
        self.assertEqual(item.status, "failed")

        # Retry
        self.queue.retry(42)

        # Process again
        prompt = self.transformer.get_next_prompt()
        self.assertEqual(prompt.issue_number, 42)

        # Complete this time
        self.transformer.mark_completed(42)

        item = self.queue.get(42)
        self.assertEqual(item.status, "completed")

    def test_prompt_format_matches_ralph_expectation(self):
        """Test transformed prompt format matches Ralph planner expectations."""
        issue = self._create_issue(
            number=7,
            title="Add user authentication",
            body="Implement OAuth2 login flow with Google provider"
        )
        self.store.save(issue)

        result = self.transformer.transform(7)

        # Verify format matches Ralph's expected input
        expected = "TASK-007: Add user authentication\n\nDescription:\nImplement OAuth2 login flow with Google provider"
        self.assertEqual(result.prompt, expected)

    def test_persistence_across_instances(self):
        """Test transformer works with persisted queue/store state."""
        # Add data with first instances
        issue = self._create_issue(42, "Test", "Body")
        self.store.save(issue)
        self.queue.enqueue(42, priority=5)

        # Create new instances pointing to same dirs
        new_store = IssueStore(self.store_dir)
        new_queue = ProcessingQueue(self.queue_dir)
        new_transformer = PromptTransformer(new_queue, new_store)

        # Verify data persisted
        result = new_transformer.transform(42)
        self.assertEqual(result.issue_number, 42)
        self.assertEqual(result.priority, 5)


if __name__ == "__main__":
    unittest.main()
