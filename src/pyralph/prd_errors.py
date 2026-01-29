"""PRD generation error classes with structured error messages.

This module provides error classes for PRD markdown generation failures,
ensuring consistent error formatting with error codes, descriptions,
suggested resolutions, and relevant CLI flags.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class PRDErrorCode(Enum):
    """Error codes for PRD generation failures."""

    # Exploration errors (E1xx)
    E100_EMPTY_EXPLORATION = "E100"
    E101_INCOMPLETE_EXPLORATION = "E101"

    # Intent errors (E2xx)
    E200_MISSING_INTENT = "E200"
    E201_EMPTY_INTENT = "E201"

    # Timeout errors (E3xx)
    E300_GENERATION_TIMEOUT = "E300"

    # Markdown generation errors (E4xx)
    E400_MARKDOWN_GENERATION_FAILED = "E400"
    E401_MARKDOWN_PARSE_FAILED = "E401"
    E402_NO_USER_STORIES = "E402"

    # Validation errors (E5xx)
    E500_VALIDATION_FAILED = "E500"


@dataclass
class PRDError:
    """Structured PRD error with code, description, resolution, and CLI flags.

    Attributes:
        code: The error code enum value
        description: Human-readable description of the error
        resolution: Suggested steps to resolve the error
        cli_flags: Relevant CLI flags that may help resolve the issue
        partial_progress: Optional indicator of partial progress (for timeouts)
    """

    code: PRDErrorCode
    description: str
    resolution: str
    cli_flags: List[str]
    partial_progress: Optional[str] = None

    def format_message(self) -> str:
        """Format the error as a human-readable message.

        Returns:
            Formatted error message with all components
        """
        lines = [
            f"[{self.code.value}] {self.description}",
            "",
            f"Resolution: {self.resolution}",
        ]

        if self.cli_flags:
            flags_str = ", ".join(self.cli_flags)
            lines.append(f"Relevant CLI flags: {flags_str}")

        if self.partial_progress:
            lines.append(f"Partial progress: {self.partial_progress}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert error to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the error
        """
        result = {
            "error_code": self.code.value,
            "description": self.description,
            "resolution": self.resolution,
            "cli_flags": self.cli_flags,
        }
        if self.partial_progress:
            result["partial_progress"] = self.partial_progress
        return result


def create_empty_exploration_error() -> PRDError:
    """Create error for when exploration returns empty results.

    Returns:
        PRDError with appropriate message for empty exploration
    """
    return PRDError(
        code=PRDErrorCode.E100_EMPTY_EXPLORATION,
        description="Insufficient codebase context: exploration found no analyzable content",
        resolution=(
            "Ensure the codebase directory contains source files. "
            "Check that --tree-ignore patterns are not excluding all content. "
            "Verify the working directory is correct."
        ),
        cli_flags=["--tree-depth", "--tree-ignore"],
    )


def create_incomplete_exploration_warning(missing_areas: List[str]) -> PRDError:
    """Create warning for incomplete exploration context.

    Args:
        missing_areas: List of context areas that are missing or incomplete

    Returns:
        PRDError with warning level message
    """
    areas_str = ", ".join(missing_areas) if missing_areas else "unknown areas"
    return PRDError(
        code=PRDErrorCode.E101_INCOMPLETE_EXPLORATION,
        description=f"Exploration context incomplete: missing {areas_str}",
        resolution=(
            "Generation will continue with available context. "
            "For more complete results, ensure all directories are accessible "
            "and increase tree depth if needed."
        ),
        cli_flags=["--tree-depth", "--tree-ignore", "--reuse-context"],
    )


def create_missing_intent_error() -> PRDError:
    """Create error for when intent is missing.

    Returns:
        PRDError with appropriate message for missing intent
    """
    return PRDError(
        code=PRDErrorCode.E200_MISSING_INTENT,
        description="Cannot generate PRD: no intent provided",
        resolution=(
            "Provide an intent describing what you want to build. "
            "Use --intent for inline text or --intent-file to load from a file."
        ),
        cli_flags=["--intent", "--intent-file"],
    )


def create_empty_intent_error() -> PRDError:
    """Create error for when intent is empty.

    Returns:
        PRDError with appropriate message for empty intent
    """
    return PRDError(
        code=PRDErrorCode.E201_EMPTY_INTENT,
        description="Cannot generate PRD: no intent provided",
        resolution=(
            "The intent cannot be empty. "
            "Provide a description of what you want to build using --intent or --intent-file."
        ),
        cli_flags=["--intent", "--intent-file"],
    )


def create_timeout_error(
    current_stage: str,
    elapsed_seconds: int,
    timeout_seconds: int
) -> PRDError:
    """Create error for when PRD generation times out.

    Args:
        current_stage: Description of the stage where timeout occurred
        elapsed_seconds: Number of seconds elapsed before timeout
        timeout_seconds: The configured timeout value

    Returns:
        PRDError with timeout information and partial progress
    """
    return PRDError(
        code=PRDErrorCode.E300_GENERATION_TIMEOUT,
        description=(
            f"PRD generation timed out after {elapsed_seconds}s "
            f"(timeout: {timeout_seconds}s)"
        ),
        resolution=(
            "Increase the timeout value with --timeout. "
            "Consider reducing --tree-depth for faster exploration. "
            "Large codebases may require longer timeouts."
        ),
        cli_flags=["--timeout", "--tree-depth"],
        partial_progress=f"Stage reached: {current_stage}",
    )


def create_markdown_generation_error(attempt: int, max_attempts: int) -> PRDError:
    """Create error for when markdown generation fails.

    Args:
        attempt: The attempt number when failure occurred
        max_attempts: Maximum number of attempts configured

    Returns:
        PRDError for markdown generation failure
    """
    return PRDError(
        code=PRDErrorCode.E400_MARKDOWN_GENERATION_FAILED,
        description=(
            f"PRD markdown generation failed after {attempt}/{max_attempts} attempts"
        ),
        resolution=(
            "Check agent connectivity and configuration. "
            "Review logs for specific agent errors. "
            "Consider increasing timeout or retries."
        ),
        cli_flags=["--timeout", "--retries"],
        partial_progress=f"Failed on attempt {attempt} of {max_attempts}",
    )


def create_markdown_parse_error(error_detail: str) -> PRDError:
    """Create error for when markdown parsing fails.

    Args:
        error_detail: Details about the parsing failure

    Returns:
        PRDError for markdown parsing failure
    """
    return PRDError(
        code=PRDErrorCode.E401_MARKDOWN_PARSE_FAILED,
        description=f"Failed to parse PRD markdown: {error_detail}",
        resolution=(
            "Ensure the markdown follows the expected format. "
            "Check for malformed headers or tables. "
            "Review the generated prd-*.md file for syntax errors."
        ),
        cli_flags=["--reuse-context"],
    )


def create_no_user_stories_error() -> PRDError:
    """Create error for when no user stories are found in markdown.

    Returns:
        PRDError for missing user stories
    """
    return PRDError(
        code=PRDErrorCode.E402_NO_USER_STORIES,
        description="No user stories found in PRD markdown",
        resolution=(
            "Ensure the markdown contains a 'User Stories' section "
            "with TASK-XXX headers. Check the markdown format follows "
            "the expected template structure."
        ),
        cli_flags=["--revise-prd"],
    )


def create_validation_error(validation_message: str) -> PRDError:
    """Create error for PRD validation failure.

    Args:
        validation_message: The validation error message

    Returns:
        PRDError for validation failure
    """
    return PRDError(
        code=PRDErrorCode.E500_VALIDATION_FAILED,
        description=f"PRD validation failed: {validation_message}",
        resolution=(
            "Review the generated PRD and fix validation issues. "
            "Use --schema to validate against a custom schema. "
            "Use --min-criteria to adjust minimum acceptance criteria requirements."
        ),
        cli_flags=["--schema", "--min-criteria", "--revise-prd"],
    )
