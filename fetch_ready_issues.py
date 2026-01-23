#!/usr/bin/env python3
"""Fetch open issues with 'ready' label from the current repository using gh CLI.

This script uses the GitHub CLI (gh) to fetch all open issues that have the 'ready'
label from the current repository. The issues can be used as planner inputs for Ralph.
"""
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional


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


def fetch_ready_issues() -> List[Issue]:
    """Fetch all open issues with the 'ready' label from the current repository.

    Returns:
        List of Issue objects representing open issues with 'ready' label.

    Raises:
        GitHubCLIError: If gh CLI command fails.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--label", "ready",
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


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    if not check_gh_cli():
        print("Error: gh CLI is not installed or not authenticated.", file=sys.stderr)
        print("Please install gh CLI and run 'gh auth login'.", file=sys.stderr)
        return 1

    try:
        issues = fetch_ready_issues()

        if not issues:
            print("No open issues with 'ready' label found.")
            return 0

        output = {
            "count": len(issues),
            "issues": [issue.to_dict() for issue in issues]
        }
        print(json.dumps(output, indent=2))
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


if __name__ == "__main__":
    sys.exit(main())
