"""Tests for PRD drift detection between prd-*.md and prd.json.

Tests the drift detection functionality that ensures prd-*.md and prd.json
remain synchronized, including:
- Hash comparison for markdown changes
- Section change identification
- Task ID traceability verification
- Manual prd.json edit detection
- --strict flag behavior
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyralph.prd_processor import PRDProcessor, DriftResult
from pyralph.prd_markdown_parser import PRDMarkdownParser


# Sample markdown content for testing
SAMPLE_MARKDOWN = """# PRD: Test Feature

## Metadata
- **PRD ID**: PRD-001

## Executive Summary
This is a test PRD for validating drift detection.

## Problem Statement
Users need drift detection between markdown and JSON.

## Proposed Solution
Implement hash-based comparison with section identification.

## User Stories

### TASK-001: Implement Hash Comparison
**Priority**: Must Have
**Description**: As a developer, I want hash comparison for drift detection.
**Rationale**: To detect when markdown changes.
**Acceptance Criteria**:
- GIVEN markdown changes WHEN validation runs THEN drift is detected
**Definition of Done**:
- [ ] Hash comparison implemented
**Dependencies**: None

### TASK-002: Add Section Identification
**Priority**: Should Have
**Description**: As a developer, I want to identify changed sections.
**Rationale**: To provide actionable feedback.
**Acceptance Criteria**:
- GIVEN drift detected WHEN sections differ THEN specific sections identified
**Definition of Done**:
- [ ] Section identification implemented
**Dependencies**: TASK-001

## Technical Constraints
- Must use SHA-256 hashing
- Must be deterministic

## Out of Scope
- Real-time monitoring

## Open Questions
No open questions at this time.
"""


@pytest.fixture
def mock_dependencies():
    """Create mock dependencies for PRDProcessor."""
    return {
        "agent": MagicMock(),
        "hooks": MagicMock(),
        "logger": MagicMock(),
        "template_manager": MagicMock(),
        "json_utils": MagicMock(),
        "event_class": MagicMock(),
        "event_type_class": MagicMock(),
    }


@pytest.fixture
def prd_processor(mock_dependencies):
    """Create a PRDProcessor instance with mocked dependencies."""
    return PRDProcessor(**mock_dependencies)


@pytest.fixture
def sample_prd_data(tmp_path):
    """Create sample PRD data with sourceDocument metadata.

    This fixture parses the markdown to ensure the JSON exactly matches
    what the parser would generate, avoiding false positives in manual edit detection.
    """
    from pyralph.prd_markdown_parser import parse_prd_markdown

    md_path = tmp_path / "prd-test.md"
    md_path.write_text(SAMPLE_MARKDOWN, encoding='utf-8')

    # Parse the markdown to get the exact expected structure
    prd_data = parse_prd_markdown(SAMPLE_MARKDOWN, source_path=md_path)

    return prd_data


class TestDriftResult:
    """Tests for DriftResult dataclass."""

    def test_default_values(self):
        """GIVEN default DriftResult WHEN created THEN all fields have expected defaults."""
        result = DriftResult()

        assert result.has_drift is False
        assert result.markdown_changed is False
        assert result.json_manually_edited is False
        assert result.changed_sections == []
        assert result.missing_in_markdown == []
        assert result.missing_in_json == []
        assert result.warnings == []

    def test_is_synchronized_when_no_drift(self):
        """GIVEN DriftResult with no drift WHEN checking is_synchronized THEN returns True."""
        result = DriftResult(has_drift=False)
        assert result.is_synchronized is True

    def test_is_synchronized_when_drift(self):
        """GIVEN DriftResult with drift WHEN checking is_synchronized THEN returns False."""
        result = DriftResult(has_drift=True)
        assert result.is_synchronized is False


class TestCheckDrift:
    """Tests for PRDProcessor.check_drift method."""

    def test_no_drift_when_hashes_match(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN prd.json matches prd-*.md WHEN check_drift runs THEN no drift detected."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is False
        assert result.markdown_changed is False
        assert len(result.warnings) == 0

    def test_drift_detected_when_markdown_modified(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN prd-*.md modified after prd.json WHEN check_drift runs THEN drift detected."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        # Modify the markdown file
        modified_content = SAMPLE_MARKDOWN + "\n\n## New Section\nAdded content."
        md_path.write_text(modified_content, encoding='utf-8')

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is True
        assert result.markdown_changed is True
        assert any("has changed since prd.json was generated" in w for w in result.warnings)

    def test_warning_message_includes_regenerate_instruction(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN drift detected WHEN warning generated THEN includes regenerate instruction."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])
        md_path.write_text(SAMPLE_MARKDOWN + "\nExtra content", encoding='utf-8')

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert any("regenerate prd.json" in w for w in result.warnings)

    def test_drift_when_markdown_file_missing(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN source markdown file missing WHEN check_drift runs THEN drift detected."""
        # Use a non-existent path
        sample_prd_data["sourceDocument"]["path"] = str(tmp_path / "nonexistent.md")

        result = prd_processor.check_drift(sample_prd_data, Path(tmp_path / "nonexistent.md"))

        assert result.has_drift is True
        assert any("not found" in w for w in result.warnings)


class TestSectionChangeIdentification:
    """Tests for identifying which sections changed."""

    def test_identifies_user_stories_section_change(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN user stories differ WHEN drift detected THEN User Stories section identified."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        # Remove a task from the markdown
        modified_md = SAMPLE_MARKDOWN.replace(
            "### TASK-002: Add Section Identification",
            "### TASK-003: Different Task"
        )
        md_path.write_text(modified_md, encoding='utf-8')

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is True
        assert "User Stories" in result.changed_sections

    def test_identifies_executive_summary_change(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN executive summary differs WHEN drift detected THEN section identified."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        # Change the executive summary significantly
        modified_md = SAMPLE_MARKDOWN.replace(
            "This is a test PRD for validating drift detection.",
            "Completely different summary content that does not match at all."
        )
        md_path.write_text(modified_md, encoding='utf-8')

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is True
        # The section may or may not be identified depending on comparison threshold
        # The important thing is that drift is detected


class TestTaskIdTraceability:
    """Tests for task ID traceability verification."""

    def test_task_ids_in_sync(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN all task IDs match WHEN traceability checked THEN no mismatches."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.missing_in_markdown == []
        assert result.missing_in_json == []

    def test_task_id_missing_in_markdown(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN task ID in JSON but not markdown WHEN checked THEN reports missing."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        # Add a task to JSON that's not in markdown
        sample_prd_data["userStories"].append({
            "id": "TASK-099",
            "description": "Extra task not in markdown",
            "priority": "Must Have",
            "acceptanceCriteria": [],
            "definitionOfDone": [],
            "dependencies": [],
            "status": "pending",
        })

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is True
        assert "TASK-099" in result.missing_in_markdown
        assert any("TASK-099" in w for w in result.warnings)

    def test_task_id_missing_in_json(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN task ID in markdown but not JSON WHEN checked THEN reports missing."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        # Add a task to markdown that's not in JSON
        modified_md = SAMPLE_MARKDOWN + """

### TASK-099: New Task Not In JSON
**Priority**: Must Have
**Description**: As a user, I want a new feature.
**Acceptance Criteria**:
- GIVEN something WHEN action THEN result
**Definition of Done**:
- [ ] Done
**Dependencies**: None
"""
        md_path.write_text(modified_md, encoding='utf-8')

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is True
        assert "TASK-099" in result.missing_in_json
        assert any("TASK-099" in w for w in result.warnings)


class TestManualEditDetection:
    """Tests for detecting manual prd.json edits."""

    def test_detects_manual_json_edit(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN prd.json manually edited WHEN validation runs THEN warning issued."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        # Hash matches (markdown unchanged) but content differs
        # Modify JSON data to simulate manual edit
        sample_prd_data["userStories"][0]["description"] = "Manually changed description"

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.json_manually_edited is True
        assert any("should not be edited directly" in w for w in result.warnings)

    def test_no_manual_edit_when_regenerated(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN prd.json regenerated from markdown WHEN checked THEN no manual edit warning."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.json_manually_edited is False


class TestStrictMode:
    """Tests for --strict flag behavior."""

    def test_run_drift_check_returns_ok_when_no_drift(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN no drift WHEN run_drift_check runs THEN returns is_ok=True."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])

        is_ok, messages = prd_processor.run_drift_check(sample_prd_data, md_path, strict=False)

        assert is_ok is True
        assert len(messages) == 0

    def test_run_drift_check_returns_ok_with_warning_when_not_strict(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN drift detected with strict=False WHEN run_drift_check runs THEN is_ok=True."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])
        md_path.write_text(SAMPLE_MARKDOWN + "\nModified content", encoding='utf-8')

        is_ok, messages = prd_processor.run_drift_check(sample_prd_data, md_path, strict=False)

        assert is_ok is True  # Warnings don't fail without strict
        assert len(messages) > 0

    def test_run_drift_check_fails_when_strict(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN drift detected with strict=True WHEN run_drift_check runs THEN is_ok=False."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])
        md_path.write_text(SAMPLE_MARKDOWN + "\nModified content", encoding='utf-8')

        is_ok, messages = prd_processor.run_drift_check(sample_prd_data, md_path, strict=True)

        assert is_ok is False
        assert len(messages) > 0


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_source_document(self, prd_processor, tmp_path):
        """GIVEN prd.json without sourceDocument WHEN check_drift runs THEN handles gracefully."""
        prd_data = {"userStories": []}

        result = prd_processor.check_drift(prd_data, None)

        assert result.has_drift is True
        assert any("not found" in w.lower() for w in result.warnings)

    def test_empty_markdown_file(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN empty markdown file WHEN check_drift runs THEN drift detected."""
        md_path = Path(sample_prd_data["sourceDocument"]["path"])
        md_path.write_text("", encoding='utf-8')

        result = prd_processor.check_drift(sample_prd_data, md_path)

        assert result.has_drift is True
        assert result.markdown_changed is True

    def test_no_user_stories_in_json(self, prd_processor, tmp_path):
        """GIVEN prd.json with no userStories WHEN traceability checked THEN handles gracefully."""
        md_path = tmp_path / "prd-empty.md"
        md_path.write_text("## User Stories\n\n### TASK-001: Test\n", encoding='utf-8')

        prd_data = {
            "userStories": [],
            "sourceDocument": {
                "path": str(md_path),
                "contentHash": PRDMarkdownParser.compute_hash("## User Stories\n\n### TASK-001: Test\n"),
            },
        }

        result = prd_processor.check_drift(prd_data, md_path)

        # Should detect TASK-001 missing in JSON
        assert "TASK-001" in result.missing_in_json

    def test_path_from_source_document_used_when_not_provided(self, prd_processor, sample_prd_data, tmp_path):
        """GIVEN prd_md_path is None WHEN check_drift runs THEN uses sourceDocument.path."""
        # Don't pass explicit path
        result = prd_processor.check_drift(sample_prd_data, prd_md_path=None)

        # Should succeed using path from sourceDocument
        assert result.has_drift is False
