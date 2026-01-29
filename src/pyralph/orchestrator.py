#!/usr/bin/env python3
"""RalphOrchestrator - Core coordination logic for Ralph autonomous development agent.

This module provides the main RalphOrchestrator class which coordinates the
three-phase workflow: Architect -> Planner -> Execute.

The actual implementation of each phase is delegated to specialized modules:
- command_runner.py: Pre/post command execution
- intent_processor.py: Intent enhancement
- prd_processor.py: PRD validation, revision, labeling
- phase_runner.py: Architect and planner phase execution
- task_executor.py: Task execution and verification
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import CONF
from .logger import Logger
from .shell import Shell
from .prd import PRDManager, JsonUtils
from .templates import TemplateManager
from .agents import get_agent
from .agents.base import AgentError
from .hooks import HookManager, Event, EventType
from .prd_errors import create_missing_intent_error, create_empty_intent_error

# Import specialized modules with clear error messages for missing dependencies
try:
    from .command_runner import CommandRunner
except ImportError as e:
    raise ImportError(
        "Failed to import command_runner module. "
        "Ensure command_runner.py exists in the pyralph package directory. "
        f"Original error: {e}"
    ) from e

try:
    from .intent_processor import IntentProcessor
except ImportError as e:
    raise ImportError(
        "Failed to import intent_processor module. "
        "Ensure intent_processor.py exists in the pyralph package directory. "
        f"Original error: {e}"
    ) from e

try:
    from .prd_processor import PRDProcessor
except ImportError as e:
    raise ImportError(
        "Failed to import prd_processor module. "
        "Ensure prd_processor.py exists in the pyralph package directory. "
        f"Original error: {e}"
    ) from e

try:
    from .phase_runner import PhaseRunner
except ImportError as e:
    raise ImportError(
        "Failed to import phase_runner module. "
        "Ensure phase_runner.py exists in the pyralph package directory. "
        f"Original error: {e}"
    ) from e

try:
    from .task_executor import TaskExecutor
except ImportError as e:
    raise ImportError(
        "Failed to import task_executor module. "
        "Ensure task_executor.py exists in the pyralph package directory. "
        f"Original error: {e}"
    ) from e


class RalphOrchestrator:
    """Main orchestrator coordinating the Ralph workflow phases.

    This class acts as a facade, delegating actual work to specialized modules
    while managing configuration, initialization, and high-level coordination.
    """

    def __init__(self, agent_name: str = "claude", enable_hooks: bool = True, enabled_hook_names: Optional[List[str]] = None,
                 intent: Optional[str] = None, intent_file: Optional[str] = None, prompt_file: Optional[str] = None,
                 enhance_intent: bool = False, enhance_intent_strict: bool = False,
                 tree_depth: int = 2, tree_ignore: Optional[List[str]] = None,
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
                 reuse_context: bool = False,
                 check_drift: bool = False, strict: bool = False,
                 explore_depth: Optional[int] = None,
                 explore_files_limit: Optional[int] = None,
                 explore_thorough: bool = False) -> None:
        """Initialize the orchestrator with all configuration options."""
        # Initialize agent
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
        CONF.ensure_directories()

        # Initialize hook system
        self.hooks = HookManager(CONF.HOOKS_DIR, Logger)
        if not enable_hooks:
            self.hooks.disable()
        elif enabled_hook_names is not None:
            self.hooks.set_enabled_hooks(enabled_hook_names)

        # Store configuration flags
        self._intent = intent
        self._intent_file = intent_file
        self._intent_file_override = intent_file
        self._prompt_file_override = prompt_file
        self._enhance_intent = enhance_intent
        self._enhance_intent_strict = enhance_intent_strict
        self._tree_depth = tree_depth
        self._tree_ignore = tree_ignore
        self._test_cmd_override = test_cmd
        self._skip_verify = skip_verify
        self._retries_override = retries
        self._timeout_override = timeout
        self._only_tasks = only
        self._except_tasks = except_tasks
        self._resume_from = resume
        self._include_patterns = include
        self._exclude_patterns = exclude
        self._context_limit = context_limit
        self._log_file = log_file
        self._log_level = log_level
        self._json_output = json_output
        self._ndjson_output = ndjson_output
        self._print_prd_flag = print_prd
        self._prd_out = prd_out
        self._archive = archive
        self._non_interactive = non_interactive
        self._ci = ci
        self._status_check = status_check
        self._pre_commands = pre or []
        self._post_commands = post or []
        self._plugin_paths = plugin or []
        self._schema_path = schema
        self._min_criteria = min_criteria
        self._labels = label or []
        self._revise_prd = revise_prd
        self._reuse_context = reuse_context
        self._check_drift = check_drift
        self._strict = strict
        self._explore_depth = explore_depth
        self._explore_files_limit = explore_files_limit
        self._explore_thorough = explore_thorough

        # Initialize PRD manager
        self._prd = PRDManager(CONF.PRD_FILE)

        # Load plugins
        self._load_plugins()

        # Initialize specialized components
        self._command_runner = CommandRunner(
            pre_commands=self._pre_commands,
            post_commands=self._post_commands,
            hooks=self.hooks,
            logger=Logger,
            shell_module=Shell,
            event_class=Event,
            event_type_class=EventType,
        )

        self._intent_processor = IntentProcessor(
            agent=self.agent,
            hooks=self.hooks,
            logger=Logger,
            template_manager=TemplateManager,
            event_class=Event,
            event_type_class=EventType,
            enhance_intent_strict=self._enhance_intent_strict,
        )

        self._prd_processor = PRDProcessor(
            agent=self.agent,
            hooks=self.hooks,
            logger=Logger,
            template_manager=TemplateManager,
            json_utils=JsonUtils,
            event_class=Event,
            event_type_class=EventType,
            schema_path=self._schema_path,
            min_criteria=self._min_criteria,
            labels=self._labels,
        )

        self._phase_runner = PhaseRunner(
            agent=self.agent,
            hooks=self.hooks,
            logger=Logger,
            template_manager=TemplateManager,
            shell_module=Shell,
            prd_manager=self._prd,
            json_utils=JsonUtils,
            command_runner=self._command_runner,
            prd_processor=self._prd_processor,
            event_class=Event,
            event_type_class=EventType,
            config=CONF,
            tree_depth=self._tree_depth,
            tree_ignore=self._tree_ignore,
            revise_prd=self._revise_prd,
            reuse_context=self._reuse_context,
            explore_depth=self._explore_depth,
            explore_files_limit=self._explore_files_limit,
            explore_thorough=self._explore_thorough,
        )

        self._task_executor = TaskExecutor(
            agent=self.agent,
            hooks=self.hooks,
            logger=Logger,
            template_manager=TemplateManager,
            shell_module=Shell,
            prd_manager=self._prd,
            command_runner=self._command_runner,
            event_class=Event,
            event_type_class=EventType,
            agent_error_class=AgentError,
            config=CONF,
            test_cmd_override=self._test_cmd_override,
            skip_verify=self._skip_verify,
            retries_override=self._retries_override,
            only_tasks=self._only_tasks,
            except_tasks=self._except_tasks,
            resume_from=self._resume_from,
            prompt_file_override=self._prompt_file_override,
        )
        self._task_executor.set_archive(self._archive)

    def _load_plugins(self) -> None:
        """Load plugins from specified paths."""
        for plugin_path_str in self._plugin_paths:
            plugin_path = Path(plugin_path_str)
            if not plugin_path.exists():
                Logger.warning(f"Plugin path not found: {plugin_path}")
                continue

            if plugin_path.is_file() and plugin_path.suffix == '.py':
                self._load_plugin_file(plugin_path)
            elif plugin_path.is_dir():
                for py_file in plugin_path.glob('*.py'):
                    if not py_file.name.startswith('_'):
                        self._load_plugin_file(py_file)
            else:
                Logger.warning(f"Invalid plugin path (must be .py file or directory): {plugin_path}")

    def _load_plugin_file(self, path: Path) -> None:
        """Load a single plugin file."""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                Logger.warning(f"Could not load plugin: {path}")
                return

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, 'EVENTS') or not hasattr(module, 'on_event'):
                Logger.warning(f"Plugin '{path.name}' missing EVENTS or on_event")
                return

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

    # Delegate phase methods to specialized modules
    def run_architect(self, user_intent: str) -> None:
        """Run the architect phase to generate architecture documentation."""
        self._phase_runner.run_architect(user_intent)

    def run_planner(self, user_intent: str) -> None:
        """Run the planner phase to create a Product Requirements Document."""
        self._phase_runner.run_planner(user_intent)

    def execute_loop(self) -> None:
        """Execute all pending tasks from the PRD."""
        self._task_executor.execute_loop()

    # Delegate command methods
    def _run_pre_commands(self, phase: str) -> bool:
        """Run pre-execution commands before a phase."""
        return self._command_runner.run_pre_commands(phase)

    def _run_post_commands(self, phase: str, success: bool) -> None:
        """Run post-execution commands after a phase."""
        self._command_runner.run_post_commands(phase, success)

    # Delegate task executor methods for backward compatibility
    def _format_acceptance_criteria(self, task: Dict[str, Any]) -> str:
        """Format acceptance criteria as a bulleted list."""
        return self._task_executor._format_acceptance_criteria(task)

    def _verify_task(self, task: Dict[str, Any], test_cmd: str):
        """Run verification and return (success, error_if_failed)."""
        return self._task_executor._verify_task(task, test_cmd)

    def _execute_task(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> bool:
        """Execute a single task with retries."""
        return self._task_executor._execute_task(prd, task, test_cmd)

    def _handle_task_failure(self, task: Dict[str, Any], retry: int, max_retries: int,
                              reason: str, detail: str, agent_error=None) -> None:
        """Handle task failure by recording details and emitting a single consolidated event."""
        return self._task_executor._handle_task_failure(task, retry, max_retries, reason, detail, agent_error)

    # Intent handling methods
    def _get_intent(self, user_intent=None):
        """Get intent from flags or interactive prompt."""
        if user_intent:
            return user_intent
        if self._intent:
            return self._intent
        if self._intent_file:
            intent_path = Path(self._intent_file)
            if not intent_path.exists():
                Logger.error(f"Intent file not found: {self._intent_file}")
                sys.exit(1)
            content = intent_path.read_text(encoding='utf-8').strip()
            if not content:
                error = create_empty_intent_error()
                Logger.error(error.format_message())
                sys.exit(1)
            return content
        if self._non_interactive:
            error = create_missing_intent_error()
            Logger.error(error.format_message())
            sys.exit(1)
        intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
        if not intent:
            sys.exit(0)
        return intent

    def _get_and_enhance_intent(self, user_intent=None) -> str:
        """Get intent and optionally enhance it based on --enhance-intent flag."""
        intent = self._get_intent(user_intent)
        if self._enhance_intent:
            intent = self._intent_processor.enhance_intent(intent)
        return intent

    # PRD utility methods
    def _archive_prd(self) -> None:
        """Archive the PRD (delegated to task executor)."""
        self._task_executor._archive_prd()

    def _print_prd(self) -> None:
        """Print the PRD contents to stdout."""
        if not self._prd.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        if self._json_output or self._ndjson_output:
            print(self._prd.read_raw())
        else:
            prd_data = self._prd.load()
            print(json.dumps(prd_data, indent=2))

    def _export_prd(self, output_path: str) -> None:
        """Export the PRD to a specified file."""
        if not self._prd.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self._prd.read_raw(), encoding='utf-8')
        Logger.info(f"📋 PRD exported to {out_path}", "MAGENTA")

    def _check_prd_drift(self) -> int:
        """Check for drift between prd-*.md and prd.json.

        Returns:
            Exit code: 0 if no drift (or drift with warnings only),
            1 if drift detected and --strict flag is set
        """
        if not self._prd.exists():
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("No PRD file found", "error", status="no_prd", exit_code=2))
            else:
                Logger.error("No PRD file found. Run planner first.")
            return 2

        prd_data = self._prd.load()
        source_doc = prd_data.get("sourceDocument", {})
        stored_path = source_doc.get("path", "")

        if not stored_path:
            Logger.error("PRD has no sourceDocument.path - cannot check drift.")
            return 2

        prd_md_path = Path(stored_path)

        is_ok, messages = self._prd_processor.run_drift_check(
            prd_data=prd_data,
            prd_md_path=prd_md_path,
            strict=self._strict
        )

        if Logger.json_output or Logger.ndjson_output:
            status = "ok" if is_ok else "drift_detected"
            exit_code = 0 if is_ok else 1
            print(Logger._format_json_message(
                "Drift check complete",
                "info" if is_ok else "warn",
                status=status,
                exit_code=exit_code,
                messages=messages,
                strict=self._strict
            ))

        return 0 if is_ok else 1

    def _check_prd_status(self) -> int:
        """Check PRD status and return appropriate exit code."""
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

        status_data = {"total": total, "completed": completed, "failed": failed, "pending": pending}

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

    def _prompt_user_for_phase(self, phase_name: str) -> bool:
        """Prompt user to run a phase, or fail in non-interactive mode."""
        if self._non_interactive:
            Logger.error(f"Cannot prompt for {phase_name} phase in non-interactive mode. Use --accept-all (-y) to auto-accept.")
            sys.exit(1)
        return input(f"{Logger.COLORS['YELLOW']}Run {phase_name} phase? (y/n): {Logger.COLORS['RESET']}").strip().lower() == 'y'

    def _run_single_phase(self, phase: str) -> None:
        """Run a single specified phase with prerequisite checks."""
        Logger.info(f"📋 Phase: {phase} only", "YELLOW")

        if phase == "planner" and not (CONF.BASE_DIR / "ARCH.md").exists():
            Logger.info("❌ Architecture doc missing. Run architect first.", "RED")
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

        if (CONF.BASE_DIR / "ARCH.md").exists():
            Logger.info("📋 Architecture doc exists, skipping architect.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Architect"):
            user_intent = self._get_and_enhance_intent()
            self.run_architect(user_intent)
        else:
            Logger.info("⏭️ Skipping architect.", "YELLOW")

        if self._prd.exists():
            Logger.info("📋 PRD exists, skipping planner.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Planner"):
            user_intent = self._get_and_enhance_intent(user_intent)
            self.run_planner(user_intent)
        else:
            Logger.info("⏭️ Skipping planner.", "YELLOW")

        if accept_all or self._prompt_user_for_phase("Execute"):
            self.execute_loop()
        else:
            Logger.info("⏭️ Skipping execute.", "YELLOW")

        Logger.info("✅ All phases complete.", "GREEN")

    def start(self, phase: str = "all", accept_all: bool = False) -> None:
        """Start the Ralph orchestrator."""
        if self._check_drift:
            exit_code = self._check_prd_drift()
            sys.exit(exit_code)

        if self._status_check:
            exit_code = self._check_prd_status()
            sys.exit(exit_code)

        if self._print_prd_flag:
            self._print_prd()
            return

        if self._prd_out:
            self._export_prd(self._prd_out)

        Logger.info(f"🤖 Ralph {self.agent.get_name()} Agent active in: {CONF.BASE_DIR}", "GREEN")

        if phase in ("architect", "planner", "execute"):
            self._run_single_phase(phase)
        else:
            self._run_all_phases(accept_all)
