#!/usr/bin/env python3
"""QA domain models for Ralph.

This module contains the core QA data classes:
- QARequirement: Represents a single requirement item in the QA checklist
- QAFindingType: Finding severity/type categories
- QAFinding: Represents a single QA finding with location and severity
"""
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional


@dataclass
class QARequirement:
    """Represents a single requirement item in the QA checklist.

    Attributes:
        id: Unique identifier for the requirement
        description: Human-readable description of the requirement
        status: Current validation status (pending/passed/failed)
        lastChecked: ISO timestamp of last validation check (None if never checked)
        linkedTasks: List of task IDs that contributed to this requirement
    """
    id: str
    description: str
    status: str = "pending"
    lastChecked: Optional[str] = None
    linkedTasks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert requirement to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "lastChecked": self.lastChecked,
            "linkedTasks": self.linkedTasks
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QARequirement':
        """Create a QARequirement from a dictionary.

        Args:
            data: Dictionary containing requirement fields

        Returns:
            QARequirement instance

        Raises:
            KeyError: If required fields (id, description) are missing
        """
        return cls(
            id=data["id"],
            description=data["description"],
            status=data.get("status", "pending"),
            lastChecked=data.get("lastChecked"),
            linkedTasks=data.get("linkedTasks", [])
        )


class QAFindingType(IntEnum):
    """Finding severity/type categories ordered by severity (highest first)."""
    ERROR = 0      # Critical issues (maps to 'critical_issues' in JSON)
    WARNING = 1    # Warnings that should be addressed
    SUGGESTION = 2 # Nice-to-have improvements
    INFO = 3       # Informational notes


@dataclass
class QAFinding:
    """Represents a single QA finding with location and severity information."""
    finding_type: QAFindingType
    category: str
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    recommendation: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    has_missing_data: bool = False
    missing_data_note: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], finding_type: QAFindingType) -> 'QAFinding':
        """Create a QAFinding from a dictionary, handling malformed/incomplete data.

        Args:
            data: Dictionary containing finding data from QA JSON output
            finding_type: The severity/type of this finding

        Returns:
            QAFinding instance with available data and notes about missing fields
        """
        missing_fields = []
        has_missing = False

        # Extract category with fallback
        category = data.get('category')
        if not category or not isinstance(category, str):
            category = 'unknown'
            missing_fields.append('category')
            has_missing = True

        # Extract description with fallback
        description = data.get('description')
        if not description or not isinstance(description, str):
            description = 'No description provided'
            missing_fields.append('description')
            has_missing = True

        # Parse location field (format: "file:line" or "file" or general text)
        file_path = None
        line_number = None
        location = data.get('location', '')

        if location and isinstance(location, str):
            # Try to parse "file:line" format
            if ':' in location:
                parts = location.rsplit(':', 1)
                potential_file = parts[0].strip()
                potential_line = parts[1].strip()

                # Check if the second part looks like a line number
                if potential_line.isdigit():
                    file_path = potential_file
                    line_number = int(potential_line)
                else:
                    # Could be file path with colon (e.g., C:\path) or general description
                    file_path = location.strip()
            else:
                # Just a file path or general description
                file_path = location.strip()
        else:
            missing_fields.append('location')
            has_missing = True

        # Extract recommendation
        recommendation = data.get('recommendation')
        if recommendation and not isinstance(recommendation, str):
            recommendation = None

        # Build missing data note
        missing_note = None
        if missing_fields:
            missing_note = f"Missing fields: {', '.join(missing_fields)}"

        return cls(
            finding_type=finding_type,
            category=category,
            description=description,
            file_path=file_path,
            line_number=line_number,
            recommendation=recommendation,
            raw_data=data,
            has_missing_data=has_missing,
            missing_data_note=missing_note
        )

    def format_location(self) -> str:
        """Format the location string for display.

        Returns:
            Formatted location string (e.g., "src/file.py:42" or "src/file.py" or "")
        """
        if self.file_path and self.line_number:
            return f"{self.file_path}:{self.line_number}"
        elif self.file_path:
            return self.file_path
        return ""
