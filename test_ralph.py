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
    TemplateManager,
    get_version,
    main,
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
        Logger.verbosity = 0
        Logger.quiet = False
        Logger.no_emoji = False
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
        Logger.verbosity = 0
        Logger.quiet = False
        Logger.no_emoji = False
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

    def test_verbosity_levels(self):
        """Test -v/-vv/-vvv verbosity levels."""
        test_cases = [
            # (verbosity_level, method, should_output)
            (0, 'debug', False),
            (1, 'debug', True),
            (0, 'trace', False),
            (1, 'trace', False),
            (2, 'trace', True),
            (0, 'ultra', False),
            (2, 'ultra', False),
            (3, 'ultra', True),
        ]
        for verbosity, method, should_output in test_cases:
            with self.subTest(verbosity=verbosity, method=method):
                self.held_output = StringIO()
                sys.stdout = self.held_output
                Logger.set_verbosity(verbosity)
                Logger.set_no_color(True)
                getattr(Logger, method)("test message")
                output = self.held_output.getvalue()
                if should_output:
                    self.assertIn("test message", output)
                else:
                    self.assertEqual(output, "")
                sys.stdout = self.original_stdout

    def test_set_verbosity_syncs_verbose_attribute(self):
        """Test that set_verbosity syncs the verbose attribute for backwards compat."""
        Logger.set_verbosity(0)
        self.assertFalse(Logger.verbose)
        self.assertEqual(Logger.verbosity, 0)

        Logger.set_verbosity(1)
        self.assertTrue(Logger.verbose)
        self.assertEqual(Logger.verbosity, 1)

        Logger.set_verbosity(3)
        self.assertTrue(Logger.verbose)
        self.assertEqual(Logger.verbosity, 3)

    def test_set_verbosity_clamps_values(self):
        """Test that set_verbosity clamps values to 0-3 range."""
        Logger.set_verbosity(-5)
        self.assertEqual(Logger.verbosity, 0)

        Logger.set_verbosity(10)
        self.assertEqual(Logger.verbosity, 3)

    def test_quiet_mode_suppresses_info(self):
        """Test that quiet mode suppresses info output."""
        self.held_output = StringIO()
        sys.stdout = self.held_output
        Logger.set_no_color(True)
        Logger.set_quiet(True)

        Logger.info("This should be suppressed")
        self.assertEqual(self.held_output.getvalue(), "")

        sys.stdout = self.original_stdout

    def test_quiet_mode_allows_warning_and_error(self):
        """Test that quiet mode still allows warning and error output."""
        self.held_output = StringIO()
        sys.stdout = self.held_output
        Logger.set_no_color(True)
        Logger.set_quiet(True)

        Logger.warning("This warning should show")
        Logger.error("This error should show")

        output = self.held_output.getvalue()
        self.assertIn("This warning should show", output)
        self.assertIn("This error should show", output)

        sys.stdout = self.original_stdout

    def test_quiet_mode_suppresses_debug_trace_ultra(self):
        """Test that quiet mode suppresses all verbosity levels."""
        self.held_output = StringIO()
        sys.stdout = self.held_output
        Logger.set_no_color(True)
        Logger.set_verbosity(3)  # Max verbosity
        Logger.set_quiet(True)

        Logger.debug("debug")
        Logger.trace("trace")
        Logger.ultra("ultra")

        self.assertEqual(self.held_output.getvalue(), "")

        sys.stdout = self.original_stdout

    def test_no_emoji_replaces_emojis(self):
        """Test that no_emoji mode replaces emojis with text."""
        self.held_output = StringIO()
        sys.stdout = self.held_output
        Logger.set_no_color(True)
        Logger.set_no_emoji(True)

        Logger.info("🤖 Robot says ✅ done")

        output = self.held_output.getvalue()
        self.assertIn("[BOT]", output)
        self.assertIn("[OK]", output)
        self.assertNotIn("🤖", output)
        self.assertNotIn("✅", output)

        sys.stdout = self.original_stdout

    def test_no_emoji_preserves_regular_text(self):
        """Test that no_emoji mode doesn't affect regular text."""
        self.held_output = StringIO()
        sys.stdout = self.held_output
        Logger.set_no_color(True)
        Logger.set_no_emoji(True)

        Logger.info("Regular text without emojis")

        output = self.held_output.getvalue()
        self.assertIn("Regular text without emojis", output)

        sys.stdout = self.original_stdout

    def test_strip_emoji_covers_all_used_emojis(self):
        """Test that _strip_emoji handles all emojis used in the codebase."""
        emojis_used = ["🤖", "🕵️", "🧠", "🚀", "✅", "❌", "⚠️", "▶️",
                       "🔒", "🛑", "⏭️", "📋", "📦", "🎉", "➡️", "⬅️", "ℹ️", "❓"]
        for emoji in emojis_used:
            with self.subTest(emoji=emoji):
                result = Logger._strip_emoji(f"Test {emoji} message")
                self.assertNotIn(emoji, result)
                self.assertIn("Test", result)
                self.assertIn("message", result)


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
        self.parser.add_argument("-v", "--verbose", action="count", default=0)
        self.parser.add_argument("--quiet", "-q", action="store_true")
        color_group = self.parser.add_mutually_exclusive_group()
        color_group.add_argument("--no-color", action="store_true")
        color_group.add_argument("--color", action="store_true")
        self.parser.add_argument("--no-emoji", action="store_true")
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
        args = self.parser.parse_args(["--accept-all", "-v", "--no-color", "--agent", "copilot", "execute"])
        self.assertTrue(args.accept_all)
        self.assertEqual(args.verbose, 1)
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

    def test_verbosity_levels_v(self):
        """Test -v increases verbosity to 1."""
        args = self.parser.parse_args(["-v"])
        self.assertEqual(args.verbose, 1)

    def test_verbosity_levels_vv(self):
        """Test -vv increases verbosity to 2."""
        args = self.parser.parse_args(["-vv"])
        self.assertEqual(args.verbose, 2)

    def test_verbosity_levels_vvv(self):
        """Test -vvv increases verbosity to 3."""
        args = self.parser.parse_args(["-vvv"])
        self.assertEqual(args.verbose, 3)

    def test_verbosity_long_form(self):
        """Test --verbose --verbose --verbose increases verbosity to 3."""
        args = self.parser.parse_args(["--verbose", "--verbose", "--verbose"])
        self.assertEqual(args.verbose, 3)

    def test_quiet_flag(self):
        """Test --quiet/-q flag."""
        args = self.parser.parse_args(["--quiet"])
        self.assertTrue(args.quiet)

        args = self.parser.parse_args(["-q"])
        self.assertTrue(args.quiet)

    def test_no_emoji_flag(self):
        """Test --no-emoji flag."""
        args = self.parser.parse_args(["--no-emoji"])
        self.assertTrue(args.no_emoji)

    def test_color_no_color_mutually_exclusive(self):
        """Test --color and --no-color are mutually exclusive."""
        args = self.parser.parse_args(["--color"])
        self.assertTrue(args.color)
        self.assertFalse(args.no_color)

        args = self.parser.parse_args(["--no-color"])
        self.assertTrue(args.no_color)
        self.assertFalse(args.color)

        # Both together should fail
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["--color", "--no-color"])

    def test_all_ux_flags_combined(self):
        """Test all UX flags can be used together."""
        args = self.parser.parse_args(["-vvv", "-q", "--no-color", "--no-emoji", "-y", "execute"])
        self.assertEqual(args.verbose, 3)
        self.assertTrue(args.quiet)
        self.assertTrue(args.no_color)
        self.assertTrue(args.no_emoji)
        self.assertTrue(args.accept_all)
        self.assertEqual(args.phase, "execute")


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


class TestTemplateManagerDeveloperTemplate(unittest.TestCase):
    """Tests for TemplateManager developer template with mandatory instructions."""

    def test_developer_template_contains_mandatory_instructions_header(self):
        """Given developer template exists, when loaded, then it contains MANDATORY INSTRUCTIONS header."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        self.assertIn("## MANDATORY INSTRUCTIONS (MUST FOLLOW)", template)

    def test_developer_template_contains_mandatory_instructions_footer(self):
        """Given developer template exists, when loaded, then it contains END MANDATORY INSTRUCTIONS marker."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        self.assertIn("## END MANDATORY INSTRUCTIONS", template)

    def test_developer_template_contains_must_strictly_adhere_instruction(self):
        """Given developer template exists, when loaded, then it instructs agent to strictly adhere."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        self.assertIn("MUST strictly adhere", template)

    def test_developer_template_contains_user_context_placeholder(self):
        """Given developer template exists, when loaded, then it contains user_context placeholder."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        self.assertIn("{{user_context}}", template)

    def test_developer_template_user_context_inside_mandatory_section(self):
        """Given developer template, when rendered, then user_context is within mandatory instructions section."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        start_marker = "## MANDATORY INSTRUCTIONS (MUST FOLLOW)"
        end_marker = "## END MANDATORY INSTRUCTIONS"
        start_idx = template.find(start_marker)
        end_idx = template.find(end_marker)
        context_idx = template.find("{{user_context}}")
        self.assertGreater(context_idx, start_idx)
        self.assertLess(context_idx, end_idx)

    def test_developer_template_renders_user_preferences(self):
        """Given user preferences, when developer template is rendered, then preferences appear in mandatory section."""
        user_prefs = "Always use snake_case for variables"
        rendered = TemplateManager.render(
            "developer.txt",
            task_id="TASK-001",
            task_description="Test task",
            memory_tree="(Memory Empty)",
            user_context=user_prefs,
            test_cmd="pytest",
            prev_errors=""
        )
        self.assertIn(user_prefs, rendered)
        self.assertIn("## MANDATORY INSTRUCTIONS (MUST FOLLOW)", rendered)
        self.assertIn("## END MANDATORY INSTRUCTIONS", rendered)

    def test_developer_template_no_longer_uses_prefs_label(self):
        """Given developer template, when loaded, then it does not use the old PREFS label."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        self.assertNotIn("PREFS:", template)

    def test_developer_template_user_context_appears_before_context(self):
        """Given developer template, when loaded, then user_context appears before CONTEXT section for higher priority."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        mandatory_section_start = template.find("## MANDATORY INSTRUCTIONS")
        context_section = template.find("CONTEXT:")
        self.assertGreater(context_section, mandatory_section_start,
            "User context (MANDATORY INSTRUCTIONS) should appear before CONTEXT for higher priority")

    def test_developer_template_user_context_immediately_after_task(self):
        """Given developer template, when loaded, then MANDATORY INSTRUCTIONS appears right after TASK section."""
        template = TemplateManager.DEFAULT_TEMPLATES["developer.txt"]
        task_idx = template.find("TASK:")
        mandatory_idx = template.find("## MANDATORY INSTRUCTIONS")
        context_idx = template.find("CONTEXT:")
        # MANDATORY INSTRUCTIONS should be between TASK and CONTEXT
        self.assertGreater(mandatory_idx, task_idx)
        self.assertLess(mandatory_idx, context_idx)


class TestPromptMdValidation(unittest.TestCase):
    """Tests for prompt.md content validation in _execute_task."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.original_base_dir = CONF.BASE_DIR
        self.original_root_dir = CONF.ROOT_DIR
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_archive_dir = CONF.ARCHIVE_DIR
        self.original_templates_dir = CONF.TEMPLATES_DIR
        self.original_prd_file = CONF.PRD_FILE
        self.original_progress_file = CONF.PROGRESS_FILE
        self.original_log_file = CONF.LOG_FILE

        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
        CONF.MEMORY_DIR = CONF.ROOT_DIR / "memory"
        CONF.ARCHIVE_DIR = CONF.ROOT_DIR / "archive"
        CONF.TEMPLATES_DIR = CONF.ROOT_DIR / "templates"
        CONF.PRD_FILE = CONF.ROOT_DIR / "prd.json"
        CONF.PROGRESS_FILE = CONF.ROOT_DIR / "progress.txt"
        CONF.LOG_FILE = CONF.ROOT_DIR / "ralph_log.txt"

    def tearDown(self):
        CONF.BASE_DIR = self.original_base_dir
        CONF.ROOT_DIR = self.original_root_dir
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.ARCHIVE_DIR = self.original_archive_dir
        CONF.TEMPLATES_DIR = self.original_templates_dir
        CONF.PRD_FILE = self.original_prd_file
        CONF.PROGRESS_FILE = self.original_progress_file
        CONF.LOG_FILE = self.original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_empty_prompt_md_uses_default_context(self):
        """Given empty prompt.md, when _execute_task runs, then default user context is used."""
        CONF.ensure_directories()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        # Create empty prompt.md
        prompt_md_path = CONF.BASE_DIR / "prompt.md"
        prompt_md_path.write_text("", encoding="utf-8")

        # Create PRD
        prd = {"id": "PRD-001", "description": "Test", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding="utf-8")

        with patch("ralph.get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "mock"
            mock_agent.run.return_value = (True, "STATUS: SUCCESS", None)
            mock_get_agent.return_value = mock_agent

            with patch("ralph.Shell.run") as mock_shell:
                mock_shell.return_value = ("", "", 0)

                with patch("ralph.Logger.warning") as mock_warning:
                    orchestrator = RalphOrchestrator(agent_name="mock")
                    orchestrator._execute_task(prd, prd["userStories"][0], "pytest")

                    # Verify warning was logged
                    mock_warning.assert_called_with(
                        "prompt.md exists but is empty, using default user context."
                    )

    def test_whitespace_only_prompt_md_uses_default_context(self):
        """Given prompt.md with only whitespace, when _execute_task runs, then default user context is used."""
        CONF.ensure_directories()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        # Create whitespace-only prompt.md
        prompt_md_path = CONF.BASE_DIR / "prompt.md"
        prompt_md_path.write_text("   \n\t\n   ", encoding="utf-8")

        prd = {"id": "PRD-001", "description": "Test", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding="utf-8")

        with patch("ralph.get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "mock"
            mock_agent.run.return_value = (True, "STATUS: SUCCESS", None)
            mock_get_agent.return_value = mock_agent

            with patch("ralph.Shell.run") as mock_shell:
                mock_shell.return_value = ("", "", 0)

                with patch("ralph.Logger.warning") as mock_warning:
                    orchestrator = RalphOrchestrator(agent_name="mock")
                    orchestrator._execute_task(prd, prd["userStories"][0], "pytest")

                    mock_warning.assert_called_with(
                        "prompt.md exists but is empty, using default user context."
                    )

    def test_non_empty_prompt_md_content_is_used(self):
        """Given non-empty prompt.md, when _execute_task runs, then its content is used."""
        CONF.ensure_directories()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        # Create non-empty prompt.md
        prompt_md_path = CONF.BASE_DIR / "prompt.md"
        prompt_md_path.write_text("Use snake_case for all variables", encoding="utf-8")

        prd = {"id": "PRD-001", "description": "Test", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding="utf-8")

        captured_prompt = []

        with patch("ralph.get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "mock"

            def capture_run(prompt, tag):
                captured_prompt.append(prompt)
                return (True, "STATUS: SUCCESS", None)

            mock_agent.run.side_effect = capture_run
            mock_get_agent.return_value = mock_agent

            with patch("ralph.Shell.run") as mock_shell:
                mock_shell.return_value = ("", "", 0)

                with patch("ralph.Logger.warning") as mock_warning:
                    orchestrator = RalphOrchestrator(agent_name="mock")
                    orchestrator._execute_task(prd, prd["userStories"][0], "pytest")

                    # Verify warning was NOT called
                    mock_warning.assert_not_called()

        # Verify the prompt contains the user context from prompt.md
        self.assertEqual(len(captured_prompt), 1)
        self.assertIn("Use snake_case for all variables", captured_prompt[0])

    def test_missing_prompt_md_uses_default_context(self):
        """Given no prompt.md file, when _execute_task runs, then default user context is used."""
        CONF.ensure_directories()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        # Do NOT create prompt.md
        prd = {"id": "PRD-001", "description": "Test", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding="utf-8")

        captured_prompt = []

        with patch("ralph.get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "mock"

            def capture_run(prompt, tag):
                captured_prompt.append(prompt)
                return (True, "STATUS: SUCCESS", None)

            mock_agent.run.side_effect = capture_run
            mock_get_agent.return_value = mock_agent

            with patch("ralph.Shell.run") as mock_shell:
                mock_shell.return_value = ("", "", 0)

                with patch("ralph.Logger.warning") as mock_warning:
                    orchestrator = RalphOrchestrator(agent_name="mock")
                    orchestrator._execute_task(prd, prd["userStories"][0], "pytest")

                    # No warning because file doesn't exist (different from empty file)
                    mock_warning.assert_not_called()

        # Verify the prompt contains the default context
        self.assertEqual(len(captured_prompt), 1)
        self.assertIn("No specific user preferences provided", captured_prompt[0])

    def test_prompt_md_variables_not_replaced_when_empty(self):
        """Given empty prompt.md, when _execute_task runs, then variable placeholders are not in output."""
        CONF.ensure_directories()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        # Create empty prompt.md
        prompt_md_path = CONF.BASE_DIR / "prompt.md"
        prompt_md_path.write_text("", encoding="utf-8")

        prd = {"id": "PRD-001", "description": "Test PRD", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding="utf-8")

        captured_prompt = []

        with patch("ralph.get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "mock"

            def capture_run(prompt, tag):
                captured_prompt.append(prompt)
                return (True, "STATUS: SUCCESS", None)

            mock_agent.run.side_effect = capture_run
            mock_get_agent.return_value = mock_agent

            with patch("ralph.Shell.run") as mock_shell:
                mock_shell.return_value = ("", "", 0)

                with patch("ralph.Logger.warning"):
                    orchestrator = RalphOrchestrator(agent_name="mock")
                    orchestrator._execute_task(prd, prd["userStories"][0], "pytest")

        # Verify the default context is used (no variable placeholders)
        self.assertEqual(len(captured_prompt), 1)
        self.assertIn("No specific user preferences provided", captured_prompt[0])
        # Make sure no unreplaced variables from prompt.md
        self.assertNotIn("{{PRD_ID}}", captured_prompt[0])


if __name__ == "__main__":
    unittest.main()
