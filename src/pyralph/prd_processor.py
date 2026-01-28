#!/usr/bin/env python3
"""PRD processor module for Ralph orchestrator.

This module contains the PRDProcessor class which handles PRD validation,
revision, and labeling operations.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .logger import Logger as LoggerType
    from .agents.base import BaseAgent
    from .prd import JsonUtils as JsonUtilsType


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
