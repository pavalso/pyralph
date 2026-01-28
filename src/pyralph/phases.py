#!/usr/bin/env python3
"""Phase strategy pattern module for Ralph orchestrator.

This module defines the Phase abstract base class and concrete phase
implementations (ArchitectPhase, PlannerPhase, ExecutePhase) following
the Strategy pattern for phase execution.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .phase_runner import PhaseRunner
    from .task_executor import TaskExecutor


class PhaseContext:
    """Context object passed to phases during execution.

    Holds all the information and dependencies needed by phases
    to execute their logic.
    """

    def __init__(
        self,
        phase_runner: "PhaseRunner" = None,
        task_executor: "TaskExecutor" = None,
        user_intent: str = "",
        **kwargs: Any
    ):
        """Initialize the phase context.

        Args:
            phase_runner: PhaseRunner instance for architect/planner phases
            task_executor: TaskExecutor instance for execute phase
            user_intent: The user's intent/description for the project
            **kwargs: Additional context data
        """
        self.phase_runner = phase_runner
        self.task_executor = task_executor
        self.user_intent = user_intent
        self._extra: Dict[str, Any] = kwargs

    def get(self, key: str, default: Any = None) -> Any:
        """Get an extra context value by key."""
        return self._extra.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set an extra context value."""
        self._extra[key] = value


class Phase(ABC):
    """Abstract base class for phase execution.

    Defines the interface that all phase implementations must follow.
    Each phase encapsulates its own execution logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of this phase."""
        pass

    @abstractmethod
    def execute(self, context: PhaseContext) -> None:
        """Execute this phase.

        Args:
            context: PhaseContext containing all necessary data
                     and dependencies for phase execution.

        Raises:
            RuntimeError: If required context dependencies are missing.
        """
        pass


class ArchitectPhase(Phase):
    """Phase for generating architecture documentation.

    Executes the architect phase which generates ARCH.md
    with project structure, tech stack, and configuration.
    """

    @property
    def name(self) -> str:
        return "architect"

    def execute(self, context: PhaseContext) -> None:
        """Execute the architect phase.

        Args:
            context: PhaseContext with phase_runner and user_intent.

        Raises:
            RuntimeError: If phase_runner or user_intent is missing.
        """
        if context.phase_runner is None:
            raise RuntimeError("ArchitectPhase requires phase_runner in context")
        if not context.user_intent:
            raise RuntimeError("ArchitectPhase requires user_intent in context")

        context.phase_runner.run_architect(context.user_intent)


class PlannerPhase(Phase):
    """Phase for generating Product Requirements Document.

    Executes the planner phase which creates a PRD with
    user stories and acceptance criteria.
    """

    @property
    def name(self) -> str:
        return "planner"

    def execute(self, context: PhaseContext) -> None:
        """Execute the planner phase.

        Args:
            context: PhaseContext with phase_runner and user_intent.

        Raises:
            RuntimeError: If phase_runner or user_intent is missing.
        """
        if context.phase_runner is None:
            raise RuntimeError("PlannerPhase requires phase_runner in context")
        if not context.user_intent:
            raise RuntimeError("PlannerPhase requires user_intent in context")

        context.phase_runner.run_planner(context.user_intent)


class ExecutePhase(Phase):
    """Phase for executing tasks from the PRD.

    Executes the execute phase which implements all pending
    tasks from the PRD with verification.
    """

    @property
    def name(self) -> str:
        return "execute"

    def execute(self, context: PhaseContext) -> None:
        """Execute the execute phase.

        Args:
            context: PhaseContext with task_executor.

        Raises:
            RuntimeError: If task_executor is missing.
        """
        if context.task_executor is None:
            raise RuntimeError("ExecutePhase requires task_executor in context")

        context.task_executor.execute_loop()


class PhaseStrategyRunner:
    """Runner for executing phases using the Strategy pattern.

    Accepts Phase instances and invokes their execute method,
    providing a uniform interface for phase execution.
    """

    def run(self, phase: Phase, context: PhaseContext) -> None:
        """Run a phase with the given context.

        Args:
            phase: A Phase instance to execute.
            context: PhaseContext containing execution data.

        Raises:
            TypeError: If phase is not a Phase instance.
        """
        if not isinstance(phase, Phase):
            raise TypeError(
                f"Expected Phase instance, got {type(phase).__name__}. "
                f"Phase must be an instance of the Phase abstract base class."
            )

        phase.execute(context)
