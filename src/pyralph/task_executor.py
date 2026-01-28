#!/usr/bin/env python3
"""Task executor module for Ralph orchestrator.

This module contains the TaskExecutor class which handles task execution,
verification, and failure handling during the execute phase.
"""

import datetime
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .logger import Logger as LoggerType
    from .agents.base import BaseAgent, AgentError
    from .prd import PRDManager
    from .command_runner import CommandRunner


class TaskExecutor:
    """Handles task execution, verification, and failure handling.

    Provides functionality for executing tasks from the PRD, verifying
    task completion through test commands, and handling failures with retries.
    """

    def __init__(
        self,
        agent: "BaseAgent",
        hooks: "HookManager",
        logger: "LoggerType",
        template_manager,
        shell_module,
        prd_manager: "PRDManager",
        command_runner: "CommandRunner",
        event_class,
        event_type_class,
        agent_error_class,
        config,
        test_cmd_override: Optional[str] = None,
        skip_verify: bool = False,
        retries_override: Optional[int] = None,
        only_tasks: Optional[List[str]] = None,
        except_tasks: Optional[List[str]] = None,
        resume_from: Optional[str] = None,
        prompt_file_override: Optional[str] = None,
    ):
        """Initialize the task executor.

        Args:
            agent: The LLM agent to use for task execution
            hooks: HookManager instance for event emission
            logger: Logger class for output
            template_manager: TemplateManager for rendering prompts
            shell_module: Shell module for running commands
            prd_manager: PRDManager for PRD file operations
            command_runner: CommandRunner for pre/post commands
            event_class: Event class for creating events
            event_type_class: EventType enum for event types
            agent_error_class: AgentError class for creating errors
            config: Configuration object
            test_cmd_override: Override for test command
            skip_verify: Whether to skip verification
            retries_override: Override for max retries
            only_tasks: List of task IDs to execute
            except_tasks: List of task IDs to skip
            resume_from: Task ID to resume from
            prompt_file_override: Override for prompt file path
        """
        self._agent = agent
        self._hooks = hooks
        self._logger = logger
        self._template_manager = template_manager
        self._shell = shell_module
        self._prd = prd_manager
        self._command_runner = command_runner
        self._event = event_class
        self._event_type = event_type_class
        self._agent_error = agent_error_class
        self._config = config
        self._test_cmd_override = test_cmd_override
        self._skip_verify = skip_verify
        self._retries_override = retries_override
        self._only_tasks = only_tasks
        self._except_tasks = except_tasks
        self._resume_from = resume_from
        self._prompt_file_override = prompt_file_override
        self._archive = True

    def set_archive(self, archive: bool) -> None:
        """Set whether to archive PRD after execution."""
        self._archive = archive

    def execute_loop(self) -> None:
        """Execute all pending tasks from the PRD.

        Iterates through user stories, executing each pending task
        with verification. Continues to next task on failure instead
        of terminating. Archives the PRD upon completion.

        Respects the following flags:
        - --test-cmd: Override the test command
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --only: Execute only specified task IDs
        - --except: Skip specified task IDs
        - --resume: Resume execution from a specific task ID
        - --pre: Run pre-commands before phase execution
        - --post: Run post-commands after phase completion
        """
        prd = self._prd.load()
        test_cmd = self._test_cmd_override if self._test_cmd_override else "pytest"

        self._logger.info(f"\n🚀 Starting Loop. Verify Command: '{test_cmd}'", "YELLOW")
        if self._skip_verify:
            self._logger.info("   ⏭️  Verification will be skipped (--skip-verify)", "YELLOW")
        self._hooks.emit(self._event(self._event_type.PHASE_START, phase="execute"))
        self._hooks.emit(self._event(self._event_type.EXECUTE_START, phase="execute", verification_command=test_cmd))

        if not self._command_runner.run_pre_commands("execute"):
            self._logger.info("⚠️ Execute aborted: pre-command failed.", "RED")
            self._hooks.emit(self._event(self._event_type.EXECUTE_END, phase="execute"))
            self._hooks.emit(self._event(self._event_type.PHASE_END, phase="execute"))
            self._command_runner.run_post_commands("execute", success=False)
            return

        failed_tasks: List[str] = []
        resume_found = self._resume_from is None

        for task in prd.get('userStories', []):
            task_id = task['id']

            if not resume_found:
                if task_id == self._resume_from:
                    resume_found = True
                    self._logger.info(f"   ➡️  Resuming from task {task_id}", "CYAN")
                else:
                    self._logger.debug(f"   ⏭️  Skipping {task_id} (before resume point)")
                    continue

            if self._only_tasks and task_id not in self._only_tasks:
                self._logger.debug(f"   ⏭️  Skipping {task_id} (not in --only list)")
                continue

            if self._except_tasks and task_id in self._except_tasks:
                self._logger.info(f"   ⏭️  Skipping {task_id} (in --except list)", "YELLOW")
                continue

            if task.get('status') == 'completed':
                continue
            if task.get('status') == 'failed':
                task['status'] = 'pending'

            self._logger.info(f"\n▶️  Task {task['id']}: {task['description']}", "CYAN")
            success = self._execute_task(prd, task, test_cmd)

            self._prd.save(prd)

            if not success:
                failed_tasks.append(task['id'])

        if not resume_found:
            self._logger.warning(f"Resume task '{self._resume_from}' not found in PRD. No tasks executed.")

        phase_success = len(failed_tasks) == 0
        if failed_tasks:
            self._logger.info(f"\n⚠️  {len(failed_tasks)} task(s) failed: {', '.join(failed_tasks)}", "YELLOW")
            self._logger.info("Run 'ralph execute' again to retry failed tasks.", "YELLOW")
        else:
            self._logger.info("\n🎉 All Tasks Complete.", "GREEN")

        self._archive_prd()
        self._hooks.emit(self._event(self._event_type.EXECUTE_END, phase="execute"))
        self._hooks.emit(self._event(self._event_type.PHASE_END, phase="execute"))
        self._command_runner.run_post_commands("execute", success=phase_success)

    def _execute_task(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> bool:
        """Execute a single task with retries.

        Respects the following flags:
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --timeout: Override agent timeout

        Returns:
            True if task completed successfully, False if max retries exhausted.
        """
        max_retries = self._retries_override if self._retries_override is not None else self._config.MAX_RETRIES

        self._hooks.emit(self._event(
            self._event_type.TASK_START, phase="execute",
            task_id=task['id'], task_description=task['description'], max_retries=max_retries
        ))

        for retry in range(max_retries):
            prev_errors = self._config.PROGRESS_FILE.read_text(encoding='utf-8') if self._config.PROGRESS_FILE.exists() else ""
            prompt = self._template_manager.render(
                "developer.txt",
                task_id=task['id'], task_description=task['description'],
                acceptance_criteria=self._format_acceptance_criteria(task),
                user_context=self._load_user_context(prd, task, test_cmd),
                test_cmd=test_cmd, prev_errors=prev_errors if prev_errors else "(No previous errors)"
            )

            success, output, agent_error = self._agent.run(prompt, f"WORKER-{task['id']}")

            if not success:
                self._handle_task_failure(task, retry, max_retries, "CLI Crash", output, agent_error=agent_error)
                continue

            if "STATUS: SUCCESS" in output:
                if self._skip_verify:
                    self._logger.info("   ⏭️  Skipping verification (--skip-verify)", "YELLOW")
                    task['status'] = 'completed'
                    if self._config.PROGRESS_FILE.exists():
                        self._config.PROGRESS_FILE.unlink()
                    self._hooks.emit(self._event(
                        self._event_type.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True

                verified, verify_error = self._verify_task(task, test_cmd)
                if verified:
                    task['status'] = 'completed'
                    if self._config.PROGRESS_FILE.exists():
                        self._config.PROGRESS_FILE.unlink()
                    self._hooks.emit(self._event(
                        self._event_type.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True
                self._handle_task_failure(task, retry, max_retries, "Verification Failed", output[-1000:], agent_error=verify_error)
            else:
                error = self._agent_error(
                    exception_type="AgentReportedFailure",
                    message="Agent did not report STATUS: SUCCESS",
                    stack_trace=f"Agent output (last 2000 chars):\n{output[-2000:]}",
                    timestamp=datetime.datetime.now().isoformat(),
                    agent_name=self._agent.get_name(),
                    task_id=task['id'],
                )
                self._handle_task_failure(task, retry, max_retries, "Agent Reported Failure", output[-1000:], agent_error=error)

        self._logger.info(f"🛑 Max retries for {task['id']}. Marking as failed and continuing.", "RED")
        task['status'] = 'failed'
        self._hooks.emit(self._event(
            self._event_type.TASK_FAILURE, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=max_retries, max_retries=max_retries
        ))
        return False

    def _verify_task(self, task: Dict[str, Any], test_cmd: str) -> Tuple[bool, Optional["AgentError"]]:
        """Run verification and return (success, error_if_failed)."""
        self._logger.info("   🔒 Verifying Agent's Claim...", "YELLOW")
        self._hooks.emit(self._event(
            self._event_type.VERIFICATION_START, phase="execute",
            task_id=task['id'], verification_command=test_cmd
        ))

        stdout, stderr, code = self._shell.run(test_cmd)
        verify_log = f"CMD: {test_cmd}\nEXIT CODE: {code}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        self._logger.file_log(verify_log, "VERIFICATION", f"WORKER-{task['id']}")

        if code == 0:
            self._logger.info("   ✅ Verified.", "GREEN")
            self._hooks.emit(self._event(
                self._event_type.VERIFICATION_SUCCESS, phase="execute",
                task_id=task['id'], verification_command=test_cmd, verification_exit_code=code
            ))
            return True, None

        self._logger.info("   🛑 Agent Hallucinated Success.", "RED")
        error = self._agent_error(
            exception_type="VerificationError",
            message=f"Test command '{test_cmd}' failed with exit code {code}",
            stack_trace=f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            timestamp=datetime.datetime.now().isoformat(),
            agent_name=self._agent.get_name(),
            task_id=task['id'],
        )
        self._hooks.emit(self._event(
            self._event_type.VERIFICATION_FAILURE, phase="execute",
            task_id=task['id'], verification_command=test_cmd,
            verification_exit_code=code, error=error
        ))
        return False, error

    def _handle_task_failure(
        self,
        task: Dict[str, Any],
        retry: int,
        max_retries: int,
        reason: str,
        detail: str,
        agent_error: Optional["AgentError"] = None
    ) -> None:
        """Handle task failure by recording details and emitting a single consolidated event.

        Args:
            task: The task that failed
            retry: Current retry attempt (0-indexed)
            max_retries: Maximum number of retries allowed
            reason: Brief description of failure reason
            detail: Detailed output/error information
            agent_error: Optional structured error from the agent
        """
        try:
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

            self._config.PROGRESS_FILE.write_text(msg, encoding='utf-8')
            self._logger.file_log(msg, "FAILURE_RECORD", f"RETRY-{retry+1}")
            self._logger.info(f"   ⚠️ Retry {retry+1}/{max_retries}: {reason}", "RED")

            self._hooks.emit(self._event(
                self._event_type.TASK_RETRY, phase="execute",
                task_id=task['id'], task_description=task['description'],
                retry_count=retry + 1, max_retries=max_retries,
                error=agent_error,
                metadata={"reason": reason}
            ))
        except Exception as e:
            self._logger.error(f"Error in failure handling for task {task.get('id', 'unknown')}: {type(e).__name__}: {e}")

    def _sanitize_id(self, text: str) -> str:
        """Sanitize an ID string to contain only alphanumeric chars and hyphens/underscores."""
        return "".join(c for c in text if c.isalnum() or c in '-_')

    def _load_user_context(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> str:
        """Load and prepare user context from prompt.md with variable substitution."""
        if self._prompt_file_override:
            prompt_md_path = Path(self._prompt_file_override)
            if not prompt_md_path.exists():
                self._logger.error(f"Prompt file not found: {self._prompt_file_override}")
                import sys
                sys.exit(1)
        else:
            prompt_md_path = self._config.BASE_DIR / "prompt.md"
            if not prompt_md_path.exists():
                return "No specific user preferences provided."

        raw_text = prompt_md_path.read_text(encoding='utf-8')
        if not raw_text.strip():
            if self._prompt_file_override:
                self._logger.warning(f"Prompt file is empty: {self._prompt_file_override}, using default user context.")
            else:
                self._logger.warning("prompt.md exists but is empty, using default user context.")
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

    def _archive_prd(self) -> None:
        """Archive the PRD after execution."""
        if not self._prd.exists():
            return
        if not self._archive:
            self._logger.debug("Skipping PRD archival (--no-archive)")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = self._config.ARCHIVE_DIR / f"prd_{ts}.json"
        shutil.move(str(self._config.PRD_FILE), str(dest))
        self._prd.invalidate_cache()
        self._logger.info(f"📦 PRD Archived to {dest}", "MAGENTA")
        self._hooks.emit(self._event(self._event_type.PRD_ARCHIVED, prd_path=str(dest)))
