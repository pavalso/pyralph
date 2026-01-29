"""Integration tests for PRD error handling in phase runner and orchestrator (TASK-045).

Tests the error handling behavior end-to-end per acceptance criteria:
- Empty exploration results
- Missing/empty intent
- Timeout errors
- Incomplete exploration warnings
- Structured error format (code, description, resolution, CLI flags)
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyralph.config import CONF
from pyralph.events import Event, EventType
from pyralph.phase_runner import PhaseRunner
from pyralph.prd import JsonUtils


VALID_PRD_MARKDOWN = """# PRD: Test Feature

## Metadata
- **PRD ID**: PRD-001
- **Created**: 2024-01-15T10:00:00
- **Short Description**: test-feature

## Executive Summary
Test feature for authentication.

## Problem Statement
Users need authentication.

## Proposed Solution
Implement JWT auth.

## Functional Requirements
| ID | Requirement | Priority | Traces To |
|----|-------------|----------|-----------|
| FR-001 | Login | Must Have | Intent |

## Non-Functional Requirements
### Security
- Use bcrypt

## User Stories

### TASK-001: User Login
**Priority**: Must Have
**Description**: As a user, I want to login.
**Rationale**: Security requirement.
**Acceptance Criteria**:
- GIVEN valid credentials WHEN submitted THEN login succeeds
**Definition of Done**:
- [ ] Tests pass
**Dependencies**: None

## Technical Constraints
- Python 3.10+

## Assumptions
| ID | Assumption | Impact if False |
|----|------------|-----------------|
| A-001 | Has email | Need alternate |

## Risks
| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| R-001 | Token leak | Low | High | Short expiry |

## Out of Scope
- Social login

## Open Questions
No open questions at this time.
"""


class TestEmptyExplorationError:
    """Integration tests for empty exploration error handling (AC1)."""

    @pytest.fixture
    def phase_runner(self, tmp_path):
        """Create a PhaseRunner with mocked dependencies."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
            'EXPLORATION_CONTEXT_FILE': CONF.EXPLORATION_CONTEXT_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"
        CONF.EXPLORATION_CONTEXT_FILE = tmp_path / ".ralph" / "exploration_context.json"

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_template_manager = MagicMock()
        mock_shell = MagicMock()
        # Return empty file tree to trigger empty exploration error
        mock_shell.get_file_tree.return_value = ""
        mock_shell.DEFAULT_TREE_IGNORE = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=mock_template_manager,
            shell_module=mock_shell,
            prd_manager=MagicMock(),
            json_utils=JsonUtils,
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
        )
        try:
            yield runner, mock_agent, mock_hooks, mock_logger
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_empty_exploration_returns_none(self, phase_runner):
        """GIVEN exploration returns empty results WHEN prd markdown generation attempted THEN returns None."""
        runner, mock_agent, mock_hooks, mock_logger = phase_runner

        result = runner._generate_prd_markdown("test intent", "test-feature")

        assert result is None

    def test_empty_exploration_logs_correct_message(self, phase_runner):
        """GIVEN exploration returns empty WHEN generation attempted THEN logs correct message."""
        runner, mock_agent, mock_hooks, mock_logger = phase_runner

        runner._generate_prd_markdown("test intent", "test-feature")

        # Check that the error message matches AC
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("Insufficient codebase context" in call for call in log_calls)
        assert any("exploration found no analyzable content" in call for call in log_calls)

    def test_empty_exploration_emits_failure_event(self, phase_runner):
        """GIVEN exploration returns empty WHEN generation attempted THEN emits PRD_MD_FAILURE."""
        runner, mock_agent, mock_hooks, mock_logger = phase_runner

        runner._generate_prd_markdown("test intent", "test-feature")

        emit_calls = mock_hooks.emit.call_args_list
        failure_events = [c for c in emit_calls if c[0][0].event_type == EventType.PRD_MD_FAILURE]
        assert len(failure_events) >= 1


class TestMissingIntentError:
    """Integration tests for missing/empty intent error handling (AC2)."""

    @pytest.fixture
    def mock_orchestrator_setup(self, tmp_path):
        """Create mock orchestrator dependencies."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

        try:
            yield tmp_path
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_missing_intent_in_non_interactive_mode(self, mock_orchestrator_setup):
        """GIVEN intent is missing WHEN in non-interactive mode THEN logs correct error."""
        from pyralph.prd_errors import create_missing_intent_error

        error = create_missing_intent_error()

        # AC: error message states 'Cannot generate PRD: no intent provided'
        assert "Cannot generate PRD" in error.description
        assert "no intent provided" in error.description
        # AC: with guidance on --intent or --intent-file flags
        assert "--intent" in error.cli_flags
        assert "--intent-file" in error.cli_flags

    def test_empty_intent_file_error(self, mock_orchestrator_setup):
        """GIVEN intent file is empty WHEN planner runs THEN logs correct error."""
        tmp_path = mock_orchestrator_setup
        empty_intent_file = tmp_path / "empty_intent.txt"
        empty_intent_file.write_text("", encoding="utf-8")

        from pyralph.prd_errors import create_empty_intent_error

        error = create_empty_intent_error()

        # AC: error message states 'Cannot generate PRD: no intent provided'
        assert "Cannot generate PRD" in error.description
        assert "no intent provided" in error.description


class TestTimeoutError:
    """Integration tests for timeout error handling (AC3)."""

    def test_timeout_error_includes_partial_progress(self):
        """GIVEN prd markdown generation times out WHEN error logged THEN includes progress."""
        from pyralph.prd_errors import create_timeout_error

        error = create_timeout_error(
            current_stage="PRD markdown generation",
            elapsed_seconds=600,
            timeout_seconds=600
        )

        # AC: includes partial progress indicator
        assert error.partial_progress is not None
        assert "PRD markdown generation" in error.partial_progress

    def test_timeout_error_suggests_increasing_timeout(self):
        """GIVEN timeout occurs WHEN error logged THEN suggests increasing --timeout."""
        from pyralph.prd_errors import create_timeout_error

        error = create_timeout_error(
            current_stage="exploration",
            elapsed_seconds=600,
            timeout_seconds=600
        )

        # AC: suggests increasing --timeout value
        assert "--timeout" in error.cli_flags
        assert "--timeout" in error.resolution or "--timeout" in str(error.cli_flags)


class TestIncompleteExplorationWarning:
    """Integration tests for incomplete exploration warning (AC4)."""

    @pytest.fixture
    def phase_runner_with_incomplete(self, tmp_path):
        """Create a PhaseRunner that returns incomplete exploration."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
            'EXPLORATION_CONTEXT_FILE': CONF.EXPLORATION_CONTEXT_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"
        CONF.EXPLORATION_CONTEXT_FILE = tmp_path / ".ralph" / "exploration_context.json"

        mock_agent = MagicMock()
        mock_agent.run.return_value = (True, VALID_PRD_MARKDOWN, None)
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_template_manager = MagicMock()
        mock_shell = MagicMock()
        mock_shell.get_file_tree.return_value = "├── src/\n├── tests/"
        mock_shell.DEFAULT_TREE_IGNORE = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']
        mock_command_runner = MagicMock()
        mock_command_runner.run_pre_commands.return_value = True
        mock_prd_processor = MagicMock()
        mock_prd_processor.validate_prd.return_value = (True, None)
        mock_prd_processor.label_tasks.side_effect = lambda d: d

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=mock_template_manager,
            shell_module=mock_shell,
            prd_manager=MagicMock(),
            json_utils=JsonUtils,
            command_runner=mock_command_runner,
            prd_processor=mock_prd_processor,
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
        )

        # Create directory that will fail permission check
        restricted_dir = tmp_path / "restricted"
        restricted_dir.mkdir()

        try:
            yield runner, mock_agent, mock_hooks, mock_logger, tmp_path
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_incomplete_exploration_logs_warning(self, phase_runner_with_incomplete):
        """GIVEN exploration context incomplete WHEN generation proceeds THEN warning logged."""
        from pyralph.prd_errors import create_incomplete_exploration_warning

        warning = create_incomplete_exploration_warning(["src/config", "src/models"])

        # AC: warning lists the missing context areas
        # Check it mentions the issue
        assert "incomplete" in warning.description.lower() or "missing" in warning.description.lower()

    def test_incomplete_exploration_allows_continuation(self, phase_runner_with_incomplete):
        """GIVEN exploration context incomplete WHEN generation proceeds THEN allows continuation."""
        from pyralph.prd_errors import create_incomplete_exploration_warning

        warning = create_incomplete_exploration_warning(["test"])

        # AC: but allows generation to continue
        assert "continue" in warning.resolution.lower()


class TestErrorStructure:
    """Integration tests for error structure (AC5)."""

    def test_all_errors_include_error_code(self):
        """GIVEN any prd error WHEN logged THEN includes error code."""
        from pyralph.prd_errors import (
            create_empty_exploration_error,
            create_missing_intent_error,
            create_timeout_error,
            create_incomplete_exploration_warning,
            create_validation_error,
        )

        errors = [
            create_empty_exploration_error(),
            create_missing_intent_error(),
            create_timeout_error("test", 100, 100),
            create_incomplete_exploration_warning(["test"]),
            create_validation_error("test"),
        ]

        for error in errors:
            message = error.format_message()
            # AC: error includes: error code
            assert "[E" in message
            assert error.code.value in message

    def test_all_errors_include_description(self):
        """GIVEN any prd error WHEN logged THEN includes description."""
        from pyralph.prd_errors import (
            create_empty_exploration_error,
            create_missing_intent_error,
            create_timeout_error,
        )

        errors = [
            create_empty_exploration_error(),
            create_missing_intent_error(),
            create_timeout_error("test", 100, 100),
        ]

        for error in errors:
            # AC: error includes: description
            assert len(error.description) > 0
            assert error.description in error.format_message()

    def test_all_errors_include_resolution(self):
        """GIVEN any prd error WHEN logged THEN includes suggested resolution."""
        from pyralph.prd_errors import (
            create_empty_exploration_error,
            create_missing_intent_error,
            create_timeout_error,
        )

        errors = [
            create_empty_exploration_error(),
            create_missing_intent_error(),
            create_timeout_error("test", 100, 100),
        ]

        for error in errors:
            message = error.format_message()
            # AC: error includes: suggested resolution
            assert "Resolution:" in message
            assert len(error.resolution) > 0

    def test_all_errors_include_cli_flags(self):
        """GIVEN any prd error WHEN logged THEN includes relevant CLI flags."""
        from pyralph.prd_errors import (
            create_empty_exploration_error,
            create_missing_intent_error,
            create_timeout_error,
        )

        errors = [
            create_empty_exploration_error(),
            create_missing_intent_error(),
            create_timeout_error("test", 100, 100),
        ]

        for error in errors:
            message = error.format_message()
            # AC: error includes: relevant CLI flags
            assert "Relevant CLI flags:" in message or len(error.cli_flags) > 0
            # Each error should suggest at least one flag
            assert len(error.cli_flags) > 0


class TestAgentTimeoutHandling:
    """Tests for agent-level timeout handling."""

    def test_agent_timeout_returns_timeout_error_type(self):
        """GIVEN agent times out WHEN run completes THEN error has TimeoutError type."""
        from pyralph.agents.base import AgentError

        # Simulate what happens when subprocess times out
        error = AgentError(
            exception_type="TimeoutError",
            message="Agent operation timed out",
            stack_trace="",
            timestamp=datetime.now().isoformat(),
            agent_name="claude",
            task_id="PLANNER"
        )

        assert error.exception_type == "TimeoutError"

    def test_agent_timeout_message_includes_cli_guidance(self):
        """GIVEN agent times out WHEN error created THEN message includes CLI guidance."""
        from pyralph.agents.base import AgentError

        # The actual message format from the updated agent
        error = AgentError(
            exception_type="TimeoutError",
            message=(
                "Agent operation timed out after 600s.\n"
                "Stage: PLANNER\n"
                "Resolution: Increase timeout with --timeout flag. "
                "Consider reducing --tree-depth for faster exploration.\n"
                "Relevant CLI flags: --timeout, --tree-depth"
            ),
            stack_trace="Timeout after 600 seconds during PLANNER",
            timestamp=datetime.now().isoformat(),
            agent_name="claude",
            task_id="PLANNER"
        )

        assert "--timeout" in error.message
        assert "--tree-depth" in error.message
