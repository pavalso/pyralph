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
from dataclasses import dataclass
from typing import List, Optional, Tuple


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


if __name__ == "__main__":
    sys.exit(main())
