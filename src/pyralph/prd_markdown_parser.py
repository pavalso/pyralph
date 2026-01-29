#!/usr/bin/env python3
"""Markdown parser for PRD documents.

This module provides parsing functionality to extract structured data
from prd-<short-description>.md files for conversion to prd.json format.
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class PRDMarkdownParser:
    """Parser for extracting structured data from PRD markdown files.

    Parses the standardized PRD markdown format and extracts all sections
    into a structured dictionary suitable for JSON serialization.
    """

    def __init__(self, content: str, source_path: Optional[Path] = None):
        """Initialize parser with markdown content.

        Args:
            content: Raw markdown content to parse
            source_path: Optional path to source file for metadata
        """
        self._content = content
        self._source_path = source_path
        self._lines = content.split('\n')

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute SHA-256 hash of content for drift detection.

        Args:
            content: Content to hash

        Returns:
            Hex-encoded SHA-256 hash
        """
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def parse(self) -> Dict[str, Any]:
        """Parse markdown content into structured dictionary.

        Returns:
            Dictionary containing all extracted PRD data suitable for JSON
        """
        result = {
            "id": self._extract_prd_id(),
            "description": self._extract_executive_summary(),
            "problemStatement": self._extract_section_content("Problem Statement"),
            "proposedSolution": self._extract_section_content("Proposed Solution"),
            "sourceDocument": self._build_source_document(),
            "functionalRequirements": self._extract_functional_requirements(),
            "nonFunctionalRequirements": self._extract_non_functional_requirements(),
            "userStories": self._extract_user_stories(),
            "technicalConstraints": self._extract_list_section("Technical Constraints"),
            "assumptions": self._extract_assumptions(),
            "risks": self._extract_risks(),
            "outOfScope": self._extract_list_section("Out of Scope"),
            "openQuestions": self._extract_open_questions(),
        }
        return result

    def _build_source_document(self) -> Dict[str, str]:
        """Build sourceDocument metadata.

        Returns:
            Dictionary with path, timestamp, and contentHash
        """
        doc = {
            "path": str(self._source_path) if self._source_path else "",
            "timestamp": datetime.now().isoformat(),
            "contentHash": self.compute_hash(self._content),
        }
        if self._source_path and self._source_path.exists():
            doc["timestamp"] = datetime.fromtimestamp(
                self._source_path.stat().st_mtime
            ).isoformat()
        return doc

    def _find_section_range(self, section_name: str) -> Tuple[int, int]:
        """Find the line range for a section.

        Args:
            section_name: Name of section header (without ## prefix)

        Returns:
            Tuple of (start_line, end_line) indices, or (-1, -1) if not found
        """
        patterns = [
            f"## {section_name}",
            f"### {section_name}",
        ]
        start_idx = -1
        for i, line in enumerate(self._lines):
            stripped = line.strip()
            if any(stripped == p or stripped.startswith(p + " ") for p in patterns):
                start_idx = i
                break

        if start_idx == -1:
            return (-1, -1)

        # Find end: next section of same or higher level, or end of file
        start_level = len(self._lines[start_idx]) - len(self._lines[start_idx].lstrip('#'))
        end_idx = len(self._lines)
        for i in range(start_idx + 1, len(self._lines)):
            line = self._lines[i].strip()
            if line.startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                if level <= start_level:
                    end_idx = i
                    break

        return (start_idx, end_idx)

    def _extract_section_content(self, section_name: str) -> str:
        """Extract raw content from a section.

        Args:
            section_name: Name of section header

        Returns:
            Section content as string, stripped of leading/trailing whitespace
        """
        start, end = self._find_section_range(section_name)
        if start == -1:
            return ""

        # Skip header line and separator lines
        content_lines = []
        for i in range(start + 1, end):
            line = self._lines[i]
            if line.strip() == '---':
                continue
            content_lines.append(line)

        return '\n'.join(content_lines).strip()

    def _extract_prd_id(self) -> str:
        """Extract PRD ID from Metadata section.

        Returns:
            PRD ID string (e.g., "PRD-001") or "PRD-001" as default
        """
        content = self._extract_section_content("Metadata")
        match = re.search(r'\*\*PRD ID\*\*:\s*(PRD-\d+)', content)
        if match:
            return match.group(1)
        # Fallback: check first heading
        for line in self._lines:
            if line.startswith('# PRD:'):
                return "PRD-001"
        return "PRD-001"

    def _extract_executive_summary(self) -> str:
        """Extract executive summary content.

        Returns:
            Executive summary text
        """
        return self._extract_section_content("Executive Summary")

    def _extract_list_section(self, section_name: str) -> List[str]:
        """Extract items from a bulleted list section.

        Args:
            section_name: Name of section header

        Returns:
            List of extracted items
        """
        content = self._extract_section_content(section_name)
        items = []
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('- '):
                items.append(line[2:].strip())
            elif line.startswith('* '):
                items.append(line[2:].strip())
        return items

    def _extract_functional_requirements(self) -> List[Dict[str, str]]:
        """Extract functional requirements from table.

        Returns:
            List of requirement dictionaries
        """
        content = self._extract_section_content("Functional Requirements")
        return self._parse_fr_table(content)

    def _parse_fr_table(self, content: str) -> List[Dict[str, str]]:
        """Parse functional requirements table.

        Args:
            content: Section content containing table

        Returns:
            List of requirement dictionaries
        """
        requirements = []
        lines = content.split('\n')
        in_table = False
        headers = []

        for line in lines:
            line = line.strip()
            if not line.startswith('|'):
                continue

            cells = [c.strip() for c in line.split('|')[1:-1]]

            if not in_table:
                # First row is header
                headers = [h.lower().replace(' ', '') for h in cells]
                in_table = True
                continue

            # Skip separator row
            if all(c.replace('-', '').replace(':', '') == '' for c in cells):
                continue

            if len(cells) >= 4:
                req = {
                    "id": cells[0],
                    "requirement": cells[1],
                    "priority": cells[2],
                    "tracesTo": cells[3] if len(cells) > 3 else "",
                }
                requirements.append(req)

        return requirements

    def _extract_non_functional_requirements(self) -> Dict[str, List[str]]:
        """Extract non-functional requirements by category.

        Returns:
            Dictionary mapping category names to requirement lists
        """
        start, end = self._find_section_range("Non-Functional Requirements")
        if start == -1:
            return {}

        nfr = {}
        current_category = None
        current_items = []

        for i in range(start + 1, end):
            line = self._lines[i].strip()

            if line.startswith('### '):
                if current_category and current_items:
                    nfr[current_category] = current_items
                current_category = line[4:].strip().lower()
                current_items = []
            elif line.startswith('- ') and current_category:
                current_items.append(line[2:].strip())
            elif line.startswith('* ') and current_category:
                current_items.append(line[2:].strip())

        if current_category and current_items:
            nfr[current_category] = current_items

        return nfr

    def _extract_user_stories(self) -> List[Dict[str, Any]]:
        """Extract user stories from User Stories section.

        Returns:
            List of user story dictionaries
        """
        start, end = self._find_section_range("User Stories")
        if start == -1:
            return []

        stories = []
        i = start + 1
        while i < end:
            line = self._lines[i].strip()

            # Look for TASK-XXX headers
            task_match = re.match(r'^###\s+(TASK-\d+):\s*(.+)$', line)
            if task_match:
                task_id = task_match.group(1)
                task_title = task_match.group(2).strip()
                story, consumed = self._parse_single_story(i, end, task_id, task_title)
                if story:
                    stories.append(story)
                i += consumed
            else:
                i += 1

        return stories

    def _parse_single_story(
        self, start_idx: int, section_end: int, task_id: str, title: str
    ) -> Tuple[Optional[Dict[str, Any]], int]:
        """Parse a single user story starting from given index.

        Args:
            start_idx: Starting line index
            section_end: End of user stories section
            task_id: The TASK-XXX identifier
            title: Story title from header

        Returns:
            Tuple of (story dict, lines consumed)
        """
        story = {
            "id": task_id,
            "description": "",
            "rationale": "",
            "priority": "Must Have",
            "acceptanceCriteria": [],
            "definitionOfDone": [],
            "dependencies": [],
            "status": "pending",
        }

        i = start_idx + 1
        current_field = None
        field_content = []

        while i < section_end:
            line = self._lines[i]
            stripped = line.strip()

            # Check for next story header
            if re.match(r'^###\s+TASK-\d+:', stripped):
                break

            # Parse field headers
            if stripped.startswith('**Priority**:'):
                story["priority"] = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('**Description**:'):
                if current_field and field_content:
                    self._store_field(story, current_field, field_content)
                current_field = "description"
                desc = stripped.split(':', 1)[1].strip()
                field_content = [desc] if desc else []
            elif stripped.startswith('**Rationale**:'):
                if current_field and field_content:
                    self._store_field(story, current_field, field_content)
                current_field = "rationale"
                rat = stripped.split(':', 1)[1].strip()
                field_content = [rat] if rat else []
            elif stripped.startswith('**Acceptance Criteria**:'):
                if current_field and field_content:
                    self._store_field(story, current_field, field_content)
                current_field = "acceptanceCriteria"
                field_content = []
            elif stripped.startswith('**Definition of Done**:'):
                if current_field and field_content:
                    self._store_field(story, current_field, field_content)
                current_field = "definitionOfDone"
                field_content = []
            elif stripped.startswith('**Dependencies**:'):
                if current_field and field_content:
                    self._store_field(story, current_field, field_content)
                deps = stripped.split(':', 1)[1].strip()
                story["dependencies"] = self._parse_dependencies(deps)
                current_field = None
                field_content = []
            elif current_field:
                # Collect content for current field
                if current_field in ("acceptanceCriteria", "definitionOfDone"):
                    if stripped.startswith('- '):
                        item = stripped[2:].strip()
                        # Remove checkbox markers
                        item = re.sub(r'^\[[ x]\]\s*', '', item)
                        if item:
                            field_content.append(item)
                else:
                    if stripped:
                        field_content.append(stripped)

            i += 1

        # Store final field
        if current_field and field_content:
            self._store_field(story, current_field, field_content)

        lines_consumed = i - start_idx
        return (story, lines_consumed)

    def _store_field(
        self, story: Dict[str, Any], field: str, content: List[str]
    ) -> None:
        """Store parsed field content into story dict.

        Args:
            story: Story dictionary to update
            field: Field name
            content: List of content lines
        """
        if field in ("acceptanceCriteria", "definitionOfDone"):
            story[field] = content
        else:
            story[field] = ' '.join(content).strip()

    def _parse_dependencies(self, deps_str: str) -> List[str]:
        """Parse dependencies string into list.

        Args:
            deps_str: Dependencies string (e.g., "TASK-001, TASK-002" or "None")

        Returns:
            List of dependency task IDs
        """
        if not deps_str or deps_str.lower() in ('none', '[]', 'n/a'):
            return []

        # Extract TASK-XXX patterns
        matches = re.findall(r'TASK-\d+', deps_str)
        return matches

    def _extract_assumptions(self) -> List[Dict[str, str]]:
        """Extract assumptions from table.

        Returns:
            List of assumption dictionaries
        """
        content = self._extract_section_content("Assumptions")
        return self._parse_assumptions_table(content)

    def _parse_assumptions_table(self, content: str) -> List[Dict[str, str]]:
        """Parse assumptions table.

        Args:
            content: Section content containing table

        Returns:
            List of assumption dictionaries
        """
        assumptions = []
        lines = content.split('\n')
        in_table = False

        for line in lines:
            line = line.strip()
            if not line.startswith('|'):
                continue

            cells = [c.strip() for c in line.split('|')[1:-1]]

            if not in_table:
                in_table = True
                continue

            # Skip separator
            if all(c.replace('-', '').replace(':', '') == '' for c in cells):
                continue

            if len(cells) >= 3:
                assumption = {
                    "id": cells[0],
                    "assumption": cells[1],
                    "impactIfFalse": cells[2],
                }
                assumptions.append(assumption)

        return assumptions

    def _extract_risks(self) -> List[Dict[str, str]]:
        """Extract risks from table.

        Returns:
            List of risk dictionaries
        """
        content = self._extract_section_content("Risks")
        return self._parse_risks_table(content)

    def _parse_risks_table(self, content: str) -> List[Dict[str, str]]:
        """Parse risks table.

        Args:
            content: Section content containing table

        Returns:
            List of risk dictionaries
        """
        risks = []
        lines = content.split('\n')
        in_table = False

        for line in lines:
            line = line.strip()
            if not line.startswith('|'):
                continue

            cells = [c.strip() for c in line.split('|')[1:-1]]

            if not in_table:
                in_table = True
                continue

            # Skip separator
            if all(c.replace('-', '').replace(':', '') == '' for c in cells):
                continue

            if len(cells) >= 5:
                risk = {
                    "id": cells[0],
                    "risk": cells[1],
                    "likelihood": cells[2],
                    "impact": cells[3],
                    "mitigation": cells[4],
                }
                risks.append(risk)

        return risks

    def _extract_open_questions(self) -> List[Dict[str, str]]:
        """Extract open questions from table.

        Returns:
            List of open question dictionaries
        """
        content = self._extract_section_content("Open Questions")

        # Check for "No open questions" statement
        if "no open questions" in content.lower():
            return []

        return self._parse_open_questions_table(content)

    def _parse_open_questions_table(self, content: str) -> List[Dict[str, str]]:
        """Parse open questions table.

        Args:
            content: Section content containing table

        Returns:
            List of question dictionaries
        """
        questions = []
        lines = content.split('\n')
        in_table = False

        for line in lines:
            line = line.strip()
            if not line.startswith('|'):
                continue

            cells = [c.strip() for c in line.split('|')[1:-1]]

            if not in_table:
                in_table = True
                continue

            # Skip separator
            if all(c.replace('-', '').replace(':', '') == '' for c in cells):
                continue

            if len(cells) >= 4:
                question = {
                    "id": cells[0],
                    "question": cells[1],
                    "owner": cells[2],
                    "status": cells[3],
                }
                questions.append(question)

        return questions


def parse_prd_markdown(
    content: str,
    source_path: Optional[Path] = None,
    timestamp: Optional[str] = None
) -> Dict[str, Any]:
    """Parse PRD markdown content into structured dictionary.

    Convenience function for parsing PRD markdown files.

    Args:
        content: Raw markdown content
        source_path: Optional path to source file
        timestamp: Optional override timestamp for sourceDocument

    Returns:
        Parsed PRD data dictionary
    """
    parser = PRDMarkdownParser(content, source_path)
    result = parser.parse()

    # Allow timestamp override
    if timestamp:
        result["sourceDocument"]["timestamp"] = timestamp

    return result
