#!/usr/bin/env python3
"""Phase runner module for Ralph orchestrator.

This module contains the PhaseRunner class which handles the execution
of architect and planner phases.
"""

import sys
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .logger import Logger as LoggerType
    from .agents.base import BaseAgent
    from .prd import PRDManager, JsonUtils as JsonUtilsType
    from .command_runner import CommandRunner
    from .prd_processor import PRDProcessor


class PhaseRunner:
    """Handles execution of architect and planner phases.

    Provides functionality for running the architect phase to generate
    architecture documentation and the planner phase to create PRDs.
    """

    def __init__(
        self,
        agent: "BaseAgent",
        hooks: "HookManager",
        logger: "LoggerType",
        template_manager,
        shell_module,
        prd_manager: "PRDManager",
        json_utils: "JsonUtilsType",
        command_runner: "CommandRunner",
        prd_processor: "PRDProcessor",
        event_class,
        event_type_class,
        config,
        tree_depth: int = 2,
        tree_ignore=None,
        revise_prd: bool = False,
    ):
        """Initialize the phase runner.

        Args:
            agent: The LLM agent to use for generation
            hooks: HookManager instance for event emission
            logger: Logger class for output
            template_manager: TemplateManager for rendering prompts
            shell_module: Shell module for running commands
            prd_manager: PRDManager for PRD file operations
            json_utils: JsonUtils class for parsing JSON
            command_runner: CommandRunner for pre/post commands
            prd_processor: PRDProcessor for PRD validation/revision
            event_class: Event class for creating events
            event_type_class: EventType enum for event types
            config: Configuration object
            tree_depth: Depth for file tree generation
            tree_ignore: Patterns to ignore in file tree
            revise_prd: Whether to revise PRD after generation
        """
        self._agent = agent
        self._hooks = hooks
        self._logger = logger
        self._template_manager = template_manager
        self._shell = shell_module
        self._prd = prd_manager
        self._json_utils = json_utils
        self._command_runner = command_runner
        self._prd_processor = prd_processor
        self._event = event_class
        self._event_type = event_type_class
        self._config = config
        self._tree_depth = tree_depth
        self._tree_ignore = tree_ignore
        self._revise_prd = revise_prd

    def _build_context(self) -> str:
        """Build context information for prompts.

        Returns:
            String containing file tree and other context information.
        """
        return self._shell.get_file_tree(depth=self._tree_depth, ignore=self._tree_ignore)

    def run_architect(self, user_intent: str) -> None:
        """Run the architect phase to generate architecture documentation.

        Generates ARCHITECTURE.md and ARCH.md with project structure,
        tech stack, and test command configuration.

        Args:
            user_intent: Description of what the user wants to build
        """
        self._logger.info("\n🕵️  Architect: Generating Architecture...", "CYAN")
        self._hooks.emit(self._event(self._event_type.PHASE_START, phase="architect"))
        self._hooks.emit(self._event(self._event_type.ARCHITECT_START, phase="architect"))

        if not self._command_runner.run_pre_commands("architect"):
            self._logger.info("⚠️ Architect aborted: pre-command failed.", "RED")
            self._hooks.emit(self._event(self._event_type.ARCHITECT_FAILURE, phase="architect"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="architect"))
            self._command_runner.run_post_commands("architect", success=False)
            sys.exit(1)

        file_tree = self._build_context()

        prompt = self._template_manager.render(
            "architect.txt",
            user_intent=user_intent,
            file_tree=file_tree
        )

        success, _, _ = self._agent.run(prompt, "ARCHITECT")
        if not success:
            self._logger.info("⚠️ Architect failed.", "RED")
            self._hooks.emit(self._event(self._event_type.ARCHITECT_FAILURE, phase="architect"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="architect"))
            self._command_runner.run_post_commands("architect", success=False)
            sys.exit(1)

        arch_md_path = self._config.BASE_DIR / "ARCH.md"
        if not arch_md_path.exists():
            self._logger.info("⚠️ Architect failed: ARCH.md was not created.", "RED")
            self._hooks.emit(self._event(self._event_type.ARCHITECT_FAILURE, phase="architect"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="architect"))
            self._command_runner.run_post_commands("architect", success=False)
            sys.exit(1)

        self._logger.info("✅ Architect completed.", "GREEN")
        self._hooks.emit(self._event(self._event_type.ARCHITECT_SUCCESS, phase="architect"))
        self._hooks.emit(self._event(self._event_type.PHASE_END, phase="architect"))
        self._command_runner.run_post_commands("architect", success=True)

    def run_planner(self, user_intent: str) -> None:
        """Run the planner phase to create a Product Requirements Document.

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
        self._logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        self._hooks.emit(self._event(self._event_type.PHASE_START, phase="planner"))
        self._hooks.emit(self._event(self._event_type.PLANNER_START, phase="planner"))

        if not self._command_runner.run_pre_commands("planner"):
            self._logger.info("⚠️ Planner aborted: pre-command failed.", "RED")
            self._hooks.emit(self._event(self._event_type.PLANNER_FAILURE, phase="planner"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
            self._command_runner.run_post_commands("planner", success=False)
            sys.exit(1)

        prompt = self._template_manager.render(
            "planner.txt",
            user_intent=user_intent
        )

        for attempt in range(3):
            success, raw, _ = self._agent.run(prompt, "PLANNER")
            if not success:
                continue

            try:
                data = self._json_utils.parse(raw)
                if "userStories" not in data:
                    raise ValueError("Missing userStories")

                is_valid, error = self._prd_processor.validate_prd(data)
                if not is_valid:
                    self._logger.info(f"⚠️ Validation failed (Attempt {attempt+1}): {error}", "YELLOW")
                    continue

                data = self._prd_processor.label_tasks(data)

                if self._revise_prd:
                    data = self._prd_processor.revise_prd(data)
                    is_valid, error = self._prd_processor.validate_prd(data)
                    if not is_valid:
                        self._logger.warning(f"Revised PRD failed validation: {error}")
                        continue

                self._prd.save(data)
                self._logger.info(f"✅ PRD Created ({len(data['userStories'])} stories).", "GREEN")
                self._hooks.emit(self._event(self._event_type.PRD_CREATED, phase="planner", prd_path=str(self._config.PRD_FILE)))
                self._hooks.emit(self._event(self._event_type.PLANNER_SUCCESS, phase="planner"))
                self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
                self._command_runner.run_post_commands("planner", success=True)
                return
            except Exception as e:
                self._logger.info(f"⚠️ JSON Error (Attempt {attempt+1}): {e}", "YELLOW")

        self._logger.info("❌ Planning Failed.", "RED")
        self._hooks.emit(self._event(self._event_type.PLANNER_FAILURE, phase="planner"))
        self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
        self._command_runner.run_post_commands("planner", success=False)
        sys.exit(1)
