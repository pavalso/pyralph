#!/usr/bin/env python3
"""Fetch open issues with 'ready' label from the current repository using gh CLI.

This script uses the GitHub CLI (gh) to fetch all open issues that have the 'ready'
label from the current repository. The issues can be used as planner inputs for Ralph.

Usage:
    python fetch_ready_issues.py [options]
    fetch-issues [options]  # If installed via pip

Options:
    --label LABEL       Filter issues by label (default: "ready")
    --format FORMAT     Output format: "json" or "text" (default: "json")
    --verbose           Enable verbose output
    --no-check          Skip gh CLI authentication check
    --help              Show this help message

Examples:
    fetch-issues --label ready
    fetch-issues --format text
    fetch-issues --label bug --verbose
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple


@dataclass
class Issue:
    """Represents a GitHub issue."""
    number: int
    title: str
    body: Optional[str]
    url: str
    labels: List[str]

    def to_dict(self) -> dict:
        """Convert issue to dictionary."""
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "labels": self.labels,
        }


class GitHubCLIError(Exception):
    """Raised when gh CLI command fails."""
    pass


def check_gh_cli() -> bool:
    """Check if gh CLI is installed and authenticated.

    Returns:
        True if gh CLI is available and authenticated, False otherwise.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def fetch_ready_issues(label: str = "ready") -> List[Issue]:
    """Fetch all open issues with the specified label from the current repository.

    Args:
        label: The label to filter issues by. Defaults to "ready".

    Returns:
        List of Issue objects representing open issues with the specified label.

    Raises:
        GitHubCLIError: If gh CLI command fails.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--label", label,
                "--state", "open",
                "--json", "number,title,body,url,labels"
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise GitHubCLIError(f"gh CLI failed: {result.stderr}")

        issues_data = json.loads(result.stdout) if result.stdout.strip() else []

        issues = []
        for item in issues_data:
            labels = [label.get("name", "") for label in item.get("labels", [])]
            issue = Issue(
                number=item["number"],
                title=item["title"],
                body=item.get("body"),
                url=item["url"],
                labels=labels
            )
            issues.append(issue)

        return issues

    except subprocess.TimeoutExpired:
        raise GitHubCLIError("gh CLI command timed out")
    except json.JSONDecodeError as e:
        raise GitHubCLIError(f"Failed to parse gh CLI output: {e}")


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="fetch-issues",
        description="Fetch open issues from GitHub for use as planner inputs.",
        epilog="Examples:\n"
               "  fetch-issues --label ready\n"
               "  fetch-issues --format text\n"
               "  fetch-issues --label bug --verbose",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--label",
        default="ready",
        help="Filter issues by label (default: ready)"
    )

    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        dest="output_format",
        help="Output format: json or text (default: json)"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output"
    )

    parser.add_argument(
        "--no-check",
        action="store_true",
        dest="no_check",
        help="Skip gh CLI authentication check"
    )

    parser.add_argument(
        "--register-hook",
        action="store_true",
        dest="register_hook",
        help="Register this script as a Ralph hook for PLANNER_SUCCESS events"
    )

    parser.add_argument(
        "--hooks-dir",
        default=".ralph/hooks",
        dest="hooks_dir",
        help="Path to Ralph hooks directory (default: .ralph/hooks)"
    )

    return parser


def format_issues_as_text(issues: List[Issue]) -> str:
    """Format issues as human-readable text.

    Args:
        issues: List of Issue objects to format.

    Returns:
        Formatted string with issue information.
    """
    if not issues:
        return "No issues found."

    lines = [f"Found {len(issues)} issue(s):", ""]
    for issue in issues:
        lines.append(f"#{issue.number}: {issue.title}")
        lines.append(f"  URL: {issue.url}")
        if issue.labels:
            lines.append(f"  Labels: {', '.join(issue.labels)}")
        if issue.body:
            # Show first 100 chars of body
            body_preview = issue.body[:100].replace('\n', ' ')
            if len(issue.body) > 100:
                body_preview += "..."
            lines.append(f"  Body: {body_preview}")
        lines.append("")

    return "\n".join(lines)


def main(args: Optional[List[str]] = None) -> int:
    """Main entry point.

    Args:
        args: Command line arguments. If None, uses sys.argv.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = create_argument_parser()
    parsed_args = parser.parse_args(args)

    # Handle --register-hook option
    if parsed_args.register_hook:
        try:
            config = generate_hook_config()
            config_path = register_hook(parsed_args.hooks_dir, config)
            print(f"Hook registered successfully: {config_path}")
            print(f"  Name: {config.name}")
            print(f"  Events: {', '.join(config.events)}")
            print(f"  Path: {config.path}")
            return 0
        except HookRegistrationError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if parsed_args.verbose:
        print(f"Label filter: {parsed_args.label}", file=sys.stderr)
        print(f"Output format: {parsed_args.output_format}", file=sys.stderr)

    if not parsed_args.no_check:
        if not check_gh_cli():
            print("Error: gh CLI is not installed or not authenticated.", file=sys.stderr)
            print("Please install gh CLI and run 'gh auth login'.", file=sys.stderr)
            return 1

    try:
        if parsed_args.verbose:
            print(f"Fetching issues with label '{parsed_args.label}'...", file=sys.stderr)

        issues = fetch_ready_issues(label=parsed_args.label)

        if not issues:
            if parsed_args.output_format == "json":
                print(json.dumps({"count": 0, "issues": []}, indent=2))
            else:
                print(f"No open issues with '{parsed_args.label}' label found.")
            return 0

        if parsed_args.output_format == "json":
            output = {
                "count": len(issues),
                "issues": [issue.to_dict() for issue in issues]
            }
            print(json.dumps(output, indent=2))
        else:
            print(format_issues_as_text(issues))

        return 0

    except GitHubCLIError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def issue_to_prompt(issue: Issue) -> str:
    """Transform a GitHub issue into a planner-compatible prompt format.

    Converts an Issue object into a structured prompt string that Ralph's planner
    phase can process. The format follows Ralph's user_intent convention.

    Args:
        issue: An Issue object containing GitHub issue data.

    Returns:
        A formatted prompt string containing the task ID, title, and description.
    """
    task_id = f"TASK-{issue.number:03d}"
    body = issue.body.strip() if issue.body and issue.body.strip() else "No description provided."
    return f"{task_id}: {issue.title}\n\nDescription:\n{body}"


def issues_to_prompts(issues: List[Issue]) -> List[str]:
    """Transform a list of GitHub issues into planner-compatible prompts.

    Batch converts multiple Issue objects into formatted prompt strings.

    Args:
        issues: List of Issue objects to transform.

    Returns:
        List of formatted prompt strings, one per issue.
    """
    return [issue_to_prompt(issue) for issue in issues]


class PlannerError(Exception):
    """Raised when the planner phase fails."""
    pass


def invoke_planner(
    user_intent: str,
    agent_name: str = "claude",
    enable_hooks: bool = True
) -> bool:
    """Invoke Ralph's planner phase programmatically for a given user intent.

    Creates a RalphOrchestrator instance and runs the planner phase to generate
    a PRD (Product Requirements Document) with user stories.

    Args:
        user_intent: The description of what needs to be planned (typically
            a transformed issue prompt from issue_to_prompt).
        agent_name: The AI agent to use for planning. Defaults to "claude".
        enable_hooks: Whether to enable hook execution. Defaults to True.

    Returns:
        True if the planner phase completed successfully, False otherwise.

    Raises:
        PlannerError: If the planner phase fails due to missing dependencies
            or other critical errors.
    """
    # Import here to avoid circular dependencies and allow the module
    # to be used without ralph.py being available
    try:
        from ralph import RalphOrchestrator, CONF
    except ImportError as e:
        raise PlannerError(f"Failed to import Ralph components: {e}")

    # Check that memory exists (architect phase must have run)
    if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
        raise PlannerError(
            "Memory directory is missing or empty. "
            "Run the architect phase first."
        )

    try:
        orchestrator = RalphOrchestrator(
            agent_name=agent_name,
            enable_hooks=enable_hooks
        )
        orchestrator.run_planner(user_intent)
        return True
    except SystemExit:
        # run_planner calls sys.exit(1) on failure
        return False


@dataclass
class PlannerResult:
    """Result of a planner invocation for a single issue."""
    issue_number: int
    success: bool
    error: Optional[str] = None


def process_ready_issues(
    issues: List[Issue],
    agent_name: str = "claude",
    enable_hooks: bool = True
) -> Tuple[List[PlannerResult], int, int]:
    """Process a list of ready issues by invoking the planner for each.

    Iterates over the provided issues, transforms each to a prompt, and
    invokes Ralph's planner phase. Processing continues even if individual
    issues fail.

    Args:
        issues: List of Issue objects to process.
        agent_name: The AI agent to use for planning. Defaults to "claude".
        enable_hooks: Whether to enable hook execution. Defaults to True.

    Returns:
        A tuple containing:
            - List of PlannerResult objects with status for each issue
            - Count of successfully processed issues
            - Count of failed issues
    """
    results: List[PlannerResult] = []
    success_count = 0
    failure_count = 0

    for issue in issues:
        prompt = issue_to_prompt(issue)

        try:
            success = invoke_planner(
                user_intent=prompt,
                agent_name=agent_name,
                enable_hooks=enable_hooks
            )

            if success:
                results.append(PlannerResult(
                    issue_number=issue.number,
                    success=True
                ))
                success_count += 1
            else:
                results.append(PlannerResult(
                    issue_number=issue.number,
                    success=False,
                    error="Planner phase did not complete successfully"
                ))
                failure_count += 1

        except PlannerError as e:
            results.append(PlannerResult(
                issue_number=issue.number,
                success=False,
                error=str(e)
            ))
            failure_count += 1

    return results, success_count, failure_count


@dataclass
class UserStory:
    """Represents a user story generated by the planner."""
    title: str
    body: str


@dataclass
class CreatedIssue:
    """Result of creating a GitHub issue."""
    number: int
    url: str
    title: str


def create_draft_issue(story: UserStory) -> CreatedIssue:
    """Create a new GitHub issue from a user story with the 'draft' label.

    Uses the GitHub CLI (gh) to create a new issue in the current repository
    with the provided title and body, automatically applying the 'draft' label.

    Args:
        story: A UserStory object containing the title and body for the issue.

    Returns:
        A CreatedIssue object containing the created issue's number, URL, and title.

    Raises:
        GitHubCLIError: If the gh CLI command fails or returns invalid output.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "create",
                "--title", story.title,
                "--body", story.body,
                "--label", "draft"
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise GitHubCLIError(f"gh CLI failed to create issue: {result.stderr}")

        # gh issue create outputs the URL of the created issue
        issue_url = result.stdout.strip()
        if not issue_url:
            raise GitHubCLIError("gh CLI returned empty output")

        # Extract issue number from URL (e.g., https://github.com/owner/repo/issues/42)
        try:
            issue_number = int(issue_url.rstrip('/').split('/')[-1])
        except (ValueError, IndexError):
            raise GitHubCLIError(f"Failed to parse issue number from URL: {issue_url}")

        return CreatedIssue(
            number=issue_number,
            url=issue_url,
            title=story.title
        )

    except subprocess.TimeoutExpired:
        raise GitHubCLIError("gh CLI command timed out while creating issue")


@dataclass
class CreateIssueResult:
    """Result of attempting to create a GitHub issue."""
    title: str
    success: bool
    issue: Optional[CreatedIssue] = None
    error: Optional[str] = None


def create_draft_issues(
    stories: List[UserStory]
) -> Tuple[List[CreateIssueResult], int, int]:
    """Create GitHub issues from a list of user stories with the 'draft' label.

    Iterates over the provided user stories and creates a GitHub issue for each,
    applying the 'draft' label. Processing continues even if individual issues
    fail to be created.

    Args:
        stories: List of UserStory objects to create issues from.

    Returns:
        A tuple containing:
            - List of CreateIssueResult objects with status for each story
            - Count of successfully created issues
            - Count of failed issues
    """
    results: List[CreateIssueResult] = []
    success_count = 0
    failure_count = 0

    for story in stories:
        try:
            created_issue = create_draft_issue(story)
            results.append(CreateIssueResult(
                title=story.title,
                success=True,
                issue=created_issue
            ))
            success_count += 1

        except GitHubCLIError as e:
            results.append(CreateIssueResult(
                title=story.title,
                success=False,
                error=str(e)
            ))
            failure_count += 1

    return results, success_count, failure_count


def update_issue_labels(
    issue_number: int,
    add_labels: Optional[List[str]] = None,
    remove_labels: Optional[List[str]] = None
) -> bool:
    """Update labels on a GitHub issue by adding and/or removing labels.

    Uses the GitHub CLI (gh) to modify labels on an existing issue in the
    current repository.

    Args:
        issue_number: The issue number to update.
        add_labels: List of label names to add to the issue.
        remove_labels: List of label names to remove from the issue.

    Returns:
        True if the label update was successful, False otherwise.

    Raises:
        GitHubCLIError: If the gh CLI command fails.
    """
    if not add_labels and not remove_labels:
        return True  # Nothing to do

    try:
        cmd = ["gh", "issue", "edit", str(issue_number)]

        if add_labels:
            cmd.extend(["--add-label", ",".join(add_labels)])

        if remove_labels:
            cmd.extend(["--remove-label", ",".join(remove_labels)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise GitHubCLIError(
                f"gh CLI failed to update labels on issue #{issue_number}: {result.stderr}"
            )

        return True

    except subprocess.TimeoutExpired:
        raise GitHubCLIError(
            f"gh CLI command timed out while updating labels on issue #{issue_number}"
        )


def mark_issue_processed(issue_number: int) -> bool:
    """Mark a GitHub issue as processed by swapping the 'ready' label with 'processed'.

    This is a convenience function that removes the 'ready' label and adds
    the 'processed' label to prevent re-processing of the issue.

    Args:
        issue_number: The issue number to mark as processed.

    Returns:
        True if the label update was successful, False otherwise.

    Raises:
        GitHubCLIError: If the gh CLI command fails.
    """
    return update_issue_labels(
        issue_number=issue_number,
        add_labels=["processed"],
        remove_labels=["ready"]
    )


@dataclass
class PollerConfig:
    """Configuration for GitHubPoller.

    Attributes:
        label: The label to filter issues by. Defaults to "ready".
        interval: Polling interval in seconds. Defaults to 60.
        on_new_issues: Optional callback invoked when new issues are detected.
            The callback receives a list of new Issue objects.
        on_error: Optional callback invoked when an error occurs during polling.
            The callback receives the exception that occurred.
    """
    label: str = "ready"
    interval: float = 60.0
    on_new_issues: Optional[Callable[[List["Issue"]], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None


class GitHubPoller:
    """Polls GitHub for new issues with a specified label at configurable intervals.

    The poller runs in a background thread and detects new issues by tracking
    previously seen issue numbers. When new issues are detected, the configured
    callback is invoked.

    Example:
        >>> def handle_new_issues(issues):
        ...     for issue in issues:
        ...         print(f"New issue: #{issue.number} - {issue.title}")
        ...
        >>> config = PollerConfig(
        ...     label="ready",
        ...     interval=30.0,
        ...     on_new_issues=handle_new_issues
        ... )
        >>> poller = GitHubPoller(config)
        >>> poller.start()
        >>> # ... later ...
        >>> poller.stop()
    """

    def __init__(self, config: Optional[PollerConfig] = None):
        """Initialize the GitHubPoller.

        Args:
            config: Optional PollerConfig with polling settings. If None,
                uses default configuration with 60 second interval.
        """
        self._config = config or PollerConfig()
        self._seen_issue_numbers: Set[int] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def config(self) -> PollerConfig:
        """Get the current poller configuration."""
        return self._config

    @property
    def interval(self) -> float:
        """Get the polling interval in seconds."""
        return self._config.interval

    @interval.setter
    def interval(self, value: float) -> None:
        """Set the polling interval in seconds.

        Args:
            value: New polling interval. Must be positive.

        Raises:
            ValueError: If value is not positive.
        """
        if value <= 0:
            raise ValueError("Polling interval must be positive")
        self._config.interval = value

    @property
    def is_running(self) -> bool:
        """Check if the poller is currently running."""
        return self._running

    @property
    def seen_issues(self) -> Set[int]:
        """Get a copy of the set of seen issue numbers."""
        with self._lock:
            return self._seen_issue_numbers.copy()

    def start(self) -> bool:
        """Start the polling loop in a background thread.

        Returns:
            True if the poller was started, False if already running.
        """
        if self._running:
            return False

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: Optional[float] = None) -> bool:
        """Stop the polling loop.

        Args:
            timeout: Maximum time to wait for the polling thread to stop.
                If None, waits indefinitely.

        Returns:
            True if the poller was stopped, False if not running.
        """
        if not self._running:
            return False

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._running = False
        return True

    def poll_once(self) -> List[Issue]:
        """Perform a single poll and return any new issues.

        This method can be called manually to perform a one-time poll
        without using the background thread.

        Returns:
            List of new issues that haven't been seen before.

        Raises:
            GitHubCLIError: If the GitHub CLI command fails.
        """
        try:
            current_issues = fetch_ready_issues(label=self._config.label)
        except GitHubCLIError:
            raise

        new_issues: List[Issue] = []

        with self._lock:
            for issue in current_issues:
                if issue.number not in self._seen_issue_numbers:
                    new_issues.append(issue)
                    self._seen_issue_numbers.add(issue.number)

        return new_issues

    def reset_seen_issues(self) -> None:
        """Clear the set of seen issue numbers.

        This causes all issues to be treated as new on the next poll.
        """
        with self._lock:
            self._seen_issue_numbers.clear()

    def _poll_loop(self) -> None:
        """Internal polling loop that runs in a background thread."""
        while not self._stop_event.is_set():
            try:
                new_issues = self.poll_once()
                if new_issues and self._config.on_new_issues is not None:
                    self._config.on_new_issues(new_issues)
            except Exception as e:
                if self._config.on_error is not None:
                    self._config.on_error(e)

            # Wait for the interval or until stop is called
            self._stop_event.wait(timeout=self._config.interval)


@dataclass
class HookConfig:
    """Configuration for a Ralph hook entry."""
    name: str
    path: str
    events: List[str]
    priority: int = 100
    timeout: float = 5.0

    def to_dict(self) -> dict:
        """Convert hook config to dictionary."""
        return {
            "name": self.name,
            "path": self.path,
            "events": self.events,
            "priority": self.priority,
            "timeout": self.timeout,
        }


def generate_hook_config(script_path: Optional[str] = None) -> HookConfig:
    """Generate a hook configuration for registering this script as a Ralph hook.

    Creates a HookConfig that subscribes to PLANNER_SUCCESS events, allowing
    the script to automatically sync completed plans to GitHub.

    Args:
        script_path: Optional path to the script. If None, uses sys.executable
            with the module path for a portable configuration.

    Returns:
        A HookConfig object configured for PLANNER_SUCCESS events.
    """
    import os

    if script_path is None:
        # Use absolute path to the current script file
        script_path = os.path.abspath(__file__)

    return HookConfig(
        name="fetch_ready_issues",
        path=script_path,
        events=["PLANNER_SUCCESS"],
        priority=100,
        timeout=30.0
    )


class HookRegistrationError(Exception):
    """Raised when hook registration fails."""
    pass


def register_hook(hooks_dir: str, config: HookConfig) -> str:
    """Register a hook by adding it to the hooks.yaml configuration file.

    Creates or updates the hooks.yaml file in the specified hooks directory
    with the provided hook configuration. If the hooks directory doesn't exist,
    it will be created.

    Args:
        hooks_dir: Path to the Ralph hooks directory (e.g., ".ralph/hooks").
        config: A HookConfig object containing the hook configuration.

    Returns:
        Path to the updated hooks.yaml file.

    Raises:
        HookRegistrationError: If registration fails due to I/O or YAML errors.
    """
    from pathlib import Path

    try:
        import yaml
    except ImportError:
        raise HookRegistrationError(
            "PyYAML is required for hook registration. Install it with: pip install pyyaml"
        )

    hooks_path = Path(hooks_dir)
    config_path = hooks_path / "hooks.yaml"

    try:
        # Create hooks directory if it doesn't exist
        hooks_path.mkdir(parents=True, exist_ok=True)

        # Load existing config or create new one
        existing_config: dict = {"hooks": []}
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded = yaml.safe_load(f)
                if loaded and isinstance(loaded, dict):
                    existing_config = loaded
                    if "hooks" not in existing_config:
                        existing_config["hooks"] = []

        # Check if hook with same name already exists
        hooks_list = existing_config.get("hooks", [])
        if not isinstance(hooks_list, list):
            hooks_list = []
            existing_config["hooks"] = hooks_list

        # Remove any existing hook with the same name
        hooks_list = [h for h in hooks_list if h.get("name") != config.name]

        # Add the new hook config
        hooks_list.append(config.to_dict())
        existing_config["hooks"] = hooks_list

        # Write the updated config
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(existing_config, f, default_flow_style=False, sort_keys=False)

        return str(config_path)

    except OSError as e:
        raise HookRegistrationError(f"Failed to write hook config: {e}")
    except yaml.YAMLError as e:
        raise HookRegistrationError(f"Failed to parse or write YAML: {e}")


@dataclass
class StoredIssue:
    """Represents a persisted GitHub issue with metadata.

    Extends the Issue data with persistence-related fields for tracking
    when issues were stored and their processing status.

    Attributes:
        number: The GitHub issue number.
        title: The issue title.
        body: The issue body/description (may be None).
        url: The URL to the issue on GitHub.
        labels: List of label names on the issue.
        stored_at: ISO 8601 timestamp when the issue was stored.
        status: Processing status ('pending', 'processing', 'completed', 'failed').
    """
    number: int
    title: str
    body: Optional[str]
    url: str
    labels: List[str]
    stored_at: str
    status: str = "pending"

    def to_dict(self) -> dict:
        """Convert stored issue to dictionary for JSON serialization."""
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "labels": self.labels,
            "stored_at": self.stored_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StoredIssue":
        """Create a StoredIssue from a dictionary.

        Args:
            data: Dictionary containing issue data.

        Returns:
            A StoredIssue instance.
        """
        return cls(
            number=data["number"],
            title=data["title"],
            body=data.get("body"),
            url=data["url"],
            labels=data.get("labels", []),
            stored_at=data["stored_at"],
            status=data.get("status", "pending"),
        )

    @classmethod
    def from_issue(cls, issue: "Issue", status: str = "pending") -> "StoredIssue":
        """Create a StoredIssue from an Issue.

        Args:
            issue: The Issue to convert.
            status: Initial processing status.

        Returns:
            A StoredIssue instance with current timestamp.
        """
        from datetime import datetime, timezone

        return cls(
            number=issue.number,
            title=issue.title,
            body=issue.body,
            url=issue.url,
            labels=issue.labels,
            stored_at=datetime.now(timezone.utc).isoformat(),
            status=status,
        )


class IssueStoreError(Exception):
    """Raised when issue storage operations fail."""
    pass


class IssueStore:
    """Persists GitHub issues locally in JSON format for audit and restart recovery.

    Issues are stored in a directory structure where each issue is saved as a
    separate JSON file named by issue number. This allows for easy auditing,
    individual issue access, and atomic updates.

    Directory structure:
        <store_dir>/
            issues/
                1.json
                2.json
                ...
            index.json  # Optional: metadata about the store

    Example:
        >>> store = IssueStore(".ralph/issues")
        >>> store.save(issue)
        >>> stored = store.get(42)
        >>> all_issues = store.list_issues()
    """

    def __init__(self, store_dir: str = ".ralph/issues"):
        """Initialize the IssueStore.

        Args:
            store_dir: Path to the directory for storing issues.
                Defaults to ".ralph/issues".
        """
        self._store_dir = Path(store_dir)
        self._issues_dir = self._store_dir / "issues"

    @property
    def store_dir(self) -> Path:
        """Get the store directory path."""
        return self._store_dir

    @property
    def issues_dir(self) -> Path:
        """Get the issues subdirectory path."""
        return self._issues_dir

    def _ensure_dirs(self) -> None:
        """Ensure the storage directories exist."""
        try:
            self._issues_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise IssueStoreError(f"Failed to create storage directory: {e}")

    def _issue_path(self, issue_number: int) -> Path:
        """Get the file path for an issue.

        Args:
            issue_number: The issue number.

        Returns:
            Path to the issue's JSON file.
        """
        return self._issues_dir / f"{issue_number}.json"

    def save(self, issue: Issue, status: str = "pending") -> StoredIssue:
        """Save an issue to the local store.

        If the issue already exists, it will be overwritten.

        Args:
            issue: The Issue to save.
            status: Initial processing status.

        Returns:
            The StoredIssue that was saved.

        Raises:
            IssueStoreError: If the save operation fails.
        """
        self._ensure_dirs()

        stored = StoredIssue.from_issue(issue, status=status)
        issue_path = self._issue_path(issue.number)

        try:
            with open(issue_path, 'w', encoding='utf-8') as f:
                json.dump(stored.to_dict(), f, indent=2)
            return stored
        except OSError as e:
            raise IssueStoreError(f"Failed to save issue #{issue.number}: {e}")
        except (TypeError, ValueError) as e:
            raise IssueStoreError(f"Failed to serialize issue #{issue.number}: {e}")

    def save_stored(self, stored_issue: StoredIssue) -> StoredIssue:
        """Save a StoredIssue to the local store.

        This method is used for updating existing stored issues.

        Args:
            stored_issue: The StoredIssue to save.

        Returns:
            The StoredIssue that was saved.

        Raises:
            IssueStoreError: If the save operation fails.
        """
        self._ensure_dirs()

        issue_path = self._issue_path(stored_issue.number)

        try:
            with open(issue_path, 'w', encoding='utf-8') as f:
                json.dump(stored_issue.to_dict(), f, indent=2)
            return stored_issue
        except OSError as e:
            raise IssueStoreError(f"Failed to save issue #{stored_issue.number}: {e}")
        except (TypeError, ValueError) as e:
            raise IssueStoreError(f"Failed to serialize issue #{stored_issue.number}: {e}")

    def get(self, issue_number: int) -> Optional[StoredIssue]:
        """Retrieve a stored issue by number.

        Args:
            issue_number: The issue number to retrieve.

        Returns:
            The StoredIssue if found, None otherwise.

        Raises:
            IssueStoreError: If reading the issue file fails (other than not found).
        """
        issue_path = self._issue_path(issue_number)

        if not issue_path.exists():
            return None

        try:
            with open(issue_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return StoredIssue.from_dict(data)
        except OSError as e:
            raise IssueStoreError(f"Failed to read issue #{issue_number}: {e}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise IssueStoreError(f"Failed to parse issue #{issue_number}: {e}")

    def exists(self, issue_number: int) -> bool:
        """Check if an issue exists in the store.

        Args:
            issue_number: The issue number to check.

        Returns:
            True if the issue exists, False otherwise.
        """
        return self._issue_path(issue_number).exists()

    def delete(self, issue_number: int) -> bool:
        """Delete a stored issue.

        Args:
            issue_number: The issue number to delete.

        Returns:
            True if the issue was deleted, False if it didn't exist.

        Raises:
            IssueStoreError: If the delete operation fails.
        """
        issue_path = self._issue_path(issue_number)

        if not issue_path.exists():
            return False

        try:
            issue_path.unlink()
            return True
        except OSError as e:
            raise IssueStoreError(f"Failed to delete issue #{issue_number}: {e}")

    def list_issues(self, status: Optional[str] = None) -> List[StoredIssue]:
        """List all stored issues, optionally filtered by status.

        Args:
            status: Optional status to filter by ('pending', 'processing',
                'completed', 'failed'). If None, returns all issues.

        Returns:
            List of StoredIssue objects, sorted by issue number.

        Raises:
            IssueStoreError: If reading issues fails.
        """
        if not self._issues_dir.exists():
            return []

        issues: List[StoredIssue] = []

        try:
            for path in self._issues_dir.glob("*.json"):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    stored = StoredIssue.from_dict(data)
                    if status is None or stored.status == status:
                        issues.append(stored)
                except (json.JSONDecodeError, KeyError, TypeError):
                    # Skip malformed files but continue processing
                    continue

            # Sort by issue number
            issues.sort(key=lambda x: x.number)
            return issues

        except OSError as e:
            raise IssueStoreError(f"Failed to list issues: {e}")

    def update_status(self, issue_number: int, status: str) -> Optional[StoredIssue]:
        """Update the status of a stored issue.

        Args:
            issue_number: The issue number to update.
            status: The new status value.

        Returns:
            The updated StoredIssue if found, None if the issue doesn't exist.

        Raises:
            IssueStoreError: If the update operation fails.
        """
        stored = self.get(issue_number)
        if stored is None:
            return None

        from dataclasses import replace
        updated = replace(stored, status=status)
        return self.save_stored(updated)

    def count(self, status: Optional[str] = None) -> int:
        """Count stored issues, optionally filtered by status.

        Args:
            status: Optional status to filter by.

        Returns:
            The number of matching issues.
        """
        if not self._issues_dir.exists():
            return 0

        if status is None:
            # Fast path: just count JSON files
            return len(list(self._issues_dir.glob("*.json")))

        # Need to read files to filter by status
        return len(self.list_issues(status=status))

    def clear(self) -> int:
        """Remove all stored issues.

        Returns:
            The number of issues that were deleted.

        Raises:
            IssueStoreError: If the clear operation fails.
        """
        if not self._issues_dir.exists():
            return 0

        count = 0
        try:
            for path in self._issues_dir.glob("*.json"):
                path.unlink()
                count += 1
            return count
        except OSError as e:
            raise IssueStoreError(f"Failed to clear issues: {e}")

    def save_batch(self, issues: List[Issue], status: str = "pending") -> List[StoredIssue]:
        """Save multiple issues in batch.

        Args:
            issues: List of Issues to save.
            status: Initial processing status for all issues.

        Returns:
            List of StoredIssue objects that were saved.

        Raises:
            IssueStoreError: If saving any issue fails.
        """
        self._ensure_dirs()
        stored_issues: List[StoredIssue] = []

        for issue in issues:
            stored = self.save(issue, status=status)
            stored_issues.append(stored)

        return stored_issues


if __name__ == "__main__":
    sys.exit(main())
