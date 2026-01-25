#!/usr/bin/env python3
"""Fetch open issues with 'ready' label from the current repository using gh CLI.

This script uses the GitHub CLI (gh) to fetch all open issues that have the 'ready'
label from the current repository. The issues can be used as planner inputs for Ralph.

TECHNICAL DEBT (TASK-015): This module exceeds the ~500 line target at ~3600 lines.
Consider future decomposition into separate modules for:
- Issue fetching and GitHub API interaction
- Issue filtering and processing
- Output formatting and display

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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple, TYPE_CHECKING

from .logger import Logger

if TYPE_CHECKING:
    from .hooks import HookManager


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
            ellipsis = "..." if len(issue.body) > 100 else ""
            lines.append(f"  Body: {body_preview}{ellipsis}")
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
            Logger.info(f"Hook registered successfully: {config_path}")
            Logger.info(f"  Name: {config.name}")
            Logger.info(f"  Events: {', '.join(config.events)}")
            Logger.info(f"  Path: {config.path}")
            return 0
        except HookRegistrationError as e:
            Logger.error(f"Error: {e}")
            return 1

    if parsed_args.verbose:
        Logger.debug(f"Label filter: {parsed_args.label}")
        Logger.debug(f"Output format: {parsed_args.output_format}")

    if not parsed_args.no_check:
        if not check_gh_cli():
            Logger.error("Error: gh CLI is not installed or not authenticated.")
            Logger.error("Please install gh CLI and run 'gh auth login'.")
            return 1

    try:
        if parsed_args.verbose:
            Logger.debug(f"Fetching issues with label '{parsed_args.label}'...")

        issues = fetch_ready_issues(label=parsed_args.label)

        if not issues:
            if parsed_args.output_format == "json":
                Logger.info(json.dumps({"count": 0, "issues": []}, indent=2))
            else:
                Logger.info(f"No open issues with '{parsed_args.label}' label found.")
            return 0

        if parsed_args.output_format == "json":
            output = {
                "count": len(issues),
                "issues": [issue.to_dict() for issue in issues]
            }
            Logger.info(json.dumps(output, indent=2))
        else:
            Logger.info(format_issues_as_text(issues))

        return 0

    except GitHubCLIError as e:
        Logger.error(f"Error: {e}")
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
        from orchestrator import RalphOrchestrator
        from config import CONF
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


class OrchestrationError(Exception):
    """Raised when the orchestration loop fails."""
    pass


@dataclass
class OrchestrationResult:
    """Result of orchestration loop execution for a single issue.

    Attributes:
        issue_number: The GitHub issue number that was processed.
        planner_success: Whether the PRD generation (planner phase) succeeded.
        execution_success: Whether the execute loop completed all tasks.
        tasks_completed: Number of tasks that completed successfully.
        tasks_failed: Number of tasks that failed.
        tasks_total: Total number of tasks in the PRD.
        marked_processed: Whether the issue was marked with 'processed' label.
        error: Error message if orchestration failed.
        skipped_no_stories: True if execution was skipped due to empty PRD.
    """
    issue_number: int
    planner_success: bool
    execution_success: bool
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_total: int = 0
    marked_processed: bool = False
    error: Optional[str] = None
    skipped_no_stories: bool = False


def invoke_orchestration(
    user_intent: str,
    agent_name: str = "claude",
    enable_hooks: bool = True,
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    skip_verify: bool = False
) -> Tuple[bool, int, int, int, bool, Optional[str]]:
    """Invoke Ralph's full orchestration loop (planner -> execute) for an issue.

    This function runs the planner phase to generate a PRD, then executes all
    tasks in the PRD sequentially. It handles edge cases like empty PRDs and
    agent timeouts.

    Args:
        user_intent: The description of what needs to be built (typically
            a transformed issue prompt from issue_to_prompt).
        agent_name: The AI agent to use. Defaults to "claude".
        enable_hooks: Whether to enable hook execution. Defaults to True.
        timeout: Optional agent timeout in seconds.
        retries: Optional max retries per task.
        skip_verify: Whether to skip test verification. Defaults to False.

    Returns:
        A tuple containing:
            - success: True if orchestration completed successfully
            - tasks_completed: Number of tasks that completed
            - tasks_failed: Number of tasks that failed
            - tasks_total: Total number of tasks in the PRD
            - skipped_no_stories: True if skipped due to empty PRD
            - error: Error message if orchestration failed, None otherwise

    Raises:
        OrchestrationError: If critical setup fails (e.g., missing memory).
    """
    try:
        from orchestrator import RalphOrchestrator
        from config import CONF
        from logger import Logger
    except ImportError as e:
        raise OrchestrationError(f"Failed to import Ralph components: {e}")

    # Check that memory exists (architect phase must have run)
    if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
        raise OrchestrationError(
            "Memory directory is missing or empty. "
            "Run the architect phase first."
        )

    try:
        # Create orchestrator with appropriate settings
        orchestrator = RalphOrchestrator(
            agent_name=agent_name,
            enable_hooks=enable_hooks,
            timeout=timeout,
            retries=retries,
            skip_verify=skip_verify
        )

        # Run planner phase to generate PRD
        try:
            orchestrator.run_planner(user_intent)
        except SystemExit:
            return False, 0, 0, 0, False, "Planner phase failed"

        # Load PRD and check for user stories
        prd = orchestrator._prd.load()
        user_stories = prd.get('userStories', [])

        if not user_stories:
            Logger.warning(
                f"PRD contains no user stories - skipping execution"
            )
            return True, 0, 0, 0, True, None

        tasks_total = len(user_stories)

        # Run execute loop
        orchestrator.execute_loop()

        # Count completed and failed tasks from the PRD
        prd = orchestrator._prd.load()  # Reload to get updated statuses
        tasks_completed = sum(
            1 for task in prd.get('userStories', [])
            if task.get('status') == 'completed'
        )
        tasks_failed = sum(
            1 for task in prd.get('userStories', [])
            if task.get('status') == 'failed'
        )

        # Determine overall success
        success = tasks_failed == 0 and tasks_completed == tasks_total
        error = None if success else f"{tasks_failed} task(s) failed"

        return success, tasks_completed, tasks_failed, tasks_total, False, error

    except SystemExit:
        return False, 0, 0, 0, False, "Orchestration aborted"
    except Exception as e:
        # Catch agent timeout and other errors
        error_msg = str(e)
        if "timeout" in error_msg.lower() or "TimeoutExpired" in type(e).__name__:
            error_msg = f"Agent timeout: {error_msg}"
        return False, 0, 0, 0, False, error_msg


def process_ready_issues(
    issues: List[Issue],
    agent_name: str = "claude",
    enable_hooks: bool = True,
    execute_orchestration: bool = True,
    mark_processed: bool = True,
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    skip_verify: bool = False,
    processed_issues: Optional[Set[int]] = None
) -> Tuple[List[OrchestrationResult], int, int]:
    """Process a list of ready issues by invoking the full orchestration loop.

    For each issue, this function:
    1. Transforms the issue into a prompt
    2. Invokes Ralph's planner phase to generate a PRD
    3. Executes all tasks in the PRD (if execute_orchestration=True)
    4. Optionally marks the issue with 'processed' label on success

    Processing continues sequentially even if individual issues fail, with
    failures recorded in the results. Each issue's orchestration completes
    fully before the next begins (no concurrency).

    Args:
        issues: List of Issue objects to process.
        agent_name: The AI agent to use. Defaults to "claude".
        enable_hooks: Whether to enable hook execution. Defaults to True.
        execute_orchestration: Whether to run the full orchestration loop
            (planner -> execute). If False, only runs the planner phase.
            Defaults to True.
        mark_processed: Whether to mark issues with 'processed' label on
            successful completion. Defaults to True.
        timeout: Optional agent timeout in seconds.
        retries: Optional max retries per task.
        skip_verify: Whether to skip test verification. Defaults to False.
        processed_issues: Optional set of issue numbers to skip (for resume
            after interruption). Issues in this set are skipped.

    Returns:
        A tuple containing:
            - List of OrchestrationResult objects with status for each issue
            - Count of successfully processed issues
            - Count of failed issues
    """
    from logger import Logger

    results: List[OrchestrationResult] = []
    success_count = 0
    failure_count = 0

    for issue in issues:
        # Handle resume: skip already-processed issues
        if processed_issues and issue.number in processed_issues:
            Logger.info(
                f"Skipping issue #{issue.number} (already processed)"
            )
            continue

        prompt = issue_to_prompt(issue)

        if not execute_orchestration:
            # Legacy mode: only run planner phase
            try:
                planner_success = invoke_planner(
                    user_intent=prompt,
                    agent_name=agent_name,
                    enable_hooks=enable_hooks
                )

                if planner_success:
                    results.append(OrchestrationResult(
                        issue_number=issue.number,
                        planner_success=True,
                        execution_success=True  # No execution to fail
                    ))
                    success_count += 1
                else:
                    results.append(OrchestrationResult(
                        issue_number=issue.number,
                        planner_success=False,
                        execution_success=False,
                        error="Planner phase did not complete successfully"
                    ))
                    failure_count += 1

            except PlannerError as e:
                results.append(OrchestrationResult(
                    issue_number=issue.number,
                    planner_success=False,
                    execution_success=False,
                    error=str(e)
                ))
                failure_count += 1
            continue

        # Full orchestration mode
        try:
            (
                success,
                tasks_completed,
                tasks_failed,
                tasks_total,
                skipped_no_stories,
                error
            ) = invoke_orchestration(
                user_intent=prompt,
                agent_name=agent_name,
                enable_hooks=enable_hooks,
                timeout=timeout,
                retries=retries,
                skip_verify=skip_verify
            )

            # Handle edge case: PRD with no user stories
            if skipped_no_stories:
                Logger.warning(
                    f"Issue #{issue.number}: PRD contains no user stories, "
                    "execution skipped"
                )
                results.append(OrchestrationResult(
                    issue_number=issue.number,
                    planner_success=True,
                    execution_success=True,  # No tasks to fail
                    skipped_no_stories=True
                ))
                success_count += 1
                continue

            # Create result object
            result = OrchestrationResult(
                issue_number=issue.number,
                planner_success=True,  # We got past planner if we're here
                execution_success=success,
                tasks_completed=tasks_completed,
                tasks_failed=tasks_failed,
                tasks_total=tasks_total,
                error=error
            )

            if success:
                # Optionally mark issue as processed
                if mark_processed:
                    try:
                        mark_issue_processed(issue.number)
                        result.marked_processed = True
                        Logger.info(
                            f"Issue #{issue.number}: Marked as processed"
                        )
                    except GitHubCLIError as e:
                        Logger.warning(
                            f"Issue #{issue.number}: Failed to mark as "
                            f"processed: {e}"
                        )
                success_count += 1
            else:
                failure_count += 1
                Logger.warning(
                    f"Issue #{issue.number}: Orchestration failed - {error}"
                )

            results.append(result)

        except OrchestrationError as e:
            results.append(OrchestrationResult(
                issue_number=issue.number,
                planner_success=False,
                execution_success=False,
                error=str(e)
            ))
            failure_count += 1
            Logger.error(f"Issue #{issue.number}: {e}")

    return results, success_count, failure_count


@dataclass
class BatchProcessResult:
    """Result of batch processing GitHub issues.

    Attributes:
        issues_found: Number of issues found with the specified label.
        issues_processed: Number of issues successfully processed.
        issues_failed: Number of issues that failed to process.
        results: List of OrchestrationResult objects with status for each issue.
        message: Human-readable summary message.
        total_tasks_completed: Total tasks completed across all issues.
        total_tasks_failed: Total tasks that failed across all issues.
    """
    issues_found: int
    issues_processed: int
    issues_failed: int
    results: List[OrchestrationResult]
    message: str
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0


def batch_process_issues(
    label: str = "ready",
    agent_name: str = "claude",
    enable_hooks: bool = True,
    execute_orchestration: bool = True,
    mark_processed: bool = True,
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    skip_verify: bool = False,
    processed_issues: Optional[Set[int]] = None
) -> BatchProcessResult:
    """Fetch GitHub issues and execute full orchestration loop for each.

    This function is the main entry point for batch processing GitHub issues.
    For each issue with the specified label, it:
    1. Transforms the issue into a planner prompt
    2. Invokes Ralph's planner phase to generate a PRD
    3. Executes all tasks in the PRD sequentially (architect -> planner -> execute)
    4. Optionally marks the issue with 'processed' label on success

    Processing is sequential - each issue's orchestration completes fully before
    the next begins (no concurrency). This avoids resource contention.

    Processing continues even if individual issues fail, with failures recorded
    in the results. Failed issues remain in 'failed' state for later retry.

    Args:
        label: The label to filter issues by. Defaults to "ready".
        agent_name: The AI agent to use. Defaults to "claude".
        enable_hooks: Whether to enable hook execution. Defaults to True.
        execute_orchestration: Whether to run the full orchestration loop.
            If False, only runs the planner phase. Defaults to True.
        mark_processed: Whether to mark issues with 'processed' label on
            successful completion. Defaults to True.
        timeout: Optional agent timeout in seconds.
        retries: Optional max retries per task.
        skip_verify: Whether to skip test verification. Defaults to False.
        processed_issues: Optional set of issue numbers to skip (for resume
            after interruption). Issues in this set are skipped.

    Returns:
        A BatchProcessResult containing processing statistics and individual
        results for each issue.

    Raises:
        GitHubCLIError: If the gh CLI is not installed or not authenticated.
            The error message includes instructions for authentication.

    Example:
        >>> result = batch_process_issues(label="ready")
        >>> print(f"Processed {result.issues_processed} of {result.issues_found} issues")
        >>> print(f"Tasks: {result.total_tasks_completed} completed, {result.total_tasks_failed} failed")
        >>> for r in result.results:
        ...     if not r.execution_success:
        ...         print(f"Issue #{r.issue_number} failed: {r.error}")
    """
    # Check gh CLI authentication before proceeding
    if not check_gh_cli():
        raise GitHubCLIError(
            "GitHub CLI (gh) is not installed or not authenticated. "
            "Please install gh CLI and run 'gh auth login' to authenticate."
        )

    # Fetch all open issues with the specified label
    issues = fetch_ready_issues(label=label)

    # Handle edge case: no issues found
    if not issues:
        return BatchProcessResult(
            issues_found=0,
            issues_processed=0,
            issues_failed=0,
            results=[],
            message=f"No open issues with '{label}' label found."
        )

    # Process each issue sequentially through full orchestration
    results, success_count, failure_count = process_ready_issues(
        issues=issues,
        agent_name=agent_name,
        enable_hooks=enable_hooks,
        execute_orchestration=execute_orchestration,
        mark_processed=mark_processed,
        timeout=timeout,
        retries=retries,
        skip_verify=skip_verify,
        processed_issues=processed_issues
    )

    # Calculate total tasks across all issues
    total_tasks_completed = sum(r.tasks_completed for r in results)
    total_tasks_failed = sum(r.tasks_failed for r in results)

    # Build summary message
    total = len(issues)
    if processed_issues:
        skipped = len(processed_issues & {i.number for i in issues})
        total = total - skipped

    if failure_count == 0:
        message = f"Successfully processed all {total} issue(s)."
        if total_tasks_completed > 0:
            message += f" ({total_tasks_completed} task(s) completed)"
    elif success_count == 0:
        message = f"Failed to process all {total} issue(s)."
    else:
        message = f"Processed {success_count} of {total} issue(s). {failure_count} failed."
        if total_tasks_completed > 0 or total_tasks_failed > 0:
            message += f" ({total_tasks_completed} task(s) completed, {total_tasks_failed} failed)"

    return BatchProcessResult(
        issues_found=len(issues),
        issues_processed=success_count,
        issues_failed=failure_count,
        results=results,
        message=message,
        total_tasks_completed=total_tasks_completed,
        total_tasks_failed=total_tasks_failed
    )


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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            if not self._running:
                return False
            self._stop_event.set()
            thread = self._thread

        if thread is not None:
            thread.join(timeout=timeout)

        with self._lock:
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
    if script_path is None:
        # Use absolute path to the current script file
        script_path = str(Path(__file__).resolve())

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


@dataclass
class QueueItem:
    """Represents an item in the processing queue.

    Attributes:
        issue_number: The GitHub issue number.
        priority: Processing priority (lower = higher priority). Defaults to 0.
        added_at: ISO 8601 timestamp when the item was added to the queue.
        status: Processing status ('pending', 'processing', 'completed', 'failed').
        started_at: ISO 8601 timestamp when processing started (None if not started).
        completed_at: ISO 8601 timestamp when processing completed (None if not completed).
        error: Error message if processing failed (None otherwise).
        retry_count: Number of times processing has been retried.
    """
    issue_number: int
    priority: int = 0
    added_at: str = ""
    status: str = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0

    def __post_init__(self) -> None:
        """Set added_at timestamp if not provided."""
        if not self.added_at:
            from datetime import datetime, timezone
            self.added_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Convert queue item to dictionary for JSON serialization."""
        return {
            "issue_number": self.issue_number,
            "priority": self.priority,
            "added_at": self.added_at,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QueueItem":
        """Create a QueueItem from a dictionary.

        Args:
            data: Dictionary containing queue item data.

        Returns:
            A QueueItem instance.
        """
        return cls(
            issue_number=data["issue_number"],
            priority=data.get("priority", 0),
            added_at=data.get("added_at", ""),
            status=data.get("status", "pending"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            retry_count=data.get("retry_count", 0),
        )


class ProcessingQueueError(Exception):
    """Raised when processing queue operations fail."""
    pass


class ProcessingQueue:
    """Maintains a processing queue for GitHub issues with FIFO ordering and state tracking.

    The queue ensures issues are processed in order (by priority, then by addition time)
    and tracks the processing state of each issue. Queue state is persisted to disk
    for recovery after restarts.

    Directory structure:
        <queue_dir>/
            queue.json  # Queue state and metadata

    Example:
        >>> queue = ProcessingQueue(".ralph/queue")
        >>> queue.enqueue(42)
        >>> queue.enqueue(43, priority=1)  # Higher priority (lower number)
        >>> item = queue.dequeue()  # Returns issue 43 first (higher priority)
        >>> queue.mark_completed(43)
        >>> item = queue.dequeue()  # Returns issue 42
    """

    # Valid status transitions
    VALID_STATUSES = {"pending", "processing", "completed", "failed"}

    def __init__(self, queue_dir: str = ".ralph/queue"):
        """Initialize the ProcessingQueue.

        Args:
            queue_dir: Path to the directory for storing queue state.
                Defaults to ".ralph/queue".
        """
        self._queue_dir = Path(queue_dir)
        self._queue_file = self._queue_dir / "queue.json"
        self._lock = threading.Lock()
        self._items: List[QueueItem] = []
        self._load()

    @property
    def queue_dir(self) -> Path:
        """Get the queue directory path."""
        return self._queue_dir

    def _ensure_dir(self) -> None:
        """Ensure the queue directory exists."""
        try:
            self._queue_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ProcessingQueueError(f"Failed to create queue directory: {e}")

    def _load(self) -> None:
        """Load queue state from disk."""
        if not self._queue_file.exists():
            return

        try:
            with open(self._queue_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            items_data = data.get("items", [])
            self._items = [QueueItem.from_dict(item) for item in items_data]
        except OSError as e:
            raise ProcessingQueueError(f"Failed to load queue state: {e}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ProcessingQueueError(f"Failed to parse queue state: {e}")

    def _save(self) -> None:
        """Save queue state to disk."""
        self._ensure_dir()

        data = {
            "items": [item.to_dict() for item in self._items],
        }

        try:
            with open(self._queue_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            raise ProcessingQueueError(f"Failed to save queue state: {e}")
        except (TypeError, ValueError) as e:
            raise ProcessingQueueError(f"Failed to serialize queue state: {e}")

    def _sort_items(self) -> None:
        """Sort items by priority (ascending) and then by added_at (ascending)."""
        self._items.sort(key=lambda x: (x.priority, x.added_at))

    def enqueue(self, issue_number: int, priority: int = 0) -> QueueItem:
        """Add an issue to the processing queue.

        If the issue is already in the queue with a non-terminal status
        (pending or processing), this operation is idempotent and returns
        the existing item.

        Args:
            issue_number: The GitHub issue number to add.
            priority: Processing priority (lower = higher priority). Defaults to 0.

        Returns:
            The QueueItem that was added or already exists.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        with self._lock:
            # Check if already in queue with non-terminal status
            for item in self._items:
                if item.issue_number == issue_number and item.status in ("pending", "processing"):
                    return item

            # Create new queue item
            item = QueueItem(issue_number=issue_number, priority=priority)
            self._items.append(item)
            self._sort_items()
            self._save()
            return item

    def dequeue(self) -> Optional[QueueItem]:
        """Get the next pending issue from the queue and mark it as processing.

        Returns the highest priority pending item (lowest priority number,
        then earliest added_at time).

        Returns:
            The QueueItem that is now processing, or None if no pending items.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        from datetime import datetime, timezone

        with self._lock:
            for item in self._items:
                if item.status == "pending":
                    item.status = "processing"
                    item.started_at = datetime.now(timezone.utc).isoformat()
                    self._save()
                    return item
            return None

    def peek(self) -> Optional[QueueItem]:
        """View the next pending issue without removing it from the queue.

        Returns:
            The next pending QueueItem, or None if no pending items.
        """
        with self._lock:
            for item in self._items:
                if item.status == "pending":
                    return item
            return None

    def mark_completed(self, issue_number: int) -> Optional[QueueItem]:
        """Mark an issue as completed.

        Args:
            issue_number: The issue number to mark as completed.

        Returns:
            The updated QueueItem, or None if the issue is not in the queue.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        from datetime import datetime, timezone

        with self._lock:
            for item in self._items:
                if item.issue_number == issue_number:
                    item.status = "completed"
                    item.completed_at = datetime.now(timezone.utc).isoformat()
                    self._save()
                    return item
            return None

    def mark_failed(self, issue_number: int, error: Optional[str] = None) -> Optional[QueueItem]:
        """Mark an issue as failed.

        Args:
            issue_number: The issue number to mark as failed.
            error: Optional error message describing the failure.

        Returns:
            The updated QueueItem, or None if the issue is not in the queue.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        from datetime import datetime, timezone

        with self._lock:
            for item in self._items:
                if item.issue_number == issue_number:
                    item.status = "failed"
                    item.completed_at = datetime.now(timezone.utc).isoformat()
                    item.error = error
                    self._save()
                    return item
            return None

    def retry(self, issue_number: int) -> Optional[QueueItem]:
        """Reset a failed or completed issue to pending for retry.

        Args:
            issue_number: The issue number to retry.

        Returns:
            The updated QueueItem, or None if the issue is not in the queue.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        with self._lock:
            for item in self._items:
                if item.issue_number == issue_number:
                    item.status = "pending"
                    item.started_at = None
                    item.completed_at = None
                    item.error = None
                    item.retry_count += 1
                    self._sort_items()
                    self._save()
                    return item
            return None

    def get(self, issue_number: int) -> Optional[QueueItem]:
        """Get a queue item by issue number.

        Args:
            issue_number: The issue number to look up.

        Returns:
            The QueueItem if found, None otherwise.
        """
        with self._lock:
            for item in self._items:
                if item.issue_number == issue_number:
                    return item
            return None

    def remove(self, issue_number: int) -> bool:
        """Remove an issue from the queue.

        Args:
            issue_number: The issue number to remove.

        Returns:
            True if the issue was removed, False if it wasn't in the queue.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        with self._lock:
            for i, item in enumerate(self._items):
                if item.issue_number == issue_number:
                    del self._items[i]
                    self._save()
                    return True
            return False

    def list_items(self, status: Optional[str] = None) -> List[QueueItem]:
        """List all items in the queue, optionally filtered by status.

        Args:
            status: Optional status to filter by ('pending', 'processing',
                'completed', 'failed'). If None, returns all items.

        Returns:
            List of QueueItem objects.
        """
        with self._lock:
            if status is None:
                return list(self._items)
            return [item for item in self._items if item.status == status]

    def count(self, status: Optional[str] = None) -> int:
        """Count items in the queue, optionally filtered by status.

        Args:
            status: Optional status to filter by.

        Returns:
            The number of matching items.
        """
        with self._lock:
            if status is None:
                return len(self._items)
            return sum(1 for item in self._items if item.status == status)

    def clear(self, status: Optional[str] = None) -> int:
        """Clear items from the queue, optionally filtered by status.

        Args:
            status: Optional status to filter by. If None, clears all items.

        Returns:
            The number of items that were removed.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        with self._lock:
            if status is None:
                count = len(self._items)
                self._items = []
            else:
                original_count = len(self._items)
                self._items = [item for item in self._items if item.status != status]
                count = original_count - len(self._items)

            self._save()
            return count

    def is_empty(self) -> bool:
        """Check if the queue has no pending items.

        Returns:
            True if there are no pending items, False otherwise.
        """
        return self.count(status="pending") == 0

    def has_processing(self) -> bool:
        """Check if there are any items currently being processed.

        Returns:
            True if there are processing items, False otherwise.
        """
        return self.count(status="processing") > 0

    def reset_processing(self) -> int:
        """Reset all processing items back to pending.

        This is useful for recovery after a crash where items were left
        in the processing state.

        Returns:
            The number of items that were reset.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        count = 0
        with self._lock:
            for item in self._items:
                if item.status == "processing":
                    item.status = "pending"
                    item.started_at = None
                    count += 1

            if count > 0:
                self._sort_items()
                self._save()

            return count

    def enqueue_batch(self, issue_numbers: List[int], priority: int = 0) -> List[QueueItem]:
        """Add multiple issues to the queue.

        Args:
            issue_numbers: List of issue numbers to add.
            priority: Processing priority for all items. Defaults to 0.

        Returns:
            List of QueueItem objects that were added or already exist.

        Raises:
            ProcessingQueueError: If the operation fails.
        """
        items: List[QueueItem] = []
        with self._lock:
            for issue_number in issue_numbers:
                # Check if already in queue with non-terminal status
                existing = None
                for item in self._items:
                    if item.issue_number == issue_number and item.status in ("pending", "processing"):
                        existing = item
                        break

                if existing:
                    items.append(existing)
                else:
                    item = QueueItem(issue_number=issue_number, priority=priority)
                    self._items.append(item)
                    items.append(item)

            self._sort_items()
            self._save()

        return items


class PromptTransformerError(Exception):
    """Raised when prompt transformation operations fail."""
    pass


@dataclass
class TransformedPrompt:
    """Represents a transformed prompt ready for the planner phase.

    Attributes:
        issue_number: The GitHub issue number.
        prompt: The Ralph-compatible PRD prompt string.
        priority: Processing priority from the queue (lower = higher priority).
    """
    issue_number: int
    prompt: str
    priority: int = 0


class PromptTransformer:
    """Transforms queued issues into Ralph-compatible PRD prompts.

    This class bridges the ProcessingQueue and IssueStore to produce prompts
    that can be consumed by Ralph's planner phase. It retrieves pending issues
    from the queue, looks up their details from the store, and transforms them
    into the expected prompt format.

    Example:
        >>> store = IssueStore(".ralph/issues")
        >>> queue = ProcessingQueue(".ralph/queue")
        >>> transformer = PromptTransformer(queue, store)
        >>> # Get all pending prompts
        >>> prompts = transformer.get_pending_prompts()
        >>> # Get and dequeue the next prompt for processing
        >>> prompt = transformer.get_next_prompt()
    """

    def __init__(self, queue: ProcessingQueue, store: IssueStore):
        """Initialize the PromptTransformer.

        Args:
            queue: The ProcessingQueue containing queued issue numbers.
            store: The IssueStore containing issue details.
        """
        self._queue = queue
        self._store = store

    @property
    def queue(self) -> ProcessingQueue:
        """Get the processing queue."""
        return self._queue

    @property
    def store(self) -> IssueStore:
        """Get the issue store."""
        return self._store

    def _stored_issue_to_issue(self, stored: StoredIssue) -> Issue:
        """Convert a StoredIssue to an Issue for prompt transformation.

        Args:
            stored: The StoredIssue to convert.

        Returns:
            An Issue object with the same core fields.
        """
        return Issue(
            number=stored.number,
            title=stored.title,
            body=stored.body,
            url=stored.url,
            labels=stored.labels,
        )

    def transform(self, issue_number: int) -> TransformedPrompt:
        """Transform a single issue into a Ralph-compatible prompt.

        Retrieves the issue from the store and transforms it into the
        prompt format expected by Ralph's planner phase.

        Args:
            issue_number: The GitHub issue number to transform.

        Returns:
            A TransformedPrompt containing the issue number and prompt string.

        Raises:
            PromptTransformerError: If the issue is not found in the store.
        """
        stored_issue = self._store.get(issue_number)
        if stored_issue is None:
            raise PromptTransformerError(
                f"Issue {issue_number} not found in store"
            )

        issue = self._stored_issue_to_issue(stored_issue)
        prompt = issue_to_prompt(issue)

        # Try to get priority from queue if the issue is queued
        priority = 0
        for item in self._queue.list_items():
            if item.issue_number == issue_number:
                priority = item.priority
                break

        return TransformedPrompt(
            issue_number=issue_number,
            prompt=prompt,
            priority=priority,
        )

    def transform_batch(self, issue_numbers: List[int]) -> List[TransformedPrompt]:
        """Transform multiple issues into Ralph-compatible prompts.

        Args:
            issue_numbers: List of issue numbers to transform.

        Returns:
            List of TransformedPrompt objects. Issues not found in the store
            are silently skipped.
        """
        prompts: List[TransformedPrompt] = []
        for issue_number in issue_numbers:
            try:
                prompt = self.transform(issue_number)
                prompts.append(prompt)
            except PromptTransformerError:
                # Skip issues not in store
                continue
        return prompts

    def get_pending_prompts(self) -> List[TransformedPrompt]:
        """Get prompts for all pending issues in the queue.

        Retrieves all issues with 'pending' status from the queue,
        looks up their details, and transforms them into prompts.
        Results are ordered by queue priority (lower = higher priority).

        Returns:
            List of TransformedPrompt objects for pending issues,
            ordered by priority. Issues not found in the store are skipped.
        """
        pending_items = self._queue.list_items(status="pending")
        issue_numbers = [item.issue_number for item in pending_items]
        return self.transform_batch(issue_numbers)

    def get_next_prompt(self) -> Optional[TransformedPrompt]:
        """Get and dequeue the next pending issue as a prompt.

        This method dequeues the next pending issue from the queue
        (marking it as 'processing') and transforms it into a prompt.

        Returns:
            A TransformedPrompt for the next issue, or None if the queue
            is empty or the issue is not found in the store.

        Raises:
            PromptTransformerError: If the dequeued issue is not found
                in the store.
        """
        item = self._queue.dequeue()
        if item is None:
            return None

        try:
            return self.transform(item.issue_number)
        except PromptTransformerError:
            # Issue not in store - mark as failed and re-raise
            self._queue.mark_failed(
                item.issue_number,
                error="Issue not found in store"
            )
            raise

    def peek_next_prompt(self) -> Optional[TransformedPrompt]:
        """Preview the next pending issue as a prompt without dequeuing.

        Returns:
            A TransformedPrompt for the next issue, or None if the queue
            is empty or the issue is not found in the store.
        """
        item = self._queue.peek()
        if item is None:
            return None

        try:
            return self.transform(item.issue_number)
        except PromptTransformerError:
            return None

    def count_pending(self) -> int:
        """Count the number of pending issues in the queue.

        Returns:
            The number of issues with 'pending' status.
        """
        return self._queue.count(status="pending")

    def count_transformable(self) -> int:
        """Count pending issues that can be transformed (exist in store).

        Returns:
            The number of pending issues that have entries in the store.
        """
        count = 0
        for item in self._queue.list_items(status="pending"):
            if self._store.get(item.issue_number) is not None:
                count += 1
        return count

    def mark_completed(self, issue_number: int) -> bool:
        """Mark an issue as completed in the queue.

        Args:
            issue_number: The issue number to mark as completed.

        Returns:
            True if the issue was found and marked, False otherwise.
        """
        result = self._queue.mark_completed(issue_number)
        return result is not None

    def mark_failed(self, issue_number: int, error: Optional[str] = None) -> bool:
        """Mark an issue as failed in the queue.

        Args:
            issue_number: The issue number to mark as failed.
            error: Optional error message describing the failure.

        Returns:
            True if the issue was found and marked, False otherwise.
        """
        result = self._queue.mark_failed(issue_number, error=error)
        return result is not None


class PlannerInvokerError(Exception):
    """Raised when planner invocation operations fail."""
    pass


@dataclass
class InvocationResult:
    """Result of invoking the planner for a single issue.

    Attributes:
        issue_number: The GitHub issue number that was processed.
        success: Whether the planner invocation succeeded.
        error: Error message if the invocation failed (None otherwise).
    """
    issue_number: int
    success: bool
    error: Optional[str] = None


class PlannerInvoker:
    """Invokes Ralph's planner phase for each queued issue to generate PRDs automatically.

    This class orchestrates the automatic PRD generation workflow by:
    1. Getting the next pending issue from the queue via PromptTransformer
    2. Invoking Ralph's planner phase with the transformed prompt
    3. Tracking success/failure status in the queue
    4. Optionally marking processed GitHub issues with appropriate labels

    Example:
        >>> store = IssueStore(".ralph/issues")
        >>> queue = ProcessingQueue(".ralph/queue")
        >>> transformer = PromptTransformer(queue, store)
        >>> invoker = PlannerInvoker(transformer)
        >>> # Process a single issue
        >>> result = invoker.process_next()
        >>> # Process all pending issues
        >>> results = invoker.process_all()
    """

    def __init__(
        self,
        transformer: PromptTransformer,
        agent_name: str = "claude",
        enable_hooks: bool = True,
        mark_github_issues: bool = False
    ):
        """Initialize the PlannerInvoker.

        Args:
            transformer: A PromptTransformer instance for getting prompts.
            agent_name: The AI agent to use for planning. Defaults to "claude".
            enable_hooks: Whether to enable Ralph hook execution. Defaults to True.
            mark_github_issues: Whether to update GitHub issue labels after
                processing (swap 'ready' for 'processed'). Defaults to False.
        """
        self._transformer = transformer
        self._agent_name = agent_name
        self._enable_hooks = enable_hooks
        self._mark_github_issues = mark_github_issues

    @property
    def transformer(self) -> PromptTransformer:
        """Get the prompt transformer."""
        return self._transformer

    @property
    def agent_name(self) -> str:
        """Get the agent name used for planning."""
        return self._agent_name

    @property
    def enable_hooks(self) -> bool:
        """Get whether hooks are enabled."""
        return self._enable_hooks

    @property
    def mark_github_issues(self) -> bool:
        """Get whether GitHub issues are marked after processing."""
        return self._mark_github_issues

    def process_next(self) -> Optional[InvocationResult]:
        """Process the next pending issue in the queue.

        Dequeues the next pending issue, transforms it to a prompt,
        and invokes Ralph's planner phase to generate a PRD.

        Returns:
            An InvocationResult if there was an issue to process,
            None if the queue is empty.
        """
        try:
            prompt = self._transformer.get_next_prompt()
        except PromptTransformerError as e:
            # Issue was dequeued but not found in store - already marked failed
            # Find the most recently failed item (the one we just tried)
            failed_items = self._transformer.queue.list_items(status="failed")
            if failed_items:
                # Return the last failed item (most recent)
                return InvocationResult(
                    issue_number=failed_items[-1].issue_number,
                    success=False,
                    error=str(e)
                )
            return None

        if prompt is None:
            return None

        issue_number = prompt.issue_number

        try:
            success = invoke_planner(
                user_intent=prompt.prompt,
                agent_name=self._agent_name,
                enable_hooks=self._enable_hooks
            )

            if success:
                self._transformer.mark_completed(issue_number)
                if self._mark_github_issues:
                    try:
                        mark_issue_processed(issue_number)
                    except GitHubCLIError:
                        # Don't fail the whole operation if label update fails
                        pass
                return InvocationResult(
                    issue_number=issue_number,
                    success=True
                )
            else:
                error_msg = "Planner phase did not complete successfully"
                self._transformer.mark_failed(issue_number, error=error_msg)
                return InvocationResult(
                    issue_number=issue_number,
                    success=False,
                    error=error_msg
                )

        except PlannerError as e:
            self._transformer.mark_failed(issue_number, error=str(e))
            return InvocationResult(
                issue_number=issue_number,
                success=False,
                error=str(e)
            )

    def process_all(self) -> Tuple[List[InvocationResult], int, int]:
        """Process all pending issues in the queue.

        Iterates through all pending issues, invoking the planner for each.
        Processing continues even if individual issues fail.

        Returns:
            A tuple containing:
                - List of InvocationResult objects with status for each issue
                - Count of successfully processed issues
                - Count of failed issues
        """
        results: List[InvocationResult] = []
        success_count = 0
        failure_count = 0

        while True:
            result = self.process_next()
            if result is None:
                break

            results.append(result)
            if result.success:
                success_count += 1
            else:
                failure_count += 1

        return results, success_count, failure_count

    def count_pending(self) -> int:
        """Count the number of pending issues in the queue.

        Returns:
            The number of issues with 'pending' status.
        """
        return self._transformer.count_pending()

    def has_pending(self) -> bool:
        """Check if there are pending issues to process.

        Returns:
            True if there are pending issues, False otherwise.
        """
        return self.count_pending() > 0


# ==============================================================================
# ISSUE WATCHER - Daemon orchestration
# ==============================================================================

@dataclass
class WatcherConfig:
    """Configuration for the IssueWatcher daemon.

    Attributes:
        label: The label to filter issues by. Defaults to "ready".
        poll_interval: Polling interval in seconds. Defaults to 60.
        store_dir: Directory for storing issues. Defaults to ".ralph/issues".
        queue_dir: Directory for queue state. Defaults to ".ralph/queue".
        hooks_dir: Directory for hooks. Defaults to ".ralph/hooks".
        pid_file: Path to the PID file for daemon management. Defaults to ".ralph/watcher.pid".
        log_file: Path to the watcher log file. Defaults to ".ralph/watcher.log".
        agent_name: The AI agent to use for planning. Defaults to "claude".
        enable_hooks: Whether to enable Ralph hook execution. Defaults to True.
        mark_github_issues: Whether to update GitHub issue labels after processing. Defaults to False.
        auto_process: Whether to automatically process issues after detection. Defaults to True.
    """
    label: str = "ready"
    poll_interval: float = 60.0
    store_dir: str = ".ralph/issues"
    queue_dir: str = ".ralph/queue"
    hooks_dir: str = ".ralph/hooks"
    pid_file: str = ".ralph/watcher.pid"
    log_file: str = ".ralph/watcher.log"
    agent_name: str = "claude"
    enable_hooks: bool = True
    mark_github_issues: bool = False
    auto_process: bool = True


class IssueWatcherError(Exception):
    """Raised when issue watcher operations fail."""
    pass


@dataclass
class WatcherStatus:
    """Status information for the IssueWatcher daemon.

    Attributes:
        running: Whether the watcher is currently running.
        pid: Process ID if running, None otherwise.
        issues_stored: Number of issues in the store.
        issues_pending: Number of pending issues in the queue.
        issues_processing: Number of issues currently being processed.
        issues_completed: Number of completed issues in the queue.
        issues_failed: Number of failed issues in the queue.
    """
    running: bool
    pid: Optional[int] = None
    issues_stored: int = 0
    issues_pending: int = 0
    issues_processing: int = 0
    issues_completed: int = 0
    issues_failed: int = 0

    def to_dict(self) -> dict:
        """Convert status to dictionary."""
        return {
            "running": self.running,
            "pid": self.pid,
            "issues_stored": self.issues_stored,
            "issues_pending": self.issues_pending,
            "issues_processing": self.issues_processing,
            "issues_completed": self.issues_completed,
            "issues_failed": self.issues_failed,
        }


class IssueWatcher:
    """Orchestrates the issue watching daemon that polls GitHub, stores issues,
    queues them for processing, and invokes the planner to generate PRDs.

    This class ties together all the previously implemented components:
    - GitHubPoller: Polls GitHub for new issues with the specified label
    - IssueStore: Persists issues locally for audit and recovery
    - ProcessingQueue: Maintains ordered processing queue with state tracking
    - PromptTransformer: Transforms issues into Ralph-compatible prompts
    - PlannerInvoker: Invokes Ralph's planner phase to generate PRDs

    The watcher can be started as a foreground process or controlled via
    CLI commands for daemon management.

    Example:
        >>> config = WatcherConfig(label="ready", poll_interval=30.0)
        >>> watcher = IssueWatcher(config)
        >>> watcher.start()  # Blocks and runs polling loop
        >>> # Or check status
        >>> status = watcher.get_status()
        >>> print(f"Pending: {status.issues_pending}")
    """

    def __init__(self, config: Optional[WatcherConfig] = None, hooks: Optional["HookManager"] = None):
        """Initialize the IssueWatcher.

        Args:
            config: Optional WatcherConfig with watcher settings. If None,
                uses default configuration.
            hooks: Optional HookManager for emitting events. If None and
                enable_hooks is True, creates one from hooks_dir.
        """
        self._config = config or WatcherConfig()
        self._store = IssueStore(self._config.store_dir)
        self._queue = ProcessingQueue(self._config.queue_dir)
        self._transformer = PromptTransformer(self._queue, self._store)
        self._invoker = PlannerInvoker(
            self._transformer,
            agent_name=self._config.agent_name,
            enable_hooks=self._config.enable_hooks,
            mark_github_issues=self._config.mark_github_issues
        )
        self._poller: Optional[GitHubPoller] = None
        self._running = False
        self._stop_event = threading.Event()
        self._pid_path = Path(self._config.pid_file)
        self._log_path = Path(self._config.log_file)

        # Initialize HookManager
        if hooks is not None:
            self._hooks = hooks
        elif self._config.enable_hooks:
            from hooks import HookManager
            self._hooks = HookManager(Path(self._config.hooks_dir))
        else:
            self._hooks = None

    @property
    def config(self) -> WatcherConfig:
        """Get the watcher configuration."""
        return self._config

    @property
    def store(self) -> IssueStore:
        """Get the issue store."""
        return self._store

    @property
    def queue(self) -> ProcessingQueue:
        """Get the processing queue."""
        return self._queue

    @property
    def is_running(self) -> bool:
        """Check if the watcher is currently running."""
        return self._running

    @property
    def hooks(self) -> Optional["HookManager"]:
        """Get the hook manager."""
        return self._hooks

    def _emit(self, event: "Event") -> None:
        """Emit an event if hooks are enabled.

        Args:
            event: The event to emit.
        """
        if self._hooks is not None:
            self._hooks.emit(event)

    def _log(self, message: str) -> None:
        """Write a log message to the watcher log file.

        Args:
            message: The message to log.
        """
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()
        log_line = f"[{timestamp}] {message}\n"

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(log_line)
        except OSError:
            pass  # Silently ignore log errors

    def _write_pid(self) -> None:
        """Write the current process ID to the PID file."""
        import os

        try:
            self._pid_path.parent.mkdir(parents=True, exist_ok=True)
            self._pid_path.write_text(str(os.getpid()), encoding='utf-8')
        except OSError as e:
            raise IssueWatcherError(f"Failed to write PID file: {e}")

    def _remove_pid(self) -> None:
        """Remove the PID file."""
        try:
            if self._pid_path.exists():
                self._pid_path.unlink()
        except OSError:
            pass  # Ignore removal errors

    def _read_pid(self) -> Optional[int]:
        """Read the process ID from the PID file.

        Returns:
            The PID if the file exists and is valid, None otherwise.
        """
        try:
            if self._pid_path.exists():
                content = self._pid_path.read_text(encoding='utf-8').strip()
                return int(content)
        except (OSError, ValueError):
            pass
        return None

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with the given PID is running.

        Args:
            pid: The process ID to check.

        Returns:
            True if the process is running, False otherwise.
        """
        import os

        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _on_new_issues(self, issues: List[Issue]) -> None:
        """Callback invoked when new issues are detected by the poller.

        Stores the issues, enqueues them for processing, and optionally
        triggers automatic processing.

        Args:
            issues: List of newly detected Issue objects.
        """
        from hooks import Event, EventType

        self._log(f"Detected {len(issues)} new issue(s)")

        for issue in issues:
            # Emit ISSUE_DETECTED event
            self._emit(Event(
                EventType.ISSUE_DETECTED,
                issue_number=issue.number,
                issue_title=issue.title,
                issue_url=issue.url,
                issues_count=len(issues)
            ))

            # Store the issue
            try:
                self._store.save(issue, status="pending")
                self._log(f"Stored issue #{issue.number}: {issue.title}")
                # Emit ISSUE_STORED event
                self._emit(Event(
                    EventType.ISSUE_STORED,
                    issue_number=issue.number,
                    issue_title=issue.title,
                    issue_url=issue.url
                ))
            except IssueStoreError as e:
                self._log(f"Failed to store issue #{issue.number}: {e}")
                continue

            # Enqueue for processing
            try:
                self._queue.enqueue(issue.number)
                self._log(f"Enqueued issue #{issue.number} for processing")
                # Emit ISSUE_QUEUED event
                self._emit(Event(
                    EventType.ISSUE_QUEUED,
                    issue_number=issue.number,
                    issue_title=issue.title,
                    issue_url=issue.url
                ))
            except ProcessingQueueError as e:
                self._log(f"Failed to enqueue issue #{issue.number}: {e}")

        # Trigger automatic processing if enabled
        if self._config.auto_process and issues:
            self._process_pending()

    def _on_poll_error(self, error: Exception) -> None:
        """Callback invoked when an error occurs during polling.

        Args:
            error: The exception that occurred.
        """
        from hooks import Event, EventType

        self._log(f"Polling error: {error}")
        self._emit(Event(
            EventType.POLL_ERROR,
            error=str(error)
        ))

    def _process_pending(self) -> None:
        """Process all pending issues in the queue."""
        from hooks import Event, EventType

        self._log("Processing pending issues...")

        while not self._stop_event.is_set():
            result = self._invoker.process_next()
            if result is None:
                break

            # Emit ISSUE_PROCESSING_START event
            self._emit(Event(
                EventType.ISSUE_PROCESSING_START,
                issue_number=result.issue_number
            ))

            if result.success:
                self._log(f"Successfully processed issue #{result.issue_number}")
                # Emit ISSUE_PROCESSING_SUCCESS event
                self._emit(Event(
                    EventType.ISSUE_PROCESSING_SUCCESS,
                    issue_number=result.issue_number
                ))
            else:
                self._log(f"Failed to process issue #{result.issue_number}: {result.error}")
                # Emit ISSUE_PROCESSING_FAILURE event
                self._emit(Event(
                    EventType.ISSUE_PROCESSING_FAILURE,
                    issue_number=result.issue_number,
                    error=result.error
                ))

        self._log("Finished processing pending issues")

    def get_status(self) -> WatcherStatus:
        """Get the current status of the watcher.

        Returns:
            A WatcherStatus object with current state information.
        """
        pid = self._read_pid()
        running = False

        if pid is not None:
            running = self._is_process_running(pid)
            if not running:
                # Stale PID file - clean it up
                self._remove_pid()
                pid = None

        return WatcherStatus(
            running=running,
            pid=pid,
            issues_stored=self._store.count(),
            issues_pending=self._queue.count(status="pending"),
            issues_processing=self._queue.count(status="processing"),
            issues_completed=self._queue.count(status="completed"),
            issues_failed=self._queue.count(status="failed"),
        )

    def start(self, foreground: bool = True) -> bool:
        """Start the watcher daemon.

        Args:
            foreground: If True, run in the foreground (blocking).
                If False, the watcher should be started as a background process
                by the caller.

        Returns:
            True if the watcher was started, False if already running.

        Raises:
            IssueWatcherError: If starting the watcher fails.
        """
        from hooks import Event, EventType

        # Check if already running
        status = self.get_status()
        if status.running:
            return False

        self._running = True
        self._stop_event.clear()
        self._write_pid()
        self._log("Watcher started")

        # Emit WATCHER_START event
        self._emit(Event(EventType.WATCHER_START))

        # Reset any stale processing items from previous crashes
        reset_count = self._queue.reset_processing()
        if reset_count > 0:
            self._log(f"Reset {reset_count} stale processing item(s) to pending")

        # Initialize poller
        poller_config = PollerConfig(
            label=self._config.label,
            interval=self._config.poll_interval,
            on_new_issues=self._on_new_issues,
            on_error=self._on_poll_error
        )
        self._poller = GitHubPoller(poller_config)

        # Load existing seen issues from store to avoid reprocessing
        for stored in self._store.list_issues():
            self._poller._seen_issue_numbers.add(stored.number)
        self._log(f"Loaded {len(self._poller._seen_issue_numbers)} known issue(s)")

        if foreground:
            self._run_foreground()

        return True

    def _run_foreground(self) -> None:
        """Run the watcher in the foreground (blocking)."""
        self._log("Running in foreground mode")

        try:
            # Start the poller
            if self._poller is not None:
                self._poller.start()

            # Wait for stop signal
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)

        except KeyboardInterrupt:
            self._log("Received keyboard interrupt")
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources when stopping."""
        from hooks import Event, EventType

        if self._poller is not None:
            self._poller.stop(timeout=5.0)
            self._poller = None

        self._running = False
        self._remove_pid()
        self._log("Watcher stopped")

        # Emit WATCHER_STOP event
        self._emit(Event(EventType.WATCHER_STOP))

    def stop(self) -> bool:
        """Stop the watcher daemon.

        If the watcher is running in the current process, sets the stop event.
        If running in another process, sends SIGTERM.

        Returns:
            True if a stop signal was sent, False if not running.
        """
        import os
        import signal

        status = self.get_status()
        if not status.running:
            return False

        pid = status.pid
        if pid is None:
            return False

        # If running in current process
        if pid == os.getpid():
            self._stop_event.set()
            return True

        # Send SIGTERM to the other process
        try:
            os.kill(pid, signal.SIGTERM)
            self._log(f"Sent SIGTERM to process {pid}")
            return True
        except OSError as e:
            self._log(f"Failed to send SIGTERM to process {pid}: {e}")
            return False

    def poll_once(self) -> List[Issue]:
        """Perform a single poll for new issues.

        This can be used for manual polling without starting the daemon.

        Returns:
            List of new issues that were detected and stored.
        """
        from hooks import Event, EventType

        # Emit POLL_START event
        self._emit(Event(EventType.POLL_START))

        try:
            issues = fetch_ready_issues(label=self._config.label)
        except GitHubCLIError as e:
            self._log(f"Poll failed: {e}")
            # Emit POLL_ERROR event
            self._emit(Event(EventType.POLL_ERROR, error=str(e)))
            raise

        new_issues: List[Issue] = []
        for issue in issues:
            if not self._store.exists(issue.number):
                new_issues.append(issue)

        if new_issues:
            self._on_new_issues(new_issues)

        # Emit POLL_SUCCESS event
        self._emit(Event(
            EventType.POLL_SUCCESS,
            issues_count=len(new_issues)
        ))

        return new_issues

    def process_all(self) -> Tuple[List[InvocationResult], int, int]:
        """Process all pending issues in the queue.

        This can be used for manual processing without the daemon running.

        Returns:
            A tuple containing:
                - List of InvocationResult objects
                - Count of successfully processed issues
                - Count of failed issues
        """
        return self._invoker.process_all()


# ==============================================================================
# CLI for watcher daemon
# ==============================================================================

def create_watcher_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser for the watcher CLI.

    Returns:
        Configured ArgumentParser instance for the watcher commands.
    """
    parser = argparse.ArgumentParser(
        prog="ralph-watch",
        description="Manage the Ralph issue watcher daemon.",
        epilog="Examples:\n"
               "  ralph-watch start\n"
               "  ralph-watch start --interval 30\n"
               "  ralph-watch status\n"
               "  ralph-watch stop",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Start command
    start_parser = subparsers.add_parser(
        "start",
        help="Start the issue watcher daemon"
    )
    start_parser.add_argument(
        "--label",
        default="ready",
        help="Filter issues by label (default: ready)"
    )
    start_parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Polling interval in seconds (default: 60)"
    )
    start_parser.add_argument(
        "--agent",
        default="claude",
        help="AI agent to use for planning (default: claude)"
    )
    start_parser.add_argument(
        "--no-hooks",
        action="store_true",
        dest="no_hooks",
        help="Disable Ralph hook execution"
    )
    start_parser.add_argument(
        "--mark-issues",
        action="store_true",
        dest="mark_issues",
        help="Update GitHub issue labels after processing"
    )
    start_parser.add_argument(
        "--no-auto-process",
        action="store_true",
        dest="no_auto_process",
        help="Don't automatically process issues (just store and queue)"
    )
    start_parser.add_argument(
        "--foreground", "-f",
        action="store_true",
        help="Run in foreground (default behavior)"
    )

    # Stop command
    subparsers.add_parser(
        "stop",
        help="Stop the issue watcher daemon"
    )

    # Status command
    status_parser = subparsers.add_parser(
        "status",
        help="Show watcher daemon status"
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output status as JSON"
    )

    # Poll command (one-time poll)
    poll_parser = subparsers.add_parser(
        "poll",
        help="Perform a one-time poll for new issues"
    )
    poll_parser.add_argument(
        "--label",
        default="ready",
        help="Filter issues by label (default: ready)"
    )

    # Process command (process pending issues)
    process_parser = subparsers.add_parser(
        "process",
        help="Process all pending issues in the queue"
    )
    process_parser.add_argument(
        "--agent",
        default="claude",
        help="AI agent to use for planning (default: claude)"
    )
    process_parser.add_argument(
        "--no-hooks",
        action="store_true",
        dest="no_hooks",
        help="Disable Ralph hook execution"
    )

    return parser


def watcher_main(args: Optional[List[str]] = None) -> int:
    """Main entry point for the watcher CLI.

    Args:
        args: Command line arguments. If None, uses sys.argv.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = create_watcher_parser()
    parsed_args = parser.parse_args(args)

    if parsed_args.command is None:
        parser.print_help()
        return 1

    if parsed_args.command == "start":
        return _handle_start(parsed_args)
    elif parsed_args.command == "stop":
        return _handle_stop()
    elif parsed_args.command == "status":
        return _handle_status(parsed_args)
    elif parsed_args.command == "poll":
        return _handle_poll(parsed_args)
    elif parsed_args.command == "process":
        return _handle_process(parsed_args)

    return 1


def _handle_start(args: argparse.Namespace) -> int:
    """Handle the 'start' command.

    Args:
        args: Parsed command line arguments.

    Returns:
        Exit code.
    """
    config = WatcherConfig(
        label=args.label,
        poll_interval=args.interval,
        agent_name=args.agent,
        enable_hooks=not args.no_hooks,
        mark_github_issues=args.mark_issues,
        auto_process=not args.no_auto_process
    )

    watcher = IssueWatcher(config)
    status = watcher.get_status()

    if status.running:
        Logger.warning(f"Watcher is already running (PID: {status.pid})")
        return 1

    Logger.info("Starting watcher...")
    Logger.info(f"  Label: {config.label}")
    Logger.info(f"  Poll interval: {config.poll_interval}s")
    Logger.info(f"  Agent: {config.agent_name}")
    Logger.info(f"  Auto-process: {config.auto_process}")

    try:
        watcher.start(foreground=True)
        return 0
    except IssueWatcherError as e:
        Logger.error(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        Logger.info("\nStopped.")
        return 0


def _handle_stop() -> int:
    """Handle the 'stop' command.

    Returns:
        Exit code.
    """
    watcher = IssueWatcher()
    status = watcher.get_status()

    if not status.running:
        Logger.warning("Watcher is not running")
        return 1

    Logger.info(f"Stopping watcher (PID: {status.pid})...")

    if watcher.stop():
        Logger.info("Stop signal sent")
        return 0
    else:
        Logger.error("Failed to stop watcher")
        return 1


def _handle_status(args: argparse.Namespace) -> int:
    """Handle the 'status' command.

    Args:
        args: Parsed command line arguments.

    Returns:
        Exit code.
    """
    watcher = IssueWatcher()
    status = watcher.get_status()

    if args.json_output:
        Logger.info(json.dumps(status.to_dict(), indent=2))
    else:
        running_str = "running" if status.running else "stopped"
        Logger.info(f"Watcher status: {running_str}")
        if status.running:
            Logger.info(f"  PID: {status.pid}")
        Logger.info(f"  Issues stored: {status.issues_stored}")
        Logger.info(f"  Queue pending: {status.issues_pending}")
        Logger.info(f"  Queue processing: {status.issues_processing}")
        Logger.info(f"  Queue completed: {status.issues_completed}")
        Logger.info(f"  Queue failed: {status.issues_failed}")

    return 0


def _handle_poll(args: argparse.Namespace) -> int:
    """Handle the 'poll' command.

    Args:
        args: Parsed command line arguments.

    Returns:
        Exit code.
    """
    config = WatcherConfig(label=args.label)
    watcher = IssueWatcher(config)

    Logger.info(f"Polling for issues with label '{args.label}'...")

    try:
        new_issues = watcher.poll_once()
        if new_issues:
            Logger.info(f"Found {len(new_issues)} new issue(s):")
            for issue in new_issues:
                Logger.info(f"  #{issue.number}: {issue.title}")
        else:
            Logger.info("No new issues found")
        return 0
    except GitHubCLIError as e:
        Logger.error(f"Error: {e}")
        return 1


def _handle_process(args: argparse.Namespace) -> int:
    """Handle the 'process' command.

    Args:
        args: Parsed command line arguments.

    Returns:
        Exit code.
    """
    config = WatcherConfig(
        agent_name=args.agent,
        enable_hooks=not args.no_hooks,
        auto_process=False  # We're manually processing
    )
    watcher = IssueWatcher(config)

    pending_count = watcher.queue.count(status="pending")
    if pending_count == 0:
        Logger.info("No pending issues to process")
        return 0

    Logger.info(f"Processing {pending_count} pending issue(s)...")

    try:
        results, success_count, failure_count = watcher.process_all()
        Logger.info(f"Processed {len(results)} issue(s): {success_count} succeeded, {failure_count} failed")

        for result in results:
            status_str = "SUCCESS" if result.success else f"FAILED: {result.error}"
            Logger.info(f"  #{result.issue_number}: {status_str}")

        if failure_count == 0:
            return 0
        return 1
    except PlannerError as e:
        Logger.error(f"Error: {e}")
        return 1


# ==============================================================================
# CLI for batch issue processing
# ==============================================================================

def create_batch_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser for the batch processor CLI.

    Returns:
        Configured ArgumentParser instance for batch processing.
    """
    parser = argparse.ArgumentParser(
        prog="ralph-batch",
        description="Process GitHub issues in batch through Ralph's orchestration loop.",
        epilog="Examples:\n"
               "  ralph-batch --label ready\n"
               "  ralph-batch --verbose\n"
               "  ralph-batch --dry-run\n"
               "  ralph-batch --label enhancement --verbose",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--label",
        default="ready",
        help="Filter issues by label (default: ready)"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed progress logging for each issue"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Fetch issues and generate PRDs but skip Ralph execution"
    )

    parser.add_argument(
        "--agent",
        default="claude",
        help="AI agent to use for processing (default: claude)"
    )

    parser.add_argument(
        "--no-hooks",
        action="store_true",
        dest="no_hooks",
        help="Disable Ralph hook execution"
    )

    parser.add_argument(
        "--no-mark",
        action="store_true",
        dest="no_mark",
        help="Don't mark issues with 'processed' label on success"
    )

    return parser


def batch_main(args: Optional[List[str]] = None) -> int:
    """Main entry point for the batch processor CLI.

    Args:
        args: Command line arguments. If None, uses sys.argv.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = create_batch_parser()
    parsed_args = parser.parse_args(args)

    # Check gh CLI is available first
    if not check_gh_cli():
        Logger.error(
            "GitHub CLI (gh) is not installed or not authenticated. "
            "Please install gh CLI and run 'gh auth login' to authenticate."
        )
        return 1

    # Check memory directory exists (architect phase must have run)
    try:
        from config import CONF
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            raise PlannerError(
                "Memory directory is missing or empty. "
                "Run the architect phase first: ralph --architect"
            )
    except ImportError as e:
        Logger.error(f"Failed to import Ralph components: {e}")
        return 1
    except PlannerError as e:
        Logger.error(f"Planner error: {e}")
        return 1

    verbose = parsed_args.verbose
    dry_run = parsed_args.dry_run

    if verbose:
        Logger.info(f"Starting batch processing with label: {parsed_args.label}")
        if dry_run:
            Logger.info("Dry-run mode: PRDs will be generated but Ralph execution skipped")

    try:
        result = batch_process_issues(
            label=parsed_args.label,
            agent_name=parsed_args.agent,
            enable_hooks=not parsed_args.no_hooks,
            execute_orchestration=not dry_run,
            mark_processed=not parsed_args.no_mark
        )

        # Output summary
        if verbose:
            Logger.info("")
            Logger.info("=" * 60)
            Logger.info("BATCH PROCESSING SUMMARY")
            Logger.info("=" * 60)

        Logger.info(f"Total issues found: {result.issues_found}")
        Logger.info(f"Successfully processed: {result.issues_processed}")
        Logger.info(f"Failed: {result.issues_failed}")

        if result.total_tasks_completed > 0 or result.total_tasks_failed > 0:
            Logger.info(f"Total tasks completed: {result.total_tasks_completed}")
            Logger.info(f"Total tasks failed: {result.total_tasks_failed}")

        if verbose and result.results:
            Logger.info("")
            Logger.info("Individual issue results:")
            for r in result.results:
                if r.execution_success:
                    status = "SUCCESS"
                    if r.tasks_total > 0:
                        status += f" ({r.tasks_completed}/{r.tasks_total} tasks)"
                else:
                    status = f"FAILED: {r.error}" if r.error else "FAILED"
                Logger.info(f"  #{r.issue_number}: {status}")

        Logger.info("")
        Logger.info(result.message)

        # Return exit code based on results
        if result.issues_failed > 0:
            return 1
        return 0

    except GitHubCLIError as e:
        Logger.error(f"Error: {e}")
        return 1
    except PlannerError as e:
        Logger.error(f"Planner error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
