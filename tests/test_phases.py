"""Tests for the Phase Strategy pattern implementation."""

import unittest
from unittest.mock import MagicMock, patch

from pyralph.phases import (
    Phase,
    PhaseContext,
    PhaseStrategyRunner,
    ArchitectPhase,
    PlannerPhase,
    ExecutePhase,
)


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


class TestArchitectPhase(unittest.TestCase):
    """Tests for ArchitectPhase class."""

    def test_name_property(self):
        phase = ArchitectPhase()
        self.assertEqual(phase.name, "architect")

    def test_execute_calls_run_architect(self):
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="Build an app")
        phase = ArchitectPhase()

        phase.execute(context)

        mock_runner.run_architect.assert_called_once_with("Build an app")

    def test_execute_raises_without_phase_runner(self):
        context = PhaseContext(user_intent="Build an app")
        phase = ArchitectPhase()

        with self.assertRaises(RuntimeError) as cm:
            phase.execute(context)
        self.assertIn("phase_runner", str(cm.exception))

    def test_execute_raises_without_user_intent(self):
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="")
        phase = ArchitectPhase()

        with self.assertRaises(RuntimeError) as cm:
            phase.execute(context)
        self.assertIn("user_intent", str(cm.exception))

    def test_is_phase_subclass(self):
        phase = ArchitectPhase()
        self.assertIsInstance(phase, Phase)


class TestPlannerPhase(unittest.TestCase):
    """Tests for PlannerPhase class."""

    def test_name_property(self):
        phase = PlannerPhase()
        self.assertEqual(phase.name, "planner")

    def test_execute_calls_run_planner(self):
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="Create a webapp")
        phase = PlannerPhase()

        phase.execute(context)

        mock_runner.run_planner.assert_called_once_with("Create a webapp")

    def test_execute_raises_without_phase_runner(self):
        context = PhaseContext(user_intent="Create a webapp")
        phase = PlannerPhase()

        with self.assertRaises(RuntimeError) as cm:
            phase.execute(context)
        self.assertIn("phase_runner", str(cm.exception))

    def test_execute_raises_without_user_intent(self):
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="")
        phase = PlannerPhase()

        with self.assertRaises(RuntimeError) as cm:
            phase.execute(context)
        self.assertIn("user_intent", str(cm.exception))

    def test_is_phase_subclass(self):
        phase = PlannerPhase()
        self.assertIsInstance(phase, Phase)


class TestExecutePhase(unittest.TestCase):
    """Tests for ExecutePhase class."""

    def test_name_property(self):
        phase = ExecutePhase()
        self.assertEqual(phase.name, "execute")

    def test_execute_calls_execute_loop(self):
        mock_executor = MagicMock()
        context = PhaseContext(task_executor=mock_executor)
        phase = ExecutePhase()

        phase.execute(context)

        mock_executor.execute_loop.assert_called_once()

    def test_execute_raises_without_task_executor(self):
        context = PhaseContext()
        phase = ExecutePhase()

        with self.assertRaises(RuntimeError) as cm:
            phase.execute(context)
        self.assertIn("task_executor", str(cm.exception))

    def test_is_phase_subclass(self):
        phase = ExecutePhase()
        self.assertIsInstance(phase, Phase)


class TestPhaseStrategyRunner(unittest.TestCase):
    """Tests for PhaseStrategyRunner class."""

    def test_run_invokes_phase_execute(self):
        mock_phase = MagicMock(spec=Phase)
        context = PhaseContext()
        runner = PhaseStrategyRunner()

        runner.run(mock_phase, context)

        mock_phase.execute.assert_called_once_with(context)

    def test_run_with_architect_phase(self):
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="Build CLI")
        phase = ArchitectPhase()
        runner = PhaseStrategyRunner()

        runner.run(phase, context)

        mock_runner.run_architect.assert_called_once_with("Build CLI")

    def test_run_with_planner_phase(self):
        mock_runner = MagicMock()
        context = PhaseContext(phase_runner=mock_runner, user_intent="Build API")
        phase = PlannerPhase()
        runner = PhaseStrategyRunner()

        runner.run(phase, context)

        mock_runner.run_planner.assert_called_once_with("Build API")

    def test_run_with_execute_phase(self):
        mock_executor = MagicMock()
        context = PhaseContext(task_executor=mock_executor)
        phase = ExecutePhase()
        runner = PhaseStrategyRunner()

        runner.run(phase, context)

        mock_executor.execute_loop.assert_called_once()

    def test_run_raises_typeerror_for_non_phase(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with self.assertRaises(TypeError) as cm:
            runner.run("not a phase", context)
        self.assertIn("Expected Phase instance", str(cm.exception))
        self.assertIn("str", str(cm.exception))

    def test_run_raises_typeerror_for_none(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with self.assertRaises(TypeError) as cm:
            runner.run(None, context)
        self.assertIn("Expected Phase instance", str(cm.exception))
        self.assertIn("NoneType", str(cm.exception))

    def test_run_raises_typeerror_for_dict(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with self.assertRaises(TypeError) as cm:
            runner.run({"name": "fake"}, context)
        self.assertIn("Expected Phase instance", str(cm.exception))

    def test_run_raises_typeerror_for_class_not_instance(self):
        runner = PhaseStrategyRunner()
        context = PhaseContext()

        with self.assertRaises(TypeError) as cm:
            runner.run(ArchitectPhase, context)
        self.assertIn("Expected Phase instance", str(cm.exception))


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
