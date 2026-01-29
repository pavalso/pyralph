"""Tests for PhaseRunner PRD markdown-first workflow.

Tests the requirement that prd-<short-description>.md must be generated
before prd.json, and that prd.json is derived from the markdown file.
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


class TestGenerateShortDescription:
    """Tests for _generate_short_description method."""

    CASES = [
        ("add user authentication", "user-authentication"),
        ("implement API refactor for the system", "api-refactor-system"),
        ("Create a new login page", "new-login-page"),
        ("I want to build a dashboard", "dashboard"),
        ("fix the bug in checkout flow", "fix-bug-checkout-flow"),
        ("", "prd"),
        ("   ", "prd"),
    ]

    @pytest.fixture
    def phase_runner(self, tmp_path):
        """Create a minimal PhaseRunner for testing."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

        runner = PhaseRunner(
            agent=MagicMock(),
            hooks=MagicMock(),
            logger=MagicMock(),
            template_manager=MagicMock(),
            shell_module=MagicMock(),
            prd_manager=MagicMock(),
            json_utils=JsonUtils,
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
        )
        try:
            yield runner
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    @pytest.mark.parametrize("user_intent,expected", CASES)
    def test_generates_kebab_case(self, phase_runner, user_intent, expected):
        """GIVEN a user intent WHEN generating short description THEN returns kebab-case."""
        result = phase_runner._generate_short_description(user_intent)
        assert result == expected

    def test_removes_punctuation(self, phase_runner):
        """GIVEN intent with punctuation WHEN generating THEN punctuation is removed."""
        result = phase_runner._generate_short_description("user's authentication!")
        assert "'" not in result
        assert "!" not in result

    def test_handles_multiple_hyphens(self, phase_runner):
        """GIVEN intent that produces multiple hyphens WHEN generating THEN hyphens are collapsed."""
        result = phase_runner._generate_short_description("   test   value   ")
        assert "--" not in result


class TestGetPrdMdPath:
    """Tests for _get_prd_md_path method."""

    @pytest.fixture
    def phase_runner(self, tmp_path):
        """Create a minimal PhaseRunner for testing."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

        runner = PhaseRunner(
            agent=MagicMock(),
            hooks=MagicMock(),
            logger=MagicMock(),
            template_manager=MagicMock(),
            shell_module=MagicMock(),
            prd_manager=MagicMock(),
            json_utils=JsonUtils,
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
        )
        try:
            yield runner
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_returns_path_in_ralph_dir(self, phase_runner):
        """GIVEN short description WHEN getting path THEN returns path in .ralph directory."""
        path = phase_runner._get_prd_md_path("user-authentication")
        assert path.parent == CONF.ROOT_DIR
        assert path.name == "prd-user-authentication.md"


class TestGeneratePrdMarkdown:
    """Tests for _generate_prd_markdown method."""

    @pytest.fixture
    def phase_runner(self, tmp_path):
        """Create a PhaseRunner with mocked dependencies."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_template_manager = MagicMock()
        mock_shell = MagicMock()
        mock_shell.get_file_tree.return_value = "├── src/\n├── tests/"

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
            yield runner, mock_agent, mock_hooks
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_creates_markdown_file_on_success(self, phase_runner):
        """GIVEN agent succeeds WHEN generating markdown THEN creates file."""
        runner, mock_agent, mock_hooks = phase_runner
        mock_agent.run.return_value = (True, "# PRD: Test\n\n## Content", None)

        result = runner._generate_prd_markdown("test intent", "test-feature")

        assert result is not None
        assert result.exists()
        assert result.name == "prd-test-feature.md"
        content = result.read_text(encoding='utf-8')
        assert "# PRD: Test" in content

    def test_emits_prd_md_start_event(self, phase_runner):
        """GIVEN planner phase WHEN generating markdown THEN emits PRD_MD_START event."""
        runner, mock_agent, mock_hooks = phase_runner
        mock_agent.run.return_value = (True, "# PRD Content", None)

        runner._generate_prd_markdown("test intent", "test-feature")

        emit_calls = mock_hooks.emit.call_args_list
        start_events = [c for c in emit_calls if c[0][0].event_type == EventType.PRD_MD_START]
        assert len(start_events) == 1

    def test_emits_prd_md_success_event_on_success(self, phase_runner):
        """GIVEN markdown created WHEN generation completes THEN emits PRD_MD_SUCCESS event."""
        runner, mock_agent, mock_hooks = phase_runner
        mock_agent.run.return_value = (True, "# PRD Content", None)

        result = runner._generate_prd_markdown("test intent", "test-feature")

        emit_calls = mock_hooks.emit.call_args_list
        success_events = [c for c in emit_calls if c[0][0].event_type == EventType.PRD_MD_SUCCESS]
        assert len(success_events) == 1
        assert success_events[0][0][0].prd_md_path == str(result)

    def test_retries_on_agent_failure(self, phase_runner):
        """GIVEN agent fails WHEN generating markdown THEN retries up to 3 times."""
        runner, mock_agent, mock_hooks = phase_runner
        mock_agent.run.return_value = (False, "", None)

        result = runner._generate_prd_markdown("test intent", "test-feature")

        assert result is None
        assert mock_agent.run.call_count == 3

    def test_emits_prd_md_failure_on_failure(self, phase_runner):
        """GIVEN all attempts fail WHEN generation completes THEN emits PRD_MD_FAILURE event."""
        runner, mock_agent, mock_hooks = phase_runner
        mock_agent.run.return_value = (False, "", None)

        runner._generate_prd_markdown("test intent", "test-feature")

        emit_calls = mock_hooks.emit.call_args_list
        failure_events = [c for c in emit_calls if c[0][0].event_type == EventType.PRD_MD_FAILURE]
        assert len(failure_events) == 1


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


class TestGeneratePrdJsonFromMarkdown:
    """Tests for _generate_prd_json_from_markdown method."""

    @pytest.fixture
    def phase_runner(self, tmp_path):
        """Create a PhaseRunner with mocked dependencies."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_template_manager = MagicMock()

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=mock_template_manager,
            shell_module=MagicMock(),
            prd_manager=MagicMock(),
            json_utils=JsonUtils,
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
        )
        try:
            yield runner, mock_agent, tmp_path
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_blocks_when_markdown_not_exists(self, phase_runner):
        """GIVEN markdown file does not exist WHEN generating json THEN blocks and returns None."""
        runner, mock_agent, tmp_path = phase_runner
        non_existent_path = CONF.ROOT_DIR / "prd-missing.md"

        result = runner._generate_prd_json_from_markdown(non_existent_path)

        assert result is None
        mock_agent.run.assert_not_called()

    def test_derives_json_from_markdown_content(self, phase_runner):
        """GIVEN markdown exists WHEN generating json THEN derives from markdown content."""
        runner, mock_agent, tmp_path = phase_runner
        md_path = CONF.ROOT_DIR / "prd-test.md"
        md_path.write_text(VALID_PRD_MARKDOWN, encoding='utf-8')

        result = runner._generate_prd_json_from_markdown(md_path)

        assert result is not None
        assert "sourceDocument" in result
        assert result["sourceDocument"]["path"] == str(md_path)
        # Parser-based: no agent calls needed
        mock_agent.run.assert_not_called()

    def test_includes_source_document_timestamp(self, phase_runner):
        """GIVEN json generated WHEN complete THEN includes sourceDocument timestamp."""
        runner, mock_agent, tmp_path = phase_runner
        md_path = CONF.ROOT_DIR / "prd-test.md"
        md_path.write_text(VALID_PRD_MARKDOWN, encoding='utf-8')

        result = runner._generate_prd_json_from_markdown(md_path)

        assert result is not None
        assert "timestamp" in result["sourceDocument"]
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(result["sourceDocument"]["timestamp"])

    def test_includes_content_hash_for_drift_detection(self, phase_runner):
        """GIVEN json generated WHEN complete THEN includes contentHash for drift detection."""
        runner, mock_agent, tmp_path = phase_runner
        md_path = CONF.ROOT_DIR / "prd-test.md"
        md_path.write_text(VALID_PRD_MARKDOWN, encoding='utf-8')

        result = runner._generate_prd_json_from_markdown(md_path)

        assert result is not None
        assert "contentHash" in result["sourceDocument"]
        assert len(result["sourceDocument"]["contentHash"]) == 64  # SHA-256 hex

    def test_deterministic_generation(self, phase_runner):
        """GIVEN same markdown WHEN parsed twice THEN produces identical output."""
        runner, mock_agent, tmp_path = phase_runner
        md_path = CONF.ROOT_DIR / "prd-test.md"
        md_path.write_text(VALID_PRD_MARKDOWN, encoding='utf-8')

        result1 = runner._generate_prd_json_from_markdown(md_path)
        result2 = runner._generate_prd_json_from_markdown(md_path)

        # Hash should be identical
        assert result1["sourceDocument"]["contentHash"] == result2["sourceDocument"]["contentHash"]
        # User stories should match
        assert result1["userStories"] == result2["userStories"]

    def test_returns_none_for_empty_stories(self, phase_runner):
        """GIVEN markdown without user stories WHEN parsing THEN returns None."""
        runner, mock_agent, tmp_path = phase_runner
        md_path = CONF.ROOT_DIR / "prd-test.md"
        md_path.write_text("# PRD: Empty\n\n## Executive Summary\nNo stories.", encoding='utf-8')

        result = runner._generate_prd_json_from_markdown(md_path)

        assert result is None


class TestRunPlannerMarkdownFirst:
    """Integration tests for run_planner markdown-first workflow."""

    @pytest.fixture
    def phase_runner_setup(self, tmp_path):
        """Create a complete PhaseRunner setup for integration tests."""
        original_conf = {
            'BASE_DIR': CONF.BASE_DIR,
            'ROOT_DIR': CONF.ROOT_DIR,
            'PRD_FILE': CONF.PRD_FILE,
        }
        CONF.BASE_DIR = tmp_path
        CONF.ROOT_DIR = tmp_path / ".ralph"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_template_manager = MagicMock()
        mock_shell = MagicMock()
        mock_shell.get_file_tree.return_value = "├── src/"
        mock_prd_manager = MagicMock()
        mock_command_runner = MagicMock()
        mock_command_runner.run_pre_commands.return_value = True
        mock_prd_processor = MagicMock()
        mock_prd_processor.validate_prd.return_value = (True, None)
        mock_prd_processor.label_tasks.return_value = {"userStories": []}

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=mock_template_manager,
            shell_module=mock_shell,
            prd_manager=mock_prd_manager,
            json_utils=JsonUtils,
            command_runner=mock_command_runner,
            prd_processor=mock_prd_processor,
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
        )
        try:
            yield {
                'runner': runner,
                'agent': mock_agent,
                'hooks': mock_hooks,
                'logger': mock_logger,
                'prd_manager': mock_prd_manager,
                'prd_processor': mock_prd_processor,
            }
        finally:
            for attr, value in original_conf.items():
                setattr(CONF, attr, value)

    def test_generates_markdown_before_json(self, phase_runner_setup):
        """GIVEN planner phase WHEN run THEN generates markdown first, then parses for json."""
        setup = phase_runner_setup
        call_order = []

        def track_md_call(*args, **kwargs):
            if "prd_markdown.txt" in str(args):
                call_order.append("markdown")
            return "rendered template"

        setup['runner']._template_manager.render.side_effect = track_md_call

        # Agent returns valid markdown that parser can handle
        setup['agent'].run.return_value = (True, VALID_PRD_MARKDOWN, None)
        setup['prd_processor'].label_tasks.side_effect = lambda d: d

        setup['runner'].run_planner("test user intent")

        # Only markdown template is rendered (json is now parsed, not agent-generated)
        assert call_order == ["markdown"]
        # Agent only called once for markdown generation
        assert setup['agent'].run.call_count == 1

    def test_blocks_json_if_markdown_fails(self, phase_runner_setup):
        """GIVEN markdown generation fails WHEN run_planner THEN exits without json generation."""
        setup = phase_runner_setup
        setup['agent'].run.return_value = (False, "", None)

        with pytest.raises(SystemExit) as exc_info:
            setup['runner'].run_planner("test user intent")

        assert exc_info.value.code == 1
        # Should only have called for markdown (3 retries), not for json
        assert setup['agent'].run.call_count == 3

    def test_prd_json_includes_source_document(self, phase_runner_setup):
        """GIVEN successful generation WHEN json saved THEN includes sourceDocument."""
        setup = phase_runner_setup
        saved_data = None

        def capture_save(data):
            nonlocal saved_data
            saved_data = data

        setup['prd_manager'].save.side_effect = capture_save
        setup['agent'].run.return_value = (True, VALID_PRD_MARKDOWN, None)
        setup['prd_processor'].label_tasks.side_effect = lambda d: d

        setup['runner'].run_planner("test intent")

        assert saved_data is not None
        assert "sourceDocument" in saved_data
        assert "path" in saved_data["sourceDocument"]
        assert "prd-" in saved_data["sourceDocument"]["path"]
        assert ".md" in saved_data["sourceDocument"]["path"]

    def test_prd_json_includes_content_hash(self, phase_runner_setup):
        """GIVEN successful generation WHEN json saved THEN includes contentHash."""
        setup = phase_runner_setup
        saved_data = None

        def capture_save(data):
            nonlocal saved_data
            saved_data = data

        setup['prd_manager'].save.side_effect = capture_save
        setup['agent'].run.return_value = (True, VALID_PRD_MARKDOWN, None)
        setup['prd_processor'].label_tasks.side_effect = lambda d: d

        setup['runner'].run_planner("test intent")

        assert saved_data is not None
        assert "contentHash" in saved_data["sourceDocument"]
        assert len(saved_data["sourceDocument"]["contentHash"]) == 64

    def test_emits_prd_created_with_md_path(self, phase_runner_setup):
        """GIVEN successful generation WHEN complete THEN PRD_CREATED includes md_path."""
        setup = phase_runner_setup
        setup['agent'].run.return_value = (True, VALID_PRD_MARKDOWN, None)
        setup['prd_processor'].label_tasks.side_effect = lambda d: d

        setup['runner'].run_planner("test intent")

        emit_calls = setup['hooks'].emit.call_args_list
        prd_created_events = [c for c in emit_calls if c[0][0].event_type == EventType.PRD_CREATED]
        assert len(prd_created_events) == 1
        event = prd_created_events[0][0][0]
        assert event.prd_md_path is not None
        assert "prd-" in event.prd_md_path

    def test_task_ids_match_markdown_source(self, phase_runner_setup):
        """GIVEN markdown with TASK-001 WHEN parsed THEN prd.json has matching TASK-001."""
        setup = phase_runner_setup
        saved_data = None

        def capture_save(data):
            nonlocal saved_data
            saved_data = data

        setup['prd_manager'].save.side_effect = capture_save
        setup['agent'].run.return_value = (True, VALID_PRD_MARKDOWN, None)
        setup['prd_processor'].label_tasks.side_effect = lambda d: d

        setup['runner'].run_planner("test intent")

        assert saved_data is not None
        task_ids = [s["id"] for s in saved_data["userStories"]]
        assert "TASK-001" in task_ids

    def test_acceptance_criteria_preserved(self, phase_runner_setup):
        """GIVEN markdown with Given-When-Then criteria WHEN parsed THEN preserved exactly."""
        setup = phase_runner_setup
        saved_data = None

        def capture_save(data):
            nonlocal saved_data
            saved_data = data

        setup['prd_manager'].save.side_effect = capture_save
        setup['agent'].run.return_value = (True, VALID_PRD_MARKDOWN, None)
        setup['prd_processor'].label_tasks.side_effect = lambda d: d

        setup['runner'].run_planner("test intent")

        assert saved_data is not None
        story = saved_data["userStories"][0]
        ac = story["acceptanceCriteria"]
        assert any("GIVEN valid credentials" in c for c in ac)


class TestNewTemplatesExist:
    """Tests that new templates are available."""

    def test_prd_markdown_template_exists(self):
        """GIVEN template manager WHEN loading prd_markdown.txt THEN template exists."""
        from pyralph.templates import TemplateManager
        template = TemplateManager.load("prd_markdown.txt")
        assert "Product Manager" in template
        assert "{{user_intent}}" in template
        assert "{{file_tree}}" in template
        assert "{{short_description}}" in template

    def test_prd_from_markdown_template_exists(self):
        """GIVEN template manager WHEN loading prd_from_markdown.txt THEN template exists."""
        from pyralph.templates import TemplateManager
        template = TemplateManager.load("prd_from_markdown.txt")
        assert "Technical Translator" in template
        assert "{{prd_markdown_content}}" in template
        assert "sourceDocument" in template


class TestNewEventTypesExist:
    """Tests that new event types are available."""

    NEW_EVENTS = ["PRD_MD_START", "PRD_MD_SUCCESS", "PRD_MD_FAILURE"]

    def test_event_types_exist(self):
        """GIVEN EventType enum WHEN checking new events THEN all exist."""
        for name in self.NEW_EVENTS:
            assert hasattr(EventType, name)

    def test_event_has_prd_md_path_field(self):
        """GIVEN Event class WHEN creating event THEN prd_md_path field available."""
        event = Event(EventType.PRD_MD_SUCCESS, prd_md_path="/path/to/prd-test.md")
        assert event.prd_md_path == "/path/to/prd-test.md"

    def test_event_serializes_prd_md_path(self):
        """GIVEN Event with prd_md_path WHEN serializing THEN includes prd_md_path."""
        event = Event(EventType.PRD_MD_SUCCESS, prd_md_path="/path/to/prd.md")
        result = event.to_dict()
        assert result["prd_md_path"] == "/path/to/prd.md"
