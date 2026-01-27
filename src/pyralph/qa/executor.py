#!/usr/bin/env python3
"""QA Executor module for Ralph.

This module provides the QAExecutor class that runs QA rule validation
using an autonomous agent against code changes.
"""

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..agents import get_agent, list_agents
from ..agents.base import AgentError
from ..logger import Logger
from .rules import QARulesLoader, QARule


# Custom error types for QA execution
class QAExecutorError(Exception):
    """Base exception for QA executor errors."""
    pass


class AgentTimeoutError(QAExecutorError):
    """Raised when the agent times out during execution."""
    pass


class AgentUnavailableError(QAExecutorError):
    """Raised when the specified agent is unavailable or fails to respond."""
    pass


class MalformedResponseError(QAExecutorError):
    """Raised when the agent returns a response that cannot be parsed."""
    pass


@dataclass
class QAViolation:
    """Represents a single QA rule violation."""
    rule_id: str
    rule_name: str
    severity: str
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert violation to dictionary."""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "details": self.details,
        }


@dataclass
class QAResult:
    """Represents the result of a QA validation run."""
    success: bool
    rules_checked: int
    violations: List[QAViolation] = field(default_factory=list)
    summary: str = ""
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "success": self.success,
            "rules_checked": self.rules_checked,
            "violations": [v.to_dict() for v in self.violations],
            "summary": self.summary,
        }


class QAExecutor:
    """Executes QA validation using an autonomous agent.

    This class orchestrates the QA validation process by:
    1. Loading QA rules from the .ralph/qa/ directory
    2. Gathering code changes to validate
    3. Sending rules and changes to the specified agent
    4. Parsing and reporting compliance results

    Usage:
        executor = QAExecutor(agent_name="claude")
        result = executor.run()
        if not result.success:
            for violation in result.violations:
                print(f"{violation.severity}: {violation.message}")
    """

    DEFAULT_TIMEOUT = 600  # 10 minutes

    def __init__(
        self,
        agent_name: str,
        timeout: Optional[int] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        rules_dir: Optional[str] = None,
    ):
        """Initialize the QA executor.

        Args:
            agent_name: Name of the agent to use (e.g., "claude", "copilot").
            timeout: Timeout in seconds for agent execution.
            model: Optional model identifier for the agent.
            max_tokens: Optional max tokens for agent response.
            temperature: Optional temperature for agent response.
            rules_dir: Optional path to rules directory.
        """
        self._agent_name = agent_name.lower()
        self._timeout = timeout or self.DEFAULT_TIMEOUT
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._rules_loader = QARulesLoader(rules_dir) if rules_dir else QARulesLoader()

    def _validate_agent(self) -> None:
        """Validate that the specified agent is available.

        Raises:
            AgentUnavailableError: If the agent is not in the list of available agents.
        """
        available = list_agents()
        if self._agent_name not in available:
            raise AgentUnavailableError(
                f"Agent '{self._agent_name}' is not available. "
                f"Available agents: {', '.join(available)}. "
                f"Retry with a valid agent name."
            )

    def _get_code_changes(self) -> str:
        """Get the current code changes using git diff.

        Returns:
            String containing the git diff output, or empty string if no changes.
        """
        try:
            # Get staged and unstaged changes
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding='utf-8',
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout

            # If no changes against HEAD, try getting staged changes
            result = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding='utf-8',
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            Logger.warning("Git diff timed out, proceeding without diff context")
            return ""
        except FileNotFoundError:
            Logger.warning("Git not found, proceeding without diff context")
            return ""
        except Exception as e:
            Logger.warning(f"Error getting git diff: {e}")
            return ""

    def _build_prompt(self, rules: List[QARule], code_changes: str) -> str:
        """Build the prompt for the agent.

        Args:
            rules: List of QA rules to validate against.
            code_changes: The code changes to validate.

        Returns:
            Formatted prompt string for the agent.
        """
        rules_text = "\n".join([
            f"- **{rule.id}** ({rule.severity}): {rule.name}\n"
            f"  Description: {rule.description or 'N/A'}\n"
            f"  Message: {rule.message or 'N/A'}"
            + (f"\n  Pattern: `{rule.pattern}`" if rule.pattern else "")
            for rule in rules
        ])

        prompt = f"""You are a QA validation agent. Analyze the following code changes against the defined QA rules and report any violations.

## QA Rules to Validate Against

{rules_text}

## Code Changes to Analyze

```diff
{code_changes if code_changes else "(No code changes detected - validating current codebase state)"}
```

## Instructions

1. Analyze the code changes (or current state if no changes) against each QA rule
2. Report any violations found
3. For each violation, provide:
   - The rule ID that was violated
   - The file path (if applicable)
   - The line number (if applicable)
   - A brief explanation of the violation

## Response Format

Respond with a JSON object in the following format:
```json
{{
  "success": true/false,
  "violations": [
    {{
      "rule_id": "rule-id",
      "file_path": "path/to/file.py",
      "line_number": 42,
      "message": "Description of the violation"
    }}
  ],
  "summary": "Brief summary of the QA check results"
}}
```

If there are no violations, return:
```json
{{
  "success": true,
  "violations": [],
  "summary": "All QA rules passed. No violations found."
}}
```

IMPORTANT: Your response MUST contain a valid JSON object. Do not include any text before or after the JSON.
"""
        return prompt

    def _parse_response(self, output: str, rules: List[QARule]) -> QAResult:
        """Parse the agent's response into a QAResult.

        Args:
            output: Raw output from the agent.
            rules: List of rules that were checked.

        Returns:
            QAResult object with parsed violations.

        Raises:
            MalformedResponseError: If the response cannot be parsed.
        """
        # Try to extract JSON from the response
        json_str = self._extract_json(output)
        if not json_str:
            raise MalformedResponseError(
                f"Agent response does not contain valid JSON. "
                f"Expected a JSON object with 'success', 'violations', and 'summary' fields. "
                f"Raw response (first 500 chars): {output[:500]}"
            )

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise MalformedResponseError(
                f"Failed to parse agent response as JSON: {e}. "
                f"Extracted JSON (first 500 chars): {json_str[:500]}"
            )

        if not isinstance(data, dict):
            raise MalformedResponseError(
                f"Agent response is not a JSON object. Got: {type(data).__name__}. "
                f"Expected format: {{\"success\": bool, \"violations\": [], \"summary\": str}}"
            )

        # Validate required fields
        if "success" not in data:
            raise MalformedResponseError(
                "Agent response missing required 'success' field. "
                "Expected format: {\"success\": bool, \"violations\": [], \"summary\": str}"
            )

        # Build violations list
        violations = []
        rules_by_id = {r.id: r for r in rules}

        for v in data.get("violations", []):
            if not isinstance(v, dict):
                continue
            rule_id = v.get("rule_id", "unknown")
            rule = rules_by_id.get(rule_id)
            violations.append(QAViolation(
                rule_id=rule_id,
                rule_name=rule.name if rule else "Unknown Rule",
                severity=rule.severity if rule else "major",
                message=v.get("message", ""),
                file_path=v.get("file_path"),
                line_number=v.get("line_number"),
                details=v.get("details", ""),
            ))

        return QAResult(
            success=bool(data.get("success", False)),
            rules_checked=len(rules),
            violations=violations,
            summary=data.get("summary", ""),
            raw_output=output,
        )

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON object from text.

        Args:
            text: Text that may contain JSON.

        Returns:
            Extracted JSON string or None if not found.
        """
        # Try to find JSON block in markdown code fence
        import re
        json_match = re.search(r'```(?:json)?\s*\n(\{.*?\})\s*\n```', text, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Try to find raw JSON object
        brace_start = text.find('{')
        if brace_start == -1:
            return None

        # Find matching closing brace
        depth = 0
        for i, char in enumerate(text[brace_start:], brace_start):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return text[brace_start:i + 1]

        return None

    def run(self) -> QAResult:
        """Execute the QA validation.

        Returns:
            QAResult object with validation results.

        Raises:
            AgentUnavailableError: If the agent is not available.
            AgentTimeoutError: If the agent times out.
            MalformedResponseError: If the agent response cannot be parsed.
        """
        # Validate agent availability
        self._validate_agent()

        # Load QA rules
        rules = self._rules_loader.get_enabled_rules()

        if not rules:
            Logger.info("No QA rules configured in .ralph/qa/ directory. Nothing to validate.")
            return QAResult(
                success=True,
                rules_checked=0,
                violations=[],
                summary="No QA rules configured. Skipping validation.",
            )

        Logger.info(f"Loaded {len(rules)} QA rule(s) for validation")

        # Get code changes
        code_changes = self._get_code_changes()
        if code_changes:
            Logger.info("Analyzing code changes against QA rules...")
        else:
            Logger.info("No code changes detected. Validating current codebase state...")

        # Build prompt and run agent
        prompt = self._build_prompt(rules, code_changes)

        try:
            agent = get_agent(
                self._agent_name,
                timeout_seconds=self._timeout,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )

            # Check dependencies
            if not agent.check_dependencies():
                raise AgentUnavailableError(
                    f"Agent '{self._agent_name}' dependencies are not met. "
                    f"Please ensure the required CLI tools are installed. "
                    f"Retry after installing the necessary dependencies."
                )

            # Set logger for agent
            agent.set_logger(Logger)

            Logger.info(f"Running QA validation with {agent.get_name()} agent...")
            success, output, error = agent.run(prompt, "qa-validation")

            if error:
                if "timeout" in error.message.lower() or "TimeoutExpired" in error.exception_type:
                    raise AgentTimeoutError(
                        f"Agent '{self._agent_name}' timed out after {self._timeout} seconds. "
                        f"Retry with a longer timeout using --timeout flag, or reduce the number of rules."
                    )
                raise AgentUnavailableError(
                    f"Agent '{self._agent_name}' failed to respond: {error.message}. "
                    f"Retry the command or try a different agent."
                )

            # Parse response
            result = self._parse_response(output, rules)
            return result

        except subprocess.TimeoutExpired:
            raise AgentTimeoutError(
                f"Agent '{self._agent_name}' timed out after {self._timeout} seconds. "
                f"Retry with a longer timeout using --timeout flag, or reduce the number of rules."
            )
        except (AgentTimeoutError, AgentUnavailableError, MalformedResponseError):
            raise
        except ValueError as e:
            # get_agent raises ValueError for unknown agent
            raise AgentUnavailableError(str(e))
        except Exception as e:
            raise AgentUnavailableError(
                f"Agent '{self._agent_name}' encountered an error: {e}. "
                f"Retry the command or try a different agent."
            )
