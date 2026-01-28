"""Tests for the Phase Strategy pattern implementation."""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from pyralph.phases import (
    Phase,
    PhaseContext,
    PhaseStrategyRunner,
    ArchitectPhase,
    PlannerPhase,
    ExecutePhase,
)

# Phase configurations: (phase_cls, name, method, context_key, intent_req)
# intent_req is True if the phase requires user_intent, None otherwise
PHASE_CONFIGS = [
    (ArchitectPhase, "architect", "run_architect", "phase_runner", True),
    (PlannerPhase, "planner", "run_planner", "phase_runner", True),
    (ExecutePhase, "execute", "execute_loop", "task_executor", None),
]


class TestPhaseContext(unittest.TestCase):
    """Tests for PhaseContext class."""

    def test_init_with_all_args(self):
        mock_runner = MagicMock()
        mock_executor = MagicMock()
        context = PhaseContext(
            phase_runner=mock_runner,
            task_executor=mock_executor,
            user_intent="Build a CLI tool",
            custom_key="custom_value"
        )
        self.assertIs(context.phase_runner, mock_runner)
        self.assertIs(context.task_executor, mock_executor)
        self.assertEqual(context.user_intent, "Build a CLI tool")
        self.assertEqual(context.get("custom_key"), "custom_value")

    def test_init_defaults(self):
        context = PhaseContext()
        self.assertIsNone(context.phase_runner)
        self.assertIsNone(context.task_executor)
        self.assertEqual(context.user_intent, "")

    def test_get_missing_key_returns_default(self):
        context = PhaseContext()
        self.assertIsNone(context.get("nonexistent"))
        self.assertEqual(context.get("nonexistent", "fallback"), "fallback")

    def test_set_and_get(self):
        context = PhaseContext()
        context.set("my_key", 42)
        self.assertEqual(context.get("my_key"), 42)


class TestPhaseABC(unittest.TestCase):
    """Tests for the Phase abstract base class."""

    def test_cannot_instantiate_phase_directly(self):
        with self.assertRaises(TypeError):
            Phase()

    def test_phase_requires_name_property(self):
        class IncompletePhase(Phase):
            def execute(self, context):
                pass

        with self.assertRaises(TypeError):
            IncompletePhase()

    def test_phase_requires_execute_method(self):
        class IncompletePhase(Phase):
            @property
            def name(self):
                return "incomplete"

        with self.assertRaises(TypeError):
            IncompletePhase()

    def test_valid_subclass_can_be_instantiated(self):
        class CustomPhase(Phase):
            @property
            def name(self):
                return "custom"

            def execute(self, context):
                pass

        phase = CustomPhase()
        self.assertEqual(phase.name, "custom")


class TestPhaseImplementations:
    """Parametrized tests for all concrete Phase implementations."""

    @pytest.mark.parametrize(
        "phase_cls,expected_name",
        [(cfg[0], cfg[1]) for cfg in PHASE_CONFIGS],
        ids=["ArchitectPhase", "PlannerPhase", "ExecutePhase"],
    )
    def test_name_property(self, phase_cls, expected_name):
        """Test that each phase returns its correct name."""
        phase = phase_cls()
        assert phase.name == expected_name

    @pytest.mark.parametrize(
        "phase_cls",
        [cfg[0] for cfg in PHASE_CONFIGS],
        ids=["ArchitectPhase", "PlannerPhase", "ExecutePhase"],
    )
    def test_is_phase_subclass(self, phase_cls):
        """Test that each phase is a proper Phase subclass."""
        phase = phase_cls()
        assert isinstance(phase, Phase)

    @pytest.mark.parametrize(
        "phase_cls,context_key",
        [(cfg[0], cfg[3]) for cfg in PHASE_CONFIGS],
        ids=["ArchitectPhase", "PlannerPhase", "ExecutePhase"],
    )
    def test_execute_raises_without_dependency(self, phase_cls, context_key):
        """Test that each phase raises RuntimeError when its required dependency is missing."""
        # For phases requiring user_intent, provide it so we test the context_key error
        if context_key == "phase_runner":
            context = PhaseContext(user_intent="Test intent")
        else:
            context = PhaseContext()
        phase = phase_cls()

        with pytest.raises(RuntimeError) as exc_info:
            phase.execute(context)
        assert context_key in str(exc_info.value)

    @pytest.mark.parametrize(
        "phase_cls,intent_req",
        [(cfg[0], cfg[4]) for cfg in PHASE_CONFIGS if cfg[4] is True],
        ids=["ArchitectPhase", "PlannerPhase"],
    )
    def test_execute_raises_without_user_intent(self, phase_cls, intent_req):
        """Test that phases requiring user_intent raise RuntimeError when it's missing."""
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="")
        phase = phase_cls()

        with pytest.raises(RuntimeError) as exc_info:
            phase.execute(context)
        assert "user_intent" in str(exc_info.value)

    def test_execute_phase_does_not_require_user_intent(self):
        """Test that ExecutePhase works without user_intent (edge case: intent_req=None)."""
        mock_executor = MagicMock()
        context = PhaseContext(task_executor=mock_executor, user_intent="")
        phase = ExecutePhase()

        # Should not raise - ExecutePhase doesn't require user_intent
        phase.execute(context)
        mock_executor.execute_loop.assert_called_once()


class TestPhaseExecution:
    """Tests for phase execution behavior."""

    def test_architect_phase_calls_run_architect(self):
        """Test ArchitectPhase calls run_architect with user_intent."""
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="Build an app")
        phase = ArchitectPhase()

        phase.execute(context)

        mock_runner.run_architect.assert_called_once_with("Build an app")

    def test_planner_phase_calls_run_planner(self):
        """Test PlannerPhase calls run_planner with user_intent."""
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="Create a webapp")
        phase = PlannerPhase()

        phase.execute(context)

        mock_runner.run_planner.assert_called_once_with("Create a webapp")

    def test_execute_phase_calls_execute_loop(self):
        """Test ExecutePhase calls execute_loop."""
        mock_executor = MagicMock()
        context = PhaseContext(task_executor=mock_executor)
        phase = ExecutePhase()

        phase.execute(context)

        mock_executor.execute_loop.assert_called_once()


class TestPhaseStrategyRunner:
    """Tests for PhaseStrategyRunner class."""

    def test_run_invokes_phase_execute(self):
        mock_phase = MagicMock(spec=Phase)
        context = PhaseContext()
        runner = PhaseStrategyRunner()

        runner.run(mock_phase, context)

        mock_phase.execute.assert_called_once_with(context)

    @pytest.mark.parametrize(
        "phase_cls,context_kwargs,expected_call",
        [
            (
                ArchitectPhase,
                {"phase_runner": MagicMock(), "user_intent": "Build CLI"},
                ("run_architect", "Build CLI"),
            ),
            (
                PlannerPhase,
                {"phase_runner": MagicMock(), "user_intent": "Build API"},
                ("run_planner", "Build API"),
            ),
            (
                ExecutePhase,
                {"task_executor": MagicMock()},
                ("execute_loop", None),
            ),
        ],
        ids=["ArchitectPhase", "PlannerPhase", "ExecutePhase"],
    )
    def test_run_with_phase(self, phase_cls, context_kwargs, expected_call):
        """Test PhaseStrategyRunner.run with each phase type."""
        context = PhaseContext(**context_kwargs)
        phase = phase_cls()
        runner = PhaseStrategyRunner()

        runner.run(phase, context)

        method_name, expected_arg = expected_call
        if "phase_runner" in context_kwargs:
            mock_obj = context_kwargs["phase_runner"]
        else:
            mock_obj = context_kwargs["task_executor"]

        method = getattr(mock_obj, method_name)
        if expected_arg is not None:
            method.assert_called_once_with(expected_arg)
        else:
            method.assert_called_once()

    @pytest.mark.parametrize(
        "phase_cls,context_kwargs,missing_key",
        [
            (ArchitectPhase, {"user_intent": "Build CLI"}, "phase_runner"),
            (PlannerPhase, {"user_intent": "Build API"}, "phase_runner"),
            (ExecutePhase, {}, "task_executor"),
        ],
        ids=["ArchitectPhase_missing_runner", "PlannerPhase_missing_runner", "ExecutePhase_missing_executor"],
    )
    def test_run_with_phase_missing_context_kwargs(self, phase_cls, context_kwargs, missing_key):
        """Test that missing required context_kwargs raises RuntimeError."""
        context = PhaseContext(**context_kwargs)
        phase = phase_cls()
        runner = PhaseStrategyRunner()

        with pytest.raises(RuntimeError) as exc_info:
            runner.run(phase, context)
        assert missing_key in str(exc_info.value)

    def test_run_raises_typeerror_for_non_phase(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with pytest.raises(TypeError) as exc_info:
            runner.run("not a phase", context)
        assert "Expected Phase instance" in str(exc_info.value)
        assert "str" in str(exc_info.value)

    def test_run_raises_typeerror_for_none(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with pytest.raises(TypeError) as exc_info:
            runner.run(None, context)
        assert "Expected Phase instance" in str(exc_info.value)
        assert "NoneType" in str(exc_info.value)

    def test_run_raises_typeerror_for_dict(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with pytest.raises(TypeError) as exc_info:
            runner.run({"name": "fake"}, context)
        assert "Expected Phase instance" in str(exc_info.value)

    def test_run_raises_typeerror_for_class_not_instance(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with pytest.raises(TypeError) as exc_info:
            runner.run(ArchitectPhase, context)
        assert "Expected Phase instance" in str(exc_info.value)


class TestExtensibility(unittest.TestCase):
    """Tests demonstrating extensibility of the Phase pattern."""

    def test_new_phase_only_requires_subclass(self):
        """Given a new phase type is needed, when implementing, then only a new Phase subclass is required."""

        class CustomValidationPhase(Phase):
            @property
            def name(self):
                return "validation"

            def execute(self, context):
                validator = context.get("validator")
                if validator:
                    validator.validate()

        mock_validator = MagicMock()
        context = PhaseContext(validator=mock_validator)
        phase = CustomValidationPhase()
        runner = PhaseStrategyRunner()

        runner.run(phase, context)

        mock_validator.validate.assert_called_once()

    def test_custom_phase_with_return_value(self):
        """Custom phases can store results in context."""

        class AnalysisPhase(Phase):
            @property
            def name(self):
                return "analysis"

            def execute(self, context):
                context.set("analysis_result", {"files": 10, "lines": 500})

        context = PhaseContext()
        phase = AnalysisPhase()
        runner = PhaseStrategyRunner()

        runner.run(phase, context)

        result = context.get("analysis_result")
        self.assertEqual(result, {"files": 10, "lines": 500})


if __name__ == "__main__":
    unittest.main()
