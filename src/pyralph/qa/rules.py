#!/usr/bin/env python3
"""QA Rules loader for Ralph.

This module provides the QARulesLoader class that discovers and loads quality rules
from the .ralph/qa/ directory.

Supported file formats:
    - .yaml, .yml: YAML rule definitions
    - .json: JSON rule definitions
    - .md: Markdown files with YAML frontmatter

Rule file structure:
    id: unique-rule-id
    name: Human readable name
    description: What the rule checks for
    enabled: true/false (default: true)
    severity: critical|major|minor|info (default: major)
    category: code_style|security|performance|testing|documentation
    pattern: Optional regex pattern for matching
    message: Message to display when rule is violated

Severity levels (from highest to lowest priority):
    - critical: Must be fixed immediately, blocks release
    - major: Should be fixed, significant quality issue (default)
    - minor: Should be addressed, minor quality issue
    - info: Informational, suggestions for improvement

Example YAML rule file (.ralph/qa/no_print.yaml):
    id: no-print-statements
    name: No Print Statements
    description: Disallow print() calls in production code
    severity: major
    category: code_style
    pattern: "\\bprint\\s*\\("
    message: Use logging instead of print statements

Example Markdown rule file (.ralph/qa/imports.md):
    ---
    id: organize-imports
    name: Organize Imports
    description: Imports should be organized in groups
    severity: minor
    category: code_style
    ---

    ## Rule Details

    This rule ensures imports are properly organized and grouped.
"""

import errno
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ..config import CONF
from ..logger import Logger


# Supported file extensions for rule files
SUPPORTED_EXTENSIONS: Set[str] = {".yaml", ".yml", ".json", ".md"}

# Valid severity levels in order from highest to lowest priority
SEVERITY_LEVELS: List[str] = ["critical", "major", "minor", "info"]
DEFAULT_SEVERITY: str = "major"


def get_severity_priority(severity: str) -> int:
    """Get the priority value for a severity level.

    Lower values indicate higher priority (critical=0, info=3).

    Args:
        severity: The severity level string.

    Returns:
        Priority value (0-3), or 1 (major) if severity is invalid.
    """
    try:
        return SEVERITY_LEVELS.index(severity)
    except ValueError:
        return SEVERITY_LEVELS.index(DEFAULT_SEVERITY)


def is_valid_severity(severity: str) -> bool:
    """Check if a severity value is valid.

    Args:
        severity: The severity value to check.

    Returns:
        True if the severity is one of: critical, major, minor, info.
    """
    return severity in SEVERITY_LEVELS


@dataclass
class QARule:
    """Represents a quality assurance rule."""

    id: str
    name: str
    description: str = ""
    enabled: bool = True
    severity: str = DEFAULT_SEVERITY
    category: str = "general"
    pattern: Optional[str] = None
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_file: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize rule to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "severity": self.severity,
            "category": self.category,
            "pattern": self.pattern,
            "message": self.message,
            "metadata": self.metadata,
            "source_file": self.source_file,
        }


class QARulesLoader:
    """Discovers, loads, and manages QA rules from the .ralph/qa/ directory.

    This class handles:
    - Auto-creating the .ralph/qa/ directory if it doesn't exist
    - Loading rules from YAML, JSON, and Markdown files
    - Gracefully handling empty directories
    - Logging warnings for malformed rule files
    - Filtering files by supported extensions
    - Detecting and warning about duplicate rule IDs
    - Safe file reading with permission error handling
    - Warning about empty .md files

    Usage:
        loader = QARulesLoader()
        rules = loader.load_rules()
        for rule in rules:
            print(f"Rule: {rule.name}")

        # Get a specific rule by ID
        rule = loader.get_rule_by_id("my-rule-id")
    """

    def __init__(self, rules_dir: Optional[Path] = None):
        """Initialize the QA rules loader.

        Args:
            rules_dir: Path to the rules directory. Defaults to CONF.QA_RULES_DIR.
        """
        self._rules_dir = rules_dir or CONF.QA_RULES_DIR
        self._rules: List[QARule] = []
        self._rules_by_id: Dict[str, QARule] = {}
        self._loaded = False

    @property
    def rules_dir(self) -> Path:
        """Get the rules directory path."""
        return self._rules_dir

    def ensure_directory(self) -> None:
        """Create the rules directory if it doesn't exist.

        Raises:
            PermissionError: If the directory cannot be created due to insufficient permissions.
        """
        try:
            self._rules_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied: Cannot create QA rules directory '{self._rules_dir}'. "
                f"Please check write permissions on the parent directory."
            ) from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise PermissionError(
                    f"Permission denied: Cannot access QA rules directory '{self._rules_dir}'. "
                    f"Please check directory permissions."
                ) from e
            raise

    def load_rules(self) -> List[QARule]:
        """Load all valid rules from the rules directory.

        Returns:
            List of QARule objects loaded from valid rule files.

        Raises:
            PermissionError: If the rules directory or its contents cannot be
                read due to insufficient permissions.
        """
        self._rules = []
        self._rules_by_id = {}
        self.ensure_directory()

        if not self._rules_dir.exists():
            Logger.debug(f"QA rules directory does not exist: {self._rules_dir}")
            self._loaded = True
            return self._rules

        if not self._check_directory_permissions():
            return self._rules

        try:
            rule_files = list(self._rules_dir.iterdir())
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied: Cannot list contents of QA rules directory '{self._rules_dir}'. "
                f"Please check read permissions."
            ) from e

        if not rule_files:
            Logger.info("No rules configured in .ralph/qa/ directory")
            self._loaded = True
            return self._rules

        for file_path in rule_files:
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                Logger.debug(f"Ignoring unsupported file format: {file_path.name}")
                continue

            self._load_rule_file(file_path)

        self._loaded = True
        return self._rules

    def _check_directory_permissions(self) -> bool:
        """Check if the rules directory has proper read permissions.

        Returns:
            True if directory is readable, False otherwise.

        Raises:
            PermissionError: If directory cannot be accessed due to permissions.
        """
        try:
            list(self._rules_dir.iterdir())
            return True
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied: Cannot read QA rules directory '{self._rules_dir}'. "
                f"Please check read permissions on this directory."
            ) from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise PermissionError(
                    f"Permission denied: Cannot access QA rules directory '{self._rules_dir}'. "
                    f"Error: {e}"
                ) from e
            Logger.warning(f"Error accessing QA rules directory: {e}")
            return False

    def _load_rule_file(self, file_path: Path) -> None:
        """Load rules from a single file.

        Args:
            file_path: Path to the rule file.

        Raises:
            PermissionError: If file cannot be read due to insufficient permissions.
        """
        try:
            content = file_path.read_text(encoding="utf-8")

            if file_path.suffix.lower() == ".md":
                rules_data = self._parse_markdown(content, file_path)
            elif file_path.suffix.lower() in {".yaml", ".yml"}:
                rules_data = self._parse_yaml(content, file_path)
            else:
                rules_data = self._parse_json(content, file_path)

            if rules_data is None:
                return

            self._process_rules_data(rules_data, file_path)

        except PermissionError as e:
            raise PermissionError(
                f"Permission denied: Cannot read rule file '{file_path}'. "
                f"Please check file permissions."
            ) from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise PermissionError(
                    f"Permission denied: Cannot access rule file '{file_path}'. "
                    f"Error: {e}"
                ) from e
            Logger.warning(f"Failed to read rule file '{file_path.name}': {e}")
        except Exception as e:
            Logger.warning(f"Unexpected error loading rule file '{file_path.name}': {type(e).__name__}: {e}")

    def _parse_markdown(self, content: str, file_path: Path) -> Optional[Any]:
        """Parse Markdown content with YAML frontmatter.

        Extracts YAML frontmatter from between --- markers at the start of the file.
        The body content (after frontmatter) is stored in metadata['body'].

        Args:
            content: Markdown string content.
            file_path: Path to the file (for error/warning messages).

        Returns:
            Parsed frontmatter data or None if parsing failed.
        """
        content = content.strip()

        # Check for empty file
        if not content:
            Logger.warning(f"Empty rule definition in '{file_path.name}': file is empty, skipping")
            return None

        # Check for frontmatter markers
        if not content.startswith("---"):
            Logger.warning(
                f"Empty rule definition in '{file_path.name}': "
                f"markdown file must start with YAML frontmatter (---), skipping"
            )
            return None

        # Find the closing frontmatter marker
        # Skip the first --- and find the next ---
        end_marker_match = re.search(r'\n---\s*\n', content[3:])
        if not end_marker_match:
            # Try to find --- at end of file (no trailing newline after body)
            end_marker_match = re.search(r'\n---\s*$', content[3:])

        if not end_marker_match:
            Logger.warning(
                f"Malformed markdown in rule file '{file_path.name}': "
                f"missing closing frontmatter marker (---)"
            )
            return None

        # Extract frontmatter (between the two --- markers)
        frontmatter_end = end_marker_match.start() + 3  # +3 for initial ---
        frontmatter_content = content[3:frontmatter_end].strip()

        # Check if frontmatter is empty
        if not frontmatter_content:
            Logger.warning(
                f"Empty rule definition in '{file_path.name}': "
                f"YAML frontmatter is empty, skipping"
            )
            return None

        # Extract body content (after the closing ---)
        body_start = frontmatter_end + end_marker_match.end() - end_marker_match.start()
        body_content = content[body_start:].strip() if body_start < len(content) else ""

        # Parse the YAML frontmatter
        try:
            import yaml
            data = yaml.safe_load(frontmatter_content)
        except ImportError:
            Logger.warning(f"PyYAML not installed, cannot load Markdown rule file '{file_path.name}'")
            return None
        except yaml.YAMLError as e:
            Logger.warning(f"Malformed YAML frontmatter in rule file '{file_path.name}': {e}")
            return None

        if data is None:
            Logger.warning(
                f"Empty rule definition in '{file_path.name}': "
                f"YAML frontmatter parsed to empty, skipping"
            )
            return None

        # Store body content in metadata for documentation purposes
        if isinstance(data, dict) and body_content:
            if "metadata" not in data:
                data["metadata"] = {}
            data["metadata"]["body"] = body_content

        return data

    def _parse_yaml(self, content: str, file_path: Path) -> Optional[Any]:
        """Parse YAML content.

        Args:
            content: YAML string content.
            file_path: Path to the file (for error messages).

        Returns:
            Parsed data or None if parsing failed.
        """
        try:
            import yaml
            return yaml.safe_load(content)
        except ImportError:
            Logger.warning(f"PyYAML not installed, cannot load YAML rule file '{file_path.name}'")
            return None
        except yaml.YAMLError as e:
            Logger.warning(f"Malformed YAML in rule file '{file_path.name}': {e}")
            return None

    def _parse_json(self, content: str, file_path: Path) -> Optional[Any]:
        """Parse JSON content.

        Args:
            content: JSON string content.
            file_path: Path to the file (for error messages).

        Returns:
            Parsed data or None if parsing failed.
        """
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            Logger.warning(f"Malformed JSON in rule file '{file_path.name}': {e}")
            return None

    def _process_rules_data(self, data: Any, file_path: Path) -> None:
        """Process parsed rule data and create QARule objects.

        Detects and warns about duplicate rule IDs. When duplicates are found,
        the later rule (from the current file) takes precedence.

        Args:
            data: Parsed rule data (dict or list of dicts).
            file_path: Path to the source file.
        """
        if data is None:
            return

        # Handle single rule or list of rules
        if isinstance(data, dict):
            rules_list = [data]
        elif isinstance(data, list):
            rules_list = data
        else:
            Logger.warning(f"Invalid rule format in '{file_path.name}': expected dict or list")
            return

        for rule_data in rules_list:
            rule = self._create_rule(rule_data, file_path)
            if rule:
                # Check for duplicate rule ID
                if rule.id in self._rules_by_id:
                    existing_rule = self._rules_by_id[rule.id]
                    Logger.warning(
                        f"Duplicate rule ID '{rule.id}' found in '{file_path.name}'. "
                        f"Previously defined in '{existing_rule.source_file}'. "
                        f"The rule from '{file_path.name}' will take precedence."
                    )
                    # Remove the old rule from the list
                    self._rules = [r for r in self._rules if r.id != rule.id]

                self._rules.append(rule)
                self._rules_by_id[rule.id] = rule

    def _create_rule(self, data: Any, file_path: Path) -> Optional[QARule]:
        """Create a QARule from parsed data.

        Args:
            data: Rule data dictionary.
            file_path: Path to the source file.

        Returns:
            QARule object or None if data is invalid.
        """
        if not isinstance(data, dict):
            Logger.warning(f"Invalid rule entry in '{file_path.name}': expected dict, got {type(data).__name__}")
            return None

        rule_id = data.get("id")
        rule_name = data.get("name")

        if not rule_id:
            Logger.warning(f"Rule in '{file_path.name}' missing required 'id' field")
            return None

        if not rule_name:
            Logger.warning(f"Rule in '{file_path.name}' missing required 'name' field")
            return None

        # Handle severity: validate and apply defaults
        severity = self._validate_severity(data, rule_id, file_path)

        # Extract known fields and put rest in metadata
        known_fields = {"id", "name", "description", "enabled", "severity", "category", "pattern", "message", "metadata"}
        extra_metadata = {k: v for k, v in data.items() if k not in known_fields}

        # Merge existing metadata with extra fields
        metadata = data.get("metadata", {}) or {}
        metadata.update(extra_metadata)

        return QARule(
            id=str(rule_id),
            name=str(rule_name),
            description=str(data.get("description", "")),
            enabled=bool(data.get("enabled", True)),
            severity=severity,
            category=str(data.get("category", "general")),
            pattern=data.get("pattern"),
            message=str(data.get("message", "")),
            metadata=metadata,
            source_file=str(file_path),
        )

    def _validate_severity(self, data: Dict[str, Any], rule_id: str, file_path: Path) -> str:
        """Validate and normalize severity level for a rule.

        Args:
            data: Rule data dictionary.
            rule_id: The rule's ID for logging.
            file_path: Path to the source file for logging.

        Returns:
            A valid severity level string.
        """
        if "severity" not in data:
            Logger.debug(
                f"Rule '{rule_id}' in '{file_path.name}' has no severity specified, "
                f"defaulting to '{DEFAULT_SEVERITY}'"
            )
            return DEFAULT_SEVERITY

        severity_value = str(data.get("severity", "")).lower()

        if not is_valid_severity(severity_value):
            Logger.warning(
                f"Invalid severity '{data.get('severity')}' for rule '{rule_id}' in '{file_path.name}'. "
                f"Valid values are: {', '.join(SEVERITY_LEVELS)}. Defaulting to '{DEFAULT_SEVERITY}'."
            )
            return DEFAULT_SEVERITY

        return severity_value

    def get_rules(self) -> List[QARule]:
        """Get all loaded rules.

        Returns:
            List of loaded QARule objects. Loads rules if not already loaded.
        """
        if not self._loaded:
            self.load_rules()
        return list(self._rules)

    def get_rule_by_id(self, rule_id: str) -> Optional[QARule]:
        """Get a specific rule by its unique ID.

        Args:
            rule_id: The unique identifier of the rule to retrieve.

        Returns:
            The QARule with the specified ID, or None if not found.
        """
        if not self._loaded:
            self.load_rules()
        return self._rules_by_id.get(rule_id)

    def get_all_rule_ids(self) -> List[str]:
        """Get a list of all loaded rule IDs.

        Returns:
            List of rule ID strings.
        """
        if not self._loaded:
            self.load_rules()
        return list(self._rules_by_id.keys())

    def get_enabled_rules(self) -> List[QARule]:
        """Get only enabled rules.

        Returns:
            List of enabled QARule objects.
        """
        if not self._loaded:
            self.load_rules()
        return [rule for rule in self._rules if rule.enabled]

    def get_rules_by_category(self, category: str) -> List[QARule]:
        """Get rules filtered by category.

        Args:
            category: Category to filter by.

        Returns:
            List of QARule objects matching the category.
        """
        return [rule for rule in self.get_rules() if rule.category == category]

    def get_rules_by_severity(self, severity: str) -> List[QARule]:
        """Get rules filtered by severity.

        Args:
            severity: Severity to filter by (critical, major, minor, info).

        Returns:
            List of QARule objects matching the severity.
        """
        return [rule for rule in self.get_rules() if rule.severity == severity]

    def reload(self) -> List[QARule]:
        """Force reload all rules from disk.

        Returns:
            List of freshly loaded QARule objects.
        """
        self._loaded = False
        return self.load_rules()
