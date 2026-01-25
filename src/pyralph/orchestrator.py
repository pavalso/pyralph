#!/usr/bin/env python3
"""RalphOrchestrator - Core execution logic for Ralph autonomous development agent.

NOTE: This module is the documented exception to the ~500 line target (TASK-015).
As the core orchestrator containing the main RalphOrchestrator class with all CLI
flag handling and execution logic, it is expected to be the largest module in the
codebase.
"""

import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import configuration
from .config import CONF

# Import logging
from .logger import Logger

# Import shell
from .shell import Shell

# Import PRD utilities
from .prd import PRDManager, JsonUtils

# Import memory management
from .memory import MemoryManager

# Import templates
from .templates import PromptFormatter, TemplateManager

# Import QA models, checklist management, and findings analyzer
from .qa.models import QARequirement, QAFinding, QAFindingType
from .qa.checklist import QAChecklistError, QAChecklistCorruptedError, QAChecklistManager
from .qa.analyzer import QAFindingsAnalyzer

# Import agents
from .agents import get_agent, list_agents
from .agents.base import AgentError

# Import hooks
from .hooks import HookManager, Event, EventType


class RalphOrchestrator:
    def __init__(self, agent_name: str = "claude", enable_hooks: bool = True, enabled_hook_names: Optional[List[str]] = None,
                 intent: Optional[str] = None, intent_file: Optional[str] = None, prompt_file: Optional[str] = None,
                 enhance_intent: bool = False, enhance_intent_strict: bool = False,
                 tree_depth: int = 2, tree_ignore: Optional[List[str]] = None, memory_out: Optional[str] = None,
                 test_cmd: Optional[str] = None, skip_verify: bool = False, retries: Optional[int] = None,
                 timeout: Optional[int] = None, only: Optional[List[str]] = None, except_tasks: Optional[List[str]] = None,
                 resume: Optional[str] = None, include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                 context_limit: Optional[int] = None,
                 model: Optional[str] = None, temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None, seed: Optional[int] = None,
                 log_file: Optional[str] = None, log_level: Optional[str] = None,
                 json_output: bool = False, ndjson_output: bool = False,
                 print_prd: bool = False, prd_out: Optional[str] = None, archive: bool = True,
                 non_interactive: bool = False, ci: bool = False, status_check: bool = False,
                 pre: Optional[List[str]] = None, post: Optional[List[str]] = None,
                 plugin: Optional[List[str]] = None,
                 schema: Optional[str] = None, min_criteria: Optional[int] = None,
                 label: Optional[List[str]] = None, revise_prd: bool = False,
                 qa_review: bool = False, qa_strict: bool = False, qa_path: Optional[str] = None,
                 qa_checklist: Optional[Path] = None) -> None:
        # Use --timeout override if provided, otherwise use config default
        agent_timeout = timeout if timeout is not None else CONF.TIMEOUT_SECONDS
        self.agent = get_agent(agent_name, timeout_seconds=agent_timeout,
                               model=model, temperature=temperature,
                               max_tokens=max_tokens, seed=seed)
        if hasattr(self.agent, 'set_logger'):
            self.agent.set_logger(Logger)
        if hasattr(self.agent, 'set_config'):
            self.agent.set_config(CONF)
        if not self.agent.check_dependencies():
            Logger.info(f"❌ Agent '{self.agent.get_name()}' dependencies not satisfied.", "RED")
            sys.exit(1)
        self.memory = MemoryManager()
        CONF.ensure_directories()
        self._validate_memory_on_startup()
        # Initialize hook system
        self.hooks = HookManager(CONF.HOOKS_DIR, Logger)
        if not enable_hooks:
            self.hooks.disable()
        elif enabled_hook_names is not None:
            self.hooks.set_enabled_hooks(enabled_hook_names)
        # Store intent flags for non-interactive runs
        self._intent = intent
        self._intent_file = intent_file
        self._prompt_file_override = prompt_file
        self._enhance_intent = enhance_intent
        self._enhance_intent_strict = enhance_intent_strict
        # Store architect control flags
        self._tree_depth = tree_depth
        self._tree_ignore = tree_ignore
        self._memory_out = memory_out
        # Store execution and verification flags
        self._test_cmd_override = test_cmd
        self._skip_verify = skip_verify
        self._retries_override = retries
        self._timeout_override = timeout
        self._only_tasks = only
        self._except_tasks = except_tasks
        self._resume_from = resume
        # Store context and memory control flags
        self._include_patterns = include
        self._exclude_patterns = exclude
        self._context_limit = context_limit
        # Store I/O, logging and output flags
        self._log_file = log_file
        self._log_level = log_level
        self._json_output = json_output
        self._ndjson_output = ndjson_output
        self._print_prd_flag = print_prd
        self._prd_out = prd_out
        self._archive = archive
        # Store headless operation flags
        self._non_interactive = non_interactive
        self._ci = ci
        self._status_check = status_check
        # Store extensibility and hook flags
        self._pre_commands = pre or []
        self._post_commands = post or []
        self._plugin_paths = plugin or []
        # Load plugins if specified
        self._load_plugins()
        # Store PRD and story control flags
        self._schema_path = schema
        self._min_criteria = min_criteria
        self._labels = label or []
        self._revise_prd = revise_prd
        # Store QA review flags
        self._qa_review = qa_review
        self._qa_strict = qa_strict
        self._qa_path = qa_path
        self._qa_checklist_path = qa_checklist
        # Initialize PRD manager for consolidated file operations
        self._prd = PRDManager(CONF.PRD_FILE)

    @property
    def qa_checklist_path(self) -> Path:
        """Get the effective QA checklist path.

        Returns the custom path if provided via --qa-checklist, otherwise
        returns the default path from CONF.QA_CHECKLIST_FILE.
        """
        return self._qa_checklist_path if self._qa_checklist_path else CONF.QA_CHECKLIST_FILE

    def _load_plugins(self) -> None:
        """
        Load plugins from specified paths.

        Plugins are Python files or directories containing hook definitions.
        Each plugin can register hooks programmatically via the HookManager API.
        """
        for plugin_path_str in self._plugin_paths:
            plugin_path = Path(plugin_path_str)
            if not plugin_path.exists():
                Logger.warning(f"Plugin path not found: {plugin_path}")
                continue

            if plugin_path.is_file() and plugin_path.suffix == '.py':
                self._load_plugin_file(plugin_path)
            elif plugin_path.is_dir():
                # Load all .py files in the directory
                for py_file in plugin_path.glob('*.py'):
                    if not py_file.name.startswith('_'):
                        self._load_plugin_file(py_file)
            else:
                Logger.warning(f"Invalid plugin path (must be .py file or directory): {plugin_path}")

    def _load_plugin_file(self, path: Path) -> None:
        """
        Load a single plugin file.

        The plugin file should define:
        - EVENTS: List of event names to subscribe to
        - on_event(event): Handler function
        - Optional: PRIORITY, TIMEOUT, MODIFIES_DATA

        Args:
            path: Path to the plugin Python file
        """
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                Logger.warning(f"Could not load plugin: {path}")
                return

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Check if plugin has required attributes
            if not hasattr(module, 'EVENTS') or not hasattr(module, 'on_event'):
                Logger.warning(f"Plugin '{path.name}' missing EVENTS or on_event")
                return

            # Register the plugin as a hook
            events = getattr(module, 'EVENTS', [])
            priority = getattr(module, 'PRIORITY', 100)
            timeout = getattr(module, 'TIMEOUT', 5.0)
            modifies_data = getattr(module, 'MODIFIES_DATA', False)
            handler = getattr(module, 'on_event')

            success = self.hooks.register_hook(
                name=f"plugin_{path.stem}",
                handler=handler,
                events=events,
                priority=priority,
                timeout=timeout,
                modifies_data=modifies_data
            )
            if success:
                Logger.debug(f"Loaded plugin: {path.name}")
            else:
                Logger.warning(f"Failed to register plugin: {path.name}")

        except Exception as e:
            Logger.warning(f"Error loading plugin '{path.name}': {e}")

    def _run_pre_commands(self, phase: str) -> bool:
        """
        Run pre-execution commands before a phase.

        Pre-commands are shell commands executed before each phase.
        If any command fails (non-zero exit code), the phase is aborted.

        Args:
            phase: The phase about to run (architect, planner, execute)

        Returns:
            True if all commands succeeded, False if any failed
        """
        if not self._pre_commands:
            return True

        Logger.debug(f"Running {len(self._pre_commands)} pre-command(s) for {phase} phase")
        for cmd in self._pre_commands:
            Logger.debug(f"  Pre-command: {cmd}")
            stdout, stderr, code = Shell.run(cmd, timeout=60)
            if code != 0:
                Logger.error(f"Pre-command failed: {cmd}")
                Logger.error(f"  Exit code: {code}")
                if stderr:
                    Logger.error(f"  Stderr: {stderr[:500]}")
                self.hooks.emit(Event(
                    EventType.ERROR,
                    phase=phase,
                    metadata={"reason": "pre_command_failed", "command": cmd, "exit_code": code}
                ))
                return False
            if stdout and Logger.verbosity >= 2:
                Logger.trace(f"  Output: {stdout[:200]}")
        return True

    def _run_post_commands(self, phase: str, success: bool) -> None:
        """
        Run post-execution commands after a phase.

        Post-commands are shell commands executed after each phase completes.
        They receive the phase result via environment variables.

        Args:
            phase: The phase that just completed (architect, planner, execute)
            success: Whether the phase completed successfully

        Security Note:
            Uses shell=True for command execution. Commands are sourced from
            user-controlled configuration (--post-command flag), so command
            injection risk is accepted as the user controls their own config.
            Environment variables RALPH_PHASE and RALPH_SUCCESS are set with
            sanitized values (fixed strings and booleans only).
        """
        if not self._post_commands:
            return

        import os
        # Set environment variables for post-commands
        env = os.environ.copy()
        env['RALPH_PHASE'] = phase
        env['RALPH_SUCCESS'] = '1' if success else '0'

        Logger.debug(f"Running {len(self._post_commands)} post-command(s) for {phase} phase")
        for cmd in self._post_commands:
            Logger.debug(f"  Post-command: {cmd}")
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True,
                    text=True, encoding='utf-8', timeout=60,
                    env=env
                )
                if result.returncode != 0:
                    Logger.warning(f"Post-command failed: {cmd} (exit code: {result.returncode})")
                elif result.stdout and Logger.verbosity >= 2:
                    Logger.trace(f"  Output: {result.stdout[:200]}")
            except subprocess.TimeoutExpired:
                Logger.warning(f"Post-command timed out: {cmd}")
            except Exception as e:
                Logger.warning(f"Post-command error: {cmd} ({e})")

    def run_architect(self, user_intent: str) -> None:
        """
        Run the architect phase to initialize project memory.

        Creates both .ralph/memory/architecture.md (internal memory) and
        ARCH.md (git-tracked documentation) with project structure,
        tech stack, and test command configuration.

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🕵️  Architect: Initializing Memory...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="architect"))
        self.hooks.emit(Event(EventType.ARCHITECT_START, phase="architect"))

        # Run pre-commands before phase execution
        if not self._run_pre_commands("architect"):
            Logger.info("⚠️ Architect aborted: pre-command failed.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            self._run_post_commands("architect", success=False)
            sys.exit(1)

        # Generate file tree with customizable depth and ignore patterns
        file_tree = Shell.get_file_tree(depth=self._tree_depth, ignore=self._tree_ignore)

        prompt = TemplateManager.render(
            "architect.txt",
            user_intent=user_intent,
            file_tree=file_tree
        )

        success, _, _ = self.agent.run(prompt, "ARCHITECT")
        if not success or not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("⚠️ Architect failed.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            self._run_post_commands("architect", success=False)
            sys.exit(1)

        arch_md_path = CONF.BASE_DIR / "ARCH.md"
        if not arch_md_path.exists():
            Logger.info("⚠️ Architect failed: ARCH.md was not created.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            self._run_post_commands("architect", success=False)
            sys.exit(1)

        # Export memory to --memory-out path if specified
        if self._memory_out:
            self._export_memory(self._memory_out)

        Logger.info("✅ Memory Initialized.", "GREEN")
        self.hooks.emit(Event(EventType.ARCHITECT_SUCCESS, phase="architect"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
        self._run_post_commands("architect", success=True)

    def _validate_prd_schema(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate PRD data against a JSON schema file.

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

        # Basic JSON schema validation (supports type, required, properties)
        errors = self._validate_against_schema(data, schema, "")
        if errors:
            return False, "; ".join(errors)
        return True, ""

    def _validate_against_schema(self, data: Any, schema: Dict[str, Any], path: str) -> List[str]:
        """
        Recursively validate data against a JSON schema.

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

        # Check type
        if "type" in schema:
            expected_type = schema["type"]
            type_map = {"string": str, "number": (int, float), "integer": int,
                        "boolean": bool, "array": list, "object": dict, "null": type(None)}
            if expected_type in type_map:
                expected = type_map[expected_type]
                if not isinstance(data, expected):
                    errors.append(f"{path or 'root'}: expected {expected_type}, got {type(data).__name__}")
                    return errors  # Don't check further if type is wrong

        # Check required properties (for objects)
        if "required" in schema and isinstance(data, dict):
            for req in schema["required"]:
                if req not in data:
                    errors.append(f"{path_prefix}{req}: required property missing")

        # Check properties (for objects)
        if "properties" in schema and isinstance(data, dict):
            for prop, prop_schema in schema["properties"].items():
                if prop in data:
                    errors.extend(self._validate_against_schema(data[prop], prop_schema, f"{path_prefix}{prop}"))

        # Check items (for arrays)
        if "items" in schema and isinstance(data, list):
            for i, item in enumerate(data):
                errors.extend(self._validate_against_schema(item, schema["items"], f"{path}[{i}]"))

        # Check minItems (for arrays)
        if "minItems" in schema and isinstance(data, list):
            if len(data) < schema["minItems"]:
                errors.append(f"{path or 'root'}: array has {len(data)} items, minimum is {schema['minItems']}")

        return errors

    def _validate_min_criteria(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate that each user story has at least the minimum number of acceptance criteria.

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

    def _apply_labels(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply custom labels to the PRD data.

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
                # Labels without = are treated as tags with empty value
                labels_dict[label.strip()] = ""

        if labels_dict:
            data["labels"] = labels_dict
        return data

    def _revise_prd_impl(self, original_prd: Dict[str, Any]) -> Dict[str, Any]:
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
        Logger.info("\n🔧 Revising PRD...", "CYAN")
        self.hooks.emit(Event(EventType.PRD_REVISE_START, phase="planner"))

        # Convert PRD to JSON string for the prompt
        original_prd_json = json.dumps(original_prd, indent=2)

        prompt = TemplateManager.render(
            "revise_prd.txt",
            original_prd=original_prd_json
        )

        success, stdout, error = self.agent.run(prompt, "REVISE_PRD")

        if not success:
            error_msg = error.message if error else "Unknown error"
            Logger.warning(f"PRD revision failed: {error_msg}")
            Logger.warning("Falling back to original PRD.")
            self.hooks.emit(Event(EventType.PRD_REVISE_FAILURE, phase="planner",
                                  metadata={"reason": "agent_failure", "error": error_msg}))
            return original_prd

        # Parse the revised PRD from response
        revised_prd, revision_summary = self._parse_revised_prd(stdout, original_prd)

        if revised_prd is None:
            Logger.warning("Could not parse revised PRD from response.")
            Logger.warning("Falling back to original PRD.")
            self.hooks.emit(Event(EventType.PRD_REVISE_FAILURE, phase="planner",
                                  metadata={"reason": "parse_failure"}))
            return original_prd

        # Validate revised PRD against schema if specified
        if self._schema_path:
            schema_valid, schema_error = self._validate_prd_schema(revised_prd)
            if not schema_valid:
                Logger.warning(f"Revised PRD failed schema validation: {schema_error}")
                Logger.warning("Falling back to original PRD.")
                self.hooks.emit(Event(EventType.PRD_REVISE_FAILURE, phase="planner",
                                      metadata={"reason": "schema_validation_failed", "error": schema_error}))
                return original_prd

        # Check if no revision was needed
        if revision_summary and "no revision" in revision_summary.lower():
            Logger.info("✅ PRD already optimal, no revision needed.", "GREEN")
        else:
            Logger.debug(f"Revision summary: {revision_summary}", "CYAN")
            Logger.info("✅ PRD revised.", "GREEN")

        self.hooks.emit(Event(EventType.PRD_REVISE_SUCCESS, phase="planner",
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
        import re

        # Extract content between <REVISED_PRD> tags
        prd_pattern = r'<REVISED_PRD>\s*(.*?)\s*</REVISED_PRD>'
        prd_match = re.search(prd_pattern, response, re.DOTALL)

        # Extract revision summary
        summary_pattern = r'<REVISION_SUMMARY>\s*(.*?)\s*</REVISION_SUMMARY>'
        summary_match = re.search(summary_pattern, response, re.DOTALL)
        revision_summary = summary_match.group(1).strip() if summary_match else None

        if not prd_match:
            Logger.warning("Could not find <REVISED_PRD> tags in response.")
            return None, revision_summary

        prd_text = prd_match.group(1).strip()

        try:
            revised_prd = JsonUtils.parse(prd_text)
            # Validate basic structure
            if "userStories" not in revised_prd:
                Logger.warning("Revised PRD missing 'userStories' key.")
                return None, revision_summary
            return revised_prd, revision_summary
        except json.JSONDecodeError as e:
            Logger.warning(f"Invalid JSON in revised PRD: {e}")
            return None, revision_summary

    def run_planner(self, user_intent: str) -> None:
        """
        Run the planner phase to create a Product Requirements Document.

        Generates a PRD with user stories and acceptance criteria,
        saved to .ralph/prd.json.

        Respects the following flags:
        - --schema: Validate PRD against a JSON schema file
        - --min-criteria: Ensure each story has at least N acceptance criteria
        - --label: Add custom labels to the PRD
        - --revise-prd: Pass PRD through revision agent before saving

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="planner"))
        self.hooks.emit(Event(EventType.PLANNER_START, phase="planner"))

        # Run pre-commands before phase execution
        if not self._run_pre_commands("planner"):
            Logger.info("⚠️ Planner aborted: pre-command failed.", "RED")
            self.hooks.emit(Event(EventType.PLANNER_FAILURE, phase="planner"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
            self._run_post_commands("planner", success=False)
            sys.exit(1)

        memory_map = self.memory.get_structure(
            include=self._include_patterns,
            exclude=self._exclude_patterns,
            limit=self._context_limit
        )

        prompt = TemplateManager.render(
            "planner.txt",
            user_intent=user_intent,
            memory_map=memory_map
        )

        for attempt in range(3):
            success, raw, _ = self.agent.run(prompt, "PLANNER")
            if not success: continue

            try:
                data = JsonUtils.parse(raw)
                if "userStories" not in data: raise ValueError("Missing userStories")

                # Validate against JSON schema if --schema is specified
                schema_valid, schema_error = self._validate_prd_schema(data)
                if not schema_valid:
                    Logger.info(f"⚠️ Schema validation failed (Attempt {attempt+1}): {schema_error}", "YELLOW")
                    continue

                # Validate minimum acceptance criteria if --min-criteria is specified
                criteria_valid, criteria_error = self._validate_min_criteria(data)
                if not criteria_valid:
                    Logger.info(f"⚠️ Criteria validation failed (Attempt {attempt+1}): {criteria_error}", "YELLOW")
                    continue

                # Apply labels if --label is specified
                data = self._apply_labels(data)

                # Revise PRD if --revise-prd is specified
                if self._revise_prd:
                    data = self._revise_prd_impl(data)
                    # Re-validate against schema after revision
                    if self._schema_path:
                        schema_valid, schema_error = self._validate_prd_schema(data)
                        if not schema_valid:
                            Logger.warning(f"Revised PRD failed schema validation: {schema_error}")
                            # This shouldn't happen as _revise_prd_impl already validates,
                            # but we check again for safety
                            continue

                self._prd.save(data)
                Logger.info(f"✅ PRD Created ({len(data['userStories'])} stories).", "GREEN")
                self.hooks.emit(Event(EventType.PRD_CREATED, phase="planner", prd_path=str(CONF.PRD_FILE)))
                self.hooks.emit(Event(EventType.PLANNER_SUCCESS, phase="planner"))
                self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
                self._run_post_commands("planner", success=True)
                return
            except Exception as e:
                Logger.info(f"⚠️ JSON Error (Attempt {attempt+1}): {e}", "YELLOW")

        Logger.info("❌ Planning Failed.", "RED")
        self.hooks.emit(Event(EventType.PLANNER_FAILURE, phase="planner"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
        self._run_post_commands("planner", success=False)
        sys.exit(1)

    def execute_loop(self) -> None:
        """
        Execute all pending tasks from the PRD.

        Iterates through user stories, executing each pending task
        with verification. Continues to next task on failure instead
        of terminating. Archives the PRD upon completion.

        Respects the following flags:
        - --test-cmd: Override the test command from memory
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --only: Execute only specified task IDs
        - --except: Skip specified task IDs
        - --resume: Resume execution from a specific task ID
        - --pre: Run pre-commands before phase execution
        - --post: Run post-commands after phase completion
        """
        prd = self._prd.load()
        # Use --test-cmd override if provided, otherwise extract from memory
        test_cmd = self._test_cmd_override if self._test_cmd_override else self.memory.extract_test_command()

        Logger.info(f"\n🚀 Starting Loop. Verify Command: '{test_cmd}'", "YELLOW")
        if self._skip_verify:
            Logger.info("   ⏭️  Verification will be skipped (--skip-verify)", "YELLOW")
        self.hooks.emit(Event(EventType.PHASE_START, phase="execute"))
        self.hooks.emit(Event(EventType.EXECUTE_START, phase="execute", verification_command=test_cmd))

        # Run pre-commands before phase execution
        if not self._run_pre_commands("execute"):
            Logger.info("⚠️ Execute aborted: pre-command failed.", "RED")
            self.hooks.emit(Event(EventType.EXECUTE_END, phase="execute"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="execute"))
            self._run_post_commands("execute", success=False)
            return

        failed_tasks: List[str] = []
        resume_found = self._resume_from is None  # If no --resume, start immediately

        for task in prd.get('userStories', []):
            task_id = task['id']

            # Handle --resume: skip tasks until we find the resume target
            if not resume_found:
                if task_id == self._resume_from:
                    resume_found = True
                    Logger.info(f"   ➡️  Resuming from task {task_id}", "CYAN")
                else:
                    Logger.debug(f"   ⏭️  Skipping {task_id} (before resume point)")
                    continue

            # Handle --only: execute only specified tasks
            if self._only_tasks and task_id not in self._only_tasks:
                Logger.debug(f"   ⏭️  Skipping {task_id} (not in --only list)")
                continue

            # Handle --except: skip specified tasks
            if self._except_tasks and task_id in self._except_tasks:
                Logger.info(f"   ⏭️  Skipping {task_id} (in --except list)", "YELLOW")
                continue

            if task.get('status') == 'completed':
                continue
            # Reset failed tasks to pending so they can be retried
            if task.get('status') == 'failed':
                task['status'] = 'pending'

            Logger.info(f"\n▶️  Task {task['id']}: {task['description']}", "CYAN")
            success = self._execute_task(prd, task, test_cmd)

            # Save state after each task attempt
            self._prd.save(prd)

            if not success:
                failed_tasks.append(task['id'])

        # Check if --resume target was not found
        if not resume_found:
            Logger.warning(f"Resume task '{self._resume_from}' not found in PRD. No tasks executed.")

        # Report summary
        phase_success = len(failed_tasks) == 0
        if failed_tasks:
            Logger.info(f"\n⚠️  {len(failed_tasks)} task(s) failed: {', '.join(failed_tasks)}", "YELLOW")
            Logger.info("Run 'ralph execute' again to retry failed tasks.", "YELLOW")
        else:
            Logger.info("\n🎉 All Tasks Complete.", "GREEN")

        self._archive_prd()
        self.hooks.emit(Event(EventType.EXECUTE_END, phase="execute"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="execute"))
        self._run_post_commands("execute", success=phase_success)

    def _sanitize_id(self, text: str) -> str:
        """Sanitize an ID string to contain only alphanumeric chars and hyphens/underscores."""
        return "".join(c for c in text if c.isalnum() or c in '-_')

    def _load_user_context(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> str:
        """Load and prepare user context from prompt.md with variable substitution."""
        import re
        # Use --prompt-file override if provided, otherwise default to prompt.md
        if self._prompt_file_override:
            prompt_md_path = Path(self._prompt_file_override)
            if not prompt_md_path.exists():
                Logger.error(f"Prompt file not found: {self._prompt_file_override}")
                sys.exit(1)
        else:
            prompt_md_path = CONF.BASE_DIR / "prompt.md"
            if not prompt_md_path.exists():
                return "No specific user preferences provided."

        raw_text = prompt_md_path.read_text(encoding='utf-8')
        # Only use prompt file if it has non-empty content
        if not raw_text.strip():
            if self._prompt_file_override:
                Logger.warning(f"Prompt file is empty: {self._prompt_file_override}, using default user context.")
            else:
                Logger.warning("prompt.md exists but is empty, using default user context.")
            return "No specific user preferences provided."

        replacements = {
            "{{PRD_ID}}": self._sanitize_id(prd['id']),
            "{{PRD_DESCRIPTION}}": prd['description'],
            "{{TASK_ID}}": self._sanitize_id(task['id']),
            "{{TASK_DESCRIPTION}}": task['description'],
            "{{TEST_CMD}}": test_cmd,
        }
        pattern = re.compile('|'.join(re.escape(k) for k in replacements.keys()))
        return pattern.sub(lambda m: replacements[m.group()], raw_text)

    def _format_acceptance_criteria(self, task: Dict[str, Any]) -> str:
        """Format acceptance criteria as a bulleted list for the developer prompt."""
        criteria = task.get('acceptanceCriteria', [])
        if not criteria:
            return "(No acceptance criteria specified)"
        return "\n".join(f"- {criterion}" for criterion in criteria)

    def _verify_task(self, task: Dict[str, Any], test_cmd: str) -> Tuple[bool, Optional[AgentError]]:
        """Run verification and return (success, error_if_failed)."""
        Logger.info("   🔒 Verifying Agent's Claim...", "YELLOW")
        self.hooks.emit(Event(
            EventType.VERIFICATION_START, phase="execute",
            task_id=task['id'], verification_command=test_cmd
        ))

        stdout, stderr, code = Shell.run(test_cmd)
        verify_log = f"CMD: {test_cmd}\nEXIT CODE: {code}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        Logger.file_log(verify_log, "VERIFICATION", f"WORKER-{task['id']}")

        if code == 0:
            Logger.info("   ✅ Verified.", "GREEN")
            self.hooks.emit(Event(
                EventType.VERIFICATION_SUCCESS, phase="execute",
                task_id=task['id'], verification_command=test_cmd, verification_exit_code=code
            ))
            return True, None

        Logger.info("   🛑 Agent Hallucinated Success.", "RED")
        error = AgentError(
            exception_type="VerificationError",
            message=f"Test command '{test_cmd}' failed with exit code {code}",
            stack_trace=f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            timestamp=datetime.datetime.now().isoformat(),
            agent_name=self.agent.get_name(),
            task_id=task['id'],
        )
        self.hooks.emit(Event(
            EventType.VERIFICATION_FAILURE, phase="execute",
            task_id=task['id'], verification_command=test_cmd,
            verification_exit_code=code, error=error
        ))
        return False, error

    def _execute_task(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> bool:
        """
        Execute a single task with retries.

        Respects the following flags:
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --timeout: Override agent timeout

        Returns:
            True if task completed successfully, False if max retries exhausted.
        """
        # Use --retries override if provided, otherwise use config default
        max_retries = self._retries_override if self._retries_override is not None else CONF.MAX_RETRIES

        self.hooks.emit(Event(
            EventType.TASK_START, phase="execute",
            task_id=task['id'], task_description=task['description'], max_retries=max_retries
        ))

        for retry in range(max_retries):
            prev_errors = CONF.PROGRESS_FILE.read_text(encoding='utf-8') if CONF.PROGRESS_FILE.exists() else ""
            prompt = TemplateManager.render(
                "developer.txt",
                task_id=task['id'], task_description=task['description'],
                acceptance_criteria=self._format_acceptance_criteria(task),
                memory_tree=self.memory.get_structure(
                    include=self._include_patterns,
                    exclude=self._exclude_patterns,
                    limit=self._context_limit
                ),
                user_context=self._load_user_context(prd, task, test_cmd),
                test_cmd=test_cmd, prev_errors=prev_errors if prev_errors else "(No previous errors)"
            )

            success, output, agent_error = self.agent.run(prompt, f"WORKER-{task['id']}")

            if not success:
                self._record_failure(retry, "CLI Crash", output, agent_error=agent_error, task_id=task['id'])
                self._emit_retry_event(task, retry)
                continue

            if "STATUS: SUCCESS" in output:
                # Handle --skip-verify: skip verification step if flag is set
                if self._skip_verify:
                    Logger.info("   ⏭️  Skipping verification (--skip-verify)", "YELLOW")
                    # Run QA review if enabled (after verification/skip-verify)
                    qa_passed, _ = self._run_qa_review(task)
                    if not qa_passed:
                        self._record_failure(retry, "QA Review Failed (Critical Issues)", "", task_id=task['id'])
                        self._emit_retry_event(task, retry, max_retries)
                        continue
                    task['status'] = 'completed'
                    if CONF.PROGRESS_FILE.exists():
                        CONF.PROGRESS_FILE.unlink()
                    self.hooks.emit(Event(
                        EventType.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True

                verified, verify_error = self._verify_task(task, test_cmd)
                if verified:
                    # Run QA review if enabled (after verification success)
                    qa_passed, _ = self._run_qa_review(task)
                    if not qa_passed:
                        self._record_failure(retry, "QA Review Failed (Critical Issues)", "", task_id=task['id'])
                        self._emit_retry_event(task, retry, max_retries)
                        continue
                    task['status'] = 'completed'
                    if CONF.PROGRESS_FILE.exists():
                        CONF.PROGRESS_FILE.unlink()
                    self.hooks.emit(Event(
                        EventType.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True
                self._record_failure(retry, "Verification Failed", output[-1000:], agent_error=verify_error, task_id=task['id'])
            else:
                error = AgentError(
                    exception_type="AgentReportedFailure",
                    message="Agent did not report STATUS: SUCCESS",
                    stack_trace=f"Agent output (last 2000 chars):\n{output[-2000:]}",
                    timestamp=datetime.datetime.now().isoformat(),
                    agent_name=self.agent.get_name(),
                    task_id=task['id'],
                )
                self._record_failure(retry, "Agent Reported Failure", output[-1000:], agent_error=error, task_id=task['id'])

            self._emit_retry_event(task, retry, max_retries)

        Logger.info(f"🛑 Max retries for {task['id']}. Marking as failed and continuing.", "RED")
        task['status'] = 'failed'
        self.hooks.emit(Event(
            EventType.TASK_FAILURE, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=max_retries, max_retries=max_retries
        ))
        return False

    def _emit_retry_event(self, task: Dict[str, Any], retry: int, max_retries: Optional[int] = None) -> None:
        """Emit a task retry event."""
        if max_retries is None:
            max_retries = self._retries_override if self._retries_override is not None else CONF.MAX_RETRIES
        self.hooks.emit(Event(
            EventType.TASK_RETRY, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=retry + 1, max_retries=max_retries
        ))

    def _record_failure(self, retry: int, reason: str, detail: str, agent_error: Optional[AgentError] = None, task_id: Optional[str] = None) -> None:
        if agent_error:
            msg = (
                f"Attempt {retry+1} Failed: {reason}\n"
                f"--- Structured Error Context ---\n"
                f"{agent_error.format_log_entry()}\n"
                f"--- Agent Output (last 1000 chars) ---\n"
                f"{detail}"
            )
        else:
            msg = f"Attempt {retry+1} Failed: {reason}\n{detail}"
        CONF.PROGRESS_FILE.write_text(msg, encoding='utf-8')
        Logger.file_log(msg, "FAILURE_RECORD", f"RETRY-{retry+1}")
        Logger.info(f"   ⚠️ Retry {retry+1}/{CONF.MAX_RETRIES}: {reason}", "RED")
        self.hooks.emit(Event(
            EventType.ERROR,
            phase="execute",
            task_id=task_id or (agent_error.task_id if agent_error else None),
            error=agent_error,
            metadata={"reason": reason, "retry": retry + 1}
        ))

    def _get_code_changes(self) -> str:
        """Get recent code changes using git diff.

        Returns:
            String containing diff output, or message if no changes or git unavailable.
        """
        try:
            stdout, stderr, code = Shell.run("git diff HEAD~1 --stat", timeout=30)
            if code != 0:
                stdout, stderr, code = Shell.run("git diff --cached --stat", timeout=30)
            if code == 0 and stdout.strip():
                diff_stdout, _, diff_code = Shell.run("git diff HEAD~1", timeout=60)
                if diff_code == 0 and diff_stdout.strip():
                    if len(diff_stdout) > 50000:
                        return diff_stdout[:50000] + "\n... (truncated, diff too large)"
                    return diff_stdout
                return "(No detailed diff available)"
            return "(No code changes detected)"
        except Exception as e:
            Logger.debug(f"Failed to get code changes: {e}")
            return "(Unable to detect code changes)"

    def _run_qa_review(self, task: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Run QA review on the implemented code.

        Args:
            task: The task that was just completed

        Returns:
            Tuple of (passed: bool, findings: Optional[Dict]).
            passed is True if no critical issues found (or QA review disabled/failed).
            findings contains the parsed QA findings if available.
        """
        if not self._qa_review:
            return True, None

        Logger.info("   🔍 Running QA review...", "CYAN")
        self.hooks.emit(Event(
            EventType.QA_REVIEW_START, phase="execute",
            task_id=task['id'], task_description=task['description']
        ))

        code_changes = self._get_code_changes()
        if code_changes in ("(No code changes detected)", "(Unable to detect code changes)"):
            Logger.info("   ⏭️  Skipping QA review: no code changes detected", "YELLOW")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SKIPPED, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"reason": "no_changes"}
            ))
            return True, None

        prompt = TemplateManager.render(
            "qa_review.txt",
            task_id=task['id'],
            task_description=task['description'],
            acceptance_criteria=self._format_acceptance_criteria(task),
            code_changes=code_changes,
            memory_map=self.memory.get_structure(
                include=self._include_patterns,
                exclude=self._exclude_patterns,
                limit=self._context_limit
            )
        )

        try:
            success, output, agent_error = self.agent.run(prompt, f"QA-{task['id']}")
        except Exception as e:
            Logger.warning(f"QA review agent failed: {e}")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"reason": "agent_exception", "error": str(e)}
            ))
            return True, None

        if not success:
            Logger.warning("QA review agent failed to respond. Continuing without QA review.")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="execute",
                task_id=task['id'], task_description=task['description'],
                error=agent_error,
                metadata={"reason": "agent_failure"}
            ))
            return True, None

        findings = self._parse_qa_findings(output)
        if findings is None:
            Logger.warning("Could not parse QA findings. Continuing without QA review.")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"reason": "parse_failure"}
            ))
            return True, None

        self._report_qa_findings(task, findings)

        has_critical = len(findings.get("critical_issues", [])) > 0
        has_warnings = len(findings.get("warnings", [])) > 0
        has_any_findings = has_critical or has_warnings

        if has_critical:
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SUCCESS, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"summary": findings.get("summary", "FAIL"), "critical_count": len(findings.get("critical_issues", []))}
            ))
            if self._qa_strict:
                Logger.info("   ❌ QA review found critical issues (--qa-strict mode)", "RED")
                return False, findings
            else:
                Logger.info("   ⚠️  QA review found critical issues (non-strict mode, continuing)", "YELLOW")
        elif has_warnings:
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SUCCESS, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"summary": findings.get("summary", "WARN"), "warning_count": len(findings.get("warnings", []))}
            ))
            Logger.info("   ⚠️  QA review found warnings.", "YELLOW")
        else:
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SUCCESS, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"summary": findings.get("summary", "PASS")}
            ))
            Logger.info("   ✅ QA review passed with no issues.", "GREEN")

        # Prompt for PRD generation if findings exist and not in strict mode (which already failed)
        if has_any_findings and not self._qa_strict:
            if self._prompt_for_prd_generation(findings):
                self._generate_prd_from_qa_findings(task, findings)
            else:
                # User declined - display summary of findings and exit gracefully
                total_issues = len(findings.get("critical_issues", [])) + len(findings.get("warnings", []))
                Logger.info(f"   📋 QA review complete with {total_issues} finding(s). Continuing without PRD generation.", "CYAN")

        return True, findings

    def _run_standalone_qa_review(self) -> None:
        """Run standalone QA review workflow on the specified path.

        This method reviews existing code (not task-based changes) at the path
        specified by --qa-path. If issues are found, it optionally generates
        a PRD to address them.

        Respects the following flags:
        - --qa-path: Path to review (required for this workflow)
        - --prd-out: Where to save the generated PRD
        - --non-interactive: Skip PRD generation prompt, just display findings
        """
        review_path = Path(self._qa_path)

        Logger.info(f"🔍 Starting standalone QA review workflow...", "CYAN")
        Logger.info(f"   📂 Review path: {review_path}", "CYAN")

        self.hooks.emit(Event(
            EventType.QA_REVIEW_START, phase="standalone_qa",
            metadata={"review_path": str(review_path)}
        ))

        # Validate path exists
        if not review_path.exists():
            Logger.error(f"Review path does not exist: {review_path}")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="standalone_qa",
                metadata={"reason": "path_not_found", "path": str(review_path)}
            ))
            sys.exit(1)

        # Get codebase files from the specified path
        codebase_files = self._get_codebase_files_for_review(review_path)
        if not codebase_files:
            Logger.warning(f"No files found to review at: {review_path}")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SKIPPED, phase="standalone_qa",
                metadata={"reason": "no_files", "path": str(review_path)}
            ))
            return

        Logger.info(f"   📄 Found {len(codebase_files.splitlines())} files to review", "CYAN")

        # Render the standalone QA review template
        prompt = TemplateManager.render(
            "qa_standalone_review.txt",
            review_path=str(review_path),
            codebase_files=codebase_files,
            memory_map=self.memory.get_structure(
                include=self._include_patterns,
                exclude=self._exclude_patterns,
                limit=self._context_limit
            )
        )

        # Run the QA agent
        Logger.info("   🤖 Running QA agent...", "CYAN")
        try:
            success, output, agent_error = self.agent.run(prompt, "QA-STANDALONE")
        except Exception as e:
            Logger.error(f"QA review agent failed: {e}")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="standalone_qa",
                metadata={"reason": "agent_exception", "error": str(e)}
            ))
            sys.exit(1)

        if not success:
            Logger.error(f"QA review agent failed to respond: {agent_error}")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="standalone_qa",
                error=agent_error,
                metadata={"reason": "agent_failure"}
            ))
            sys.exit(1)

        # Parse the findings
        findings = self._parse_qa_findings(output)
        if findings is None:
            Logger.error("Could not parse QA findings from agent response.")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="standalone_qa",
                metadata={"reason": "parse_failure"}
            ))
            sys.exit(1)

        # Report findings using a synthetic task for display purposes
        synthetic_task = {
            "id": "QA-STANDALONE",
            "description": f"Standalone QA review of {review_path}"
        }
        self._report_qa_findings(synthetic_task, findings)

        # Count findings
        critical_count = len(findings.get("critical_issues", []))
        warning_count = len(findings.get("warnings", []))
        total_issues = critical_count + warning_count

        # Emit success event
        self.hooks.emit(Event(
            EventType.QA_REVIEW_SUCCESS, phase="standalone_qa",
            metadata={
                "summary": findings.get("summary", "UNKNOWN"),
                "critical_count": critical_count,
                "warning_count": warning_count,
                "path": str(review_path)
            }
        ))

        if total_issues == 0:
            Logger.info("✅ QA review passed with no issues.", "GREEN")
            return

        # Display summary
        if critical_count > 0:
            Logger.info(f"❌ QA review found {critical_count} critical issue(s) and {warning_count} warning(s).", "RED")
        else:
            Logger.info(f"⚠️  QA review found {warning_count} warning(s).", "YELLOW")

        # Handle PRD generation
        if self._non_interactive:
            Logger.info("ℹ️  Skipping PRD generation (non-interactive mode).", "CYAN")
            return

        # Prompt for PRD generation
        if self._prompt_for_prd_generation(findings):
            self._generate_prd_from_standalone_qa(review_path, findings)
        else:
            Logger.info(f"📋 QA review complete with {total_issues} finding(s). No PRD generated.", "CYAN")

    def _get_codebase_files_for_review(self, review_path: Path) -> str:
        """Get codebase files from the specified path for QA review.

        Args:
            review_path: Path to scan for files

        Returns:
            String containing file contents or file listing for review
        """
        # Common patterns to exclude
        exclude_patterns = [
            "__pycache__", ".git", ".ralph", "node_modules", "venv",
            ".env", "*.pyc", "*.pyo", "*.egg-info", ".tox", ".pytest_cache"
        ]

        if review_path.is_file():
            # Single file review
            try:
                content = review_path.read_text(encoding='utf-8')
                return f"# {review_path.name}\n```\n{content}\n```"
            except (OSError, UnicodeDecodeError) as e:
                Logger.warning(f"Could not read file {review_path}: {e}")
                return ""

        # Directory review - collect relevant files
        files_content = []
        file_count = 0
        max_files = 50  # Limit to avoid overwhelming the agent
        max_content_size = 100000  # 100KB limit

        total_size = 0

        for file_path in review_path.rglob("*"):
            if file_count >= max_files or total_size >= max_content_size:
                files_content.append(f"\n... (truncated, {file_count} files shown)")
                break

            # Skip excluded patterns
            skip = False
            for pattern in exclude_patterns:
                if pattern.startswith("*"):
                    if file_path.suffix == pattern[1:]:
                        skip = True
                        break
                elif pattern in str(file_path):
                    skip = True
                    break
            if skip:
                continue

            if not file_path.is_file():
                continue

            # Only include source code files
            if file_path.suffix not in ['.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs', '.rb', '.php', '.cs', '.cpp', '.c', '.h', '.hpp', '.sh', '.yaml', '.yml', '.json', '.toml', '.md', '.txt']:
                continue

            try:
                content = file_path.read_text(encoding='utf-8')
                relative_path = file_path.relative_to(review_path)
                file_entry = f"\n# {relative_path}\n```\n{content}\n```"

                if total_size + len(file_entry) > max_content_size:
                    files_content.append(f"\n... (truncated due to size limit)")
                    break

                files_content.append(file_entry)
                total_size += len(file_entry)
                file_count += 1
            except (OSError, UnicodeDecodeError):
                continue

        return "\n".join(files_content)

    def _generate_prd_from_standalone_qa(self, review_path: Path, findings: Dict[str, Any]) -> None:
        """Generate a PRD from standalone QA review findings.

        Args:
            review_path: Path that was reviewed
            findings: The QA findings dictionary
        """
        Logger.info("\n🔧 Generating PRD from QA findings...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="prd_from_qa"))

        # Create a synthetic task for PRD generation
        synthetic_task = {
            "id": "QA-STANDALONE",
            "description": f"Address QA findings from review of {review_path}"
        }

        try:
            prd_data = self._create_prd_from_findings(synthetic_task, findings)
            # Update PRD ID to reflect standalone nature
            prd_data["id"] = f"PRD-QA-{review_path.name}"
            prd_data["description"] = f"Address QA findings from standalone review of {review_path}"
        except (KeyError, TypeError, ValueError) as e:
            error_msg = f"Invalid finding data: {type(e).__name__}: {e}"
            Logger.error(f"PRD generation failed: {error_msg}")
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa",
                                  metadata={"success": False, "error": error_msg}))
            return

        # Validate against JSON schema if --schema is specified
        if self._schema_path:
            schema_valid, schema_error = self._validate_prd_schema(prd_data)
            if not schema_valid:
                Logger.error(f"Generated PRD failed schema validation: {schema_error}")
                self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa",
                                      metadata={"success": False, "error": f"schema_validation: {schema_error}"}))
                return

        # Validate minimum acceptance criteria if --min-criteria is specified
        criteria_valid, criteria_error = self._validate_min_criteria(prd_data)
        if not criteria_valid:
            Logger.warning(f"Generated PRD criteria validation warning: {criteria_error}")

        # Apply labels if --label is specified
        prd_data = self._apply_labels(prd_data)

        # Archive existing PRD if present
        self._archive_prd()

        # Save the generated PRD to file
        story_count = len(prd_data.get('userStories', []))
        output_path = self._save_generated_prd(prd_data)
        if output_path:
            Logger.info(f"✅ PRD Generated from QA findings ({story_count} stories).", "GREEN")
            self.hooks.emit(Event(EventType.PRD_CREATED, phase="prd_from_qa", prd_path=str(output_path)))
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa", metadata={"success": True}))
        else:
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa", metadata={"success": False, "reason": "save_aborted"}))

    def _parse_qa_findings(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse QA findings from agent response.

        Args:
            response: Raw response from the QA agent

        Returns:
            Parsed findings dictionary or None if parsing fails
        """
        import re
        pattern = r'<QA_FINDINGS>\s*(.*?)\s*</QA_FINDINGS>'
        match = re.search(pattern, response, re.DOTALL)

        if not match:
            Logger.debug("Could not find <QA_FINDINGS> tags in response.")
            return None

        findings_text = match.group(1).strip()

        try:
            findings = JsonUtils.parse(findings_text)
            if not isinstance(findings, dict):
                return None
            return findings
        except json.JSONDecodeError as e:
            Logger.debug(f"Failed to parse QA findings JSON: {e}")
            return None

    def _report_qa_findings(self, task: Dict[str, Any], findings: Dict[str, Any]) -> None:
        """Report QA findings in the appropriate format.

        Uses QAFindingsAnalyzer to categorize, group, and format findings.
        Findings are grouped by file and sorted by severity (errors first).

        Args:
            task: The task being reviewed
            findings: Parsed QA findings dictionary
        """
        # Use the analyzer to process and categorize findings
        analyzer = QAFindingsAnalyzer(findings)
        counts = analyzer.get_counts()
        grouped = analyzer.get_findings_grouped_by_file()

        if Logger.json_output or Logger.ndjson_output:
            # JSON output includes full structured analysis
            qa_report = {
                "timestamp": datetime.datetime.now().isoformat(),
                "level": "info",
                "message": "QA Review Complete",
                "task_id": task['id'],
                "qa_summary": analyzer.summary,
                "counts": counts,
                "findings_by_file": {
                    file_path: [
                        {
                            "type": f.finding_type.name.lower(),
                            "category": f.category,
                            "description": f.description,
                            "line_number": f.line_number,
                            "recommendation": f.recommendation,
                            "has_missing_data": f.has_missing_data,
                            "missing_data_note": f.missing_data_note
                        }
                        for f in file_findings
                    ]
                    for file_path, file_findings in grouped.items()
                },
                "passed_checks": analyzer.passed_checks,
                "is_paginated": analyzer.is_large_output
            }
            print(json.dumps(qa_report))
        else:
            # Text output with severity-based formatting
            Logger.info(f"   📋 QA Summary: {analyzer.summary}", "CYAN")
            Logger.info(f"   📊 Files: {counts['files']} | Errors: {counts['errors']} | "
                       f"Warnings: {counts['warnings']} | Suggestions: {counts['suggestions']}", "CYAN")

            if not analyzer.all_findings:
                Logger.info("   ✅ No issues found.", "GREEN")
            else:
                # Display findings grouped by file, sorted by severity
                errors = analyzer.get_findings_by_type(QAFindingType.ERROR)
                warnings_list = analyzer.get_findings_by_type(QAFindingType.WARNING)
                suggestions = analyzer.get_findings_by_type(QAFindingType.SUGGESTION)

                # Show errors first (always displayed)
                if errors:
                    Logger.info(f"   ❌ Critical Issues ({len(errors)}):", "RED")
                    self._display_findings_by_file(errors, "RED", max_per_file=5, max_files=10)

                # Show warnings
                if warnings_list:
                    Logger.info(f"   ⚠️  Warnings ({len(warnings_list)}):", "YELLOW")
                    self._display_findings_by_file(warnings_list, "YELLOW", max_per_file=3, max_files=5)

                # Show suggestions only at verbose level
                if suggestions and Logger.verbosity >= 1:
                    Logger.debug(f"   💡 Suggestions ({len(suggestions)}):")
                    self._display_findings_by_file(suggestions, None, max_per_file=3, max_files=3, use_debug=True)

                # Large output summary
                if analyzer.is_large_output:
                    Logger.info(f"   📄 Output truncated ({counts['files']} files total). "
                               "Use --json for full details.", "CYAN")

        Logger.file_log(json.dumps(findings, indent=2), "QA_REVIEW", f"QA-{task['id']}")

    def _prompt_for_prd_generation(self, findings: Dict[str, Any]) -> bool:
        """Prompt user to generate a PRD to address QA findings.

        Args:
            findings: The QA findings dictionary

        Returns:
            True if user wants to generate PRD, False otherwise.
            In non-interactive mode, returns False.
        """
        if self._non_interactive:
            Logger.info("   ℹ️  Skipping PRD generation prompt (non-interactive mode)", "CYAN")
            return False

        total_issues = (
            len(findings.get("critical_issues", [])) +
            len(findings.get("warnings", []))
        )

        while True:
            response = input(
                f"{Logger.COLORS['YELLOW']}Generate a PRD to address {total_issues} QA finding(s)? (y/n): {Logger.COLORS['RESET']}"
            ).strip().lower()

            if response in ('y', 'yes'):
                return True
            elif response in ('n', 'no'):
                return False
            else:
                Logger.info("   ⚠️  Invalid input. Please enter 'y' or 'n'.", "YELLOW")

    def _generate_prd_from_qa_findings(self, task: Dict[str, Any], findings: Dict[str, Any]) -> None:
        """Generate a PRD to address QA findings.

        Creates a structured PRD document from QA findings. Each finding or group
        of related findings becomes a user story with acceptance criteria.

        Handles edge cases:
        - Single finding: Creates a single-story PRD
        - 50+ findings: Groups findings by category into logical stories

        Args:
            task: The task that was reviewed
            findings: The QA findings dictionary

        Raises:
            Logs error and notifies user if PRD generation fails
        """
        Logger.info("\n🔧 Generating PRD from QA findings...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="prd_from_qa"))

        try:
            prd_data = self._create_prd_from_findings(task, findings)
        except (KeyError, TypeError, ValueError) as e:
            error_msg = f"Invalid finding data: {type(e).__name__}: {e}"
            Logger.error(f"PRD generation failed: {error_msg}")
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa",
                                  metadata={"success": False, "error": error_msg}))
            return

        # Validate against JSON schema if --schema is specified
        if self._schema_path:
            schema_valid, schema_error = self._validate_prd_schema(prd_data)
            if not schema_valid:
                Logger.error(f"Generated PRD failed schema validation: {schema_error}")
                self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa",
                                      metadata={"success": False, "error": f"schema_validation: {schema_error}"}))
                return

        # Validate minimum acceptance criteria if --min-criteria is specified
        criteria_valid, criteria_error = self._validate_min_criteria(prd_data)
        if not criteria_valid:
            Logger.warning(f"Generated PRD criteria validation warning: {criteria_error}")

        # Apply labels if --label is specified
        prd_data = self._apply_labels(prd_data)

        # Archive existing PRD if present
        self._archive_prd()

        # Save the generated PRD to file
        story_count = len(prd_data.get('userStories', []))
        output_path = self._save_generated_prd(prd_data)
        if output_path:
            Logger.info(f"✅ PRD Generated from QA findings ({story_count} stories).", "GREEN")
            self.hooks.emit(Event(EventType.PRD_CREATED, phase="prd_from_qa", prd_path=str(output_path)))
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa", metadata={"success": True}))
        else:
            # Save was aborted (user declined overwrite), but PRD was printed to stdout
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa", metadata={"success": False, "reason": "save_aborted"}))

    def _create_prd_from_findings(self, task: Dict[str, Any], findings: Dict[str, Any]) -> Dict[str, Any]:
        """Create a structured PRD from QA findings.

        Args:
            task: The task that was reviewed
            findings: The QA findings dictionary with critical_issues, warnings, suggestions

        Returns:
            A PRD dictionary following the standard schema

        Raises:
            ValueError: If findings data is invalid
        """
        # Threshold for grouping findings by category instead of individual stories
        GROUPING_THRESHOLD = 50

        # Collect all findings with their types
        all_findings: List[Dict[str, Any]] = []

        for issue in findings.get("critical_issues", []):
            if not isinstance(issue, dict):
                continue
            all_findings.append({
                "severity": "critical",
                "category": issue.get("category", "unknown"),
                "description": issue.get("description", "No description"),
                "location": issue.get("location", ""),
                "recommendation": issue.get("recommendation", "")
            })

        for warning in findings.get("warnings", []):
            if not isinstance(warning, dict):
                continue
            all_findings.append({
                "severity": "warning",
                "category": warning.get("category", "unknown"),
                "description": warning.get("description", "No description"),
                "location": warning.get("location", ""),
                "recommendation": warning.get("recommendation", "")
            })

        for suggestion in findings.get("suggestions", []):
            if not isinstance(suggestion, dict):
                continue
            all_findings.append({
                "severity": "suggestion",
                "category": suggestion.get("category", "unknown"),
                "description": suggestion.get("description", "No description"),
                "location": suggestion.get("location", ""),
                "recommendation": suggestion.get("recommendation", "")
            })

        if not all_findings:
            raise ValueError("No valid findings to generate PRD from")

        # Generate PRD ID based on original task
        original_task_id = task.get('id', 'UNKNOWN')
        prd_id = f"PRD-QA-{original_task_id}"

        # Determine if we need to group findings (50+ threshold)
        if len(all_findings) >= GROUPING_THRESHOLD:
            user_stories = self._create_grouped_stories(all_findings, original_task_id)
        else:
            user_stories = self._create_individual_stories(all_findings, original_task_id)

        return {
            "id": prd_id,
            "description": f"Address QA findings from review of {original_task_id}: {task.get('description', 'N/A')}",
            "userStories": user_stories
        }

    def _create_individual_stories(self, findings: List[Dict[str, Any]], original_task_id: str) -> List[Dict[str, Any]]:
        """Create individual user stories for each finding or small groups.

        For fewer than 50 findings, each distinct finding becomes its own story.

        Args:
            findings: List of finding dictionaries
            original_task_id: The original task ID for context

        Returns:
            List of user story dictionaries
        """
        user_stories = []

        for i, finding in enumerate(findings, 1):
            task_id = f"TASK-{i:03d}"
            severity = finding["severity"]
            category = finding["category"]
            description = finding["description"]
            location = finding.get("location", "")
            recommendation = finding.get("recommendation", "")

            # Create user story description in proper format
            story_desc = (
                f"As a developer, I want to fix the {severity} {category} issue "
                f"so that the codebase meets quality standards."
            )

            # Build acceptance criteria
            acceptance_criteria = [
                f"Given the {category} issue '{description}', when the fix is applied, then the issue is resolved",
            ]

            if location:
                acceptance_criteria.append(
                    f"Given the issue location at {location}, when reviewing the fix, then the specific location is addressed"
                )

            if recommendation:
                acceptance_criteria.append(
                    f"Given the recommendation '{recommendation}', when implementing, then the suggested approach is followed"
                )

            acceptance_criteria.append(
                f"Given the fix is complete, when running QA review, then no {severity} issues of this type are reported"
            )

            user_stories.append({
                "id": task_id,
                "description": story_desc,
                "priority": "Must Have" if severity == "critical" else ("Should Have" if severity == "warning" else "Could Have"),
                "acceptanceCriteria": acceptance_criteria,
                "definitionOfDone": [
                    "Code reviewed and approved",
                    "Fix verified through testing",
                    "No regressions introduced"
                ],
                "risks": [
                    {
                        "type": "technical",
                        "description": f"Fix may have unintended side effects",
                        "mitigation": "Thorough testing and code review"
                    }
                ],
                "dependencies": [f"Original task: {original_task_id}"],
                "status": "pending"
            })

        return user_stories

    def _create_grouped_stories(self, findings: List[Dict[str, Any]], original_task_id: str) -> List[Dict[str, Any]]:
        """Create grouped user stories when findings exceed 50.

        Groups findings by category to create manageable stories.

        Args:
            findings: List of finding dictionaries
            original_task_id: The original task ID for context

        Returns:
            List of user story dictionaries
        """
        # Group findings by category
        by_category: Dict[str, List[Dict[str, Any]]] = {}
        for finding in findings:
            category = finding.get("category", "other")
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(finding)

        user_stories = []
        task_num = 1

        # Sort categories by severity priority (critical findings first)
        def category_priority(cat: str) -> int:
            cat_findings = by_category[cat]
            has_critical = any(f["severity"] == "critical" for f in cat_findings)
            has_warning = any(f["severity"] == "warning" for f in cat_findings)
            if has_critical:
                return 0
            if has_warning:
                return 1
            return 2

        sorted_categories = sorted(by_category.keys(), key=category_priority)

        for category in sorted_categories:
            cat_findings = by_category[category]
            task_id = f"TASK-{task_num:03d}"
            task_num += 1

            # Determine overall severity for this category
            has_critical = any(f["severity"] == "critical" for f in cat_findings)
            has_warning = any(f["severity"] == "warning" for f in cat_findings)

            severity_text = "critical" if has_critical else ("warning" if has_warning else "suggestion")
            priority = "Must Have" if has_critical else ("Should Have" if has_warning else "Could Have")

            # Create story description
            story_desc = (
                f"As a developer, I want to address all {category} issues ({len(cat_findings)} findings) "
                f"so that the codebase quality in this area is improved."
            )

            # Build acceptance criteria from findings
            acceptance_criteria = [
                f"Given {len(cat_findings)} {category} issues identified, when fixes are applied, then all issues are resolved"
            ]

            # Add specific criteria for top findings (up to 5)
            for j, finding in enumerate(cat_findings[:5], 1):
                desc = finding["description"]
                if len(desc) > 100:
                    desc = desc[:97] + "..."
                acceptance_criteria.append(
                    f"Given issue {j}: '{desc}', when fixed, then the specific problem is resolved"
                )

            if len(cat_findings) > 5:
                acceptance_criteria.append(
                    f"Given {len(cat_findings) - 5} additional {category} issues, when fixed, then all remaining issues are resolved"
                )

            acceptance_criteria.append(
                f"Given all {category} fixes are complete, when running QA review, then no {severity_text} {category} issues are reported"
            )

            user_stories.append({
                "id": task_id,
                "description": story_desc,
                "priority": priority,
                "acceptanceCriteria": acceptance_criteria,
                "definitionOfDone": [
                    "Code reviewed and approved",
                    "All fixes verified through testing",
                    f"No {category} issues remain",
                    "No regressions introduced"
                ],
                "risks": [
                    {
                        "type": "scope",
                        "description": f"Large number of {category} issues ({len(cat_findings)}) may require significant effort",
                        "mitigation": "Prioritize critical issues first, batch related fixes"
                    }
                ],
                "dependencies": [f"Original task: {original_task_id}"],
                "status": "pending"
            })

        return user_stories

    def _display_findings_by_file(self, findings: List['QAFinding'], color: Optional[str],
                                   max_per_file: int = 5, max_files: int = 10,
                                   use_debug: bool = False) -> None:
        """Display findings grouped by file.

        Args:
            findings: List of QAFinding objects to display
            color: Color to use for output (None for default)
            max_per_file: Maximum findings to show per file before truncating
            max_files: Maximum files to show before summarizing
            use_debug: If True, use Logger.debug instead of Logger.info
        """
        # Group findings by file
        by_file: Dict[str, List[QAFinding]] = {}
        for f in findings:
            key = f.file_path or "(no file)"
            if key not in by_file:
                by_file[key] = []
            by_file[key].append(f)

        # Sort files: files with more findings first
        sorted_files = sorted(by_file.keys(), key=lambda k: -len(by_file[k]))

        log_fn = Logger.debug if use_debug else Logger.info
        files_shown = 0

        for file_path in sorted_files:
            if files_shown >= max_files:
                remaining_files = len(sorted_files) - files_shown
                remaining_findings = sum(len(by_file[f]) for f in sorted_files[files_shown:])
                log_fn(f"      ... and {remaining_findings} more finding(s) in {remaining_files} file(s)", color)
                break

            file_findings = by_file[file_path]
            log_fn(f"      {file_path}:", color)

            for i, finding in enumerate(file_findings):
                if i >= max_per_file:
                    remaining = len(file_findings) - i
                    log_fn(f"        ... and {remaining} more in this file", color)
                    break

                line_info = f":{finding.line_number}" if finding.line_number else ""
                log_fn(f"        - [{finding.category}]{line_info} {finding.description}", color)

                if finding.has_missing_data and finding.missing_data_note and Logger.verbosity >= 2:
                    log_fn(f"          (Note: {finding.missing_data_note})", color)

            files_shown += 1

    def _archive_prd(self) -> None:
        if not self._prd.exists():
            return
        # Respect --no-archive flag (archive is default behavior)
        if not self._archive:
            Logger.debug("Skipping PRD archival (--no-archive)")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = CONF.ARCHIVE_DIR / f"prd_{ts}.json"
        shutil.move(str(CONF.PRD_FILE), str(dest))
        self._prd.invalidate_cache()
        Logger.info(f"📦 PRD Archived to {dest}", "MAGENTA")
        self.hooks.emit(Event(EventType.PRD_ARCHIVED, prd_path=str(dest)))

    def _export_memory(self, output_path: str) -> None:
        """
        Export memory contents to a file.

        Concatenates all memory files into a single output file for external use.

        Args:
            output_path: Path to write the exported memory content
        """
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        content_parts = []
        for path in sorted(CONF.MEMORY_DIR.rglob('*')):
            if path.is_file() and not path.name.startswith('.'):
                try:
                    rel_path = path.relative_to(CONF.MEMORY_DIR)
                    file_content = path.read_text(encoding='utf-8')
                    content_parts.append(f"# {rel_path}\n\n{file_content}")
                except Exception as e:
                    Logger.warning(f"Could not read memory file {path}: {e}")

        if content_parts:
            out_path.write_text("\n\n---\n\n".join(content_parts), encoding='utf-8')
            Logger.info(f"📋 Memory exported to {out_path}", "MAGENTA")
        else:
            Logger.warning("No memory files to export.")

    def _print_prd(self) -> None:
        """
        Print the PRD contents to stdout.

        Outputs the PRD as formatted JSON for inspection without execution.
        """
        if not self._prd.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        if self._json_output or self._ndjson_output:
            # For JSON/NDJSON mode, output as-is (already JSON)
            print(self._prd.read_raw())
        else:
            # Pretty print with indentation
            prd_data = self._prd.load()
            print(json.dumps(prd_data, indent=2))

    def _export_prd(self, output_path: str) -> None:
        """
        Export the PRD to a specified file.

        Args:
            output_path: Path to write the PRD content
        """
        if not self._prd.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self._prd.read_raw(), encoding='utf-8')
        Logger.info(f"📋 PRD exported to {out_path}", "MAGENTA")

    def _save_generated_prd(self, prd_data: Dict[str, Any]) -> Optional[Path]:
        """
        Save generated PRD to a file with overwrite protection.

        Saves the PRD to the location specified by --prd-out flag, or to the default
        .ralph/prd.json location. Handles:
        - Overwrite confirmation in interactive mode
        - Error in non-interactive mode when file exists
        - Auto-creation of output directory
        - Fallback to stdout on write errors

        Args:
            prd_data: The PRD dictionary to save

        Returns:
            Path to the saved file if successful, None if save was aborted
        """
        # Determine output path: use --prd-out if specified, otherwise default
        if self._prd_out:
            out_path = Path(self._prd_out)
        else:
            out_path = CONF.PRD_FILE

        prd_json = json.dumps(prd_data, indent=2)

        # Check if file exists and handle overwrite
        if out_path.exists():
            if self._non_interactive:
                Logger.error(f"Output file already exists: {out_path}")
                Logger.error("Use a different path or remove the existing file.")
                Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
                print(prd_json)
                return None
            else:
                # Interactive mode: prompt for overwrite confirmation
                if not self._prompt_overwrite_confirmation(out_path):
                    Logger.info("PRD save cancelled. PRD content printed to stdout:", "YELLOW")
                    print(prd_json)
                    return None

        # Ensure output directory exists
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            Logger.error(f"Failed to create output directory {out_path.parent}: {e}")
            Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
            print(prd_json)
            return None

        # Write the file with error handling
        try:
            out_path.write_text(prd_json, encoding='utf-8')
            Logger.info(f"📋 PRD saved to {out_path}", "MAGENTA")
            # Update PRD manager cache if saved to default location
            if out_path == CONF.PRD_FILE:
                self._prd.invalidate_cache()
            return out_path
        except PermissionError as e:
            Logger.error(f"Permission denied writing to {out_path}: {e}")
            Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
            print(prd_json)
            return None
        except OSError as e:
            Logger.error(f"Failed to write PRD to {out_path}: {e}")
            Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
            print(prd_json)
            return None

    def _prompt_overwrite_confirmation(self, file_path: Path) -> bool:
        """
        Prompt user to confirm overwriting an existing file.

        Args:
            file_path: Path to the file that would be overwritten

        Returns:
            True if user confirms overwrite, False otherwise
        """
        while True:
            response = input(
                f"{Logger.COLORS['YELLOW']}File {file_path} already exists. Overwrite? (y/n): {Logger.COLORS['RESET']}"
            ).strip().lower()

            if response in ('y', 'yes'):
                return True
            elif response in ('n', 'no'):
                return False
            else:
                Logger.info("   ⚠️  Invalid input. Please enter 'y' or 'n'.", "YELLOW")

    def _check_prd_status(self) -> int:
        """
        Check PRD status and return appropriate exit code.

        Returns:
            0 if all tasks completed, 1 if tasks pending/failed, 2 if no PRD exists.
        """
        if not self._prd.exists():
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("No PRD file found", "error", status="no_prd", exit_code=2))
            else:
                Logger.error("No PRD file found. Run planner first.")
            return 2

        prd = self._prd.load()
        tasks = prd.get('userStories', [])

        if not tasks:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("PRD has no tasks", "warn", status="empty", exit_code=1))
            else:
                Logger.warning("PRD has no tasks.")
            return 1

        completed = sum(1 for t in tasks if t.get('status') == 'completed')
        failed = sum(1 for t in tasks if t.get('status') == 'failed')
        pending = sum(1 for t in tasks if t.get('status') in ('pending', None))
        total = len(tasks)

        status_data = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
        }

        if completed == total:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("All tasks completed", "info", status="success", exit_code=0, **status_data))
            else:
                Logger.info(f"✅ All {total} task(s) completed.", "GREEN")
            return 0
        else:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("Tasks incomplete", "warn", status="incomplete", exit_code=1, **status_data))
            else:
                Logger.warning(f"Tasks incomplete: {completed}/{total} completed, {failed} failed, {pending} pending.")
            return 1

    def _run_qa_status(self) -> int:
        """
        Display QA checklist status and return appropriate exit code.

        Shows all requirements with their current status (pending/passed/failed),
        linked tasks, and a summary line with fulfillment percentage.

        Respects the following flags:
        - --json: Output in machine-parseable JSON format
        - --verbose: Include lastChecked timestamps and additional details

        Returns:
            0 if all requirements passed
            1 if some requirements are pending or failed
            2 if no checklist exists and user declined generation
        """
        checklist_path = self.qa_checklist_path
        checklist_manager = QAChecklistManager(checklist_path, self._prd)

        # Edge case: checklist doesn't exist
        if not checklist_manager.exists():
            # Check if PRD exists to offer generation
            if self._prd.exists():
                if Logger.json_output or Logger.ndjson_output:
                    print(Logger._format_json_message(
                        "No QA checklist found. Use 'ralph qa-status' with PRD to generate.",
                        "info",
                        status="no_checklist",
                        can_generate=True,
                        prd_exists=True,
                        exit_code=2
                    ))
                else:
                    Logger.info("No QA checklist found.", "YELLOW")
                    Logger.info(f"A PRD exists at: {self._prd._path}")
                    if not self._non_interactive:
                        response = input(f"{Logger.COLORS['YELLOW']}Generate checklist from PRD? (y/n): {Logger.COLORS['RESET']}").strip().lower()
                        if response == 'y':
                            try:
                                checklist_manager.load(auto_create=True)
                                Logger.info(f"Generated QA checklist at: {checklist_path}", "GREEN")
                                # Continue to display the newly generated checklist
                            except QAChecklistError as e:
                                Logger.error(f"Failed to generate checklist: {e}")
                                return 2
                        else:
                            Logger.info("Checklist generation skipped.")
                            return 2
                    else:
                        Logger.info("Run in interactive mode to generate checklist from PRD.")
                        return 2
            else:
                if Logger.json_output or Logger.ndjson_output:
                    print(Logger._format_json_message(
                        "No QA checklist found and no PRD available to generate one.",
                        "error",
                        status="no_checklist",
                        can_generate=False,
                        prd_exists=False,
                        exit_code=2
                    ))
                else:
                    Logger.error("No QA checklist found.")
                    Logger.error("No PRD available to generate checklist. Run planner first.")
                return 2

        # Load the checklist
        try:
            checklist_manager.load(auto_create=False)
        except (QAChecklistError, QAChecklistCorruptedError) as e:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(
                    f"Failed to load QA checklist: {e}",
                    "error",
                    status="load_error",
                    exit_code=1
                ))
            else:
                Logger.error(f"Failed to load QA checklist: {e}")
            return 1

        requirements = checklist_manager.get_all_requirements()

        if not requirements:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(
                    "QA checklist is empty",
                    "warn",
                    status="empty",
                    total=0,
                    passed=0,
                    failed=0,
                    pending=0,
                    percentage=0,
                    exit_code=1
                ))
            else:
                Logger.warning("QA checklist is empty.")
            return 1

        # Calculate statistics
        total = len(requirements)
        passed = sum(1 for r in requirements if r.status == "passed")
        failed = sum(1 for r in requirements if r.status == "failed")
        pending = sum(1 for r in requirements if r.status == "pending")
        percentage = round((passed / total) * 100) if total > 0 else 0

        # Prepare output data
        if Logger.json_output or Logger.ndjson_output:
            # JSON output mode
            requirements_data = []
            for req in requirements:
                req_data = {
                    "id": req.id,
                    "description": req.description,
                    "status": req.status,
                    "linkedTasks": req.linkedTasks
                }
                if Logger.verbosity > 0:
                    req_data["lastChecked"] = req.lastChecked
                requirements_data.append(req_data)

            output_data = {
                "requirements": requirements_data,
                "summary": {
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "pending": pending,
                    "percentage": percentage
                },
                "status": "complete" if passed == total else "incomplete",
                "exit_code": 0 if passed == total else 1
            }
            print(json.dumps(output_data, indent=2))
        else:
            # Human-readable output
            Logger.info("QA Checklist Status", "CYAN")
            Logger.info("=" * 60)

            # Status symbols and colors
            status_display = {
                "passed": ("✅", "GREEN"),
                "failed": ("❌", "RED"),
                "pending": ("⏳", "YELLOW")
            }

            for req in requirements:
                symbol, color = status_display.get(req.status, ("?", "WHITE"))
                status_line = f"{symbol} [{req.status.upper():7}] {req.id}: {req.description}"
                Logger.info(status_line, color)

                # Show linked tasks if any
                if req.linkedTasks:
                    tasks_str = ", ".join(req.linkedTasks)
                    Logger.info(f"   └─ Tasks: {tasks_str}")

                # Show lastChecked in verbose mode
                if Logger.verbosity > 0 and req.lastChecked:
                    Logger.info(f"   └─ Last checked: {req.lastChecked}")

            Logger.info("=" * 60)
            Logger.info(f"Summary: {passed} of {total} requirements fulfilled ({percentage}%)",
                       "GREEN" if passed == total else "YELLOW")

            if failed > 0:
                Logger.info(f"  Failed: {failed}", "RED")
            if pending > 0:
                Logger.info(f"  Pending: {pending}", "YELLOW")

        return 0 if passed == total else 1

    def _validate_memory_on_startup(self) -> None:
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            return
        result = self.memory.validate_memory()
        if result['total'] == 0:
            return
        for key, label in [('corrupted', 'corrupted'), ('empty', 'empty')]:
            if result[key]:
                Logger.info(f"⚠️ Memory: {len(result[key])} {label} file(s): {', '.join(result[key])}", "YELLOW")
        if result['valid']:
            Logger.debug(f"✅ Memory OK ({result['total']} files)", "GREEN")

    def _prompt_user_for_phase(self, phase_name: str) -> bool:
        """Prompt user to run a phase, or fail in non-interactive mode."""
        if self._non_interactive:
            Logger.error(f"Cannot prompt for {phase_name} phase in non-interactive mode. Use --accept-all (-y) to auto-accept.")
            sys.exit(1)
        return input(f"{Logger.COLORS['YELLOW']}Run {phase_name} phase? (y/n): {Logger.COLORS['RESET']}").strip().lower() == 'y'

    def _get_intent(self, user_intent=None):
        """Get intent from flags or interactive prompt."""
        if user_intent:
            return user_intent
        # Check --intent flag
        if self._intent:
            return self._intent
        # Check --intent-file flag
        if self._intent_file:
            intent_path = Path(self._intent_file)
            if not intent_path.exists():
                Logger.error(f"Intent file not found: {self._intent_file}")
                sys.exit(1)
            content = intent_path.read_text(encoding='utf-8').strip()
            if not content:
                Logger.error(f"Intent file is empty: {self._intent_file}")
                sys.exit(1)
            return content
        # Non-interactive mode requires --intent or --intent-file
        if self._non_interactive:
            Logger.error("Intent required in non-interactive mode. Use --intent or --intent-file.")
            sys.exit(1)
        # Interactive prompt
        intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
        if not intent:
            sys.exit(0)
        return intent

    def _enhance_intent_impl(self, original_intent: str) -> str:
        """Enhance user intent through the enhancement agent.

        Args:
            original_intent: The original user intent to enhance

        Returns:
            Enhanced intent string, or original intent on failure (unless strict mode)

        Raises:
            SystemExit: If strict mode is enabled and enhancement fails
        """
        # Validate input - empty or whitespace-only intent
        if not original_intent or not original_intent.strip():
            Logger.error("Cannot enhance empty or whitespace-only intent.")
            sys.exit(1)

        Logger.info("\n🔧 Enhancing intent...", "CYAN")
        self.hooks.emit(Event(EventType.INTENT_ENHANCE_START, phase="enhance_intent"))

        prompt = TemplateManager.render(
            "enhance_intent.txt",
            original_intent=original_intent
        )

        success, stdout, error = self.agent.run(prompt, "ENHANCE_INTENT")

        if not success:
            error_msg = error.message if error else "Unknown error"
            Logger.warning(f"Intent enhancement failed: {error_msg}")
            self.hooks.emit(Event(EventType.INTENT_ENHANCE_FAILURE, phase="enhance_intent"))

            if self._enhance_intent_strict:
                Logger.error("Intent enhancement failed in strict mode. Exiting.")
                sys.exit(1)

            Logger.warning("Falling back to original intent.")
            return original_intent

        # Extract enhanced intent from response
        enhanced_intent = self._parse_enhanced_intent(stdout, original_intent)

        # Validate enhanced intent is not empty
        if not enhanced_intent or not enhanced_intent.strip():
            Logger.warning("Enhancement agent returned empty response.")
            self.hooks.emit(Event(EventType.INTENT_ENHANCE_FAILURE, phase="enhance_intent"))

            if self._enhance_intent_strict:
                Logger.error("Intent enhancement returned invalid response in strict mode. Exiting.")
                sys.exit(1)

            Logger.warning("Falling back to original intent.")
            return original_intent

        # Log both original and enhanced intent when verbose
        Logger.debug(f"Original intent: {original_intent}", "CYAN")
        Logger.debug(f"Enhanced intent: {enhanced_intent}", "GREEN")

        Logger.info("✅ Intent enhanced.", "GREEN")
        self.hooks.emit(Event(EventType.INTENT_ENHANCE_SUCCESS, phase="enhance_intent"))

        return enhanced_intent

    def _parse_enhanced_intent(self, response: str, fallback: str) -> str:
        """Parse the enhanced intent from the agent response.

        Args:
            response: The raw response from the enhancement agent
            fallback: The fallback value if parsing fails

        Returns:
            The parsed enhanced intent or fallback value
        """
        import re
        # Try to extract content between <ENHANCED_INTENT> tags
        pattern = r'<ENHANCED_INTENT>\s*(.*?)\s*</ENHANCED_INTENT>'
        match = re.search(pattern, response, re.DOTALL)

        if match:
            return match.group(1).strip()

        # If no tags found, log warning and return fallback
        Logger.warning("Could not parse enhanced intent from response. Falling back to original.")
        return fallback

    def _get_and_enhance_intent(self, user_intent=None) -> str:
        """Get intent and optionally enhance it based on --enhance-intent flag.

        Args:
            user_intent: Optional pre-provided intent

        Returns:
            The (possibly enhanced) intent string
        """
        intent = self._get_intent(user_intent)

        if self._enhance_intent:
            intent = self._enhance_intent_impl(intent)

        return intent

    def _run_single_phase(self, phase: str) -> None:
        """Run a single specified phase with prerequisite checks."""
        Logger.info(f"📋 Phase: {phase} only", "YELLOW")

        if phase == "planner" and not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("❌ Memory missing. Run architect first.", "RED")
            sys.exit(1)
        if phase == "execute" and not self._prd.exists():
            Logger.info("❌ PRD missing. Run planner first.", "RED")
            sys.exit(1)

        if phase == "execute":
            self.execute_loop()
        else:
            user_intent = self._get_and_enhance_intent()
            if phase == "architect":
                self.run_architect(user_intent)
            else:
                self.run_planner(user_intent)

        Logger.info(f"✅ {phase.title()} complete.", "GREEN")

    def _run_all_phases(self, accept_all: bool) -> None:
        """Run all phases with optional user confirmation."""
        Logger.info("📋 Running all phases...", "YELLOW")
        user_intent = None

        # Architect phase
        if any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("📋 Memory exists, skipping architect.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Architect"):
            user_intent = self._get_and_enhance_intent()
            self.run_architect(user_intent)
        else:
            Logger.info("⏭️ Skipping architect.", "YELLOW")

        # Planner phase
        if self._prd.exists():
            Logger.info("📋 PRD exists, skipping planner.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Planner"):
            user_intent = self._get_and_enhance_intent(user_intent)
            self.run_planner(user_intent)
        else:
            Logger.info("⏭️ Skipping planner.", "YELLOW")

        # Execute phase
        if accept_all or self._prompt_user_for_phase("Execute"):
            self.execute_loop()
        else:
            Logger.info("⏭️ Skipping execute.", "YELLOW")

        Logger.info("✅ All phases complete.", "GREEN")

    def start(self, phase: str = "all", accept_all: bool = False) -> None:
        """
        Start the Ralph orchestrator.

        Args:
            phase: Which phase to run ("architect", "planner", "execute", "all", or "qa-status")
            accept_all: If True, skip user confirmation prompts

        Respects the following flags:
        - --print-prd: Print PRD contents and exit without executing
        - --prd-out: Export PRD to specified file and continue
        - --status-check: Check PRD status and exit with appropriate code
        """
        # Handle qa-status command: show QA checklist status and exit
        if phase == "qa-status":
            exit_code = self._run_qa_status()
            sys.exit(exit_code)

        # Handle --status-check flag: check PRD status and exit
        if self._status_check:
            exit_code = self._check_prd_status()
            sys.exit(exit_code)

        # Handle --print-prd flag: print PRD and exit
        if self._print_prd_flag:
            self._print_prd()
            return

        # Handle standalone QA review workflow: --qa-review with --qa-path
        if self._qa_review and self._qa_path is not None:
            self._run_standalone_qa_review()
            return

        # Handle --prd-out flag: export PRD to file
        if self._prd_out:
            self._export_prd(self._prd_out)

        Logger.info(f"🤖 Ralph {self.agent.get_name()} Agent active in: {CONF.BASE_DIR}", "GREEN")

        if phase in ("architect", "planner", "execute"):
            self._run_single_phase(phase)
        else:
            self._run_all_phases(accept_all)
