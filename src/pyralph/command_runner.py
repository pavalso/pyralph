#!/usr/bin/env python3
"""Command runner module for Ralph orchestrator.

This module contains the CommandRunner class which handles pre and post
command execution for each phase of the Ralph workflow.
"""

import subprocess
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .logger import Logger as LoggerType


class CommandRunner:
    """Handles execution of pre and post commands for orchestration phases.

    Pre-commands run before each phase and can abort execution on failure.
    Post-commands run after each phase completes and receive phase result info.
    """

    def __init__(
        self,
        pre_commands: List[str],
        post_commands: List[str],
        hooks: "HookManager",
        logger: "LoggerType",
        shell_module,
        event_class,
        event_type_class,
    ):
        """Initialize the command runner.

        Args:
            pre_commands: List of shell commands to run before phases
            post_commands: List of shell commands to run after phases
            hooks: HookManager instance for event emission
            logger: Logger class for output
            shell_module: Shell module for running pre-commands
            event_class: Event class for creating events
            event_type_class: EventType enum for event types
        """
        self._pre_commands = pre_commands
        self._post_commands = post_commands
        self._hooks = hooks
        self._logger = logger
        self._shell = shell_module
        self._event = event_class
        self._event_type = event_type_class

    def run_pre_commands(self, phase: str) -> bool:
        """Run pre-execution commands before a phase.

        Pre-commands are shell commands executed before each phase.
        If any command fails (non-zero exit code), the phase is aborted.

        Args:
            phase: The phase about to run (architect, planner, execute)

        Returns:
            True if all commands succeeded, False if any failed
        """
        if not self._pre_commands:
            return True

        self._logger.debug(f"Running {len(self._pre_commands)} pre-command(s) for {phase} phase")
        for cmd in self._pre_commands:
            self._logger.debug(f"  Pre-command: {cmd}")
            stdout, stderr, code = self._shell.run(cmd, timeout=60)
            if code != 0:
                self._logger.error(f"Pre-command failed: {cmd}")
                self._logger.error(f"  Exit code: {code}")
                if stderr:
                    self._logger.error(f"  Stderr: {stderr[:500]}")
                self._hooks.emit(self._event(
                    self._event_type.ERROR,
                    phase=phase,
                    metadata={"reason": "pre_command_failed", "command": cmd, "exit_code": code}
                ))
                return False
            if stdout and self._logger.verbosity >= 2:
                self._logger.trace(f"  Output: {stdout[:200]}")
        return True

    def run_post_commands(self, phase: str, success: bool) -> None:
        """Run post-execution commands after a phase.

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
        env = os.environ.copy()
        env['RALPH_PHASE'] = phase
        env['RALPH_SUCCESS'] = '1' if success else '0'

        self._logger.debug(f"Running {len(self._post_commands)} post-command(s) for {phase} phase")
        for cmd in self._post_commands:
            self._logger.debug(f"  Post-command: {cmd}")
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True,
                    text=True, encoding='utf-8', timeout=60,
                    env=env
                )
                if result.returncode != 0:
                    self._logger.warning(f"Post-command failed: {cmd} (exit code: {result.returncode})")
                elif result.stdout and self._logger.verbosity >= 2:
                    self._logger.trace(f"  Output: {result.stdout[:200]}")
            except subprocess.TimeoutExpired:
                self._logger.warning(f"Post-command timed out: {cmd}")
            except Exception as e:
                self._logger.warning(f"Post-command error: {cmd} ({e})")
