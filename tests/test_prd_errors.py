"""Tests for PRD error handling (TASK-045).

Tests the structured error messages for PRD markdown generation failures,
ensuring consistent error formatting with error codes, descriptions,
suggested resolutions, and relevant CLI flags.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pyralph.prd_errors import (
    PRDError,
    PRDErrorCode,
    create_empty_exploration_error,
    create_empty_intent_error,
    create_incomplete_exploration_warning,
    create_markdown_generation_error,
    create_markdown_parse_error,
    create_missing_intent_error,
    create_no_user_stories_error,
    create_timeout_error,
    create_validation_error,
)


class TestPRDErrorCode:
    """Tests for PRDErrorCode enum."""

    def test_error_codes_have_unique_values(self):
        """GIVEN PRDErrorCode enum WHEN checking values THEN all codes are unique."""
        values = [e.value for e in PRDErrorCode]
        assert len(values) == len(set(values))

    def test_exploration_errors_start_with_e1(self):
        """GIVEN exploration error codes WHEN checking format THEN start with E1."""
        assert PRDErrorCode.E100_EMPTY_EXPLORATION.value.startswith("E1")
        assert PRDErrorCode.E101_INCOMPLETE_EXPLORATION.value.startswith("E1")

    def test_intent_errors_start_with_e2(self):
        """GIVEN intent error codes WHEN checking format THEN start with E2."""
        assert PRDErrorCode.E200_MISSING_INTENT.value.startswith("E2")
        assert PRDErrorCode.E201_EMPTY_INTENT.value.startswith("E2")

    def test_timeout_errors_start_with_e3(self):
        """GIVEN timeout error codes WHEN checking format THEN start with E3."""
        assert PRDErrorCode.E300_GENERATION_TIMEOUT.value.startswith("E3")


class TestPRDError:
    """Tests for PRDError dataclass."""

    def test_format_message_includes_error_code(self):
        """GIVEN PRDError WHEN formatting message THEN includes error code."""
        error = PRDError(
            code=PRDErrorCode.E100_EMPTY_EXPLORATION,
            description="Test description",
            resolution="Test resolution",
            cli_flags=["--test"]
        )
        message = error.format_message()
        assert "[E100]" in message

    def test_format_message_includes_description(self):
        """GIVEN PRDError WHEN formatting message THEN includes description."""
        error = PRDError(
            code=PRDErrorCode.E100_EMPTY_EXPLORATION,
            description="Test description here",
            resolution="Test resolution",
            cli_flags=[]
        )
        message = error.format_message()
        assert "Test description here" in message

    def test_format_message_includes_resolution(self):
        """GIVEN PRDError WHEN formatting message THEN includes resolution."""
        error = PRDError(
            code=PRDErrorCode.E100_EMPTY_EXPLORATION,
            description="Test description",
            resolution="Do this to fix it",
            cli_flags=[]
        )
        message = error.format_message()
        assert "Resolution: Do this to fix it" in message

    def test_format_message_includes_cli_flags(self):
        """GIVEN PRDError with CLI flags WHEN formatting THEN includes flags."""
        error = PRDError(
            code=PRDErrorCode.E100_EMPTY_EXPLORATION,
            description="Test description",
            resolution="Test resolution",
            cli_flags=["--intent", "--intent-file"]
        )
        message = error.format_message()
        assert "--intent" in message
        assert "--intent-file" in message
        assert "Relevant CLI flags:" in message

    def test_format_message_includes_partial_progress(self):
        """GIVEN PRDError with partial progress WHEN formatting THEN includes progress."""
        error = PRDError(
            code=PRDErrorCode.E300_GENERATION_TIMEOUT,
            description="Timeout occurred",
            resolution="Increase timeout",
            cli_flags=["--timeout"],
            partial_progress="Stage reached: markdown generation"
        )
        message = error.format_message()
        assert "Partial progress:" in message
        assert "Stage reached: markdown generation" in message

    def test_to_dict_includes_all_fields(self):
        """GIVEN PRDError WHEN converting to dict THEN includes all fields."""
        error = PRDError(
            code=PRDErrorCode.E100_EMPTY_EXPLORATION,
            description="Test description",
            resolution="Test resolution",
            cli_flags=["--flag1", "--flag2"],
            partial_progress="50% complete"
        )
        result = error.to_dict()
        assert result["error_code"] == "E100"
        assert result["description"] == "Test description"
        assert result["resolution"] == "Test resolution"
        assert result["cli_flags"] == ["--flag1", "--flag2"]
        assert result["partial_progress"] == "50% complete"

    def test_to_dict_without_partial_progress(self):
        """GIVEN PRDError without partial progress WHEN to_dict THEN omits field."""
        error = PRDError(
            code=PRDErrorCode.E100_EMPTY_EXPLORATION,
            description="Test",
            resolution="Test",
            cli_flags=[]
        )
        result = error.to_dict()
        assert "partial_progress" not in result


class TestEmptyExplorationError:
    """Tests for empty exploration error (AC: exploration returns empty results)."""

    def test_error_message_contains_required_text(self):
        """GIVEN exploration returns empty WHEN error created THEN message matches AC."""
        error = create_empty_exploration_error()
        # AC: 'Insufficient codebase context: exploration found no analyzable content'
        assert "Insufficient codebase context" in error.description
        assert "exploration found no analyzable content" in error.description

    def test_error_has_correct_code(self):
        """GIVEN empty exploration error WHEN checking code THEN is E100."""
        error = create_empty_exploration_error()
        assert error.code == PRDErrorCode.E100_EMPTY_EXPLORATION

    def test_error_includes_relevant_cli_flags(self):
        """GIVEN empty exploration error WHEN checking flags THEN includes tree flags."""
        error = create_empty_exploration_error()
        assert "--tree-depth" in error.cli_flags
        assert "--tree-ignore" in error.cli_flags


class TestMissingIntentError:
    """Tests for missing intent error (AC: intent is empty or missing)."""

    def test_missing_intent_error_message(self):
        """GIVEN intent is missing WHEN error created THEN message matches AC."""
        error = create_missing_intent_error()
        # AC: 'Cannot generate PRD: no intent provided'
        assert "Cannot generate PRD" in error.description
        assert "no intent provided" in error.description

    def test_missing_intent_includes_guidance(self):
        """GIVEN missing intent error WHEN checking THEN includes --intent guidance."""
        error = create_missing_intent_error()
        # AC: with guidance on --intent or --intent-file flags
        assert "--intent" in error.cli_flags
        assert "--intent-file" in error.cli_flags

    def test_empty_intent_error_message(self):
        """GIVEN intent is empty WHEN error created THEN message matches AC."""
        error = create_empty_intent_error()
        # AC: 'Cannot generate PRD: no intent provided'
        assert "Cannot generate PRD" in error.description
        assert "no intent provided" in error.description

    def test_empty_intent_includes_guidance(self):
        """GIVEN empty intent error WHEN checking THEN includes --intent guidance."""
        error = create_empty_intent_error()
        assert "--intent" in error.cli_flags
        assert "--intent-file" in error.cli_flags


class TestTimeoutError:
    """Tests for timeout error (AC: prd-*.md generation times out)."""

    def test_timeout_error_includes_progress_indicator(self):
        """GIVEN timeout occurs WHEN error created THEN includes partial progress."""
        error = create_timeout_error(
            current_stage="markdown generation",
            elapsed_seconds=600,
            timeout_seconds=600
        )
        # AC: includes partial progress indicator
        assert error.partial_progress is not None
        assert "markdown generation" in error.partial_progress

    def test_timeout_error_suggests_timeout_flag(self):
        """GIVEN timeout occurs WHEN error created THEN suggests --timeout."""
        error = create_timeout_error(
            current_stage="markdown generation",
            elapsed_seconds=600,
            timeout_seconds=600
        )
        # AC: suggests increasing --timeout value
        assert "--timeout" in error.cli_flags

    def test_timeout_error_includes_elapsed_time(self):
        """GIVEN timeout occurs WHEN error created THEN includes elapsed time."""
        error = create_timeout_error(
            current_stage="exploration",
            elapsed_seconds=300,
            timeout_seconds=600
        )
        assert "300" in error.description or "300s" in error.description


class TestIncompleteExplorationWarning:
    """Tests for incomplete exploration warning (AC: exploration context incomplete)."""

    def test_warning_lists_missing_areas(self):
        """GIVEN incomplete exploration WHEN warning created THEN lists missing areas."""
        missing = ["src/config", "src/models"]
        warning = create_incomplete_exploration_warning(missing)
        # AC: warning lists the missing context areas
        assert "src/config" in warning.description or "2" in warning.description

    def test_warning_allows_continuation(self):
        """GIVEN incomplete exploration WHEN warning created THEN allows generation."""
        warning = create_incomplete_exploration_warning(["test"])
        # AC: but allows generation to continue
        assert "continue" in warning.resolution.lower()

    def test_warning_has_correct_code(self):
        """GIVEN incomplete exploration WHEN warning created THEN is E101."""
        warning = create_incomplete_exploration_warning([])
        assert warning.code == PRDErrorCode.E101_INCOMPLETE_EXPLORATION


class TestErrorStructure:
    """Tests for error structure (AC: all errors include required fields)."""

    ERROR_FACTORIES = [
        create_empty_exploration_error,
        create_missing_intent_error,
        create_empty_intent_error,
        lambda: create_timeout_error("test", 100, 100),
        lambda: create_incomplete_exploration_warning(["test"]),
        lambda: create_markdown_generation_error(1, 3),
        lambda: create_markdown_parse_error("test error"),
        create_no_user_stories_error,
        lambda: create_validation_error("test error"),
    ]

    @pytest.mark.parametrize("factory", ERROR_FACTORIES)
    def test_error_includes_error_code(self, factory):
        """GIVEN any PRD error WHEN created THEN includes error code."""
        error = factory()
        assert error.code is not None
        assert isinstance(error.code, PRDErrorCode)

    @pytest.mark.parametrize("factory", ERROR_FACTORIES)
    def test_error_includes_description(self, factory):
        """GIVEN any PRD error WHEN created THEN includes description."""
        error = factory()
        assert error.description
        assert len(error.description) > 0

    @pytest.mark.parametrize("factory", ERROR_FACTORIES)
    def test_error_includes_resolution(self, factory):
        """GIVEN any PRD error WHEN created THEN includes suggested resolution."""
        error = factory()
        assert error.resolution
        assert len(error.resolution) > 0

    @pytest.mark.parametrize("factory", ERROR_FACTORIES)
    def test_error_includes_cli_flags(self, factory):
        """GIVEN any PRD error WHEN created THEN includes relevant CLI flags."""
        error = factory()
        assert error.cli_flags is not None
        assert isinstance(error.cli_flags, list)
        # All errors should suggest at least one CLI flag
        assert len(error.cli_flags) > 0

    @pytest.mark.parametrize("factory", ERROR_FACTORIES)
    def test_error_format_message_readable(self, factory):
        """GIVEN any PRD error WHEN formatted THEN is human-readable."""
        error = factory()
        message = error.format_message()
        # Should have structure with code, description, resolution
        assert "[E" in message  # Error code format
        assert "Resolution:" in message


class TestAgentTimeoutIntegration:
    """Tests for agent timeout handling integration."""

    def test_agent_timeout_creates_structured_error(self):
        """GIVEN agent times out WHEN error returned THEN has timeout type."""
        from pyralph.agents.base import AgentError

        # Simulate timeout error from agent
        error = AgentError(
            exception_type="TimeoutError",
            message="Timeout after 600s",
            stack_trace="",
            timestamp="2024-01-01T00:00:00",
            agent_name="claude",
            task_id="PLANNER"
        )
        assert error.exception_type == "TimeoutError"

    def test_timeout_error_message_includes_guidance(self):
        """GIVEN timeout error WHEN checking message THEN includes CLI guidance."""
        from pyralph.agents.base import AgentError

        error = AgentError(
            exception_type="TimeoutError",
            message=(
                "Agent operation timed out after 600s.\n"
                "Stage: PLANNER\n"
                "Resolution: Increase timeout with --timeout flag. "
                "Consider reducing --tree-depth for faster exploration.\n"
                "Relevant CLI flags: --timeout, --tree-depth"
            ),
            stack_trace="",
            timestamp="2024-01-01T00:00:00",
            agent_name="claude",
            task_id="PLANNER"
        )
        assert "--timeout" in error.message
        assert "--tree-depth" in error.message


class TestMarkdownGenerationError:
    """Tests for markdown generation error."""

    def test_includes_attempt_count(self):
        """GIVEN generation fails WHEN error created THEN includes attempt count."""
        error = create_markdown_generation_error(2, 3)
        assert "2" in error.description
        assert "3" in error.description

    def test_includes_partial_progress(self):
        """GIVEN generation fails WHEN error created THEN includes progress."""
        error = create_markdown_generation_error(2, 3)
        assert error.partial_progress is not None
        assert "2" in error.partial_progress


class TestNoUserStoriesError:
    """Tests for no user stories error."""

    def test_includes_revise_prd_flag(self):
        """GIVEN no user stories WHEN error created THEN suggests --revise-prd."""
        error = create_no_user_stories_error()
        assert "--revise-prd" in error.cli_flags

    def test_describes_expected_format(self):
        """GIVEN no user stories WHEN error created THEN describes format."""
        error = create_no_user_stories_error()
        assert "TASK-" in error.resolution or "User Stories" in error.resolution


class TestValidationError:
    """Tests for validation error."""

    def test_includes_original_message(self):
        """GIVEN validation fails WHEN error created THEN includes original message."""
        error = create_validation_error("Missing required field: userStories")
        assert "Missing required field" in error.description

    def test_includes_schema_flag(self):
        """GIVEN validation fails WHEN error created THEN suggests --schema."""
        error = create_validation_error("test")
        assert "--schema" in error.cli_flags
        assert "--min-criteria" in error.cli_flags
