#!/usr/bin/env python3
"""PRD processor module for Ralph orchestrator.

This module contains the PRDProcessor class which handles PRD validation,
revision, labeling operations, and drift detection between prd-*.md and prd.json.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .logger import Logger as LoggerType
    from .agents.base import BaseAgent
    from .prd import JsonUtils as JsonUtilsType


@dataclass
class DriftResult:
    """Result of drift detection between prd-*.md and prd.json."""

    has_drift: bool = False
    markdown_changed: bool = False
    json_manually_edited: bool = False
    changed_sections: List[str] = field(default_factory=list)
    missing_in_markdown: List[str] = field(default_factory=list)
    missing_in_json: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_synchronized(self) -> bool:
        """Check if markdown and JSON are synchronized."""
        return not self.has_drift


class PRDProcessor:
    """Handles PRD validation, revision, and labeling operations.

    Provides functionality for validating PRDs against JSON schemas,
    revising PRDs through the LLM agent, applying labels, and parsing
    revised PRD responses.
    """

    def __init__(
        self,
        agent: "BaseAgent",
        hooks: "HookManager",
        logger: "LoggerType",
        template_manager,
        json_utils: "JsonUtilsType",
        event_class,
        event_type_class,
        schema_path: Optional[str] = None,
        min_criteria: Optional[int] = None,
        labels: Optional[List[str]] = None,
    ):
        """Initialize the PRD processor.

        Args:
            agent: The LLM agent to use for revision
            hooks: HookManager instance for event emission
            logger: Logger class for output
            template_manager: TemplateManager for rendering prompts
            json_utils: JsonUtils class for parsing JSON
            event_class: Event class for creating events
            event_type_class: EventType enum for event types
            schema_path: Optional path to JSON schema file for validation
            min_criteria: Optional minimum acceptance criteria per story
            labels: Optional list of label key=value pairs
        """
        self._agent = agent
        self._hooks = hooks
        self._logger = logger
        self._template_manager = template_manager
        self._json_utils = json_utils
        self._event = event_class
        self._event_type = event_type_class
        self._schema_path = schema_path
        self._min_criteria = min_criteria
        self._labels = labels or []

    def validate_prd(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate PRD data against schema and criteria requirements.

        Args:
            data: The PRD data to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        schema_valid, schema_error = self._validate_prd_schema(data)
        if not schema_valid:
            return False, schema_error

        criteria_valid, criteria_error = self._validate_min_criteria(data)
        if not criteria_valid:
            return False, criteria_error

        return True, ""

    def _validate_prd_schema(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate PRD data against a JSON schema file.

        Args:
            data: The PRD data to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        if not self._schema_path:
            return True, ""

        schema_path = Path(self._schema_path)
        if not schema_path.exists():
            return False, f"Schema file not found: {self._schema_path}"

        try:
            schema = json.loads(schema_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON schema: {e}"

        errors = self._validate_against_schema(data, schema, "")
        if errors:
            return False, "; ".join(errors)
        return True, ""

    def _validate_against_schema(self, data: Any, schema: Dict[str, Any], path: str) -> List[str]:
        """Recursively validate data against a JSON schema.

        Supports a subset of JSON Schema: type, required, properties, items, minItems.

        Args:
            data: The data to validate
            schema: The schema to validate against
            path: Current path in the data for error messages

        Returns:
            List of validation error messages
        """
        errors: List[str] = []
        path_prefix = f"{path}." if path else ""

        if "type" in schema:
            expected_type = schema["type"]
            type_map = {"string": str, "number": (int, float), "integer": int,
                        "boolean": bool, "array": list, "object": dict, "null": type(None)}
            if expected_type in type_map:
                expected = type_map[expected_type]
                if not isinstance(data, expected):
                    errors.append(f"{path or 'root'}: expected {expected_type}, got {type(data).__name__}")
                    return errors

        if "required" in schema and isinstance(data, dict):
            for req in schema["required"]:
                if req not in data:
                    errors.append(f"{path_prefix}{req}: required property missing")

        if "properties" in schema and isinstance(data, dict):
            for prop, prop_schema in schema["properties"].items():
                if prop in data:
                    errors.extend(self._validate_against_schema(data[prop], prop_schema, f"{path_prefix}{prop}"))

        if "items" in schema and isinstance(data, list):
            for i, item in enumerate(data):
                errors.extend(self._validate_against_schema(item, schema["items"], f"{path}[{i}]"))

        if "minItems" in schema and isinstance(data, list):
            if len(data) < schema["minItems"]:
                errors.append(f"{path or 'root'}: array has {len(data)} items, minimum is {schema['minItems']}")

        return errors

    def _validate_min_criteria(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate that each user story has at least the minimum number of acceptance criteria.

        Args:
            data: The PRD data to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        if self._min_criteria is None:
            return True, ""

        stories = data.get("userStories", [])
        violations = []
        for story in stories:
            story_id = story.get("id", "unknown")
            criteria = story.get("acceptanceCriteria", [])
            if len(criteria) < self._min_criteria:
                violations.append(f"{story_id} has {len(criteria)} criteria (minimum: {self._min_criteria})")

        if violations:
            return False, "; ".join(violations)
        return True, ""

    def label_tasks(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply custom labels to the PRD data.

        Labels are key=value pairs that get added to a 'labels' dict in the PRD.

        Args:
            data: The PRD data to annotate

        Returns:
            The PRD data with labels applied
        """
        if not self._labels:
            return data

        labels_dict: Dict[str, str] = {}
        for label in self._labels:
            if "=" in label:
                key, value = label.split("=", 1)
                labels_dict[key.strip()] = value.strip()
            else:
                labels_dict[label.strip()] = ""

        if labels_dict:
            data["labels"] = labels_dict
        return data

    def revise_prd(self, original_prd: Dict[str, Any]) -> Dict[str, Any]:
        """Revise PRD through the revision agent for quality improvements.

        Args:
            original_prd: The original PRD data to revise

        Returns:
            Revised PRD data, or original PRD on failure

        Notes:
            - If revision fails or times out, falls back to original PRD with warning
            - If revised PRD fails schema validation, falls back to original PRD
            - Logs when no revision was needed (PRD already optimal)
        """
        self._logger.info("\n🔧 Revising PRD...", "CYAN")
        self._hooks.emit(self._event(self._event_type.PRD_REVISE_START, phase="planner"))

        original_prd_json = json.dumps(original_prd, indent=2)

        prompt = self._template_manager.render(
            "revise_prd.txt",
            original_prd=original_prd_json
        )

        success, stdout, error = self._agent.run(prompt, "REVISE_PRD")

        if not success:
            error_msg = error.message if error else "Unknown error"
            self._logger.warning(f"PRD revision failed: {error_msg}")
            self._logger.warning("Falling back to original PRD.")
            self._hooks.emit(self._event(self._event_type.PRD_REVISE_FAILURE, phase="planner",
                                  metadata={"reason": "agent_failure", "error": error_msg}))
            return original_prd

        revised_prd, revision_summary = self._parse_revised_prd(stdout, original_prd)

        if revised_prd is None:
            self._logger.warning("Could not parse revised PRD from response.")
            self._logger.warning("Falling back to original PRD.")
            self._hooks.emit(self._event(self._event_type.PRD_REVISE_FAILURE, phase="planner",
                                  metadata={"reason": "parse_failure"}))
            return original_prd

        if self._schema_path:
            schema_valid, schema_error = self._validate_prd_schema(revised_prd)
            if not schema_valid:
                self._logger.warning(f"Revised PRD failed schema validation: {schema_error}")
                self._logger.warning("Falling back to original PRD.")
                self._hooks.emit(self._event(self._event_type.PRD_REVISE_FAILURE, phase="planner",
                                      metadata={"reason": "schema_validation_failed", "error": schema_error}))
                return original_prd

        if revision_summary and "no revision" in revision_summary.lower():
            self._logger.info("✅ PRD already optimal, no revision needed.", "GREEN")
        else:
            self._logger.debug(f"Revision summary: {revision_summary}", "CYAN")
            self._logger.info("✅ PRD revised.", "GREEN")

        self._hooks.emit(self._event(self._event_type.PRD_REVISE_SUCCESS, phase="planner",
                              metadata={"summary": revision_summary or ""}))
        return revised_prd

    def _parse_revised_prd(self, response: str, fallback: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Parse the revised PRD from the agent response.

        Args:
            response: The raw response from the revision agent
            fallback: The fallback PRD data if parsing fails

        Returns:
            Tuple of (revised_prd_data, revision_summary). revised_prd_data is None if parsing fails.
        """
        prd_pattern = r'<REVISED_PRD>\s*(.*?)\s*</REVISED_PRD>'
        prd_match = re.search(prd_pattern, response, re.DOTALL)

        summary_pattern = r'<REVISION_SUMMARY>\s*(.*?)\s*</REVISION_SUMMARY>'
        summary_match = re.search(summary_pattern, response, re.DOTALL)
        revision_summary = summary_match.group(1).strip() if summary_match else None

        if not prd_match:
            self._logger.warning("Could not find <REVISED_PRD> tags in response.")
            return None, revision_summary

        prd_text = prd_match.group(1).strip()

        try:
            revised_prd = self._json_utils.parse(prd_text)
            if "userStories" not in revised_prd:
                self._logger.warning("Revised PRD missing 'userStories' key.")
                return None, revision_summary
            return revised_prd, revision_summary
        except json.JSONDecodeError as e:
            self._logger.warning(f"Invalid JSON in revised PRD: {e}")
            return None, revision_summary

    def check_drift(
        self,
        prd_data: Dict[str, Any],
        prd_md_path: Optional[Path] = None,
    ) -> DriftResult:
        """Check for drift between prd-*.md and prd.json.

        Detects if the markdown source has changed since prd.json was generated,
        verifies task ID traceability, and detects manual prd.json edits.

        Args:
            prd_data: The loaded prd.json data
            prd_md_path: Optional explicit path to markdown file. If not provided,
                        uses the path from sourceDocument.

        Returns:
            DriftResult containing drift detection results
        """
        from .prd_markdown_parser import PRDMarkdownParser

        result = DriftResult()

        # Get sourceDocument metadata
        source_doc = prd_data.get("sourceDocument", {})
        stored_hash = source_doc.get("contentHash", "")
        stored_path = source_doc.get("path", "")

        # Determine markdown path
        if prd_md_path is None and stored_path:
            prd_md_path = Path(stored_path)

        if prd_md_path is None or not prd_md_path.exists():
            result.has_drift = True
            result.warnings.append(
                f"Source markdown file not found: {prd_md_path or 'unknown'}"
            )
            return result

        # Read current markdown content
        try:
            md_content = prd_md_path.read_text(encoding='utf-8')
        except IOError as e:
            result.has_drift = True
            result.warnings.append(f"Cannot read markdown file: {e}")
            return result

        # Compute current hash
        current_hash = PRDMarkdownParser.compute_hash(md_content)

        # Check for markdown changes
        if current_hash != stored_hash:
            result.has_drift = True
            result.markdown_changed = True
            result.warnings.append(
                f"{prd_md_path.name} has changed since prd.json was generated - "
                "regenerate prd.json"
            )
            # Try to identify changed sections
            result.changed_sections = self._identify_changed_sections(
                md_content, prd_data
            )

        # Check task ID traceability
        traceability_result = self._verify_task_traceability(md_content, prd_data)
        if traceability_result.missing_in_markdown or traceability_result.missing_in_json:
            result.has_drift = True
            result.missing_in_markdown = traceability_result.missing_in_markdown
            result.missing_in_json = traceability_result.missing_in_json
            if traceability_result.missing_in_markdown:
                result.warnings.append(
                    f"Task IDs in prd.json but not in markdown: "
                    f"{', '.join(traceability_result.missing_in_markdown)}"
                )
            if traceability_result.missing_in_json:
                result.warnings.append(
                    f"Task IDs in markdown but not in prd.json: "
                    f"{', '.join(traceability_result.missing_in_json)}"
                )

        # Detect manual prd.json edits by comparing regenerated JSON
        manual_edit_detected = self._detect_manual_edits(md_content, prd_data, prd_md_path)
        if manual_edit_detected:
            result.json_manually_edited = True
            result.warnings.append(
                "prd.json should not be edited directly - "
                "modify prd-<short-description>.md and regenerate"
            )

        return result

    def _identify_changed_sections(
        self,
        md_content: str,
        prd_data: Dict[str, Any]
    ) -> List[str]:
        """Identify which sections have likely changed.

        Compares markdown section content against prd.json fields to
        identify potential changes.

        Args:
            md_content: Current markdown content
            prd_data: The prd.json data

        Returns:
            List of section names that appear to have changed
        """
        changed = []

        # Map of section names to JSON fields for comparison
        section_checks = [
            ("Executive Summary", "description"),
            ("Problem Statement", "problemStatement"),
            ("Proposed Solution", "proposedSolution"),
            ("Technical Constraints", "technicalConstraints"),
            ("Out of Scope", "outOfScope"),
        ]

        for section_name, json_field in section_checks:
            # Extract section content from markdown
            pattern = rf'^##\s+{re.escape(section_name)}\s*$'
            match = re.search(pattern, md_content, re.MULTILINE)
            if match:
                # Find section content up to next ##
                start = match.end()
                next_section = re.search(r'^##\s+', md_content[start:], re.MULTILINE)
                end = start + next_section.start() if next_section else len(md_content)
                section_content = md_content[start:end].strip()

                # Compare with JSON value
                json_value = prd_data.get(json_field, "")
                if isinstance(json_value, list):
                    json_content = '\n'.join(str(item) for item in json_value)
                else:
                    json_content = str(json_value) if json_value else ""

                # Simple content presence check - if content differs significantly
                if section_content and json_content:
                    # Normalize for comparison
                    md_normalized = re.sub(r'\s+', ' ', section_content.lower())
                    json_normalized = re.sub(r'\s+', ' ', json_content.lower())
                    # Check if significant content differs
                    if md_normalized[:100] != json_normalized[:100]:
                        changed.append(section_name)

        # Check user stories section
        md_task_ids = set(re.findall(r'^###\s+(TASK-\d+):', md_content, re.MULTILINE))
        json_task_ids = {s.get("id", "") for s in prd_data.get("userStories", [])}
        if md_task_ids != json_task_ids:
            changed.append("User Stories")

        return changed

    def _verify_task_traceability(
        self,
        md_content: str,
        prd_data: Dict[str, Any]
    ) -> DriftResult:
        """Verify that task IDs appear in both markdown and JSON.

        Args:
            md_content: The markdown content
            prd_data: The prd.json data

        Returns:
            DriftResult with missing task ID information
        """
        result = DriftResult()

        # Extract TASK-XXX IDs from markdown headers
        md_task_ids: Set[str] = set(
            re.findall(r'^###\s+(TASK-\d+):', md_content, re.MULTILINE)
        )

        # Extract task IDs from prd.json
        json_task_ids: Set[str] = {
            story.get("id", "") for story in prd_data.get("userStories", [])
        }
        json_task_ids.discard("")  # Remove empty string if present

        # Find mismatches
        result.missing_in_markdown = sorted(json_task_ids - md_task_ids)
        result.missing_in_json = sorted(md_task_ids - json_task_ids)

        return result

    def _detect_manual_edits(
        self,
        md_content: str,
        prd_data: Dict[str, Any],
        prd_md_path: Path
    ) -> bool:
        """Detect if prd.json was manually edited.

        Re-parses the markdown and compares critical user story fields with the stored JSON.
        If they differ but the markdown hasn't changed, the JSON was edited.

        Args:
            md_content: The markdown content
            prd_data: The loaded prd.json data
            prd_md_path: Path to the markdown file

        Returns:
            True if manual edits detected, False otherwise
        """
        from .prd_markdown_parser import parse_prd_markdown
        from datetime import datetime

        source_doc = prd_data.get("sourceDocument", {})
        stored_hash = source_doc.get("contentHash", "")

        from .prd_markdown_parser import PRDMarkdownParser
        current_hash = PRDMarkdownParser.compute_hash(md_content)

        # Only check for manual edits if markdown hash matches (no markdown changes)
        if current_hash != stored_hash:
            return False

        # Re-parse markdown to get expected JSON
        try:
            timestamp = datetime.fromtimestamp(
                prd_md_path.stat().st_mtime
            ).isoformat()
            expected_data = parse_prd_markdown(
                md_content,
                source_path=prd_md_path,
                timestamp=timestamp
            )

            # Compare user stories - the most important field for traceability
            expected_stories = expected_data.get("userStories", [])
            actual_stories = prd_data.get("userStories", [])

            if len(expected_stories) != len(actual_stories):
                return True

            # Build lookup by task ID
            expected_by_id = {s.get("id"): s for s in expected_stories}
            actual_by_id = {s.get("id"): s for s in actual_stories}

            # Check for missing or extra task IDs
            if set(expected_by_id.keys()) != set(actual_by_id.keys()):
                return True

            # Compare critical fields for each story
            critical_fields = ["description", "acceptanceCriteria", "priority"]
            for task_id, expected_story in expected_by_id.items():
                actual_story = actual_by_id.get(task_id)
                if actual_story is None:
                    return True

                for field in critical_fields:
                    expected_val = expected_story.get(field)
                    actual_val = actual_story.get(field)
                    if expected_val != actual_val:
                        return True

        except Exception:
            # If parsing fails, can't determine manual edits - don't flag as edited
            pass

        return False

    def run_drift_check(
        self,
        prd_data: Dict[str, Any],
        prd_md_path: Optional[Path] = None,
        strict: bool = False
    ) -> Tuple[bool, List[str]]:
        """Run drift detection and return status.

        Convenience method that runs check_drift and formats results.

        Args:
            prd_data: The loaded prd.json data
            prd_md_path: Optional explicit path to markdown file
            strict: If True, treat drift as an error; otherwise as warning

        Returns:
            Tuple of (is_ok, messages). is_ok is False if drift detected and strict=True
        """
        result = self.check_drift(prd_data, prd_md_path)

        messages = []
        for warning in result.warnings:
            if strict:
                self._logger.info(f"❌ {warning}", "RED")
            else:
                self._logger.info(f"⚠️  {warning}", "YELLOW")
            messages.append(warning)

        if result.changed_sections:
            section_list = ", ".join(result.changed_sections)
            msg = f"Changed sections detected: {section_list}"
            self._logger.info(f"   {msg}", "YELLOW")
            messages.append(msg)

        if result.is_synchronized:
            self._logger.info("✅ prd.json is synchronized with source markdown", "GREEN")

        is_ok = not (result.has_drift and strict)
        return is_ok, messages
