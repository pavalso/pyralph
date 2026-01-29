#!/usr/bin/env python3
"""Phase runner module for Ralph orchestrator.

This module contains the PhaseRunner class which handles the execution
of architect and planner phases.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .exploration_context import ExplorationContextManager, create_exploration_context
from .prd_errors import (
    create_empty_exploration_error,
    create_incomplete_exploration_warning,
    create_markdown_generation_error,
    create_markdown_parse_error,
    create_no_user_stories_error,
    create_timeout_error,
    create_validation_error,
)

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
        reuse_context: bool = False,
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
            reuse_context: Whether to reuse existing exploration context
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
        self._reuse_context = reuse_context
        self._exploration_context = ExplorationContextManager(
            config.EXPLORATION_CONTEXT_FILE
        )

    def _build_context(self) -> str:
        """Build context information for prompts.

        Returns:
            String containing file tree and other context information.
        """
        return self._shell.get_file_tree(depth=self._tree_depth, ignore=self._tree_ignore)

    def _explore_codebase(self) -> Tuple[str, List[str]]:
        """Explore the codebase and return file tree with any incomplete paths.

        Returns:
            Tuple of (file_tree_string, incomplete_paths_list)
        """
        incomplete_paths: List[str] = []
        file_tree = ""

        self._hooks.emit(self._event(
            self._event_type.EXPLORATION_START,
            phase="planner"
        ))

        try:
            file_tree = self._shell.get_file_tree(
                depth=self._tree_depth,
                ignore=self._tree_ignore
            )

            # Check for any inaccessible directories in BASE_DIR
            tree_ignore = self._tree_ignore
            if tree_ignore is None:
                try:
                    tree_ignore = self._shell.DEFAULT_TREE_IGNORE
                    if not isinstance(tree_ignore, list):
                        tree_ignore = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']
                except (AttributeError, TypeError):
                    tree_ignore = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']
            ignore_set = set(tree_ignore)
            try:
                for path in self._config.BASE_DIR.iterdir():
                    if path.name in ignore_set:
                        continue
                    if path.is_dir():
                        try:
                            # Try to list directory contents to verify access
                            list(path.iterdir())
                        except PermissionError:
                            incomplete_paths.append(str(path))
                        except OSError as e:
                            incomplete_paths.append(f"{path} ({e})")
            except PermissionError:
                incomplete_paths.append(str(self._config.BASE_DIR))

            self._hooks.emit(self._event(
                self._event_type.EXPLORATION_SUCCESS,
                phase="planner",
                exploration_context_path=str(self._config.EXPLORATION_CONTEXT_FILE),
                metadata={'incomplete_paths_count': len(incomplete_paths)}
            ))

        except Exception as e:
            self._logger.info(f"⚠️ Exploration encountered error: {e}", "YELLOW")
            incomplete_paths.append(f"root: {e}")
            self._hooks.emit(self._event(
                self._event_type.EXPLORATION_FAILURE,
                phase="planner",
                error=str(e)
            ))

        # Check for empty exploration results
        if not file_tree or not file_tree.strip():
            error = create_empty_exploration_error()
            self._logger.info(f"❌ {error.format_message()}", "RED")
            self._hooks.emit(self._event(
                self._event_type.EXPLORATION_FAILURE,
                phase="planner",
                error=error.description,
                metadata={'error_code': error.code.value}
            ))

        return file_tree, incomplete_paths

    def _save_exploration_context(
        self,
        file_tree: str,
        incomplete_paths: List[str],
        user_intent: str
    ) -> None:
        """Save exploration results to exploration_context.json.

        Args:
            file_tree: The generated file tree string
            incomplete_paths: List of paths that could not be explored
            user_intent: The user's intent for context
        """
        # Get tree_ignore from instance or shell module defaults
        tree_ignore = self._tree_ignore
        if tree_ignore is None:
            try:
                tree_ignore = self._shell.DEFAULT_TREE_IGNORE
                # Ensure it's a list, not a MagicMock or other type
                if not isinstance(tree_ignore, list):
                    tree_ignore = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']
            except (AttributeError, TypeError):
                tree_ignore = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']

        exploration_summary = {
            'user_intent': user_intent,
            'tree_depth': self._tree_depth,
            'tree_ignore': tree_ignore,
        }

        context_data = create_exploration_context(
            file_tree=file_tree,
            exploration_summary=exploration_summary,
            incomplete_paths=incomplete_paths,
            metadata={
                'base_dir': str(self._config.BASE_DIR),
            }
        )

        self._exploration_context.save(context_data)
        self._logger.info(
            f"✅ Exploration context saved: {self._config.EXPLORATION_CONTEXT_FILE.name}",
            "GREEN"
        )

    def _load_or_create_exploration_context(
        self, user_intent: str
    ) -> Tuple[str, List[str]]:
        """Load existing exploration context or create new one.

        Handles the --reuse-context flag and corruption detection.

        Args:
            user_intent: The user's intent for context

        Returns:
            Tuple of (file_tree_string, incomplete_paths_list)
        """
        # Check if we should reuse existing context
        if self._reuse_context and self._exploration_context.exists():
            try:
                self._logger.info(
                    "📂 Reusing existing exploration context...", "CYAN"
                )
                context = self._exploration_context.load()
                self._hooks.emit(self._event(
                    self._event_type.EXPLORATION_CONTEXT_REUSED,
                    phase="planner",
                    exploration_context_path=str(self._config.EXPLORATION_CONTEXT_FILE)
                ))
                return context.get('file_tree', ''), context.get('incomplete_paths', [])
            except (json.JSONDecodeError, ValueError) as e:
                self._logger.info(
                    f"⚠️ Exploration context corrupted: {e}", "YELLOW"
                )
                self._logger.info(
                    "🔄 Triggering fresh exploration...", "CYAN"
                )
                self._hooks.emit(self._event(
                    self._event_type.EXPLORATION_CONTEXT_CORRUPTED,
                    phase="planner",
                    error=str(e),
                    exploration_context_path=str(self._config.EXPLORATION_CONTEXT_FILE)
                ))

        # Perform fresh exploration
        self._logger.info("🔍 Exploring codebase...", "CYAN")
        file_tree, incomplete_paths = self._explore_codebase()

        # Save exploration context
        self._save_exploration_context(file_tree, incomplete_paths, user_intent)

        return file_tree, incomplete_paths

    def get_exploration_context(self) -> Optional[Dict[str, Any]]:
        """Get the current exploration context if it exists and is valid.

        Returns:
            Exploration context dictionary or None if not available/invalid
        """
        if not self._exploration_context.is_valid():
            return None
        try:
            return self._exploration_context.load()
        except (json.JSONDecodeError, ValueError):
            return None

    def _generate_short_description(self, user_intent: str) -> str:
        """Generate a kebab-case short description from user intent.

        Extracts key action words and nouns from the intent to create
        a concise, URL-friendly identifier.

        Args:
            user_intent: The user's intent description

        Returns:
            Kebab-case short description (e.g., "user-authentication", "api-refactor")
        """
        # Remove common filler words and punctuation
        text = user_intent.lower()
        text = re.sub(r'[^\w\s-]', '', text)

        # Common words to filter out
        stop_words = {
            'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
            'ought', 'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
            'from', 'as', 'into', 'through', 'during', 'before', 'after', 'above',
            'below', 'between', 'under', 'again', 'further', 'then', 'once', 'here',
            'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few', 'more',
            'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
            'same', 'so', 'than', 'too', 'very', 'just', 'and', 'but', 'if', 'or',
            'because', 'until', 'while', 'that', 'which', 'who', 'whom', 'this',
            'these', 'those', 'am', 'i', 'me', 'my', 'myself', 'we', 'our', 'ours',
            'ourselves', 'you', 'your', 'yours', 'yourself', 'yourselves', 'he',
            'him', 'his', 'himself', 'she', 'her', 'hers', 'herself', 'it', 'its',
            'itself', 'they', 'them', 'their', 'theirs', 'themselves', 'what',
            'want', 'wants', 'wanted', 'create', 'make', 'build', 'implement',
            'add', 'develop', 'write', 'help', 'please', 'like', 'get', 'give'
        }

        words = text.split()
        filtered = [w for w in words if w not in stop_words and len(w) > 2]

        # Take first 3-4 significant words
        significant_words = filtered[:4]

        if not significant_words:
            # Fallback: use first few words if all were filtered
            significant_words = words[:3]

        short_desc = '-'.join(significant_words)

        # Ensure valid kebab-case
        short_desc = re.sub(r'-+', '-', short_desc)
        short_desc = short_desc.strip('-')

        # Limit length
        if len(short_desc) > 50:
            short_desc = short_desc[:50].rsplit('-', 1)[0]

        return short_desc or 'prd'

    def _get_prd_md_path(self, short_description: str) -> Path:
        """Get the path for the PRD markdown file.

        Args:
            short_description: Kebab-case short description

        Returns:
            Path to the PRD markdown file in the .ralph directory
        """
        return self._config.ROOT_DIR / f"prd-{short_description}.md"

    def _generate_prd_markdown(
        self, user_intent: str, short_description: str
    ) -> Optional[Path]:
        """Generate the PRD markdown file by exploring the codebase.

        Uses exploration_context.json if available and valid, or creates
        new exploration context during generation.

        Args:
            user_intent: Description of what the user wants to build
            short_description: Kebab-case identifier for the PRD

        Returns:
            Path to the created markdown file, or None if generation failed
        """
        self._logger.info("\n📝 Planner: Generating PRD markdown...", "CYAN")
        self._hooks.emit(self._event(self._event_type.PRD_MD_START, phase="planner"))

        # Load or create exploration context
        file_tree, incomplete_paths = self._load_or_create_exploration_context(user_intent)

        # Check for empty exploration results - this is a critical error
        if not file_tree or not file_tree.strip():
            error = create_empty_exploration_error()
            self._logger.info(f"❌ {error.format_message()}", "RED")
            self._hooks.emit(self._event(
                self._event_type.PRD_MD_FAILURE,
                phase="planner",
                metadata={'error_code': error.code.value}
            ))
            return None

        # Log warning if there were incomplete paths (but allow generation to continue)
        if incomplete_paths:
            warning = create_incomplete_exploration_warning(incomplete_paths)
            self._logger.info(f"⚠️ {warning.format_message()}", "YELLOW")

        timestamp = datetime.now().isoformat()

        # Build context string including exploration context info
        exploration_context = self.get_exploration_context()
        exploration_metadata = ""
        if exploration_context:
            exploration_metadata = (
                f"\n<!-- Exploration Context: {self._config.EXPLORATION_CONTEXT_FILE.name} -->\n"
                f"<!-- Timestamp: {exploration_context.get('timestamp', 'unknown')} -->\n"
            )
            if incomplete_paths:
                exploration_metadata += (
                    f"<!-- Incomplete Paths: {len(incomplete_paths)} -->\n"
                )

        prompt = self._template_manager.render(
            "prd_markdown.txt",
            user_intent=user_intent,
            file_tree=file_tree,
            short_description=short_description,
            timestamp=timestamp,
            title=user_intent[:100],
            exploration_metadata=exploration_metadata
        )

        max_attempts = 3
        for attempt in range(max_attempts):
            success, raw, agent_error = self._agent.run(prompt, "PLANNER")
            if not success:
                # Check if this was a timeout error
                if agent_error and agent_error.exception_type == "TimeoutError":
                    timeout_err = create_timeout_error(
                        current_stage="PRD markdown generation",
                        elapsed_seconds=self._agent.timeout_seconds,
                        timeout_seconds=self._agent.timeout_seconds
                    )
                    self._logger.info(f"⚠️ {timeout_err.format_message()}", "YELLOW")
                else:
                    error = create_markdown_generation_error(attempt + 1, max_attempts)
                    self._logger.info(f"⚠️ {error.description}", "YELLOW")
                continue

            # Write the markdown file
            prd_md_path = self._get_prd_md_path(short_description)
            try:
                prd_md_path.write_text(raw, encoding='utf-8')
                self._logger.info(
                    f"✅ PRD markdown created: {prd_md_path.name}", "GREEN"
                )
                self._hooks.emit(self._event(
                    self._event_type.PRD_MD_SUCCESS,
                    phase="planner",
                    prd_md_path=str(prd_md_path)
                ))
                return prd_md_path
            except IOError as e:
                self._logger.info(
                    f"⚠️ Failed to write PRD markdown (Attempt {attempt+1}): {e}",
                    "YELLOW"
                )

        error = create_markdown_generation_error(max_attempts, max_attempts)
        self._logger.info(f"❌ {error.format_message()}", "RED")
        self._hooks.emit(self._event(
            self._event_type.PRD_MD_FAILURE,
            phase="planner",
            metadata={'error_code': error.code.value}
        ))
        return None

    def _generate_prd_json_from_markdown(
        self, prd_md_path: Path
    ) -> Optional[Dict[str, Any]]:
        """Generate PRD JSON by parsing the markdown file.

        This method deterministically parses the markdown file to extract
        structured data. The same markdown content always produces the same
        JSON output, ensuring faithful representation of the source document.

        Args:
            prd_md_path: Path to the PRD markdown file

        Returns:
            Parsed PRD data dictionary, or None if generation failed
        """
        if not prd_md_path.exists():
            error_msg = (
                f"Cannot generate prd.json: {prd_md_path.name} must be generated first"
            )
            self._logger.info(f"❌ {error_msg}", "RED")
            return None

        try:
            from .prd_markdown_parser import parse_prd_markdown

            md_content = prd_md_path.read_text(encoding='utf-8')
            timestamp = datetime.fromtimestamp(
                prd_md_path.stat().st_mtime
            ).isoformat()

            data = parse_prd_markdown(
                content=md_content,
                source_path=prd_md_path,
                timestamp=timestamp
            )

            if "userStories" not in data or not data["userStories"]:
                error = create_no_user_stories_error()
                self._logger.info(f"⚠️ {error.format_message()}", "YELLOW")
                return None

            return data

        except Exception as e:
            error = create_markdown_parse_error(str(e))
            self._logger.info(f"⚠️ {error.format_message()}", "YELLOW")
            return None

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

        Generates a PRD in two stages:
        1. First, explores the codebase and generates prd-<short-description>.md
        2. Then, derives prd.json from the markdown document

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

        # Step 1: Generate short description from user intent
        short_description = self._generate_short_description(user_intent)
        prd_md_path = self._get_prd_md_path(short_description)

        # Step 2: Generate PRD markdown (explores codebase first)
        prd_md_path = self._generate_prd_markdown(user_intent, short_description)
        if prd_md_path is None:
            self._logger.info("❌ Planning Failed: PRD markdown generation failed.", "RED")
            self._hooks.emit(self._event(self._event_type.PLANNER_FAILURE, phase="planner"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
            self._command_runner.run_post_commands("planner", success=False)
            sys.exit(1)

        # Step 3: Generate prd.json from the markdown file
        self._logger.info("\n🔄 Planner: Deriving prd.json from markdown...", "CYAN")
        data = self._generate_prd_json_from_markdown(prd_md_path)
        if data is None:
            error_msg = (
                f"Cannot generate prd.json: {prd_md_path.name} must be generated first"
            )
            self._logger.info(f"❌ {error_msg}", "RED")
            self._hooks.emit(self._event(self._event_type.PLANNER_FAILURE, phase="planner"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
            self._command_runner.run_post_commands("planner", success=False)
            sys.exit(1)

        # Step 4: Validate and process the PRD
        is_valid, validation_error = self._prd_processor.validate_prd(data)
        if not is_valid:
            prd_error = create_validation_error(validation_error or "Unknown validation error")
            self._logger.info(f"❌ {prd_error.format_message()}", "RED")
            self._hooks.emit(self._event(
                self._event_type.PLANNER_FAILURE,
                phase="planner",
                metadata={'error_code': prd_error.code.value}
            ))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
            self._command_runner.run_post_commands("planner", success=False)
            sys.exit(1)

        data = self._prd_processor.label_tasks(data)

        if self._revise_prd:
            data = self._prd_processor.revise_prd(data)
            is_valid, validation_error = self._prd_processor.validate_prd(data)
            if not is_valid:
                prd_error = create_validation_error(
                    f"Revised PRD failed: {validation_error or 'Unknown error'}"
                )
                self._logger.info(f"❌ {prd_error.format_message()}", "RED")
                self._hooks.emit(self._event(
                    self._event_type.PLANNER_FAILURE,
                    phase="planner",
                    metadata={'error_code': prd_error.code.value}
                ))
                self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
                self._command_runner.run_post_commands("planner", success=False)
                sys.exit(1)

        # Step 5: Save prd.json with sourceDocument metadata
        self._prd.save(data)
        self._logger.info(f"✅ PRD Created ({len(data['userStories'])} stories).", "GREEN")
        self._hooks.emit(self._event(
            self._event_type.PRD_CREATED,
            phase="planner",
            prd_path=str(self._config.PRD_FILE),
            prd_md_path=str(prd_md_path)
        ))
        self._hooks.emit(self._event(self._event_type.PLANNER_SUCCESS, phase="planner"))
        self._hooks.emit(self._event(self._event_type.PHASE_END, phase="planner"))
        self._command_runner.run_post_commands("planner", success=True)
