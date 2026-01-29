"""Tests for PRD markdown parser.

Tests the parsing of prd-<short-description>.md files into structured
JSON format for prd.json generation.
"""
import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from pyralph.prd_markdown_parser import PRDMarkdownParser, parse_prd_markdown


SAMPLE_PRD_MARKDOWN = """# PRD: Test Feature Implementation

## Table of Contents
- [Metadata](#metadata)
- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Proposed Solution](#proposed-solution)
- [Functional Requirements](#functional-requirements)
- [Non-Functional Requirements](#non-functional-requirements)
- [User Stories](#user-stories)
- [Technical Constraints](#technical-constraints)
- [Assumptions](#assumptions)
- [Risks](#risks)
- [Out of Scope](#out-of-scope)
- [Open Questions](#open-questions)

---

## Metadata
- **PRD ID**: PRD-001
- **Created**: 2024-01-15T10:00:00
- **Short Description**: test-feature

---

## Executive Summary
This PRD describes a test feature that will improve the user experience by adding authentication capabilities.

---

## Project Context
### Project Structure
- src/ - Main source code
- tests/ - Test files

### Dependencies
- pytest for testing

### Architectural Patterns
Repository pattern for data access

### Test Framework
pytest with fixtures

### Conventions
snake_case naming

---

## Problem Statement
Users cannot authenticate securely. This affects all users trying to access protected resources. Without solving this, the application remains insecure.

---

## Proposed Solution
Implement JWT-based authentication with secure token storage. This approach was chosen for its stateless nature and industry adoption.

---

## Functional Requirements
| ID | Requirement | Priority | Traces To |
|----|-------------|----------|-----------|
| FR-001 | User can login with email and password | Must Have | User intent |
| FR-002 | System issues JWT token on successful login | Must Have | Security requirement |
| FR-003 | User can refresh expired tokens | Should Have | User convenience |

---

## Non-Functional Requirements
### Performance
- Login response time under 500ms
- Token validation under 50ms

### Security
- Passwords hashed with bcrypt
- Tokens expire after 24 hours

### Reliability
- 99.9% uptime for auth service

---

## User Stories

### TASK-001: User Login
**Priority**: Must Have

**Description**: As a user, I want to login with my email and password so that I can access protected resources.

**Rationale**: Authentication is a fundamental security requirement for any protected application.

**Acceptance Criteria**:
- GIVEN valid credentials WHEN user submits login form THEN system returns JWT token
- GIVEN invalid credentials WHEN user submits login form THEN system returns 401 error
- GIVEN empty fields WHEN user submits login form THEN system shows validation error

**Definition of Done**:
- [ ] Login endpoint implemented
- [ ] Unit tests passing
- [ ] Integration tests passing

**Dependencies**: None

### TASK-002: Token Refresh
**Priority**: Should Have

**Description**: As a user, I want to refresh my token so that I stay logged in without re-entering credentials.

**Rationale**: Improves user experience by reducing friction of repeated logins.

**Acceptance Criteria**:
- GIVEN valid refresh token WHEN refresh requested THEN new access token issued
- GIVEN expired refresh token WHEN refresh requested THEN user must re-authenticate
- GIVEN invalid refresh token WHEN refresh requested THEN 401 error returned

**Definition of Done**:
- [ ] Refresh endpoint implemented
- [ ] Token rotation working
- [ ] All tests passing

**Dependencies**: TASK-001

---

## Technical Constraints
- Must use existing database schema
- Must be backwards compatible with v1 API
- Python 3.10+ required

---

## Assumptions
| ID | Assumption | Impact if False |
|----|------------|-----------------|
| A-001 | Users have email addresses | Need alternate identifier |
| A-002 | Database supports transactions | Must implement manual rollback |

---

## Risks
| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| R-001 | Token leakage | Low | High | Use secure cookies, short expiry |
| R-002 | Brute force attacks | Medium | Medium | Rate limiting, account lockout |

---

## Out of Scope
- Social login (OAuth)
- Multi-factor authentication
- Password recovery flow

---

## Open Questions
| ID | Question | Owner | Status |
|----|----------|-------|--------|
| Q-001 | What is maximum token lifetime? | Security Team | Open |
| Q-002 | Should we support remember me? | Product Owner | Open |
"""


class TestPRDMarkdownParser:
    """Tests for PRDMarkdownParser class."""

    def test_parses_prd_id(self):
        """GIVEN markdown with PRD ID WHEN parsing THEN extracts correct ID."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        assert result["id"] == "PRD-001"

    def test_parses_executive_summary(self):
        """GIVEN markdown with summary WHEN parsing THEN extracts description."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        assert "test feature" in result["description"].lower()
        assert "authentication" in result["description"].lower()

    def test_parses_problem_statement(self):
        """GIVEN markdown WHEN parsing THEN extracts problem statement."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        assert "cannot authenticate" in result["problemStatement"].lower()

    def test_parses_proposed_solution(self):
        """GIVEN markdown WHEN parsing THEN extracts proposed solution."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        assert "JWT" in result["proposedSolution"]

    def test_parses_functional_requirements(self):
        """GIVEN markdown with FR table WHEN parsing THEN extracts all requirements."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        frs = result["functionalRequirements"]

        assert len(frs) == 3
        assert frs[0]["id"] == "FR-001"
        assert "login" in frs[0]["requirement"].lower()
        assert frs[0]["priority"] == "Must Have"

    def test_parses_non_functional_requirements(self):
        """GIVEN markdown with NFRs WHEN parsing THEN extracts by category."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        nfrs = result["nonFunctionalRequirements"]

        assert "performance" in nfrs
        assert "security" in nfrs
        assert "reliability" in nfrs
        assert len(nfrs["performance"]) == 2
        assert len(nfrs["security"]) == 2

    def test_parses_user_stories(self):
        """GIVEN markdown with stories WHEN parsing THEN extracts all stories."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        stories = result["userStories"]

        assert len(stories) == 2
        assert stories[0]["id"] == "TASK-001"
        assert stories[1]["id"] == "TASK-002"

    def test_user_story_has_all_fields(self):
        """GIVEN markdown story WHEN parsing THEN all fields are extracted."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        story = result["userStories"][0]

        assert story["id"] == "TASK-001"
        assert "As a user" in story["description"]
        assert "fundamental security" in story["rationale"]
        assert story["priority"] == "Must Have"
        assert len(story["acceptanceCriteria"]) == 3
        assert len(story["definitionOfDone"]) == 3
        assert story["dependencies"] == []
        assert story["status"] == "pending"

    def test_acceptance_criteria_preserved_exactly(self):
        """GIVEN markdown AC WHEN parsing THEN criteria preserved as written."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        story = result["userStories"][0]

        ac = story["acceptanceCriteria"]
        assert any("GIVEN valid credentials" in c for c in ac)
        assert any("GIVEN invalid credentials" in c for c in ac)
        assert any("GIVEN empty fields" in c for c in ac)

    def test_parses_dependencies(self):
        """GIVEN story with dependencies WHEN parsing THEN extracts task IDs."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        story = result["userStories"][1]

        assert story["dependencies"] == ["TASK-001"]

    def test_parses_technical_constraints(self):
        """GIVEN markdown with constraints WHEN parsing THEN extracts list."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        constraints = result["technicalConstraints"]

        assert len(constraints) == 3
        assert any("database schema" in c.lower() for c in constraints)

    def test_parses_assumptions(self):
        """GIVEN markdown with assumptions table WHEN parsing THEN extracts all."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        assumptions = result["assumptions"]

        assert len(assumptions) == 2
        assert assumptions[0]["id"] == "A-001"
        assert "email" in assumptions[0]["assumption"].lower()

    def test_parses_risks(self):
        """GIVEN markdown with risks table WHEN parsing THEN extracts all."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        risks = result["risks"]

        assert len(risks) == 2
        assert risks[0]["id"] == "R-001"
        assert risks[0]["likelihood"] == "Low"
        assert risks[0]["impact"] == "High"

    def test_parses_out_of_scope(self):
        """GIVEN markdown with out of scope WHEN parsing THEN extracts list."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        oos = result["outOfScope"]

        assert len(oos) == 3
        assert any("Social login" in item for item in oos)

    def test_parses_open_questions(self):
        """GIVEN markdown with questions WHEN parsing THEN extracts table."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()
        questions = result["openQuestions"]

        assert len(questions) == 2
        assert questions[0]["id"] == "Q-001"
        assert questions[0]["status"] == "Open"

    def test_no_open_questions_returns_empty_list(self):
        """GIVEN markdown stating no questions WHEN parsing THEN returns []."""
        md = """## Open Questions
No open questions at this time.
"""
        parser = PRDMarkdownParser(md)
        result = parser.parse()
        assert result["openQuestions"] == []


class TestSourceDocument:
    """Tests for sourceDocument metadata generation."""

    def test_includes_content_hash(self):
        """GIVEN markdown content WHEN parsing THEN sourceDocument has hash."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()

        assert "contentHash" in result["sourceDocument"]
        assert len(result["sourceDocument"]["contentHash"]) == 64  # SHA-256 hex

    def test_hash_is_deterministic(self):
        """GIVEN same content WHEN parsed twice THEN hash is identical."""
        parser1 = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        parser2 = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)

        result1 = parser1.parse()
        result2 = parser2.parse()

        assert result1["sourceDocument"]["contentHash"] == result2["sourceDocument"]["contentHash"]

    def test_hash_changes_with_content(self):
        """GIVEN different content WHEN parsed THEN hash differs."""
        parser1 = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        parser2 = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN + "\nExtra content")

        result1 = parser1.parse()
        result2 = parser2.parse()

        assert result1["sourceDocument"]["contentHash"] != result2["sourceDocument"]["contentHash"]

    def test_includes_path_when_provided(self, tmp_path):
        """GIVEN source path WHEN parsing THEN sourceDocument has path."""
        md_file = tmp_path / "prd-test.md"
        md_file.write_text(SAMPLE_PRD_MARKDOWN)

        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN, source_path=md_file)
        result = parser.parse()

        assert str(md_file) in result["sourceDocument"]["path"]

    def test_includes_timestamp_from_file(self, tmp_path):
        """GIVEN source file WHEN parsing THEN timestamp from file mtime."""
        md_file = tmp_path / "prd-test.md"
        md_file.write_text(SAMPLE_PRD_MARKDOWN)

        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN, source_path=md_file)
        result = parser.parse()

        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(result["sourceDocument"]["timestamp"])

    def test_compute_hash_static_method(self):
        """GIVEN content WHEN computing hash THEN returns SHA-256."""
        content = "test content"
        expected = hashlib.sha256(content.encode('utf-8')).hexdigest()

        result = PRDMarkdownParser.compute_hash(content)

        assert result == expected


class TestDeterministicGeneration:
    """Tests for deterministic JSON generation from markdown."""

    def test_same_markdown_produces_same_json(self):
        """GIVEN same markdown WHEN parsed multiple times THEN output identical."""
        result1 = parse_prd_markdown(SAMPLE_PRD_MARKDOWN)
        result2 = parse_prd_markdown(SAMPLE_PRD_MARKDOWN)

        # Compare everything except timestamp (which may vary)
        result1["sourceDocument"]["timestamp"] = "fixed"
        result2["sourceDocument"]["timestamp"] = "fixed"

        assert result1 == result2

    def test_timestamp_override(self):
        """GIVEN timestamp parameter WHEN parsing THEN uses provided timestamp."""
        fixed_ts = "2024-01-01T00:00:00"
        result = parse_prd_markdown(SAMPLE_PRD_MARKDOWN, timestamp=fixed_ts)

        assert result["sourceDocument"]["timestamp"] == fixed_ts


class TestTaskIdMatching:
    """Tests that task IDs in JSON match markdown sections."""

    def test_task_ids_match_markdown_headers(self):
        """GIVEN markdown with TASK-XXX headers WHEN parsed THEN all IDs preserved."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()

        task_ids = [s["id"] for s in result["userStories"]]

        # These should match the ### TASK-XXX headers in markdown
        assert "TASK-001" in task_ids
        assert "TASK-002" in task_ids
        assert len(task_ids) == 2

    def test_no_extra_task_ids_generated(self):
        """GIVEN markdown WHEN parsed THEN no task IDs invented."""
        parser = PRDMarkdownParser(SAMPLE_PRD_MARKDOWN)
        result = parser.parse()

        # Count TASK-XXX occurrences in markdown headers
        import re
        header_tasks = re.findall(r'^###\s+(TASK-\d+):', SAMPLE_PRD_MARKDOWN, re.MULTILINE)

        json_tasks = [s["id"] for s in result["userStories"]]

        assert len(json_tasks) == len(header_tasks)
        assert set(json_tasks) == set(header_tasks)


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_markdown(self):
        """GIVEN empty markdown WHEN parsing THEN returns default structure."""
        parser = PRDMarkdownParser("")
        result = parser.parse()

        assert result["id"] == "PRD-001"
        assert result["userStories"] == []

    def test_missing_sections(self):
        """GIVEN markdown missing sections WHEN parsing THEN uses defaults."""
        md = """## Executive Summary
Just a summary.

## User Stories

### TASK-001: Simple Task
**Priority**: Must Have
**Description**: As a user, I want something.
**Acceptance Criteria**:
- GIVEN condition WHEN action THEN result
**Definition of Done**:
- [ ] Done criteria
**Dependencies**: None
"""
        parser = PRDMarkdownParser(md)
        result = parser.parse()

        assert result["description"] == "Just a summary."
        assert len(result["userStories"]) == 1
        assert result["functionalRequirements"] == []
        assert result["technicalConstraints"] == []

    def test_malformed_table(self):
        """GIVEN malformed table WHEN parsing THEN handles gracefully."""
        md = """## Functional Requirements
| ID | Requirement |
| FR-001 | Missing columns |
"""
        parser = PRDMarkdownParser(md)
        result = parser.parse()

        # Should not crash, may return empty or partial
        assert isinstance(result["functionalRequirements"], list)

    def test_special_characters_in_content(self):
        """GIVEN content with special chars WHEN parsing THEN preserved."""
        md = """## Executive Summary
Test with special chars: <script>, "quotes", 'apostrophes', & ampersand.

## User Stories

### TASK-001: Special Chars Test
**Priority**: Must Have
**Description**: As a user, I want to use <brackets> & "quotes".
**Acceptance Criteria**:
- GIVEN input with <html> WHEN submitted THEN escaped properly
**Definition of Done**:
- [ ] Handle special chars
**Dependencies**: None
"""
        parser = PRDMarkdownParser(md)
        result = parser.parse()

        assert "<script>" in result["description"]
        assert "<brackets>" in result["userStories"][0]["description"]

    def test_multiple_dependencies(self):
        """GIVEN story with multiple deps WHEN parsing THEN all extracted."""
        md = """## User Stories

### TASK-003: Multi-Dep Task
**Priority**: Must Have
**Description**: As a user, I want a feature.
**Acceptance Criteria**:
- GIVEN condition WHEN action THEN result
**Definition of Done**:
- [ ] Done
**Dependencies**: TASK-001, TASK-002
"""
        parser = PRDMarkdownParser(md)
        result = parser.parse()

        deps = result["userStories"][0]["dependencies"]
        assert "TASK-001" in deps
        assert "TASK-002" in deps
