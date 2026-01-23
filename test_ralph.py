import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime as dt
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents import get_agent, list_agents
from agents.base import AgentError
from agents.claude import ClaudeAgent
from agents.copilot import GithubAgent
from ralph import (
    Config,
    CONF,
    JsonUtils,
    Logger,
    MemoryManager,
    RalphOrchestrator,
    Shell,
    get_version
)
from hooks import (
    Event,
    EventType,
    HookManager,
    PythonHook,
    ExecutableHook,
    FunctionHook,
)


# ==============================================================================
# SHARED TEST FIXTURES
# ==============================================================================


class TempConfigTestCase(unittest.TestCase):
    """Base test class providing temporary directory and CONF management."""

    config_attrs = ('BASE_DIR', 'ROOT_DIR', 'MEMORY_DIR', 'ARCHIVE_DIR', 'PRD_FILE')

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self._original_conf = {attr: getattr(CONF, attr) for attr in self.config_attrs if hasattr(CONF, attr)}
        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        CONF.ARCHIVE_DIR = self.temp_path / ".ralph" / "archive"
        CONF.PRD_FILE = self.temp_path / ".ralph" / "prd.json"

    def tearDown(self):
        for attr, value in self._original_conf.items():
            setattr(CONF, attr, value)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_mock_agent(self, name="MockAgent", check_deps=True):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = check_deps
        mock_agent.get_name.return_value = name
        return mock_agent

    def create_mock_orchestrator(self, agent_name="mock", mock_agent=None):
        if mock_agent is None:
            mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            return RalphOrchestrator(agent_name=agent_name)


class TempHooksTestCase(unittest.TestCase):
    """Base test class for hook-related tests with temp directories."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.hooks_dir = self.temp_path / "hooks"
        self.hooks_dir.mkdir(parents=True)
        self.manager = HookManager(self.hooks_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_hook_file(self, name, content):
        """Create a hook file in the hooks directory."""
        hook_file = self.hooks_dir / name
        hook_file.write_text(content, encoding='utf-8')
        return hook_file


# ==============================================================================
# CONFIG TESTS
# ==============================================================================


class TestConfig(unittest.TestCase):
    """Tests for Config dataclass default values and ensure_directories()."""

    PATH_TEST_CASES = [
        ("BASE_DIR", Path.cwd(), None),
        ("ROOT_DIR", ".ralph", "BASE_DIR"),
        ("MEMORY_DIR", "memory", "ROOT_DIR"),
        ("ARCHIVE_DIR", "archive", "ROOT_DIR"),
        ("TEMPLATES_DIR", "templates", "ROOT_DIR"),
        ("HOOKS_DIR", "hooks", "ROOT_DIR"),
        ("PRD_FILE", "prd.json", "ROOT_DIR"),
        ("PROGRESS_FILE", "progress.txt", "ROOT_DIR"),
        ("LOG_FILE", "ralph_log.txt", "ROOT_DIR"),
    ]
    SCALAR_TEST_CASES = [("MAX_RETRIES", 3), ("TIMEOUT_SECONDS", 600)]
    CREATED_DIRS = ["ROOT_DIR", "MEMORY_DIR", "ARCHIVE_DIR", "TEMPLATES_DIR", "HOOKS_DIR"]

    def test_default_paths(self):
        config = Config()
        for attr_name, suffix_or_value, base_attr in self.PATH_TEST_CASES:
            with self.subTest(attr=attr_name):
                actual = getattr(config, attr_name)
                expected = suffix_or_value if base_attr is None else getattr(config, base_attr) / suffix_or_value
                self.assertEqual(actual, expected)

    def test_default_scalars(self):
        config = Config()
        for attr_name, expected in self.SCALAR_TEST_CASES:
            with self.subTest(attr=attr_name):
                self.assertEqual(getattr(config, attr_name), expected)

    def test_ensure_directories_creates_all_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = Config(
                BASE_DIR=base, ROOT_DIR=base / ".ralph", MEMORY_DIR=base / ".ralph" / "memory",
                ARCHIVE_DIR=base / ".ralph" / "archive", TEMPLATES_DIR=base / ".ralph" / "templates",
                HOOKS_DIR=base / ".ralph" / "hooks",
            )
            for dir_attr in self.CREATED_DIRS:
                self.assertFalse(getattr(config, dir_attr).exists())
            config.ensure_directories()
            config.ensure_directories()  # Idempotent
            for dir_attr in self.CREATED_DIRS:
                with self.subTest(directory=dir_attr):
                    self.assertTrue(getattr(config, dir_attr).is_dir())


# ==============================================================================
# LOGGER TESTS
# ==============================================================================


class TestLogger(unittest.TestCase):
    """Tests for Logger color, verbose, info, debug, and file logging."""

    def setUp(self):
        Logger.no_color = False
        Logger.verbose = False
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_log.txt"
        self.original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file
        self.held_output = StringIO()
        self.original_stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self.original_stdout
        Logger.no_color = False
        Logger.verbose = False
        CONF.LOG_FILE = self.original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_color_and_verbose_toggles(self):
        for setting, setter, attr in [(True, Logger.set_no_color, 'no_color'), (True, Logger.set_verbose, 'verbose')]:
            with self.subTest(attr=attr):
                setter(setting)
                self.assertEqual(getattr(Logger, attr), setting)
                setter(False)
                self.assertEqual(getattr(Logger, attr), False)

    def test_colors_dict_contains_expected_keys(self):
        self.assertEqual(set(Logger.COLORS.keys()), {"RESET", "GREEN", "RED", "CYAN", "YELLOW", "MAGENTA"})

    def test_info_and_debug_output(self):
        test_cases = [
            # (verbose, no_color, method, message, color, expected_in, expected_not_in)
            (False, True, 'info', "Plain", None, ["Plain"], ["\033["]),
            (False, False, 'info', "Colored", "GREEN", ["\033[92m", "Colored"], []),
            (False, False, 'debug', "Debug", None, [], ["Debug"]),  # Not verbose
            (True, True, 'debug', "Debug", None, ["[DEBUG] Debug"], ["\033["]),
        ]
        for verbose, no_color, method, msg, color, expected_in, expected_not_in in test_cases:
            with self.subTest(method=method, verbose=verbose, no_color=no_color):
                self.held_output = StringIO()
                sys.stdout = self.held_output
                Logger.set_verbose(verbose)
                Logger.set_no_color(no_color)
                getattr(Logger, method)(msg, color) if color else getattr(Logger, method)(msg)
                output = self.held_output.getvalue()
                for exp in expected_in:
                    self.assertIn(exp, output)
                for not_exp in expected_not_in:
                    self.assertNotIn(not_exp, output)
                sys.stdout = self.original_stdout

    def test_file_log_functionality(self):
        test_cases = [
            ("PROMPT", "TAG", "➡️", "TYPE: PROMPT", "TAG: TAG"),
            ("RESPONSE", "TAG", "⬅️", "TYPE: RESPONSE", "TAG: TAG"),
            ("ERROR", "TAG", "❌", "TYPE: ERROR", "TAG: TAG"),
            ("INFO", None, "ℹ️", "TYPE: INFO", "TAG: UNKNOWN"),
        ]
        for log_type, tag, icon, expected_type, expected_tag in test_cases:
            with self.subTest(log_type=log_type):
                if self.log_file.exists():
                    self.log_file.unlink()
                Logger.file_log("Content", log_type, tag) if tag else Logger.file_log("Content", log_type)
                content = self.log_file.read_text(encoding="utf-8")
                for expected in [icon, expected_type, expected_tag, "Content"]:
                    self.assertIn(expected, content)


# ==============================================================================
# SHELL TESTS
# ==============================================================================


class TestShell(unittest.TestCase):
    """Tests for Shell.run() and get_file_tree()."""

    def test_run_basic_commands(self):
        stdout, stderr, code = Shell.run("echo hello")
        self.assertIn("hello", stdout)
        self.assertEqual(code, 0)

        stdout, stderr, code = Shell.run("exit 1")
        self.assertEqual(code, 1)

    def test_run_result_types(self):
        result = Shell.run("echo test")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[0], str)
        self.assertIsInstance(result[1], str)
        self.assertIsInstance(result[2], int)

    def test_run_timeout_behavior(self):
        with patch('ralph.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1)
            stdout, stderr, code = Shell.run("some_command", timeout=1)
        self.assertEqual(stdout, "")
        self.assertIn("Timed Out", stderr)
        self.assertEqual(code, 1)

    def test_get_file_tree_excludes_directories(self):
        result = Shell.get_file_tree()
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)
        for excluded in [".git", ".ralph", "__pycache__"]:
            for line in result.split('\n'):
                entry = line.split('/')[-1].split('\\')[-1].strip().lstrip('├─└│ ')
                self.assertNotEqual(entry, excluded)


# ==============================================================================
# JSON UTILS TESTS
# ==============================================================================


class TestJsonUtils(unittest.TestCase):
    """Tests for JsonUtils.parse() method."""

    VALID_CASES = [
        ('{"key": "value"}', {"key": "value"}),
        ('{"a": 1, "b": 2}', {"a": 1, "b": 2}),
        ('{"outer": {"inner": "value"}}', {"outer": {"inner": "value"}}),
        ('{}', {}),
        ('{"value": null}', {"value": None}),
        ('{"active": true}', {"active": True}),
    ]
    CODE_FENCE_CASES = [
        ('```\n{"key": "value"}\n```', {"key": "value"}),
        ('```json\n{"key": "value"}\n```', {"key": "value"}),
    ]
    SURROUNDING_TEXT_CASES = [
        ('Here is the JSON:\n{"key": "value"}', {"key": "value"}),
        ('{"key": "value"}\nThat was the output.', {"key": "value"}),
    ]
    INVALID_CASES = ['not json at all', '{"key": ', 'just some text']

    def test_parse_valid_json(self):
        for input_text, expected in self.VALID_CASES:
            with self.subTest(input=input_text[:30]):
                self.assertEqual(JsonUtils.parse(input_text), expected)

    def test_parse_code_fence(self):
        for input_text, expected in self.CODE_FENCE_CASES:
            with self.subTest(input=input_text[:30]):
                self.assertEqual(JsonUtils.parse(input_text), expected)

    def test_parse_surrounding_text(self):
        for input_text, expected in self.SURROUNDING_TEXT_CASES:
            with self.subTest(input=input_text[:30]):
                self.assertEqual(JsonUtils.parse(input_text), expected)

    def test_parse_raises_on_invalid(self):
        for input_text in self.INVALID_CASES:
            with self.subTest(input=input_text):
                with self.assertRaises(Exception):
                    JsonUtils.parse(input_text)

    def test_parse_strips_comments(self):
        result = JsonUtils.parse('{"key": "value"// comment\n}')
        self.assertEqual(result, {"key": "value"})


# ==============================================================================
# MEMORY MANAGER TESTS
# ==============================================================================


class TestMemoryManager(TempConfigTestCase):
    """Tests for MemoryManager methods."""

    def setUp(self):
        super().setUp()
        self.memory = MemoryManager()

    def test_validate_memory_structure(self):
        result = self.memory.validate_memory()
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result.keys()), {'valid', 'corrupted', 'empty', 'total'})

    def test_validate_memory_scenarios(self):
        test_cases = [
            # (setup_func, expected_valid, expected_total, desc)
            (lambda: None, True, 0, "empty_dir_not_exists"),
            (lambda: (CONF.MEMORY_DIR.mkdir(parents=True), (CONF.MEMORY_DIR / "test.md").write_text("content", encoding="utf-8")), True, 1, "valid_file"),
            (lambda: (CONF.MEMORY_DIR.mkdir(parents=True), (CONF.MEMORY_DIR / "empty.md").write_text("", encoding="utf-8")), False, 1, "empty_file"),
        ]
        for setup_func, expected_valid, expected_total, desc in test_cases:
            with self.subTest(scenario=desc):
                self.tearDown()
                self.setUp()
                setup_func()
                result = self.memory.validate_memory()
                self.assertEqual(result['valid'], expected_valid)
                self.assertEqual(result['total'], expected_total)

    def test_get_structure(self):
        self.assertEqual(self.memory.get_structure(), "(Memory Empty)")
        CONF.MEMORY_DIR.mkdir(parents=True)
        self.assertEqual(self.memory.get_structure(), "(Memory Empty)")
        (CONF.MEMORY_DIR / "arch.md").write_text("content", encoding="utf-8")
        result = self.memory.get_structure()
        self.assertIn("arch.md", result)
        self.assertTrue(result.startswith("- "))

    def test_extract_test_command(self):
        self.assertEqual(self.memory.extract_test_command(), "pytest")
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `npm run test`\n", encoding="utf-8")
        self.assertEqual(self.memory.extract_test_command(), "npm run test")


# ==============================================================================
# ORCHESTRATOR TESTS
# ==============================================================================


class TestRalphOrchestratorInit(TempConfigTestCase):
    """Tests for RalphOrchestrator initialization."""

    def test_init_creates_agent_and_memory(self):
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIsNotNone(orchestrator.agent)
            self.assertIsInstance(orchestrator.memory, MemoryManager)

    def test_init_calls_agent_setup_methods(self):
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent) as mock_get_agent:
            RalphOrchestrator(agent_name="claude")
            mock_get_agent.assert_called_once()
            mock_agent.check_dependencies.assert_called_once()
            mock_agent.set_logger.assert_called_once_with(Logger)
            mock_agent.set_config.assert_called_once_with(CONF)

    def test_init_exits_when_dependencies_not_satisfied(self):
        mock_agent = self.create_mock_agent(check_deps=False)
        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.sys.exit') as mock_exit:
                RalphOrchestrator(agent_name="mock")
                mock_exit.assert_called_once_with(1)

    def test_init_ensures_directories_exist(self):
        self.assertFalse(CONF.ROOT_DIR.exists())
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            RalphOrchestrator(agent_name="mock")
            for dir_path in [CONF.ROOT_DIR, CONF.MEMORY_DIR, CONF.ARCHIVE_DIR]:
                self.assertTrue(dir_path.exists())

    def test_init_raises_for_unknown_agent(self):
        with self.assertRaises(ValueError) as context:
            get_agent("nonexistent_agent")
        self.assertIn("Unknown agent", str(context.exception))


class TestRalphOrchestratorArchivePrd(TempConfigTestCase):
    """Tests for RalphOrchestrator._archive_prd()."""

    def test_archive_prd_moves_and_preserves_content(self):
        orchestrator = self.create_mock_orchestrator()
        prd_content = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')

        orchestrator._archive_prd()

        self.assertFalse(CONF.PRD_FILE.exists())
        archived = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(encoding='utf-8'), prd_content)
        self.assertRegex(archived[0].name, r"^prd_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json$")

    def test_archive_prd_does_nothing_when_no_prd(self):
        orchestrator = self.create_mock_orchestrator()
        orchestrator._archive_prd()
        self.assertEqual(len(list(CONF.ARCHIVE_DIR.glob("prd_*.json"))), 0)


class TestRalphOrchestratorArchitectVerification(TempConfigTestCase):
    """Tests for RalphOrchestrator.run_architect() ARCH.md verification."""

    def test_run_architect_requires_arch_md(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        orchestrator = self.create_mock_orchestrator(mock_agent=mock_agent)
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")

        # Without ARCH.md - should exit
        with patch('ralph.sys.exit') as mock_exit:
            with patch('ralph.Logger.info'):
                orchestrator.run_architect("test intent")
            mock_exit.assert_called_once_with(1)

        # With ARCH.md - should succeed
        mock_exit.reset_mock()
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch\n", encoding="utf-8")
        with patch('ralph.sys.exit') as mock_exit:
            with patch('ralph.Logger.info'):
                orchestrator.run_architect("test intent")
            mock_exit.assert_not_called()


# ==============================================================================
# CLI ARGUMENT TESTS
# ==============================================================================


class TestCliArguments(unittest.TestCase):
    """Tests for CLI argument parsing."""

    def setUp(self):
        self.agent = list_agents()[0]
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--version", action="version", version="Ralph test")
        self.parser.add_argument("--accept-all", "-y", action="store_true")
        self.parser.add_argument("--verbose", action="store_true")
        self.parser.add_argument("--no-color", action="store_true")
        self.parser.add_argument("--agent", choices=list_agents(), default=self.agent)
        self.parser.add_argument("--no-hooks", action="store_true")
        self.parser.add_argument("--hooks", nargs="+", metavar="NAME")

    def test_phase_arguments(self):
        for phase in ["architect", "planner", "execute", "all"]:
            with self.subTest(phase=phase):
                self.assertEqual(self.parser.parse_args([phase]).phase, phase)
        self.assertEqual(self.parser.parse_args([]).phase, "all")
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["invalid"])

    def test_flag_arguments(self):
        args = self.parser.parse_args(["--accept-all", "--verbose", "--no-color", "--agent", "copilot", "execute"])
        self.assertTrue(args.accept_all)
        self.assertTrue(args.verbose)
        self.assertTrue(args.no_color)
        self.assertEqual(args.agent, "copilot")
        self.assertEqual(args.phase, "execute")

    def test_hooks_arguments(self):
        args = self.parser.parse_args(["--hooks", "hook_a", "hook_b"])
        self.assertEqual(args.hooks, ["hook_a", "hook_b"])
        self.assertFalse(self.parser.parse_args(["--no-hooks"]).hooks)

    def test_get_version_and_list_agents(self):
        self.assertIsInstance(get_version(), str)
        self.assertTrue(len(get_version()) > 0)
        agents = list_agents()
        self.assertIn("claude", agents)
        self.assertIn("copilot", agents)


# ==============================================================================
# AGENT ERROR TESTS
# ==============================================================================


class TestAgentError(unittest.TestCase):
    """Tests for AgentError dataclass."""

    def test_from_exception_captures_all_fields(self):
        try:
            raise ValueError("test error")
        except ValueError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-001")

        self.assertEqual(error.exception_type, "ValueError")
        self.assertEqual(error.message, "test error")
        self.assertEqual(error.agent_name, "TestAgent")
        self.assertEqual(error.task_id, "TASK-001")
        self.assertIn("Traceback", error.stack_trace)
        dt.fromisoformat(error.timestamp)  # Should not raise

    def test_format_log_entry_contains_all_fields(self):
        try:
            raise TypeError("type error")
        except TypeError as e:
            error = AgentError.from_exception(e, "Copilot", "TASK-007")
        log_entry = error.format_log_entry()
        for expected in ["AGENT ERROR", "Agent: Copilot", "Task ID: TASK-007", "TypeError", "type error"]:
            self.assertIn(expected, log_entry)


class TestAgentStructuredErrorReturn(unittest.TestCase):
    """Tests for agent implementations returning structured errors."""

    def test_claude_agent_error_handling(self):
        agent = ClaudeAgent(timeout_seconds=5)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="out", stderr="err")
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test", "TAG")
        self.assertFalse(success)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "CLIError")

    def test_copilot_agent_error_handling(self):
        agent = GithubAgent(timeout_seconds=5)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="out", stderr="err")
            with patch('shutil.which', return_value='/usr/bin/copilot'):
                success, output, error = agent.run("test", "TAG")
        self.assertFalse(success)
        self.assertIsInstance(error, AgentError)


# ==============================================================================
# EVENT TESTS
# ==============================================================================


class TestEvent(unittest.TestCase):
    """Tests for Event dataclass and EventType enum."""

    DOCUMENTED_EVENTS = [
        "PHASE_START", "PHASE_END", "ARCHITECT_START", "ARCHITECT_SUCCESS", "ARCHITECT_FAILURE",
        "PLANNER_START", "PLANNER_SUCCESS", "PLANNER_FAILURE", "EXECUTE_START", "EXECUTE_END",
        "TASK_START", "TASK_SUCCESS", "TASK_FAILURE", "TASK_RETRY",
        "VERIFICATION_START", "VERIFICATION_SUCCESS", "VERIFICATION_FAILURE",
        "PRD_CREATED", "PRD_ARCHIVED", "ERROR",
    ]

    def test_event_types_exist(self):
        for name in self.DOCUMENTED_EVENTS:
            with self.subTest(event=name):
                self.assertTrue(hasattr(EventType, name))
        self.assertEqual(len(EventType), 20)

    def test_event_creation_and_serialization(self):
        event = Event(EventType.TASK_SUCCESS, phase="execute", task_id="TASK-001", metadata={"key": "value"})
        self.assertEqual(event.phase, "execute")
        self.assertEqual(event.task_id, "TASK-001")
        dt.fromisoformat(event.timestamp)

        result = event.to_dict()
        self.assertEqual(result["event_type"], "TASK_SUCCESS")
        self.assertEqual(result["task_id"], "TASK-001")

        parsed = json.loads(event.to_json())
        self.assertEqual(parsed["event_type"], "TASK_SUCCESS")

    def test_event_with_agent_error(self):
        error = AgentError("TestError", "msg", "trace", "2026-01-01T00:00:00", "agent", "TASK-001")
        event = Event(EventType.ERROR, error=error)
        result = event.to_dict()
        self.assertIsNotNone(result["error"])

    def test_all_event_types_serialize(self):
        for event_type in EventType:
            with self.subTest(event_type=event_type):
                event = Event(event_type)
                parsed = json.loads(event.to_json())
                self.assertEqual(parsed["event_type"], event_type.name)


# ==============================================================================
# HOOK MANAGER TESTS
# ==============================================================================


class TestHookManager(TempHooksTestCase):
    """Tests for HookManager class."""

    def test_hook_manager_init_and_enable_disable(self):
        self.assertEqual(self.manager.hooks_dir, self.hooks_dir)
        self.assertTrue(self.manager.is_enabled)
        self.manager.disable()
        self.assertFalse(self.manager.is_enabled)
        self.manager.enable()
        self.assertTrue(self.manager.is_enabled)

    def test_discover_scenarios(self):
        # Empty directory
        self.assertEqual(self.manager.discover(), 0)

        # Skips underscore files
        self.create_hook_file("_private.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        self.assertEqual(self.manager.discover(), 0)

        # Valid hook
        self.create_hook_file("valid.py", 'EVENTS = ["TASK_START"]\nPRIORITY = 50\ndef on_event(e): pass')
        self.assertEqual(HookManager(self.hooks_dir).discover(), 1)

    def test_discover_rejects_invalid_hooks(self):
        invalid_cases = [
            ("no_events.py", 'def on_event(e): pass'),
            ("no_handler.py", 'EVENTS = ["TASK_START"]'),
        ]
        for filename, content in invalid_cases:
            with self.subTest(file=filename):
                self.tearDown()
                self.setUp()
                self.create_hook_file(filename, content)
                mock_logger = MagicMock()
                manager = HookManager(self.hooks_dir, mock_logger)
                self.assertEqual(manager.discover(), 0)
                mock_logger.warning.assert_called()

    def test_emit_only_calls_subscribed_hooks(self):
        self.create_hook_file("task_hook.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        self.manager.discover()
        # PHASE_START should not trigger task_hook
        self.manager.emit(Event(EventType.PHASE_START))  # Should not raise

    def test_emit_calls_hooks_in_priority_order(self):
        import test_ralph
        test_ralph.execution_order = []
        self.create_hook_file("high.py", '''
EVENTS = ["TASK_START"]
PRIORITY = 200
def on_event(e):
    import sys
    sys.modules["test_ralph"].execution_order.append("high")
''')
        self.create_hook_file("low.py", '''
EVENTS = ["TASK_START"]
PRIORITY = 10
def on_event(e):
    import sys
    sys.modules["test_ralph"].execution_order.append("low")
''')
        self.manager.discover()
        self.manager.emit(Event(EventType.TASK_START))
        self.assertEqual(test_ralph.execution_order, ["low", "high"])

    def test_set_enabled_hooks(self):
        self.manager.set_enabled_hooks(None)
        self.assertTrue(self.manager.is_hook_enabled("any"))
        self.manager.set_enabled_hooks([])
        self.assertFalse(self.manager.is_hook_enabled("any"))
        self.manager.set_enabled_hooks(["hook_a"])
        self.assertTrue(self.manager.is_hook_enabled("hook_a"))
        self.assertFalse(self.manager.is_hook_enabled("hook_b"))


class TestHookModification(TempHooksTestCase):
    """Tests for hook event modification."""

    def test_modifying_hook_returns_modified_event(self):
        self.create_hook_file("modifier.py", '''
from hooks import Event
EVENTS = ["TASK_START"]
MODIFIES_DATA = True
def on_event(e):
    return Event(event_type=e.event_type, task_id="MODIFIED-" + (e.task_id or ""))
''')
        self.manager.discover()
        result = self.manager.emit(Event(EventType.TASK_START, task_id="TASK-001"))
        self.assertEqual(result.task_id, "MODIFIED-TASK-001")

    def test_non_modifying_hook_return_ignored(self):
        self.create_hook_file("observer.py", '''
from hooks import Event
EVENTS = ["TASK_START"]
def on_event(e):
    return Event(event_type=e.event_type, task_id="IGNORED")
''')
        self.manager.discover()
        result = self.manager.emit(Event(EventType.TASK_START, task_id="ORIGINAL"))
        self.assertEqual(result.task_id, "ORIGINAL")


# ==============================================================================
# HOOK TYPES TESTS
# ==============================================================================


class TestHookTypes(TempHooksTestCase):
    """Tests for PythonHook, ExecutableHook, and FunctionHook."""

    def test_python_hook_parse_events(self):
        self.assertEqual(PythonHook._parse_events(["TASK_START", "task_success"]),
                         {EventType.TASK_START, EventType.TASK_SUCCESS})
        self.assertEqual(PythonHook._parse_events(["INVALID"]), set())
        self.assertEqual(PythonHook._parse_events([]), set())

    def test_executable_hook_creation(self):
        path = self.temp_path / "test.sh"
        events = {EventType.TASK_START}
        hook = ExecutableHook(path, events, priority=50, timeout=10.0, modifies_data=True)
        self.assertEqual(hook.name, "test.sh")
        self.assertEqual(hook.events, events)
        self.assertEqual(hook.priority, 50)
        self.assertTrue(hook.modifies_data)

    def test_executable_hook_parse_modified_event(self):
        original = Event(EventType.TASK_START, task_id="TASK-001", phase="execute")
        result = ExecutableHook._parse_modified_event('{"task_id": "MODIFIED"}', original)
        self.assertEqual(result.task_id, "MODIFIED")
        self.assertEqual(result.phase, "execute")
        self.assertEqual(result.event_type, EventType.TASK_START)

        self.assertIsNone(ExecutableHook._parse_modified_event("not json", original))

    def test_function_hook_execution(self):
        called = []
        hook = FunctionHook("test", lambda e: called.append(e.task_id), {EventType.TASK_START})
        hook.execute(Event(EventType.TASK_START, task_id="TASK-001"))
        self.assertEqual(called, ["TASK-001"])

    def test_function_hook_modification(self):
        hook = FunctionHook("test", lambda e: Event(e.event_type, task_id="MODIFIED"),
                           {EventType.TASK_START}, modifies_data=True)
        result = hook.execute(Event(EventType.TASK_START, task_id="ORIGINAL"))
        self.assertEqual(result.task_id, "MODIFIED")


# ==============================================================================
# HOOK MANAGER BEHAVIOR TESTS
# ==============================================================================


class TestHookManagerBehavior(TempHooksTestCase):
    """Tests for HookManager error isolation, concurrency, and lifecycle."""

    def test_exception_isolation(self):
        execution_log = []
        self.manager.register_hook("failing", lambda e: (_ for _ in ()).throw(RuntimeError()),
                                   ["TASK_START"], priority=10)
        self.manager.register_hook("succeeding", lambda e: execution_log.append("success"),
                                   ["TASK_START"], priority=20)
        self.manager.emit(Event(EventType.TASK_START))
        self.assertIn("success", execution_log)

    def test_concurrent_emit_calls(self):
        results = []
        lock = threading.Lock()

        def handler(e):
            with lock:
                results.append(e.task_id)

        self.manager.register_hook("handler", handler, ["TASK_START"])
        threads = [threading.Thread(target=lambda i=i: self.manager.emit(Event(EventType.TASK_START, task_id=f"T-{i}")))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 10)

    def test_lifecycle_operations(self):
        self.manager.register_hook("hook1", lambda e: None, ["TASK_START"])
        self.assertEqual(len(self.manager.get_all_hooks()), 1)
        self.manager.clear_hooks()
        self.assertEqual(len(self.manager.get_all_hooks()), 0)
        self.assertFalse(self.manager.unregister_hook("nonexistent"))


class TestHookConfigFile(TempHooksTestCase):
    """Tests for hook configuration file loading."""

    def setUp(self):
        super().setUp()
        self.scripts_dir = self.temp_path / "scripts"
        self.scripts_dir.mkdir(parents=True)

    def _create_script(self, name):
        script = self.scripts_dir / name
        script.write_text("#!/bin/bash\nexit 0", encoding='utf-8')
        return script

    def _create_config(self, content):
        (self.hooks_dir / "hooks.yaml").write_text(content, encoding='utf-8')

    def test_config_loads_hooks(self):
        script = self._create_script("notify.bat")
        self._create_config(f'''
hooks:
  - name: my_notifier
    path: {script}
    events: [TASK_START]
    priority: 50
    timeout: 10.0
    modifies_data: true
''')
        manager = HookManager(self.hooks_dir)
        self.assertEqual(manager.discover(), 1)
        hooks = manager.get_hooks_for_event(EventType.TASK_START)
        self.assertEqual(hooks[0].name, "my_notifier")
        self.assertEqual(hooks[0].priority, 50)
        self.assertTrue(hooks[0].modifies_data)


# ==============================================================================
# PROGRESS RETRY CONTEXT TESTS
# ==============================================================================


class TestProgressRetryContext(TempConfigTestCase):
    """Tests for structured failure information in progress.txt."""

    def test_record_failure_with_agent_error(self):
        Logger.set_verbose(False)
        mock_agent = self.create_mock_agent()
        progress_file = self.temp_path / "progress.txt"
        original = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orch = RalphOrchestrator.__new__(RalphOrchestrator)
                    orch.agent = mock_agent
                    orch.hooks = MagicMock()
                    error = AgentError("VerificationError", "Test failed", "trace", "2024-01-01T12:00:00", "Agent", "TASK-001")
                    orch._record_failure(0, "Verification Failed", "output", agent_error=error)
                    content = progress_file.read_text(encoding='utf-8')
                    for expected in ["Structured Error Context", "VerificationError", "Test failed", "TASK-001"]:
                        self.assertIn(expected, content)
        finally:
            CONF.PROGRESS_FILE = original


# ==============================================================================
# EVENT LIFECYCLE TESTS
# ==============================================================================


class TestEventLifecycle(TempConfigTestCase):
    """Tests for event emission during orchestrator phases."""

    def setUp(self):
        super().setUp()
        self.hooks_dir = self.temp_path / ".ralph" / "hooks"
        self.hooks_dir.mkdir(parents=True)
        self.emitted_events = []

    def _create_mock_orchestrator(self):
        mock_conf = MagicMock()
        mock_conf.HOOKS_DIR = self.hooks_dir
        mock_conf.MEMORY_DIR = CONF.MEMORY_DIR
        mock_conf.ROOT_DIR = CONF.ROOT_DIR
        mock_conf.ARCHIVE_DIR = CONF.ARCHIVE_DIR
        mock_conf.TEMPLATES_DIR = self.temp_path / ".ralph" / "templates"
        mock_conf.PRD_FILE = CONF.PRD_FILE
        mock_conf.PROGRESS_FILE = self.temp_path / ".ralph" / "progress.txt"
        mock_conf.LOG_FILE = self.temp_path / ".ralph" / "ralph_log.txt"
        mock_conf.TIMEOUT_SECONDS = 600
        mock_conf.MAX_RETRIES = 3
        mock_conf.BASE_DIR = self.temp_path
        mock_conf.ensure_directories = MagicMock()
        mock_conf.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "test_agent"

        with patch('ralph.CONF', mock_conf):
            with patch('ralph.get_agent', return_value=mock_agent):
                orch = RalphOrchestrator(enable_hooks=True)
                original_emit = orch.hooks.emit
                orch.hooks.emit = lambda e: (self.emitted_events.append(e), original_emit(e))[1]
                return orch, mock_agent, mock_conf

    def test_architect_phase_events(self):
        orch, mock_agent, mock_conf = self._create_mock_orchestrator()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("test", encoding='utf-8')
        (self.temp_path / "ARCH.md").write_text("test", encoding='utf-8')

        with patch('ralph.CONF', mock_conf):
            orch.run_architect("test")

        types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.PHASE_START, types)
        self.assertIn(EventType.ARCHITECT_START, types)
        self.assertIn(EventType.ARCHITECT_SUCCESS, types)
        self.assertIn(EventType.PHASE_END, types)

    def test_planner_phase_events(self):
        orch, mock_agent, mock_conf = self._create_mock_orchestrator()
        prd = {"id": "PRD-001", "description": "test", "userStories": []}
        mock_agent.run.return_value = (True, json.dumps(prd), None)

        with patch('ralph.CONF', mock_conf):
            orch.run_planner("test")

        types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.PLANNER_START, types)
        self.assertIn(EventType.PRD_CREATED, types)


if __name__ == "__main__":
    unittest.main()
