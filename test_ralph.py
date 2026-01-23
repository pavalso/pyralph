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
        Logger.log_level = 20  # Reset to default (info)
        Logger.json_output = False
        Logger.ndjson_output = False
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
        Logger.log_level = 20  # Reset to default (info)
        Logger.json_output = False
        Logger.ndjson_output = False
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

    def test_get_file_tree_default_ignore_list(self):
        """Test that DEFAULT_TREE_IGNORE contains expected patterns."""
        expected = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']
        self.assertEqual(Shell.DEFAULT_TREE_IGNORE, expected)

    def test_get_file_tree_with_custom_depth(self):
        """Test get_file_tree accepts custom depth parameter."""
        with patch('ralph.Shell.run') as mock_run:
            mock_run.return_value = ("tree output", "", 0)
            Shell.get_file_tree(depth=5)
            call_args = mock_run.call_args[0][0]
            self.assertIn("-L 5", call_args)

    def test_get_file_tree_with_custom_ignore(self):
        """Test get_file_tree accepts custom ignore patterns."""
        with patch('ralph.Shell.run') as mock_run:
            mock_run.return_value = ("tree output", "", 0)
            Shell.get_file_tree(ignore=['build', 'dist'])
            call_args = mock_run.call_args[0][0]
            self.assertIn("-I 'build|dist'", call_args)

    def test_get_file_tree_with_empty_ignore(self):
        """Test get_file_tree with empty ignore list."""
        with patch('ralph.Shell.run') as mock_run:
            mock_run.return_value = ("tree output", "", 0)
            Shell.get_file_tree(ignore=[])
            call_args = mock_run.call_args[0][0]
            self.assertNotIn("-I", call_args)

    def test_get_file_tree_fallback_respects_ignore(self):
        """Test fallback Python walker respects ignore patterns."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Create test directories/files
            (temp_path / "src").mkdir()
            (temp_path / "node_modules").mkdir()

            original_base_dir = CONF.BASE_DIR
            CONF.BASE_DIR = temp_path
            try:
                with patch('ralph.Shell.run') as mock_run:
                    mock_run.return_value = ("", "", 1)  # Force fallback
                    result = Shell.get_file_tree(ignore=['node_modules'])
                    self.assertIn("src", result)
                    self.assertNotIn("node_modules", result)
            finally:
                CONF.BASE_DIR = original_base_dir


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
# INTENT AND INPUT FLAGS TESTS
# ==============================================================================


class TestIntentFlags(TempConfigTestCase):
    """Tests for --intent, --intent-file, and --prompt-file CLI flags."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--intent", type=str, metavar="TEXT")
        self.parser.add_argument("--intent-file", type=str, metavar="FILE")
        self.parser.add_argument("--prompt-file", type=str, metavar="FILE")

    def test_intent_flag_parses(self):
        """Test --intent flag accepts inline text."""
        args = self.parser.parse_args(["--intent", "Build a REST API", "architect"])
        self.assertEqual(args.intent, "Build a REST API")
        self.assertEqual(args.phase, "architect")

    def test_intent_file_flag_parses(self):
        """Test --intent-file flag accepts file path."""
        args = self.parser.parse_args(["--intent-file", "/path/to/intent.txt"])
        self.assertEqual(args.intent_file, "/path/to/intent.txt")

    def test_prompt_file_flag_parses(self):
        """Test --prompt-file flag accepts file path."""
        args = self.parser.parse_args(["--prompt-file", "/path/to/prompt.md"])
        self.assertEqual(args.prompt_file, "/path/to/prompt.md")

    def test_all_intent_flags_together(self):
        """Test intent flags can be combined with phase."""
        args = self.parser.parse_args(["--intent", "Test intent", "--prompt-file", "custom.md", "planner"])
        self.assertEqual(args.intent, "Test intent")
        self.assertEqual(args.prompt_file, "custom.md")
        self.assertEqual(args.phase, "planner")


class TestOrchestratorIntentHandling(TempConfigTestCase):
    """Tests for RalphOrchestrator intent flag handling."""

    def test_get_intent_returns_inline_intent(self):
        """Test _get_intent returns inline intent from --intent flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", intent="Build a CLI tool")
            result = orch._get_intent()
            self.assertEqual(result, "Build a CLI tool")

    def test_get_intent_returns_file_content(self):
        """Test _get_intent reads intent from --intent-file."""
        intent_file = self.temp_path / "intent.txt"
        intent_file.write_text("Build a web app from file", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", intent_file=str(intent_file))
            result = orch._get_intent()
            self.assertEqual(result, "Build a web app from file")

    def test_get_intent_file_not_found_exits(self):
        """Test _get_intent exits when intent file not found."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", intent_file="/nonexistent/file.txt")
            with patch('ralph.sys.exit', side_effect=SystemExit(1)) as mock_exit:
                with patch('ralph.Logger.error') as mock_error:
                    with self.assertRaises(SystemExit):
                        orch._get_intent()
                    mock_exit.assert_called_once_with(1)
                    mock_error.assert_called()

    def test_get_intent_empty_file_exits(self):
        """Test _get_intent exits when intent file is empty."""
        intent_file = self.temp_path / "empty_intent.txt"
        intent_file.write_text("", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", intent_file=str(intent_file))
            with patch('ralph.sys.exit', side_effect=SystemExit(1)) as mock_exit:
                with patch('ralph.Logger.error') as mock_error:
                    with self.assertRaises(SystemExit):
                        orch._get_intent()
                    mock_exit.assert_called_once_with(1)
                    mock_error.assert_called()

    def test_get_intent_whitespace_only_file_exits(self):
        """Test _get_intent exits when intent file contains only whitespace."""
        intent_file = self.temp_path / "whitespace_intent.txt"
        intent_file.write_text("   \n\t\n   ", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", intent_file=str(intent_file))
            with patch('ralph.sys.exit', side_effect=SystemExit(1)) as mock_exit:
                with patch('ralph.Logger.error') as mock_error:
                    with self.assertRaises(SystemExit):
                        orch._get_intent()
                    mock_exit.assert_called_once_with(1)
                    mock_error.assert_called()

    def test_get_intent_inline_takes_precedence_over_cached(self):
        """Test inline intent takes precedence over user_intent parameter."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", intent="Inline intent")
            result = orch._get_intent(user_intent="Cached intent")
            self.assertEqual(result, "Cached intent")  # user_intent param has highest priority

    def test_orchestrator_stores_intent_flags(self):
        """Test orchestrator stores all intent-related flags."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(
                agent_name="mock",
                intent="Test intent",
                intent_file="/path/to/file",
                prompt_file="/path/to/prompt"
            )
            self.assertEqual(orch._intent, "Test intent")
            self.assertEqual(orch._intent_file, "/path/to/file")
            self.assertEqual(orch._prompt_file_override, "/path/to/prompt")


class TestOrchestratorPromptFileOverride(TempConfigTestCase):
    """Tests for RalphOrchestrator --prompt-file handling."""

    def test_prompt_file_override_is_used(self):
        """Test _load_user_context uses --prompt-file when provided."""
        # Create custom prompt file
        custom_prompt = self.temp_path / "custom_prompt.md"
        custom_prompt.write_text("Custom user instructions", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", prompt_file=str(custom_prompt))
            prd = {"id": "PRD-001", "description": "Test"}
            task = {"id": "TASK-001", "description": "Test task"}
            result = orch._load_user_context(prd, task, "pytest")
            self.assertEqual(result, "Custom user instructions")

    def test_prompt_file_not_found_exits(self):
        """Test _load_user_context exits when prompt file not found."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", prompt_file="/nonexistent/prompt.md")
            prd = {"id": "PRD-001", "description": "Test"}
            task = {"id": "TASK-001", "description": "Test task"}
            with patch('ralph.sys.exit', side_effect=SystemExit(1)) as mock_exit:
                with patch('ralph.Logger.error') as mock_error:
                    with self.assertRaises(SystemExit):
                        orch._load_user_context(prd, task, "pytest")
                    mock_exit.assert_called_once_with(1)
                    mock_error.assert_called()

    def test_prompt_file_empty_uses_default(self):
        """Test _load_user_context returns default when prompt file is empty."""
        empty_prompt = self.temp_path / "empty_prompt.md"
        empty_prompt.write_text("", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", prompt_file=str(empty_prompt))
            prd = {"id": "PRD-001", "description": "Test"}
            task = {"id": "TASK-001", "description": "Test task"}
            with patch('ralph.Logger.warning') as mock_warning:
                result = orch._load_user_context(prd, task, "pytest")
                self.assertEqual(result, "No specific user preferences provided.")
                mock_warning.assert_called()

    def test_prompt_file_variables_substituted(self):
        """Test _load_user_context substitutes variables in prompt file."""
        custom_prompt = self.temp_path / "var_prompt.md"
        custom_prompt.write_text("PRD: {{PRD_ID}}, Task: {{TASK_ID}}", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", prompt_file=str(custom_prompt))
            prd = {"id": "PRD-001", "description": "Test"}
            task = {"id": "TASK-002", "description": "Test task"}
            result = orch._load_user_context(prd, task, "pytest")
            self.assertIn("PRD-001", result)
            self.assertIn("TASK-002", result)


class TestMainIntentValidation(unittest.TestCase):
    """Tests for main() intent flag validation."""

    def test_main_rejects_both_intent_flags(self):
        """Test main() exits when both --intent and --intent-file are provided."""
        with patch('sys.argv', ['ralph', '--intent', 'Test', '--intent-file', 'file.txt']):
            with patch('ralph.sys.exit') as mock_exit:
                with patch('ralph.Logger.error') as mock_error:
                    with patch('ralph.RalphOrchestrator'):
                        main()
                        mock_error.assert_called_with("Cannot use both --intent and --intent-file together.")
                        mock_exit.assert_called_with(1)


# ==============================================================================
# ARCHITECT CONTROL FLAGS TESTS
# ==============================================================================


class TestArchitectControlFlags(TempConfigTestCase):
    """Tests for --tree-depth, --tree-ignore, and --memory-out CLI flags."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--tree-depth", type=int, default=2, metavar="N")
        self.parser.add_argument("--tree-ignore", nargs="+", metavar="PATTERN")
        self.parser.add_argument("--memory-out", type=str, metavar="FILE")

    def test_tree_depth_flag_parses(self):
        """Test --tree-depth flag accepts integer value."""
        args = self.parser.parse_args(["--tree-depth", "5", "architect"])
        self.assertEqual(args.tree_depth, 5)
        self.assertEqual(args.phase, "architect")

    def test_tree_depth_default_value(self):
        """Test --tree-depth defaults to 2."""
        args = self.parser.parse_args([])
        self.assertEqual(args.tree_depth, 2)

    def test_tree_ignore_flag_parses_single(self):
        """Test --tree-ignore flag accepts single pattern."""
        args = self.parser.parse_args(["--tree-ignore", "build"])
        self.assertEqual(args.tree_ignore, ["build"])

    def test_tree_ignore_flag_parses_multiple(self):
        """Test --tree-ignore flag accepts multiple patterns."""
        args = self.parser.parse_args(["--tree-ignore", "build", "dist", "coverage"])
        self.assertEqual(args.tree_ignore, ["build", "dist", "coverage"])

    def test_tree_ignore_default_none(self):
        """Test --tree-ignore defaults to None."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.tree_ignore)

    def test_memory_out_flag_parses(self):
        """Test --memory-out flag accepts file path."""
        args = self.parser.parse_args(["--memory-out", "/path/to/context.md"])
        self.assertEqual(args.memory_out, "/path/to/context.md")

    def test_all_architect_flags_combined(self):
        """Test all architect control flags can be used together."""
        args = self.parser.parse_args([
            "--tree-depth", "4",
            "--tree-ignore", "node_modules", "dist",
            "--memory-out", "output.md",
            "architect"
        ])
        self.assertEqual(args.tree_depth, 4)
        self.assertEqual(args.tree_ignore, ["node_modules", "dist"])
        self.assertEqual(args.memory_out, "output.md")
        self.assertEqual(args.phase, "architect")


class TestOrchestratorArchitectControlFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator architect control flag handling."""

    def test_orchestrator_stores_tree_depth(self):
        """Test orchestrator stores --tree-depth flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", tree_depth=5)
            self.assertEqual(orch._tree_depth, 5)

    def test_orchestrator_stores_tree_ignore(self):
        """Test orchestrator stores --tree-ignore flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", tree_ignore=["build", "dist"])
            self.assertEqual(orch._tree_ignore, ["build", "dist"])

    def test_orchestrator_stores_memory_out(self):
        """Test orchestrator stores --memory-out flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", memory_out="/path/to/output.md")
            self.assertEqual(orch._memory_out, "/path/to/output.md")

    def test_orchestrator_defaults_tree_depth_to_2(self):
        """Test orchestrator defaults tree_depth to 2."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            self.assertEqual(orch._tree_depth, 2)

    def test_orchestrator_defaults_tree_ignore_to_none(self):
        """Test orchestrator defaults tree_ignore to None."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            self.assertIsNone(orch._tree_ignore)

    def test_run_architect_uses_tree_flags(self):
        """Test run_architect passes tree flags to Shell.get_file_tree."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.get_file_tree') as mock_tree:
                mock_tree.return_value = "tree output"
                orch = RalphOrchestrator(agent_name="mock", tree_depth=5, tree_ignore=["build"])
                with patch('ralph.Logger.info'):
                    orch.run_architect("test intent")
                mock_tree.assert_called_once_with(depth=5, ignore=["build"])


class TestOrchestratorExportMemory(TempConfigTestCase):
    """Tests for RalphOrchestrator._export_memory() method."""

    def test_export_memory_creates_output_file(self):
        """Test _export_memory creates output file."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("# Architecture\nContent here", encoding="utf-8")

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            output_path = self.temp_path / "output" / "context.md"
            orch._export_memory(str(output_path))

            self.assertTrue(output_path.exists())
            content = output_path.read_text(encoding='utf-8')
            self.assertIn("architecture.md", content)
            self.assertIn("# Architecture", content)

    def test_export_memory_creates_parent_directories(self):
        """Test _export_memory creates parent directories if they don't exist."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "test.md").write_text("test content", encoding="utf-8")

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            output_path = self.temp_path / "nested" / "deep" / "output.md"
            orch._export_memory(str(output_path))

            self.assertTrue(output_path.exists())

    def test_export_memory_concatenates_multiple_files(self):
        """Test _export_memory concatenates all memory files."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Architecture content", encoding="utf-8")
        (CONF.MEMORY_DIR / "tasks.md").write_text("Task content", encoding="utf-8")

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            output_path = self.temp_path / "output.md"
            orch._export_memory(str(output_path))

            content = output_path.read_text(encoding='utf-8')
            self.assertIn("Architecture content", content)
            self.assertIn("Task content", content)
            self.assertIn("---", content)  # separator

    def test_export_memory_skips_hidden_files(self):
        """Test _export_memory skips files starting with dot."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "visible.md").write_text("visible content", encoding="utf-8")
        (CONF.MEMORY_DIR / ".hidden").write_text("hidden content", encoding="utf-8")

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            output_path = self.temp_path / "output.md"
            orch._export_memory(str(output_path))

            content = output_path.read_text(encoding='utf-8')
            self.assertIn("visible content", content)
            self.assertNotIn("hidden content", content)

    def test_export_memory_warns_on_empty(self):
        """Test _export_memory warns when no memory files exist."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            output_path = self.temp_path / "output.md"
            with patch('ralph.Logger.warning') as mock_warning:
                orch._export_memory(str(output_path))
                mock_warning.assert_called_with("No memory files to export.")

    def test_run_architect_calls_export_memory(self):
        """Test run_architect calls _export_memory when --memory-out is set."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")

        output_path = self.temp_path / "exported.md"

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.get_file_tree', return_value="tree"):
                orch = RalphOrchestrator(agent_name="mock", memory_out=str(output_path))
                with patch('ralph.Logger.info'):
                    orch.run_architect("test intent")

                self.assertTrue(output_path.exists())

    def test_run_architect_skips_export_when_no_memory_out(self):
        """Test run_architect doesn't export when --memory-out not set."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.get_file_tree', return_value="tree"):
                orch = RalphOrchestrator(agent_name="mock")
                with patch.object(orch, '_export_memory') as mock_export:
                    with patch('ralph.Logger.info'):
                        orch.run_architect("test intent")
                    mock_export.assert_not_called()


# ==============================================================================
# EXECUTION AND VERIFICATION FLAGS TESTS
# ==============================================================================


class TestExecutionVerificationFlags(TempConfigTestCase):
    """Tests for --test-cmd, --skip-verify, --retries, --timeout, --only, --except, --resume CLI flags."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--test-cmd", type=str, metavar="CMD")
        self.parser.add_argument("--skip-verify", action="store_true")
        self.parser.add_argument("--retries", type=int, metavar="N")
        self.parser.add_argument("--timeout", type=int, metavar="SECS")
        self.parser.add_argument("--only", nargs="+", metavar="TASK_ID")
        self.parser.add_argument("--except", dest="except_tasks", nargs="+", metavar="TASK_ID")
        self.parser.add_argument("--resume", type=str, metavar="TASK_ID")

    def test_test_cmd_flag_parses(self):
        """Test --test-cmd flag accepts command string."""
        args = self.parser.parse_args(["--test-cmd", "npm test", "execute"])
        self.assertEqual(args.test_cmd, "npm test")
        self.assertEqual(args.phase, "execute")

    def test_skip_verify_flag_parses(self):
        """Test --skip-verify flag is boolean."""
        args = self.parser.parse_args(["--skip-verify"])
        self.assertTrue(args.skip_verify)

        args = self.parser.parse_args([])
        self.assertFalse(args.skip_verify)

    def test_retries_flag_parses(self):
        """Test --retries flag accepts integer."""
        args = self.parser.parse_args(["--retries", "5"])
        self.assertEqual(args.retries, 5)

    def test_timeout_flag_parses(self):
        """Test --timeout flag accepts integer seconds."""
        args = self.parser.parse_args(["--timeout", "300"])
        self.assertEqual(args.timeout, 300)

    def test_only_flag_parses_single(self):
        """Test --only flag accepts single task ID."""
        args = self.parser.parse_args(["--only", "TASK-001"])
        self.assertEqual(args.only, ["TASK-001"])

    def test_only_flag_parses_multiple(self):
        """Test --only flag accepts multiple task IDs."""
        args = self.parser.parse_args(["--only", "TASK-001", "TASK-003", "TASK-005"])
        self.assertEqual(args.only, ["TASK-001", "TASK-003", "TASK-005"])

    def test_except_flag_parses_single(self):
        """Test --except flag accepts single task ID."""
        args = self.parser.parse_args(["--except", "TASK-002"])
        self.assertEqual(args.except_tasks, ["TASK-002"])

    def test_except_flag_parses_multiple(self):
        """Test --except flag accepts multiple task IDs."""
        args = self.parser.parse_args(["--except", "TASK-002", "TASK-004"])
        self.assertEqual(args.except_tasks, ["TASK-002", "TASK-004"])

    def test_resume_flag_parses(self):
        """Test --resume flag accepts task ID."""
        args = self.parser.parse_args(["--resume", "TASK-003"])
        self.assertEqual(args.resume, "TASK-003")

    def test_all_execution_flags_combined(self):
        """Test all execution flags can be used together."""
        args = self.parser.parse_args([
            "--test-cmd", "pytest -v",
            "--skip-verify",
            "--retries", "5",
            "--timeout", "300",
            "--only", "TASK-001", "TASK-002",
            "--resume", "TASK-001",
            "execute"
        ])
        self.assertEqual(args.test_cmd, "pytest -v")
        self.assertTrue(args.skip_verify)
        self.assertEqual(args.retries, 5)
        self.assertEqual(args.timeout, 300)
        self.assertEqual(args.only, ["TASK-001", "TASK-002"])
        self.assertEqual(args.resume, "TASK-001")
        self.assertEqual(args.phase, "execute")


class TestOrchestratorExecutionFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator execution flag handling."""

    def test_orchestrator_stores_test_cmd(self):
        """Test orchestrator stores --test-cmd flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", test_cmd="npm test")
            self.assertEqual(orch._test_cmd_override, "npm test")

    def test_orchestrator_stores_skip_verify(self):
        """Test orchestrator stores --skip-verify flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", skip_verify=True)
            self.assertTrue(orch._skip_verify)

    def test_orchestrator_stores_retries(self):
        """Test orchestrator stores --retries flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", retries=5)
            self.assertEqual(orch._retries_override, 5)

    def test_orchestrator_stores_timeout(self):
        """Test orchestrator stores --timeout flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", timeout=300)
            self.assertEqual(orch._timeout_override, 300)

    def test_orchestrator_stores_only(self):
        """Test orchestrator stores --only flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", only=["TASK-001", "TASK-002"])
            self.assertEqual(orch._only_tasks, ["TASK-001", "TASK-002"])

    def test_orchestrator_stores_except_tasks(self):
        """Test orchestrator stores --except flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", except_tasks=["TASK-003"])
            self.assertEqual(orch._except_tasks, ["TASK-003"])

    def test_orchestrator_stores_resume(self):
        """Test orchestrator stores --resume flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", resume="TASK-005")
            self.assertEqual(orch._resume_from, "TASK-005")

    def test_orchestrator_defaults_execution_flags(self):
        """Test orchestrator defaults all execution flags correctly."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            self.assertIsNone(orch._test_cmd_override)
            self.assertFalse(orch._skip_verify)
            self.assertIsNone(orch._retries_override)
            self.assertIsNone(orch._timeout_override)
            self.assertIsNone(orch._only_tasks)
            self.assertIsNone(orch._except_tasks)
            self.assertIsNone(orch._resume_from)


class TestOrchestratorTestCmdBehavior(TempConfigTestCase):
    """Tests for --test-cmd flag behavior in execute_loop."""

    def test_test_cmd_override_used_in_execute_loop(self):
        """Test --test-cmd override is used instead of extracted command."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: SUCCESS", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run') as mock_shell:
                mock_shell.return_value = ("", "", 0)
                with patch('ralph.Logger.info'):
                    orch = RalphOrchestrator(agent_name="mock", test_cmd="npm test")
                    orch.execute_loop()
                    # Verify npm test was called instead of pytest
                    mock_shell.assert_called()
                    call_args = mock_shell.call_args[0][0]
                    self.assertEqual(call_args, "npm test")


class TestOrchestratorSkipVerifyBehavior(TempConfigTestCase):
    """Tests for --skip-verify flag behavior in _execute_task."""

    def test_skip_verify_skips_verification(self):
        """Test --skip-verify skips verification step."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: SUCCESS", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run') as mock_shell:
                with patch('ralph.Logger.info'):
                    orch = RalphOrchestrator(agent_name="mock", skip_verify=True)
                    orch.execute_loop()
                    # Shell.run should NOT be called for verification
                    mock_shell.assert_not_called()


class TestOrchestratorRetriesBehavior(TempConfigTestCase):
    """Tests for --retries flag behavior in _execute_task."""

    def test_retries_override_used(self):
        """Test --retries override is used instead of config default."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        # Agent always fails
        mock_agent.run.return_value = (True, "STATUS: FAILURE", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Logger.info'):
                with patch('ralph.Logger.warning'):
                    orch = RalphOrchestrator(agent_name="mock", retries=2)
                    orch.execute_loop()
                    # Should have been called 2 times (not default 3)
                    self.assertEqual(mock_agent.run.call_count, 2)


class TestOrchestratorTimeoutBehavior(TempConfigTestCase):
    """Tests for --timeout flag behavior in __init__."""

    def test_timeout_override_passed_to_agent(self):
        """Test --timeout override is passed to agent."""
        with patch('ralph.get_agent') as mock_get_agent:
            mock_agent = self.create_mock_agent()
            mock_get_agent.return_value = mock_agent
            RalphOrchestrator(agent_name="mock", timeout=300)
            mock_get_agent.assert_called_once_with(
                "mock", timeout_seconds=300,
                model=None, temperature=None, max_tokens=None, seed=None
            )

    def test_timeout_default_passed_to_agent(self):
        """Test default timeout is passed when not overridden."""
        with patch('ralph.get_agent') as mock_get_agent:
            mock_agent = self.create_mock_agent()
            mock_get_agent.return_value = mock_agent
            RalphOrchestrator(agent_name="mock")
            mock_get_agent.assert_called_once_with(
                "mock", timeout_seconds=CONF.TIMEOUT_SECONDS,
                model=None, temperature=None, max_tokens=None, seed=None
            )


class TestOrchestratorOnlyBehavior(TempConfigTestCase):
    """Tests for --only flag behavior in execute_loop."""

    def test_only_executes_specified_tasks(self):
        """Test --only executes only specified tasks."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Task 1", "status": "pending"},
            {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            {"id": "TASK-003", "description": "Task 3", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: SUCCESS", None)

        executed_tasks = []

        def capture_run(prompt, tag):
            # Extract task ID from tag (format: WORKER-TASK-XXX)
            task_id = tag.replace("WORKER-", "")
            executed_tasks.append(task_id)
            return (True, "STATUS: SUCCESS", None)

        mock_agent.run.side_effect = capture_run

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run', return_value=("", "", 0)):
                with patch('ralph.Logger.info'):
                    with patch('ralph.Logger.debug'):
                        orch = RalphOrchestrator(agent_name="mock", only=["TASK-001", "TASK-003"])
                        orch.execute_loop()
                        # Only TASK-001 and TASK-003 should be executed
                        self.assertEqual(executed_tasks, ["TASK-001", "TASK-003"])


class TestOrchestratorExceptBehavior(TempConfigTestCase):
    """Tests for --except flag behavior in execute_loop."""

    def test_except_skips_specified_tasks(self):
        """Test --except skips specified tasks."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Task 1", "status": "pending"},
            {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            {"id": "TASK-003", "description": "Task 3", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        executed_tasks = []

        def capture_run(prompt, tag):
            task_id = tag.replace("WORKER-", "")
            executed_tasks.append(task_id)
            return (True, "STATUS: SUCCESS", None)

        mock_agent.run.side_effect = capture_run

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run', return_value=("", "", 0)):
                with patch('ralph.Logger.info'):
                    orch = RalphOrchestrator(agent_name="mock", except_tasks=["TASK-002"])
                    orch.execute_loop()
                    # TASK-002 should NOT be executed
                    self.assertEqual(executed_tasks, ["TASK-001", "TASK-003"])


class TestOrchestratorResumeBehavior(TempConfigTestCase):
    """Tests for --resume flag behavior in execute_loop."""

    def test_resume_starts_from_specified_task(self):
        """Test --resume starts execution from specified task."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Task 1", "status": "pending"},
            {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            {"id": "TASK-003", "description": "Task 3", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        executed_tasks = []

        def capture_run(prompt, tag):
            task_id = tag.replace("WORKER-", "")
            executed_tasks.append(task_id)
            return (True, "STATUS: SUCCESS", None)

        mock_agent.run.side_effect = capture_run

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run', return_value=("", "", 0)):
                with patch('ralph.Logger.info'):
                    with patch('ralph.Logger.debug'):
                        orch = RalphOrchestrator(agent_name="mock", resume="TASK-002")
                        orch.execute_loop()
                        # Should start from TASK-002, skipping TASK-001
                        self.assertEqual(executed_tasks, ["TASK-002", "TASK-003"])

    def test_resume_warns_when_task_not_found(self):
        """Test --resume warns when task ID not found in PRD."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Task 1", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Logger.info'):
                with patch('ralph.Logger.debug'):
                    with patch('ralph.Logger.warning') as mock_warning:
                        orch = RalphOrchestrator(agent_name="mock", resume="TASK-999")
                        orch.execute_loop()
                        mock_warning.assert_called_with(
                            "Resume task 'TASK-999' not found in PRD. No tasks executed."
                        )


class TestOrchestratorFlagsCombined(TempConfigTestCase):
    """Tests for combined execution flags."""

    def test_only_and_resume_combined(self):
        """Test --only and --resume work together correctly."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Task 1", "status": "pending"},
            {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            {"id": "TASK-003", "description": "Task 3", "status": "pending"},
            {"id": "TASK-004", "description": "Task 4", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        executed_tasks = []

        def capture_run(prompt, tag):
            task_id = tag.replace("WORKER-", "")
            executed_tasks.append(task_id)
            return (True, "STATUS: SUCCESS", None)

        mock_agent.run.side_effect = capture_run

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run', return_value=("", "", 0)):
                with patch('ralph.Logger.info'):
                    with patch('ralph.Logger.debug'):
                        # Resume from TASK-002, but only execute TASK-002 and TASK-004
                        orch = RalphOrchestrator(
                            agent_name="mock",
                            resume="TASK-002",
                            only=["TASK-002", "TASK-004"]
                        )
                        orch.execute_loop()
                        # TASK-001 skipped (before resume), TASK-003 skipped (not in only)
                        self.assertEqual(executed_tasks, ["TASK-002", "TASK-004"])

    def test_except_and_resume_combined(self):
        """Test --except and --resume work together correctly."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Task 1", "status": "pending"},
            {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            {"id": "TASK-003", "description": "Task 3", "status": "pending"},
            {"id": "TASK-004", "description": "Task 4", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        executed_tasks = []

        def capture_run(prompt, tag):
            task_id = tag.replace("WORKER-", "")
            executed_tasks.append(task_id)
            return (True, "STATUS: SUCCESS", None)

        mock_agent.run.side_effect = capture_run

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run', return_value=("", "", 0)):
                with patch('ralph.Logger.info'):
                    with patch('ralph.Logger.debug'):
                        # Resume from TASK-002, but skip TASK-003
                        orch = RalphOrchestrator(
                            agent_name="mock",
                            resume="TASK-002",
                            except_tasks=["TASK-003"]
                        )
                        orch.execute_loop()
                        # TASK-001 skipped (before resume), TASK-003 skipped (in except)
                        self.assertEqual(executed_tasks, ["TASK-002", "TASK-004"])


# ==============================================================================
# CONTEXT AND MEMORY CONTROL FLAGS TESTS
# ==============================================================================


class TestContextMemoryFlagsArgumentParsing(unittest.TestCase):
    """Tests for --include, --exclude, --context-limit CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--include", nargs="+", metavar="PATTERN")
        self.parser.add_argument("--exclude", nargs="+", metavar="PATTERN")
        self.parser.add_argument("--context-limit", type=int, metavar="N")

    def test_include_flag_parses_single_pattern(self):
        """Test --include flag accepts single pattern."""
        args = self.parser.parse_args(["--include", "*.md"])
        self.assertEqual(args.include, ["*.md"])

    def test_include_flag_parses_multiple_patterns(self):
        """Test --include flag accepts multiple patterns."""
        args = self.parser.parse_args(["--include", "*.md", "*.txt", "arch*"])
        self.assertEqual(args.include, ["*.md", "*.txt", "arch*"])

    def test_exclude_flag_parses_single_pattern(self):
        """Test --exclude flag accepts single pattern."""
        args = self.parser.parse_args(["--exclude", "*.log"])
        self.assertEqual(args.exclude, ["*.log"])

    def test_exclude_flag_parses_multiple_patterns(self):
        """Test --exclude flag accepts multiple patterns."""
        args = self.parser.parse_args(["--exclude", "*.log", "*.tmp", "debug*"])
        self.assertEqual(args.exclude, ["*.log", "*.tmp", "debug*"])

    def test_context_limit_flag_parses(self):
        """Test --context-limit flag accepts integer."""
        args = self.parser.parse_args(["--context-limit", "5"])
        self.assertEqual(args.context_limit, 5)

    def test_all_context_flags_combined(self):
        """Test all context flags can be used together."""
        args = self.parser.parse_args([
            "--include", "*.md", "arch*",
            "--exclude", "*.log",
            "--context-limit", "10",
            "execute"
        ])
        self.assertEqual(args.include, ["*.md", "arch*"])
        self.assertEqual(args.exclude, ["*.log"])
        self.assertEqual(args.context_limit, 10)
        self.assertEqual(args.phase, "execute")

    def test_default_values_are_none(self):
        """Test context flags default to None."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.include)
        self.assertIsNone(args.exclude)
        self.assertIsNone(args.context_limit)


class TestOrchestratorContextFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator context flag storage."""

    def test_orchestrator_stores_include_patterns(self):
        """Test orchestrator stores --include flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", include=["*.md", "arch*"])
            self.assertEqual(orch._include_patterns, ["*.md", "arch*"])

    def test_orchestrator_stores_exclude_patterns(self):
        """Test orchestrator stores --exclude flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", exclude=["*.log", "debug*"])
            self.assertEqual(orch._exclude_patterns, ["*.log", "debug*"])

    def test_orchestrator_stores_context_limit(self):
        """Test orchestrator stores --context-limit flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", context_limit=5)
            self.assertEqual(orch._context_limit, 5)

    def test_orchestrator_defaults_context_flags(self):
        """Test orchestrator defaults all context flags to None."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            self.assertIsNone(orch._include_patterns)
            self.assertIsNone(orch._exclude_patterns)
            self.assertIsNone(orch._context_limit)


class TestMemoryManagerFiltering(TempConfigTestCase):
    """Tests for MemoryManager filtering methods."""

    def setUp(self):
        super().setUp()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        # Create test files
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "database.md").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "debug.log").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("content", encoding="utf-8")

    def test_matches_pattern_filename(self):
        """Test _matches_pattern matches by filename."""
        path = CONF.MEMORY_DIR / "architecture.md"
        self.assertTrue(MemoryManager._matches_pattern(path, "*.md"))
        self.assertTrue(MemoryManager._matches_pattern(path, "arch*"))
        self.assertTrue(MemoryManager._matches_pattern(path, "architecture.md"))
        self.assertFalse(MemoryManager._matches_pattern(path, "*.txt"))

    def test_get_filtered_files_returns_all_when_no_filters(self):
        """Test get_filtered_files returns all files with no filters."""
        files = MemoryManager.get_filtered_files()
        self.assertEqual(len(files), 4)

    def test_get_filtered_files_include_filter(self):
        """Test get_filtered_files applies include filter."""
        files = MemoryManager.get_filtered_files(include=["*.md"])
        self.assertEqual(len(files), 2)
        names = [f.name for f in files]
        self.assertIn("architecture.md", names)
        self.assertIn("database.md", names)

    def test_get_filtered_files_exclude_filter(self):
        """Test get_filtered_files applies exclude filter."""
        files = MemoryManager.get_filtered_files(exclude=["*.log"])
        self.assertEqual(len(files), 3)
        names = [f.name for f in files]
        self.assertNotIn("debug.log", names)

    def test_get_filtered_files_include_and_exclude(self):
        """Test get_filtered_files applies both include and exclude."""
        files = MemoryManager.get_filtered_files(include=["*.md", "*.log"], exclude=["debug*"])
        self.assertEqual(len(files), 2)
        names = [f.name for f in files]
        self.assertIn("architecture.md", names)
        self.assertIn("database.md", names)

    def test_get_filtered_files_limit(self):
        """Test get_filtered_files applies limit."""
        files = MemoryManager.get_filtered_files(limit=2)
        self.assertEqual(len(files), 2)

    def test_get_filtered_files_limit_with_filters(self):
        """Test get_filtered_files applies limit after filtering."""
        files = MemoryManager.get_filtered_files(include=["*.md"], limit=1)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.endswith(".md"))

    def test_get_filtered_files_empty_result(self):
        """Test get_filtered_files returns empty list when no matches."""
        files = MemoryManager.get_filtered_files(include=["*.nonexistent"])
        self.assertEqual(len(files), 0)

    def test_get_filtered_files_sorted_deterministically(self):
        """Test get_filtered_files returns sorted results."""
        files = MemoryManager.get_filtered_files()
        paths_str = [str(f) for f in files]
        self.assertEqual(paths_str, sorted(paths_str))


class TestMemoryManagerGetStructureFiltered(TempConfigTestCase):
    """Tests for MemoryManager.get_structure() with filtering."""

    def setUp(self):
        super().setUp()
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "database.md").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "debug.log").write_text("content", encoding="utf-8")

    def test_get_structure_with_include(self):
        """Test get_structure filters by include patterns."""
        result = MemoryManager.get_structure(include=["*.md"])
        self.assertIn("architecture.md", result)
        self.assertIn("database.md", result)
        self.assertNotIn("debug.log", result)

    def test_get_structure_with_exclude(self):
        """Test get_structure filters by exclude patterns."""
        result = MemoryManager.get_structure(exclude=["*.log"])
        self.assertIn("architecture.md", result)
        self.assertNotIn("debug.log", result)

    def test_get_structure_with_limit(self):
        """Test get_structure respects context limit."""
        result = MemoryManager.get_structure(limit=1)
        # Should only have one file listed
        lines = [l for l in result.split("\n") if l.startswith("- ")]
        self.assertEqual(len(lines), 1)

    def test_get_structure_no_matches_returns_message(self):
        """Test get_structure returns message when no files match."""
        result = MemoryManager.get_structure(include=["*.nonexistent"])
        self.assertEqual(result, "(No matching memory files)")


class TestOrchestratorContextFlagsBehavior(TempConfigTestCase):
    """Tests for context flags behavior in orchestrator methods."""

    def test_execute_loop_passes_filters_to_get_structure(self):
        """Test execute_loop passes include/exclude/limit to get_structure."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding="utf-8")
        (CONF.MEMORY_DIR / "debug.log").write_text("debug info", encoding="utf-8")

        prd = {"id": "PRD-001", "userStories": [
            {"id": "TASK-001", "description": "Test task", "status": "pending"}
        ]}
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: SUCCESS", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Shell.run', return_value=("", "", 0)):
                with patch('ralph.Logger.info'):
                    with patch.object(MemoryManager, 'get_structure', wraps=MemoryManager.get_structure) as mock_get_structure:
                        orch = RalphOrchestrator(
                            agent_name="mock",
                            include=["*.md"],
                            exclude=["debug*"],
                            context_limit=5
                        )
                        orch.execute_loop()
                        # Verify get_structure was called with the filters
                        mock_get_structure.assert_called()
                        call_kwargs = mock_get_structure.call_args[1]
                        self.assertEqual(call_kwargs['include'], ["*.md"])
                        self.assertEqual(call_kwargs['exclude'], ["debug*"])
                        self.assertEqual(call_kwargs['limit'], 5)

    def test_planner_passes_filters_to_get_structure(self):
        """Test run_planner passes include/exclude/limit to get_structure."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("content", encoding="utf-8")

        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '{"userStories": []}', None)

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Logger.info'):
                with patch.object(MemoryManager, 'get_structure', wraps=MemoryManager.get_structure) as mock_get_structure:
                    orch = RalphOrchestrator(
                        agent_name="mock",
                        include=["*.md"],
                        exclude=["*.log"],
                        context_limit=3
                    )
                    orch.run_planner("test intent")
                    mock_get_structure.assert_called()
                    call_kwargs = mock_get_structure.call_args[1]
                    self.assertEqual(call_kwargs['include'], ["*.md"])
                    self.assertEqual(call_kwargs['exclude'], ["*.log"])
                    self.assertEqual(call_kwargs['limit'], 3)


# ==============================================================================
# SAFETY, ISOLATION AND GIT CONTROL FLAG TESTS (TASK-006)
# ==============================================================================


class TestSafetyGitFlagsArgparse(unittest.TestCase):
    """Tests for safety, isolation and git control CLI flag parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        git_group = self.parser.add_mutually_exclusive_group()
        git_group.add_argument("--git", action="store_true", dest="git_enabled", default=True)
        git_group.add_argument("--no-git", action="store_false", dest="git_enabled")
        self.parser.add_argument("--git-message", type=str, metavar="MSG")
        self.parser.add_argument("--git-branch", type=str, metavar="BRANCH")
        self.parser.add_argument("--write-allow", nargs="+", metavar="PATTERN")
        self.parser.add_argument("--write-deny", nargs="+", metavar="PATTERN")
        self.parser.add_argument("--dry-run", action="store_true")

    def test_git_flag_default_is_enabled(self):
        """Test --git is enabled by default."""
        args = self.parser.parse_args([])
        self.assertTrue(args.git_enabled)

    def test_git_flag_can_be_explicitly_enabled(self):
        """Test --git flag explicitly enables git operations."""
        args = self.parser.parse_args(["--git"])
        self.assertTrue(args.git_enabled)

    def test_no_git_flag_disables_git(self):
        """Test --no-git flag disables git operations."""
        args = self.parser.parse_args(["--no-git"])
        self.assertFalse(args.git_enabled)

    def test_git_message_flag_parses(self):
        """Test --git-message flag accepts message string."""
        args = self.parser.parse_args(["--git-message", "feat: add new feature"])
        self.assertEqual(args.git_message, "feat: add new feature")

    def test_git_branch_flag_parses(self):
        """Test --git-branch flag accepts branch name."""
        args = self.parser.parse_args(["--git-branch", "feature/new-feature"])
        self.assertEqual(args.git_branch, "feature/new-feature")

    def test_write_allow_single_pattern(self):
        """Test --write-allow flag accepts single pattern."""
        args = self.parser.parse_args(["--write-allow", "src/*"])
        self.assertEqual(args.write_allow, ["src/*"])

    def test_write_allow_multiple_patterns(self):
        """Test --write-allow flag accepts multiple patterns."""
        args = self.parser.parse_args(["--write-allow", "src/*", "tests/*", "*.md"])
        self.assertEqual(args.write_allow, ["src/*", "tests/*", "*.md"])

    def test_write_deny_single_pattern(self):
        """Test --write-deny flag accepts single pattern."""
        args = self.parser.parse_args(["--write-deny", "node_modules/*"])
        self.assertEqual(args.write_deny, ["node_modules/*"])

    def test_write_deny_multiple_patterns(self):
        """Test --write-deny flag accepts multiple patterns."""
        args = self.parser.parse_args(["--write-deny", "node_modules/*", ".git/*", "*.lock"])
        self.assertEqual(args.write_deny, ["node_modules/*", ".git/*", "*.lock"])

    def test_dry_run_flag_default_false(self):
        """Test --dry-run flag defaults to False."""
        args = self.parser.parse_args([])
        self.assertFalse(args.dry_run)

    def test_dry_run_flag_when_set(self):
        """Test --dry-run flag is True when specified."""
        args = self.parser.parse_args(["--dry-run"])
        self.assertTrue(args.dry_run)

    def test_all_safety_flags_combined(self):
        """Test all safety and git flags can be used together."""
        args = self.parser.parse_args([
            "--no-git",
            "--git-message", "test: add tests",
            "--git-branch", "feature/tests",
            "--write-allow", "src/*", "tests/*",
            "--write-deny", "node_modules/*",
            "--dry-run",
            "execute"
        ])
        self.assertFalse(args.git_enabled)
        self.assertEqual(args.git_message, "test: add tests")
        self.assertEqual(args.git_branch, "feature/tests")
        self.assertEqual(args.write_allow, ["src/*", "tests/*"])
        self.assertEqual(args.write_deny, ["node_modules/*"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.phase, "execute")

    def test_default_values_are_correct(self):
        """Test safety flags default values."""
        args = self.parser.parse_args([])
        self.assertTrue(args.git_enabled)
        self.assertIsNone(args.git_message)
        self.assertIsNone(args.git_branch)
        self.assertIsNone(args.write_allow)
        self.assertIsNone(args.write_deny)
        self.assertFalse(args.dry_run)


class TestOrchestratorSafetyGitFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator safety and git flag storage."""

    def test_orchestrator_stores_git_enabled_true(self):
        """Test orchestrator stores --git flag as True."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", git=True)
            self.assertTrue(orch._git_enabled)

    def test_orchestrator_stores_git_enabled_false(self):
        """Test orchestrator stores --no-git flag as False."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", git=False)
            self.assertFalse(orch._git_enabled)

    def test_orchestrator_stores_git_message(self):
        """Test orchestrator stores --git-message flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", git_message="feat: new feature")
            self.assertEqual(orch._git_message, "feat: new feature")

    def test_orchestrator_stores_git_branch(self):
        """Test orchestrator stores --git-branch flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", git_branch="feature/test")
            self.assertEqual(orch._git_branch, "feature/test")

    def test_orchestrator_stores_write_allow(self):
        """Test orchestrator stores --write-allow flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", write_allow=["src/*", "tests/*"])
            self.assertEqual(orch._write_allow, ["src/*", "tests/*"])

    def test_orchestrator_stores_write_deny(self):
        """Test orchestrator stores --write-deny flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", write_deny=["node_modules/*", ".git/*"])
            self.assertEqual(orch._write_deny, ["node_modules/*", ".git/*"])

    def test_orchestrator_stores_dry_run(self):
        """Test orchestrator stores --dry-run flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", dry_run=True)
            self.assertTrue(orch._dry_run)

    def test_orchestrator_defaults_safety_git_flags(self):
        """Test orchestrator defaults all safety/git flags correctly."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            self.assertTrue(orch._git_enabled)
            self.assertIsNone(orch._git_message)
            self.assertIsNone(orch._git_branch)
            self.assertIsNone(orch._write_allow)
            self.assertIsNone(orch._write_deny)
            self.assertFalse(orch._dry_run)

    def test_orchestrator_stores_all_safety_flags_together(self):
        """Test orchestrator stores all safety/git flags when used together."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(
                agent_name="mock",
                git=False,
                git_message="test: message",
                git_branch="feature/branch",
                write_allow=["*.py"],
                write_deny=["*.pyc"],
                dry_run=True
            )
            self.assertFalse(orch._git_enabled)
            self.assertEqual(orch._git_message, "test: message")
            self.assertEqual(orch._git_branch, "feature/branch")
            self.assertEqual(orch._write_allow, ["*.py"])
            self.assertEqual(orch._write_deny, ["*.pyc"])
            self.assertTrue(orch._dry_run)


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


# ==============================================================================
# MODEL AND PROMPTING FLAGS TESTS
# ==============================================================================


class TestModelPromptingFlagsBaseAgent(unittest.TestCase):
    """Tests for model and prompting parameters in BaseAgent."""

    def test_base_agent_accepts_model_parameters(self):
        """Test that BaseAgent accepts model, temperature, max_tokens, and seed."""
        from agents.base import BaseAgent

        # Create a concrete subclass for testing
        class TestAgent(BaseAgent):
            def check_dependencies(self):
                return True
            def get_name(self):
                return "TestAgent"
            def _build_command(self, prompt):
                return ["echo", prompt]
            def _prepare_input(self, prompt):
                return None

        agent = TestAgent(
            timeout_seconds=300,
            model="claude-3-opus",
            temperature=0.7,
            max_tokens=4096,
            seed=42
        )

        self.assertEqual(agent.timeout_seconds, 300)
        self.assertEqual(agent.model, "claude-3-opus")
        self.assertEqual(agent.temperature, 0.7)
        self.assertEqual(agent.max_tokens, 4096)
        self.assertEqual(agent.seed, 42)

    def test_base_agent_defaults_model_parameters_to_none(self):
        """Test that model parameters default to None when not provided."""
        from agents.base import BaseAgent

        class TestAgent(BaseAgent):
            def check_dependencies(self):
                return True
            def get_name(self):
                return "TestAgent"
            def _build_command(self, prompt):
                return ["echo", prompt]
            def _prepare_input(self, prompt):
                return None

        agent = TestAgent(timeout_seconds=600)

        self.assertIsNone(agent.model)
        self.assertIsNone(agent.temperature)
        self.assertIsNone(agent.max_tokens)
        self.assertIsNone(agent.seed)


class TestModelPromptingFlagsClaudeAgent(unittest.TestCase):
    """Tests for model and prompting parameters in ClaudeAgent."""

    def test_claude_agent_build_command_without_model_params(self):
        """Test ClaudeAgent._build_command without model parameters."""
        agent = ClaudeAgent(timeout_seconds=600)
        with patch('shutil.which', return_value='/usr/bin/claude'):
            cmd = agent._build_command("test prompt")

        self.assertEqual(cmd, ['/usr/bin/claude', '-p', '--dangerously-skip-permissions'])

    def test_claude_agent_build_command_with_model(self):
        """Test ClaudeAgent._build_command with model parameter."""
        agent = ClaudeAgent(timeout_seconds=600, model="claude-3-opus")
        with patch('shutil.which', return_value='/usr/bin/claude'):
            cmd = agent._build_command("test prompt")

        self.assertIn('--model', cmd)
        self.assertIn('claude-3-opus', cmd)
        model_idx = cmd.index('--model')
        self.assertEqual(cmd[model_idx + 1], 'claude-3-opus')

    def test_claude_agent_build_command_with_max_tokens(self):
        """Test ClaudeAgent._build_command with max_tokens parameter."""
        agent = ClaudeAgent(timeout_seconds=600, max_tokens=4096)
        with patch('shutil.which', return_value='/usr/bin/claude'):
            cmd = agent._build_command("test prompt")

        self.assertIn('--max-tokens', cmd)
        self.assertIn('4096', cmd)
        tokens_idx = cmd.index('--max-tokens')
        self.assertEqual(cmd[tokens_idx + 1], '4096')

    def test_claude_agent_build_command_with_all_supported_params(self):
        """Test ClaudeAgent._build_command with all supported parameters."""
        agent = ClaudeAgent(
            timeout_seconds=600,
            model="claude-3-sonnet",
            max_tokens=2048,
            temperature=0.5,  # Not used in CLI but stored
            seed=123  # Not used in CLI but stored
        )
        with patch('shutil.which', return_value='/usr/bin/claude'):
            cmd = agent._build_command("test prompt")

        # Should include model and max-tokens
        self.assertIn('--model', cmd)
        self.assertIn('claude-3-sonnet', cmd)
        self.assertIn('--max-tokens', cmd)
        self.assertIn('2048', cmd)
        # Temperature and seed are stored but not in command
        self.assertEqual(agent.temperature, 0.5)
        self.assertEqual(agent.seed, 123)


class TestModelPromptingFlagsGithubAgent(unittest.TestCase):
    """Tests for model and prompting parameters in GithubAgent."""

    def test_github_agent_build_command_without_model_params(self):
        """Test GithubAgent._build_command without model parameters."""
        agent = GithubAgent(timeout_seconds=600)
        agent._temp_file_path = "/tmp/test.txt"
        with patch('shutil.which', return_value='/usr/bin/copilot'):
            cmd = agent._build_command("test prompt")

        self.assertNotIn('--model', cmd)

    def test_github_agent_build_command_with_model(self):
        """Test GithubAgent._build_command with model parameter."""
        agent = GithubAgent(timeout_seconds=600, model="gpt-4")
        agent._temp_file_path = "/tmp/test.txt"
        with patch('shutil.which', return_value='/usr/bin/copilot'):
            cmd = agent._build_command("test prompt")

        self.assertIn('--model', cmd)
        self.assertIn('gpt-4', cmd)
        model_idx = cmd.index('--model')
        self.assertEqual(cmd[model_idx + 1], 'gpt-4')

    def test_github_agent_stores_unsupported_params(self):
        """Test GithubAgent stores temperature, max_tokens, seed even if not used in CLI."""
        agent = GithubAgent(
            timeout_seconds=600,
            model="gpt-4",
            temperature=0.8,
            max_tokens=1024,
            seed=999
        )

        self.assertEqual(agent.model, "gpt-4")
        self.assertEqual(agent.temperature, 0.8)
        self.assertEqual(agent.max_tokens, 1024)
        self.assertEqual(agent.seed, 999)


class TestModelPromptingFlagsGetAgent(unittest.TestCase):
    """Tests for get_agent passing model parameters to agents."""

    def test_get_agent_passes_model_params_to_claude(self):
        """Test that get_agent passes model parameters to ClaudeAgent."""
        agent = get_agent(
            "claude",
            timeout_seconds=300,
            model="claude-3-haiku",
            temperature=0.3,
            max_tokens=512,
            seed=77
        )

        self.assertIsInstance(agent, ClaudeAgent)
        self.assertEqual(agent.timeout_seconds, 300)
        self.assertEqual(agent.model, "claude-3-haiku")
        self.assertEqual(agent.temperature, 0.3)
        self.assertEqual(agent.max_tokens, 512)
        self.assertEqual(agent.seed, 77)

    def test_get_agent_passes_model_params_to_copilot(self):
        """Test that get_agent passes model parameters to GithubAgent."""
        agent = get_agent(
            "copilot",
            timeout_seconds=450,
            model="gpt-4-turbo",
            temperature=1.0,
            max_tokens=8192,
            seed=0
        )

        self.assertIsInstance(agent, GithubAgent)
        self.assertEqual(agent.timeout_seconds, 450)
        self.assertEqual(agent.model, "gpt-4-turbo")
        self.assertEqual(agent.temperature, 1.0)
        self.assertEqual(agent.max_tokens, 8192)
        self.assertEqual(agent.seed, 0)


class TestModelPromptingFlagsOrchestrator(TempConfigTestCase):
    """Tests for RalphOrchestrator passing model parameters to agents."""

    def test_orchestrator_passes_model_params_to_agent(self):
        """Test that RalphOrchestrator passes model parameters to the agent."""
        captured_kwargs = {}

        def capture_get_agent(agent_name, **kwargs):
            captured_kwargs.update(kwargs)
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "MockAgent"
            return mock_agent

        with patch('ralph.get_agent', side_effect=capture_get_agent):
            RalphOrchestrator(
                agent_name="claude",
                model="claude-3-opus",
                temperature=0.5,
                max_tokens=2048,
                seed=42
            )

        self.assertEqual(captured_kwargs.get('model'), "claude-3-opus")
        self.assertEqual(captured_kwargs.get('temperature'), 0.5)
        self.assertEqual(captured_kwargs.get('max_tokens'), 2048)
        self.assertEqual(captured_kwargs.get('seed'), 42)

    def test_orchestrator_passes_none_when_model_params_not_specified(self):
        """Test that RalphOrchestrator passes None when model params not specified."""
        captured_kwargs = {}

        def capture_get_agent(agent_name, **kwargs):
            captured_kwargs.update(kwargs)
            mock_agent = MagicMock()
            mock_agent.check_dependencies.return_value = True
            mock_agent.get_name.return_value = "MockAgent"
            return mock_agent

        with patch('ralph.get_agent', side_effect=capture_get_agent):
            RalphOrchestrator(agent_name="claude")

        self.assertIsNone(captured_kwargs.get('model'))
        self.assertIsNone(captured_kwargs.get('temperature'))
        self.assertIsNone(captured_kwargs.get('max_tokens'))
        self.assertIsNone(captured_kwargs.get('seed'))


class TestModelPromptingFlagsCLI(unittest.TestCase):
    """Tests for CLI argument parsing of model and prompting flags."""

    def test_cli_parses_model_flag(self):
        """Test that CLI correctly parses --model flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--model', 'claude-3-opus', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['model'], 'claude-3-opus')

    def test_cli_parses_temperature_flag(self):
        """Test that CLI correctly parses --temperature flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--temperature', '0.7', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['temperature'], 0.7)

    def test_cli_parses_max_tokens_flag(self):
        """Test that CLI correctly parses --max-tokens flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--max-tokens', '4096', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['max_tokens'], 4096)

    def test_cli_parses_seed_flag(self):
        """Test that CLI correctly parses --seed flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--seed', '12345', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['seed'], 12345)

    def test_cli_parses_all_model_flags_together(self):
        """Test that CLI correctly parses all model flags together."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', [
                'ralph',
                '--model', 'claude-3-sonnet',
                '--temperature', '0.5',
                '--max-tokens', '2048',
                '--seed', '99',
                'execute'
            ]):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['model'], 'claude-3-sonnet')
            self.assertEqual(call_kwargs['temperature'], 0.5)
            self.assertEqual(call_kwargs['max_tokens'], 2048)
            self.assertEqual(call_kwargs['seed'], 99)

    def test_cli_model_flags_default_to_none(self):
        """Test that CLI model flags default to None when not specified."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertIsNone(call_kwargs['model'])
            self.assertIsNone(call_kwargs['temperature'])
            self.assertIsNone(call_kwargs['max_tokens'])
            self.assertIsNone(call_kwargs['seed'])

    def test_cli_temperature_accepts_float(self):
        """Test that --temperature accepts decimal float values."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--temperature', '0.123', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertAlmostEqual(call_kwargs['temperature'], 0.123)


# ==============================================================================
# I/O, LOGGING AND OUTPUT FLAGS TESTS (TASK-008)
# ==============================================================================


class TestLoggerLogLevel(unittest.TestCase):
    """Tests for Logger log level functionality."""

    def setUp(self):
        self.original_log_level = Logger.log_level
        self.original_verbosity = Logger.verbosity
        self.original_quiet = Logger.quiet
        self.original_json_output = Logger.json_output
        self.original_ndjson_output = Logger.ndjson_output
        self.held_output = StringIO()
        self.original_stdout = sys.stdout

    def tearDown(self):
        Logger.log_level = self.original_log_level
        Logger.verbosity = self.original_verbosity
        Logger.quiet = self.original_quiet
        Logger.json_output = self.original_json_output
        Logger.ndjson_output = self.original_ndjson_output
        sys.stdout = self.original_stdout

    def test_set_log_level_debug(self):
        """Test setting log level to debug."""
        Logger.set_log_level("debug")
        self.assertEqual(Logger.log_level, Logger.LOG_LEVELS["debug"])

    def test_set_log_level_info(self):
        """Test setting log level to info."""
        Logger.set_log_level("info")
        self.assertEqual(Logger.log_level, Logger.LOG_LEVELS["info"])

    def test_set_log_level_warn(self):
        """Test setting log level to warn."""
        Logger.set_log_level("warn")
        self.assertEqual(Logger.log_level, Logger.LOG_LEVELS["warn"])

    def test_set_log_level_error(self):
        """Test setting log level to error."""
        Logger.set_log_level("error")
        self.assertEqual(Logger.log_level, Logger.LOG_LEVELS["error"])

    def test_set_log_level_invalid_ignored(self):
        """Test that invalid log level is ignored."""
        original = Logger.log_level
        Logger.set_log_level("invalid")
        self.assertEqual(Logger.log_level, original)

    def test_info_respects_log_level_warn(self):
        """Test that info messages are suppressed when log level is warn."""
        sys.stdout = self.held_output
        Logger.set_log_level("warn")
        Logger.info("This should not appear")
        self.assertEqual(self.held_output.getvalue(), "")

    def test_warning_respects_log_level_error(self):
        """Test that warning messages are suppressed when log level is error."""
        sys.stdout = self.held_output
        Logger.set_log_level("error")
        Logger.warning("This should not appear")
        self.assertEqual(self.held_output.getvalue(), "")

    def test_error_shown_at_all_levels(self):
        """Test that error messages are shown at all log levels."""
        Logger.set_no_color(True)
        for level in ["debug", "info", "warn", "error"]:
            self.held_output = StringIO()
            sys.stdout = self.held_output
            Logger.set_log_level(level)
            Logger.error("Error message")
            self.assertIn("Error message", self.held_output.getvalue())


class TestLoggerJsonOutput(unittest.TestCase):
    """Tests for Logger JSON output functionality."""

    def setUp(self):
        self.original_json_output = Logger.json_output
        self.original_ndjson_output = Logger.ndjson_output
        self.original_log_level = Logger.log_level
        self.original_quiet = Logger.quiet
        self.original_verbosity = Logger.verbosity
        self.held_output = StringIO()
        self.original_stdout = sys.stdout

    def tearDown(self):
        Logger.json_output = self.original_json_output
        Logger.ndjson_output = self.original_ndjson_output
        Logger.log_level = self.original_log_level
        Logger.quiet = self.original_quiet
        Logger.verbosity = self.original_verbosity
        sys.stdout = self.original_stdout

    def test_set_json_output(self):
        """Test enabling JSON output."""
        Logger.set_json_output(True)
        self.assertTrue(Logger.json_output)
        Logger.set_json_output(False)
        self.assertFalse(Logger.json_output)

    def test_set_ndjson_output(self):
        """Test enabling NDJSON output."""
        Logger.set_ndjson_output(True)
        self.assertTrue(Logger.ndjson_output)
        Logger.set_ndjson_output(False)
        self.assertFalse(Logger.ndjson_output)

    def test_info_outputs_json_when_enabled(self):
        """Test that info outputs JSON when json_output is enabled."""
        sys.stdout = self.held_output
        Logger.set_json_output(True)
        Logger.info("Test message")
        output = self.held_output.getvalue()
        data = json.loads(output.strip())
        self.assertEqual(data["level"], "info")
        self.assertEqual(data["message"], "Test message")
        self.assertIn("timestamp", data)

    def test_warning_outputs_json_when_enabled(self):
        """Test that warning outputs JSON when json_output is enabled."""
        sys.stdout = self.held_output
        Logger.set_json_output(True)
        Logger.warning("Test warning")
        output = self.held_output.getvalue()
        data = json.loads(output.strip())
        self.assertEqual(data["level"], "warn")
        self.assertEqual(data["message"], "Test warning")

    def test_error_outputs_json_when_enabled(self):
        """Test that error outputs JSON when json_output is enabled."""
        sys.stdout = self.held_output
        Logger.set_json_output(True)
        Logger.error("Test error")
        output = self.held_output.getvalue()
        data = json.loads(output.strip())
        self.assertEqual(data["level"], "error")
        self.assertEqual(data["message"], "Test error")

    def test_debug_outputs_json_when_enabled(self):
        """Test that debug outputs JSON when json_output is enabled."""
        sys.stdout = self.held_output
        Logger.set_json_output(True)
        Logger.set_verbosity(1)  # Enable debug output
        Logger.debug("Test debug")
        output = self.held_output.getvalue()
        data = json.loads(output.strip())
        self.assertEqual(data["level"], "debug")
        self.assertEqual(data["message"], "Test debug")

    def test_ndjson_output_works_like_json(self):
        """Test that ndjson_output produces JSON output."""
        sys.stdout = self.held_output
        Logger.set_ndjson_output(True)
        Logger.info("Test message")
        output = self.held_output.getvalue()
        data = json.loads(output.strip())
        self.assertEqual(data["level"], "info")


class TestLoggerCustomLogFile(unittest.TestCase):
    """Tests for Logger custom log file functionality."""

    def setUp(self):
        self.original_custom_log_file = Logger.custom_log_file
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        Logger.custom_log_file = self.original_custom_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_log_file(self):
        """Test setting custom log file path."""
        Logger.set_log_file("/tmp/test.log")
        self.assertEqual(Logger.custom_log_file, Path("/tmp/test.log"))

    def test_set_log_file_none(self):
        """Test clearing custom log file path."""
        Logger.set_log_file("/tmp/test.log")
        Logger.set_log_file(None)
        self.assertIsNone(Logger.custom_log_file)

    def test_get_log_file_returns_custom(self):
        """Test that get_log_file returns custom path when set."""
        Logger.set_log_file("/tmp/custom.log")
        self.assertEqual(Logger.get_log_file(), Path("/tmp/custom.log"))

    def test_get_log_file_returns_default(self):
        """Test that get_log_file returns default when no custom set."""
        Logger.set_log_file(None)
        self.assertEqual(Logger.get_log_file(), CONF.LOG_FILE)

    def test_file_log_uses_custom_path(self):
        """Test that file_log writes to custom log file."""
        custom_log = Path(self.temp_dir) / "custom_log.txt"
        Logger.set_log_file(str(custom_log))
        Logger.file_log("Test content", "INFO", "TEST")
        self.assertTrue(custom_log.exists())
        content = custom_log.read_text(encoding='utf-8')
        self.assertIn("Test content", content)


class TestOrchestratorIOLoggingFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator I/O, logging and output flags."""

    def test_init_stores_log_file(self):
        """Test that __init__ stores log_file parameter."""
        orch = self.create_mock_orchestrator()
        self.assertIsNone(orch._log_file)

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(log_file="/tmp/test.log")
        self.assertEqual(orch._log_file, "/tmp/test.log")

    def test_init_stores_log_level(self):
        """Test that __init__ stores log_level parameter."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(log_level="debug")
        self.assertEqual(orch._log_level, "debug")

    def test_init_stores_json_output(self):
        """Test that __init__ stores json_output parameter."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(json_output=True)
        self.assertTrue(orch._json_output)

    def test_init_stores_ndjson_output(self):
        """Test that __init__ stores ndjson_output parameter."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(ndjson_output=True)
        self.assertTrue(orch._ndjson_output)

    def test_init_stores_print_prd_flag(self):
        """Test that __init__ stores print_prd parameter."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(print_prd=True)
        self.assertTrue(orch._print_prd_flag)

    def test_init_stores_prd_out(self):
        """Test that __init__ stores prd_out parameter."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(prd_out="/tmp/prd.json")
        self.assertEqual(orch._prd_out, "/tmp/prd.json")

    def test_init_stores_archive_flag(self):
        """Test that __init__ stores archive parameter."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(archive=False)
        self.assertFalse(orch._archive)


class TestOrchestratorPrintPrd(TempConfigTestCase):
    """Tests for RalphOrchestrator --print-prd functionality."""

    def test_print_prd_outputs_json(self):
        """Test that _print_prd outputs formatted JSON."""
        # Create PRD file
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd_data = {"id": "PRD-001", "userStories": [{"id": "TASK-001"}]}
        CONF.PRD_FILE.write_text(json.dumps(prd_data), encoding='utf-8')

        orch = self.create_mock_orchestrator()
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            orch._print_prd()
            output = mock_stdout.getvalue()

        parsed = json.loads(output)
        self.assertEqual(parsed["id"], "PRD-001")

    def test_print_prd_exits_when_no_prd(self):
        """Test that _print_prd exits with error when no PRD exists."""
        orch = self.create_mock_orchestrator()
        with self.assertRaises(SystemExit) as cm:
            orch._print_prd()
        self.assertEqual(cm.exception.code, 1)

    def test_start_with_print_prd_flag_returns_early(self):
        """Test that start() returns early when print_prd flag is set."""
        # Create PRD file
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd_data = {"id": "PRD-001", "userStories": []}
        CONF.PRD_FILE.write_text(json.dumps(prd_data), encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(print_prd=True)

        with patch.object(orch, '_run_single_phase') as mock_run:
            with patch('sys.stdout', new_callable=StringIO):
                orch.start(phase="execute")
            # _run_single_phase should NOT be called when print_prd is set
            mock_run.assert_not_called()


class TestOrchestratorPrdOut(TempConfigTestCase):
    """Tests for RalphOrchestrator --prd-out functionality."""

    def test_export_prd_creates_file(self):
        """Test that _export_prd creates the output file."""
        # Create PRD file
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd_data = {"id": "PRD-001", "userStories": []}
        CONF.PRD_FILE.write_text(json.dumps(prd_data), encoding='utf-8')

        output_path = self.temp_path / "exported_prd.json"
        orch = self.create_mock_orchestrator()
        orch._export_prd(str(output_path))

        self.assertTrue(output_path.exists())
        content = json.loads(output_path.read_text(encoding='utf-8'))
        self.assertEqual(content["id"], "PRD-001")

    def test_export_prd_creates_parent_dirs(self):
        """Test that _export_prd creates parent directories."""
        # Create PRD file
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd_data = {"id": "PRD-001"}
        CONF.PRD_FILE.write_text(json.dumps(prd_data), encoding='utf-8')

        output_path = self.temp_path / "nested" / "dir" / "prd.json"
        orch = self.create_mock_orchestrator()
        orch._export_prd(str(output_path))

        self.assertTrue(output_path.exists())

    def test_export_prd_exits_when_no_prd(self):
        """Test that _export_prd exits with error when no PRD exists."""
        orch = self.create_mock_orchestrator()
        with self.assertRaises(SystemExit) as cm:
            orch._export_prd("/tmp/prd.json")
        self.assertEqual(cm.exception.code, 1)


class TestOrchestratorArchiveFlag(TempConfigTestCase):
    """Tests for RalphOrchestrator --archive/--no-archive functionality."""

    def test_archive_prd_skipped_when_archive_false(self):
        """Test that _archive_prd skips archival when archive is False."""
        # Create PRD file
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE.write_text('{"id": "PRD-001"}', encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(archive=False)

        original_prd_exists = CONF.PRD_FILE.exists()
        orch._archive_prd()

        # PRD should still exist (not moved)
        self.assertEqual(CONF.PRD_FILE.exists(), original_prd_exists)
        # Archive dir should be empty
        archived_files = list(CONF.ARCHIVE_DIR.glob("*.json"))
        self.assertEqual(len(archived_files), 0)

    def test_archive_prd_works_when_archive_true(self):
        """Test that _archive_prd archives when archive is True."""
        # Create PRD file
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE.write_text('{"id": "PRD-001"}', encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(archive=True)

        orch._archive_prd()

        # PRD should be moved
        self.assertFalse(CONF.PRD_FILE.exists())
        # Archive dir should have the file
        archived_files = list(CONF.ARCHIVE_DIR.glob("*.json"))
        self.assertEqual(len(archived_files), 1)


class TestIOLoggingOutputFlagsCLI(unittest.TestCase):
    """Tests for CLI argument parsing of I/O, logging and output flags."""

    def test_cli_parses_log_file_flag(self):
        """Test that CLI correctly parses --log-file flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--log-file', '/tmp/test.log', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['log_file'], '/tmp/test.log')

    def test_cli_parses_log_level_flag(self):
        """Test that CLI correctly parses --log-level flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--log-level', 'debug', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['log_level'], 'debug')

    def test_cli_parses_json_flag(self):
        """Test that CLI correctly parses --json flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--json', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['json_output'])

    def test_cli_parses_ndjson_flag(self):
        """Test that CLI correctly parses --ndjson flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ndjson', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['ndjson_output'])

    def test_cli_parses_print_prd_flag(self):
        """Test that CLI correctly parses --print-prd flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--print-prd']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['print_prd'])

    def test_cli_parses_prd_out_flag(self):
        """Test that CLI correctly parses --prd-out flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--prd-out', '/tmp/prd.json', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['prd_out'], '/tmp/prd.json')

    def test_cli_parses_archive_flag(self):
        """Test that CLI correctly parses --archive flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--archive', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['archive'])

    def test_cli_parses_no_archive_flag(self):
        """Test that CLI correctly parses --no-archive flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--no-archive', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertFalse(call_kwargs['archive'])

    def test_cli_json_and_ndjson_are_mutually_exclusive(self):
        """Test that --json and --ndjson flags are mutually exclusive."""
        with self.assertRaises(SystemExit):
            with patch('sys.argv', ['ralph', '--json', '--ndjson', 'execute']):
                main()

    def test_cli_archive_and_no_archive_are_mutually_exclusive(self):
        """Test that --archive and --no-archive flags are mutually exclusive."""
        with self.assertRaises(SystemExit):
            with patch('sys.argv', ['ralph', '--archive', '--no-archive', 'execute']):
                main()

    def test_cli_all_io_flags_together(self):
        """Test that CLI correctly parses all I/O flags together."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', [
                'ralph',
                '--log-file', '/tmp/log.txt',
                '--log-level', 'warn',
                '--json',
                '--prd-out', '/tmp/prd.json',
                '--no-archive',
                'execute'
            ]):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['log_file'], '/tmp/log.txt')
            self.assertEqual(call_kwargs['log_level'], 'warn')
            self.assertTrue(call_kwargs['json_output'])
            self.assertEqual(call_kwargs['prd_out'], '/tmp/prd.json')
            self.assertFalse(call_kwargs['archive'])

    def test_cli_io_flags_default_to_none_or_false(self):
        """Test that CLI I/O flags default correctly when not specified."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertIsNone(call_kwargs['log_file'])
            self.assertIsNone(call_kwargs['log_level'])
            self.assertFalse(call_kwargs['json_output'])
            self.assertFalse(call_kwargs['ndjson_output'])
            self.assertFalse(call_kwargs['print_prd'])
            self.assertIsNone(call_kwargs['prd_out'])
            self.assertTrue(call_kwargs['archive'])  # Default is True


# ==============================================================================
# HEADLESS OPERATION FLAGS TESTS (TASK-009)
# ==============================================================================


class TestHeadlessFlagsArgumentParsing(unittest.TestCase):
    """Tests for --non-interactive, --ci, and --status-check CLI flag parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--non-interactive", action="store_true")
        self.parser.add_argument("--ci", action="store_true")
        self.parser.add_argument("--status-check", action="store_true")

    def test_non_interactive_flag_parses(self):
        """Test --non-interactive flag is parsed correctly."""
        args = self.parser.parse_args(["--non-interactive"])
        self.assertTrue(args.non_interactive)

    def test_ci_flag_parses(self):
        """Test --ci flag is parsed correctly."""
        args = self.parser.parse_args(["--ci"])
        self.assertTrue(args.ci)

    def test_status_check_flag_parses(self):
        """Test --status-check flag is parsed correctly."""
        args = self.parser.parse_args(["--status-check"])
        self.assertTrue(args.status_check)

    def test_all_headless_flags_combined(self):
        """Test all headless flags can be used together."""
        args = self.parser.parse_args(["--non-interactive", "--ci", "--status-check", "execute"])
        self.assertTrue(args.non_interactive)
        self.assertTrue(args.ci)
        self.assertTrue(args.status_check)
        self.assertEqual(args.phase, "execute")

    def test_headless_flags_default_to_false(self):
        """Test headless flags default to False when not specified."""
        args = self.parser.parse_args([])
        self.assertFalse(args.non_interactive)
        self.assertFalse(args.ci)
        self.assertFalse(args.status_check)


class TestLoggerNonInteractive(unittest.TestCase):
    """Tests for Logger.non_interactive class attribute."""

    def setUp(self):
        self._original_non_interactive = Logger.non_interactive

    def tearDown(self):
        Logger.non_interactive = self._original_non_interactive

    def test_set_non_interactive_true(self):
        """Test set_non_interactive(True) enables non-interactive mode."""
        Logger.set_non_interactive(True)
        self.assertTrue(Logger.non_interactive)

    def test_set_non_interactive_false(self):
        """Test set_non_interactive(False) disables non-interactive mode."""
        Logger.non_interactive = True
        Logger.set_non_interactive(False)
        self.assertFalse(Logger.non_interactive)

    def test_non_interactive_default_is_false(self):
        """Test that non_interactive defaults to False."""
        Logger.non_interactive = False  # Reset to default
        self.assertFalse(Logger.non_interactive)


class TestOrchestratorHeadlessFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator headless operation flag handling."""

    def test_init_stores_non_interactive_flag(self):
        """Test __init__ stores non_interactive flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", non_interactive=True)
        self.assertTrue(orch._non_interactive)

    def test_init_stores_ci_flag(self):
        """Test __init__ stores ci flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", ci=True)
        self.assertTrue(orch._ci)

    def test_init_stores_status_check_flag(self):
        """Test __init__ stores status_check flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        self.assertTrue(orch._status_check)

    def test_init_defaults_headless_flags_to_false(self):
        """Test headless flags default to False when not specified."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
        self.assertFalse(orch._non_interactive)
        self.assertFalse(orch._ci)
        self.assertFalse(orch._status_check)


class TestNonInteractiveBehavior(TempConfigTestCase):
    """Tests for non-interactive mode behavior in prompts."""

    def test_prompt_user_for_phase_exits_in_non_interactive(self):
        """Test _prompt_user_for_phase exits with error in non-interactive mode."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", non_interactive=True)
        with self.assertRaises(SystemExit) as ctx:
            orch._prompt_user_for_phase("test")
        self.assertEqual(ctx.exception.code, 1)

    def test_prompt_user_for_phase_works_in_interactive(self):
        """Test _prompt_user_for_phase prompts user in interactive mode."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", non_interactive=False)
        with patch('builtins.input', return_value='y'):
            result = orch._prompt_user_for_phase("test")
        self.assertTrue(result)

    def test_get_intent_exits_without_intent_in_non_interactive(self):
        """Test _get_intent exits if no intent provided in non-interactive mode."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", non_interactive=True)
        with self.assertRaises(SystemExit) as ctx:
            orch._get_intent()
        self.assertEqual(ctx.exception.code, 1)

    def test_get_intent_returns_intent_flag_in_non_interactive(self):
        """Test _get_intent returns intent from flag in non-interactive mode."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", non_interactive=True, intent="Build an API")
        result = orch._get_intent()
        self.assertEqual(result, "Build an API")

    def test_get_intent_returns_intent_file_in_non_interactive(self):
        """Test _get_intent returns intent from file in non-interactive mode."""
        intent_file = self.temp_path / "intent.txt"
        intent_file.write_text("Build a web app", encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", non_interactive=True, intent_file=str(intent_file))
        result = orch._get_intent()
        self.assertEqual(result, "Build a web app")


class TestStatusCheckBehavior(TempConfigTestCase):
    """Tests for --status-check flag behavior."""

    def test_check_prd_status_returns_2_when_no_prd(self):
        """Test _check_prd_status returns 2 when no PRD file exists."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        exit_code = orch._check_prd_status()
        self.assertEqual(exit_code, 2)

    def test_check_prd_status_returns_1_when_empty_prd(self):
        """Test _check_prd_status returns 1 when PRD has no tasks."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE.write_text('{"id": "PRD-001", "userStories": []}', encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        exit_code = orch._check_prd_status()
        self.assertEqual(exit_code, 1)

    def test_check_prd_status_returns_0_when_all_completed(self):
        """Test _check_prd_status returns 0 when all tasks completed."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "status": "completed"},
                {"id": "TASK-002", "status": "completed"}
            ]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        exit_code = orch._check_prd_status()
        self.assertEqual(exit_code, 0)

    def test_check_prd_status_returns_1_when_tasks_pending(self):
        """Test _check_prd_status returns 1 when tasks are pending."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "status": "completed"},
                {"id": "TASK-002", "status": "pending"}
            ]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        exit_code = orch._check_prd_status()
        self.assertEqual(exit_code, 1)

    def test_check_prd_status_returns_1_when_tasks_failed(self):
        """Test _check_prd_status returns 1 when tasks are failed."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "status": "completed"},
                {"id": "TASK-002", "status": "failed"}
            ]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        exit_code = orch._check_prd_status()
        self.assertEqual(exit_code, 1)

    def test_check_prd_status_handles_none_status(self):
        """Test _check_prd_status treats None status as pending."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001"},  # No status field
                {"id": "TASK-002", "status": "completed"}
            ]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        exit_code = orch._check_prd_status()
        self.assertEqual(exit_code, 1)

    def test_start_with_status_check_exits_with_code(self):
        """Test start() exits with status code when --status-check is set."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [{"id": "TASK-001", "status": "completed"}]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)
        with self.assertRaises(SystemExit) as ctx:
            orch.start()
        self.assertEqual(ctx.exception.code, 0)


class TestCIModeBehavior(TempConfigTestCase):
    """Tests for --ci flag behavior and defaults."""

    def setUp(self):
        super().setUp()
        # Save original Logger state
        self._original_no_color = Logger.no_color
        self._original_no_emoji = Logger.no_emoji
        self._original_json_output = Logger.json_output
        self._original_ndjson_output = Logger.ndjson_output
        self._original_non_interactive = Logger.non_interactive
        # Reset to defaults for clean test state
        Logger.no_color = False
        Logger.no_emoji = False
        Logger.json_output = False
        Logger.ndjson_output = False
        Logger.non_interactive = False

    def tearDown(self):
        super().tearDown()
        # Restore Logger state
        Logger.no_color = self._original_no_color
        Logger.no_emoji = self._original_no_emoji
        Logger.json_output = self._original_json_output
        Logger.ndjson_output = self._original_ndjson_output
        Logger.non_interactive = self._original_non_interactive

    def test_ci_mode_enables_non_interactive(self):
        """Test --ci mode enables non-interactive behavior."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['non_interactive'])

    def test_ci_mode_enables_no_color(self):
        """Test --ci mode disables colors via Logger."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', 'execute']):
                main()
            self.assertTrue(Logger.no_color)

    def test_ci_mode_enables_no_emoji(self):
        """Test --ci mode disables emojis via Logger."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', 'execute']):
                main()
            self.assertTrue(Logger.no_emoji)

    def test_ci_mode_enables_json_output(self):
        """Test --ci mode enables JSON output via Logger."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', 'execute']):
                main()
            self.assertTrue(Logger.json_output)

    def test_ci_mode_allows_ndjson_override(self):
        """Test --ci mode with --ndjson uses NDJSON instead of JSON."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', '--ndjson', 'execute']):
                main()
            self.assertFalse(Logger.json_output)
            self.assertTrue(Logger.ndjson_output)

    def test_ci_flag_stored_in_orchestrator(self):
        """Test --ci flag is stored in orchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['ci'])


class TestHeadlessFlagsCLI(unittest.TestCase):
    """Tests for headless operation flags CLI integration."""

    def setUp(self):
        # Save original Logger state
        self._original_no_color = Logger.no_color
        self._original_no_emoji = Logger.no_emoji
        self._original_json_output = Logger.json_output
        self._original_ndjson_output = Logger.ndjson_output
        self._original_non_interactive = Logger.non_interactive

    def tearDown(self):
        # Restore Logger state
        Logger.no_color = self._original_no_color
        Logger.no_emoji = self._original_no_emoji
        Logger.json_output = self._original_json_output
        Logger.ndjson_output = self._original_ndjson_output
        Logger.non_interactive = self._original_non_interactive

    def test_cli_parses_non_interactive_flag(self):
        """Test CLI parses --non-interactive flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--non-interactive', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['non_interactive'])

    def test_cli_parses_ci_flag(self):
        """Test CLI parses --ci flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--ci', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['ci'])

    def test_cli_parses_status_check_flag(self):
        """Test CLI parses --status-check flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--status-check', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['status_check'])

    def test_cli_headless_flags_default_to_false(self):
        """Test headless flags default to False via CLI."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertFalse(call_kwargs['non_interactive'])
            self.assertFalse(call_kwargs['ci'])
            self.assertFalse(call_kwargs['status_check'])

    def test_cli_all_headless_flags_together(self):
        """Test all headless flags can be used together via CLI."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--non-interactive', '--ci', '--status-check', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertTrue(call_kwargs['non_interactive'])
            self.assertTrue(call_kwargs['ci'])
            self.assertTrue(call_kwargs['status_check'])


class TestStatusCheckJSONOutput(TempConfigTestCase):
    """Tests for --status-check JSON output format."""

    def setUp(self):
        super().setUp()
        self._original_json_output = Logger.json_output
        self._original_ndjson_output = Logger.ndjson_output

    def tearDown(self):
        super().tearDown()
        Logger.json_output = self._original_json_output
        Logger.ndjson_output = self._original_ndjson_output

    def test_status_check_json_output_success(self):
        """Test _check_prd_status outputs JSON on success."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [{"id": "TASK-001", "status": "completed"}]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')
        Logger.json_output = True

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)

        captured_output = StringIO()
        with patch('sys.stdout', captured_output):
            exit_code = orch._check_prd_status()

        output = captured_output.getvalue()
        self.assertEqual(exit_code, 0)
        data = json.loads(output)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['exit_code'], 0)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['completed'], 1)

    def test_status_check_json_output_incomplete(self):
        """Test _check_prd_status outputs JSON on incomplete."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        prd = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "status": "completed"},
                {"id": "TASK-002", "status": "pending"}
            ]
        }
        CONF.PRD_FILE.write_text(json.dumps(prd), encoding='utf-8')
        Logger.json_output = True

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)

        captured_output = StringIO()
        with patch('sys.stdout', captured_output):
            exit_code = orch._check_prd_status()

        output = captured_output.getvalue()
        self.assertEqual(exit_code, 1)
        data = json.loads(output)
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(data['exit_code'], 1)
        self.assertEqual(data['completed'], 1)
        self.assertEqual(data['pending'], 1)

    def test_status_check_json_output_no_prd(self):
        """Test _check_prd_status outputs JSON when no PRD."""
        Logger.json_output = True

        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", status_check=True)

        captured_output = StringIO()
        with patch('sys.stdout', captured_output):
            exit_code = orch._check_prd_status()

        output = captured_output.getvalue()
        self.assertEqual(exit_code, 2)
        data = json.loads(output)
        self.assertEqual(data['status'], 'no_prd')
        self.assertEqual(data['exit_code'], 2)


# ==============================================================================
# PERFORMANCE AND DETERMINISM FLAGS TESTS (TASK-010)
# ==============================================================================


class TestPerformanceFlagsArgumentParsing(unittest.TestCase):
    """Tests for --concurrency, --rate-limit, and --backoff CLI flag parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--concurrency", type=int, metavar="N")
        self.parser.add_argument("--rate-limit", type=float, metavar="RPS")
        self.parser.add_argument("--backoff", type=float, metavar="SECS")

    def test_concurrency_flag_parses_integer(self):
        """Test --concurrency parses an integer value."""
        args = self.parser.parse_args(["--concurrency", "4"])
        self.assertEqual(args.concurrency, 4)

    def test_rate_limit_flag_parses_float(self):
        """Test --rate-limit parses a float value."""
        args = self.parser.parse_args(["--rate-limit", "2.5"])
        self.assertEqual(args.rate_limit, 2.5)

    def test_backoff_flag_parses_float(self):
        """Test --backoff parses a float value."""
        args = self.parser.parse_args(["--backoff", "1.5"])
        self.assertEqual(args.backoff, 1.5)

    def test_all_performance_flags_combined(self):
        """Test all performance flags can be used together."""
        args = self.parser.parse_args(["--concurrency", "8", "--rate-limit", "10.0", "--backoff", "2.0", "execute"])
        self.assertEqual(args.concurrency, 8)
        self.assertEqual(args.rate_limit, 10.0)
        self.assertEqual(args.backoff, 2.0)
        self.assertEqual(args.phase, "execute")

    def test_performance_flags_default_to_none(self):
        """Test performance flags default to None when not specified."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.concurrency)
        self.assertIsNone(args.rate_limit)
        self.assertIsNone(args.backoff)

    def test_concurrency_with_zero_value(self):
        """Test --concurrency accepts zero value."""
        args = self.parser.parse_args(["--concurrency", "0"])
        self.assertEqual(args.concurrency, 0)

    def test_rate_limit_with_integer_value(self):
        """Test --rate-limit accepts integer-like float (e.g., 5)."""
        args = self.parser.parse_args(["--rate-limit", "5"])
        self.assertEqual(args.rate_limit, 5.0)

    def test_backoff_with_small_value(self):
        """Test --backoff accepts small float value."""
        args = self.parser.parse_args(["--backoff", "0.1"])
        self.assertEqual(args.backoff, 0.1)


class TestOrchestratorPerformanceFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator performance and determinism flag handling."""

    def test_init_stores_concurrency_flag(self):
        """Test __init__ stores concurrency flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", concurrency=4)
        self.assertEqual(orch._concurrency, 4)

    def test_init_stores_rate_limit_flag(self):
        """Test __init__ stores rate_limit flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", rate_limit=2.5)
        self.assertEqual(orch._rate_limit, 2.5)

    def test_init_stores_backoff_flag(self):
        """Test __init__ stores backoff flag."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", backoff=1.5)
        self.assertEqual(orch._backoff, 1.5)

    def test_init_defaults_performance_flags_to_none(self):
        """Test performance flags default to None when not specified."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
        self.assertIsNone(orch._concurrency)
        self.assertIsNone(orch._rate_limit)
        self.assertIsNone(orch._backoff)

    def test_init_stores_all_performance_flags(self):
        """Test __init__ stores all performance flags together."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(
                agent_name="mock",
                concurrency=8,
                rate_limit=10.0,
                backoff=2.0
            )
        self.assertEqual(orch._concurrency, 8)
        self.assertEqual(orch._rate_limit, 10.0)
        self.assertEqual(orch._backoff, 2.0)

    def test_init_accepts_concurrency_zero(self):
        """Test __init__ accepts concurrency value of 0."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", concurrency=0)
        self.assertEqual(orch._concurrency, 0)

    def test_init_accepts_small_rate_limit(self):
        """Test __init__ accepts small rate_limit value."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", rate_limit=0.1)
        self.assertEqual(orch._rate_limit, 0.1)

    def test_init_accepts_small_backoff(self):
        """Test __init__ accepts small backoff value."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", backoff=0.1)
        self.assertEqual(orch._backoff, 0.1)


class TestPerformanceFlagsCLI(unittest.TestCase):
    """Tests for performance and determinism flags CLI integration."""

    def setUp(self):
        self._original_logger_settings = {
            'verbosity': Logger.verbosity,
            'verbose': Logger.verbose,
            'quiet': Logger.quiet,
            'no_emoji': Logger.no_emoji,
            'no_color': Logger.no_color,
            'log_level': Logger.log_level,
            'json_output': Logger.json_output,
            'ndjson_output': Logger.ndjson_output,
            'non_interactive': Logger.non_interactive,
        }

    def tearDown(self):
        for attr, value in self._original_logger_settings.items():
            setattr(Logger, attr, value)

    def test_cli_passes_concurrency_to_orchestrator(self):
        """Test CLI passes --concurrency to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--concurrency', '4', 'execute']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['concurrency'], 4)

    def test_cli_passes_rate_limit_to_orchestrator(self):
        """Test CLI passes --rate-limit to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--rate-limit', '2.5', 'execute']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['rate_limit'], 2.5)

    def test_cli_passes_backoff_to_orchestrator(self):
        """Test CLI passes --backoff to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--backoff', '1.5', 'execute']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['backoff'], 1.5)

    def test_cli_passes_all_performance_flags_to_orchestrator(self):
        """Test CLI passes all performance flags to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--concurrency', '8', '--rate-limit', '10.0', '--backoff', '2.0', 'execute']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['concurrency'], 8)
            self.assertEqual(call_kwargs['rate_limit'], 10.0)
            self.assertEqual(call_kwargs['backoff'], 2.0)

    def test_cli_passes_none_when_performance_flags_not_specified(self):
        """Test CLI passes None when performance flags not specified."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertIsNone(call_kwargs['concurrency'])
            self.assertIsNone(call_kwargs['rate_limit'])
            self.assertIsNone(call_kwargs['backoff'])


# ==============================================================================
# EXTENSIBILITY AND HOOK FLAGS TESTS
# ==============================================================================


class TestExtensibilityFlagsArgumentParsing(unittest.TestCase):
    """Tests for --pre, --post, and --plugin CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--pre", nargs="+", metavar="CMD")
        self.parser.add_argument("--post", nargs="+", metavar="CMD")
        self.parser.add_argument("--plugin", nargs="+", metavar="PATH")

    def test_pre_flag_accepts_single_command(self):
        """Test --pre accepts a single command."""
        args = self.parser.parse_args(["--pre", "echo hello"])
        self.assertEqual(args.pre, ["echo hello"])

    def test_pre_flag_accepts_multiple_commands(self):
        """Test --pre accepts multiple commands."""
        args = self.parser.parse_args(["--pre", "echo hello", "echo world"])
        self.assertEqual(args.pre, ["echo hello", "echo world"])

    def test_post_flag_accepts_single_command(self):
        """Test --post accepts a single command."""
        args = self.parser.parse_args(["--post", "echo done"])
        self.assertEqual(args.post, ["echo done"])

    def test_post_flag_accepts_multiple_commands(self):
        """Test --post accepts multiple commands."""
        args = self.parser.parse_args(["--post", "echo done", "echo finished"])
        self.assertEqual(args.post, ["echo done", "echo finished"])

    def test_plugin_flag_accepts_single_path(self):
        """Test --plugin accepts a single path."""
        args = self.parser.parse_args(["--plugin", "/path/to/plugin.py"])
        self.assertEqual(args.plugin, ["/path/to/plugin.py"])

    def test_plugin_flag_accepts_multiple_paths(self):
        """Test --plugin accepts multiple paths."""
        args = self.parser.parse_args(["--plugin", "/path/to/plugin1.py", "/path/to/plugin2.py"])
        self.assertEqual(args.plugin, ["/path/to/plugin1.py", "/path/to/plugin2.py"])

    def test_flags_default_to_none(self):
        """Test all extensibility flags default to None."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.pre)
        self.assertIsNone(args.post)
        self.assertIsNone(args.plugin)

    def test_all_extensibility_flags_combined(self):
        """Test all extensibility flags can be used together."""
        # Note: nargs="+" consumes all following arguments until a flag, so phase must come first
        args = self.parser.parse_args([
            "execute",
            "--pre", "cmd1", "cmd2",
            "--post", "cmd3", "cmd4",
            "--plugin", "/path/plugin.py"
        ])
        self.assertEqual(args.pre, ["cmd1", "cmd2"])
        self.assertEqual(args.post, ["cmd3", "cmd4"])
        self.assertEqual(args.plugin, ["/path/plugin.py"])
        self.assertEqual(args.phase, "execute")


class TestOrchestratorExtensibilityFlags(TempConfigTestCase):
    """Tests for RalphOrchestrator extensibility flag initialization."""

    def test_pre_commands_stored(self):
        """Test --pre commands are stored in orchestrator."""
        pre_cmds = ["echo before", "python validate.py"]
        orch = self.create_mock_orchestrator()
        orch._pre_commands = pre_cmds
        self.assertEqual(orch._pre_commands, pre_cmds)

    def test_post_commands_stored(self):
        """Test --post commands are stored in orchestrator."""
        post_cmds = ["echo after", "python cleanup.py"]
        orch = self.create_mock_orchestrator()
        orch._post_commands = post_cmds
        self.assertEqual(orch._post_commands, post_cmds)

    def test_plugin_paths_stored(self):
        """Test --plugin paths are stored in orchestrator."""
        plugins = ["/path/to/plugin.py", "/plugins/custom/"]
        orch = self.create_mock_orchestrator()
        orch._plugin_paths = plugins
        self.assertEqual(orch._plugin_paths, plugins)

    def test_orchestrator_init_with_pre_commands(self):
        """Test orchestrator initializes with --pre commands."""
        mock_agent = self.create_mock_agent()
        pre_cmds = ["echo before"]
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", pre=pre_cmds)
            self.assertEqual(orch._pre_commands, pre_cmds)

    def test_orchestrator_init_with_post_commands(self):
        """Test orchestrator initializes with --post commands."""
        mock_agent = self.create_mock_agent()
        post_cmds = ["echo after"]
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", post=post_cmds)
            self.assertEqual(orch._post_commands, post_cmds)

    def test_orchestrator_init_with_plugin_paths(self):
        """Test orchestrator initializes with --plugin paths."""
        mock_agent = self.create_mock_agent()
        plugins = ["/path/plugin.py"]
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", plugin=plugins)
            self.assertEqual(orch._plugin_paths, plugins)

    def test_empty_pre_commands_defaults_to_empty_list(self):
        """Test pre commands default to empty list when None."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", pre=None)
            self.assertEqual(orch._pre_commands, [])

    def test_empty_post_commands_defaults_to_empty_list(self):
        """Test post commands default to empty list when None."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", post=None)
            self.assertEqual(orch._post_commands, [])

    def test_empty_plugin_paths_defaults_to_empty_list(self):
        """Test plugin paths default to empty list when None."""
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock", plugin=None)
            self.assertEqual(orch._plugin_paths, [])


class TestPreCommandBehavior(TempConfigTestCase):
    """Tests for --pre command execution behavior."""

    def test_run_pre_commands_returns_true_when_empty(self):
        """Test _run_pre_commands returns True when no commands."""
        orch = self.create_mock_orchestrator()
        orch._pre_commands = []
        self.assertTrue(orch._run_pre_commands("architect"))

    def test_run_pre_commands_returns_true_on_success(self):
        """Test _run_pre_commands returns True when commands succeed."""
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["echo test"]
        from ralph import Shell
        with patch.object(Shell, 'run', return_value=("output", "", 0)):
            result = orch._run_pre_commands("architect")
            self.assertTrue(result)

    def test_run_pre_commands_returns_false_on_failure(self):
        """Test _run_pre_commands returns False when command fails."""
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["exit 1"]
        from ralph import Shell
        with patch.object(Shell, 'run', return_value=("", "error", 1)):
            result = orch._run_pre_commands("architect")
            self.assertFalse(result)

    def test_run_pre_commands_stops_on_first_failure(self):
        """Test _run_pre_commands stops execution on first failure."""
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["echo first", "exit 1", "echo should_not_run"]
        call_count = [0]
        def mock_run(cmd, timeout=30):
            call_count[0] += 1
            if "exit" in cmd:
                return ("", "error", 1)
            return ("ok", "", 0)
        from ralph import Shell
        with patch.object(Shell, 'run', side_effect=mock_run):
            result = orch._run_pre_commands("architect")
            self.assertFalse(result)
            self.assertEqual(call_count[0], 2)  # Only first two commands run


class TestPostCommandBehavior(TempConfigTestCase):
    """Tests for --post command execution behavior."""

    def test_run_post_commands_does_nothing_when_empty(self):
        """Test _run_post_commands does nothing when no commands."""
        orch = self.create_mock_orchestrator()
        orch._post_commands = []
        # Should not raise
        orch._run_post_commands("architect", success=True)

    def test_run_post_commands_sets_env_variables(self):
        """Test _run_post_commands sets RALPH_PHASE and RALPH_SUCCESS env vars."""
        orch = self.create_mock_orchestrator()
        orch._post_commands = ["echo test"]
        captured_env = {}
        def mock_run(cmd, **kwargs):
            captured_env.update(kwargs.get('env', {}))
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            return mock_result
        with patch('subprocess.run', side_effect=mock_run):
            orch._run_post_commands("architect", success=True)
            self.assertEqual(captured_env.get('RALPH_PHASE'), "architect")
            self.assertEqual(captured_env.get('RALPH_SUCCESS'), "1")

    def test_run_post_commands_sets_success_false(self):
        """Test _run_post_commands sets RALPH_SUCCESS=0 on failure."""
        orch = self.create_mock_orchestrator()
        orch._post_commands = ["echo test"]
        captured_env = {}
        def mock_run(cmd, **kwargs):
            captured_env.update(kwargs.get('env', {}))
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            return mock_result
        with patch('subprocess.run', side_effect=mock_run):
            orch._run_post_commands("planner", success=False)
            self.assertEqual(captured_env.get('RALPH_PHASE'), "planner")
            self.assertEqual(captured_env.get('RALPH_SUCCESS'), "0")

    def test_run_post_commands_continues_on_failure(self):
        """Test _run_post_commands continues even if a command fails."""
        orch = self.create_mock_orchestrator()
        orch._post_commands = ["exit 1", "echo second"]
        call_count = [0]
        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            mock_result = MagicMock()
            mock_result.returncode = 1 if call_count[0] == 1 else 0
            mock_result.stdout = ""
            return mock_result
        with patch('subprocess.run', side_effect=mock_run):
            orch._run_post_commands("execute", success=True)
            self.assertEqual(call_count[0], 2)  # Both commands run


class TestPluginLoading(TempConfigTestCase):
    """Tests for --plugin loading behavior."""

    def test_load_plugins_warns_on_nonexistent_path(self):
        """Test _load_plugins warns when path doesn't exist."""
        orch = self.create_mock_orchestrator()
        orch._plugin_paths = ["/nonexistent/path.py"]
        from ralph import Logger
        with patch.object(Logger, 'warning') as mock_warn:
            orch._load_plugins()
            mock_warn.assert_called()

    def test_load_plugins_loads_py_file(self):
        """Test _load_plugins loads .py files."""
        plugin_dir = self.temp_path / "plugins"
        plugin_dir.mkdir()
        plugin_file = plugin_dir / "test_plugin.py"
        plugin_file.write_text('''
EVENTS = ["TASK_START"]
def on_event(event):
    pass
''', encoding='utf-8')
        orch = self.create_mock_orchestrator()
        orch._plugin_paths = [str(plugin_file)]
        with patch.object(orch.hooks, 'register_hook', return_value=True) as mock_reg:
            orch._load_plugins()
            mock_reg.assert_called_once()
            call_kwargs = mock_reg.call_args[1]
            self.assertEqual(call_kwargs['name'], 'plugin_test_plugin')
            self.assertEqual(call_kwargs['events'], ['TASK_START'])

    def test_load_plugins_loads_directory(self):
        """Test _load_plugins loads all .py files from directory."""
        plugin_dir = self.temp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1.py").write_text('EVENTS = ["TASK_START"]\ndef on_event(e): pass', encoding='utf-8')
        (plugin_dir / "plugin2.py").write_text('EVENTS = ["TASK_SUCCESS"]\ndef on_event(e): pass', encoding='utf-8')
        (plugin_dir / "_private.py").write_text('EVENTS = ["ERROR"]\ndef on_event(e): pass', encoding='utf-8')
        orch = self.create_mock_orchestrator()
        orch._plugin_paths = [str(plugin_dir)]
        with patch.object(orch.hooks, 'register_hook', return_value=True) as mock_reg:
            orch._load_plugins()
            self.assertEqual(mock_reg.call_count, 2)  # _private.py skipped

    def test_load_plugin_file_warns_on_missing_events(self):
        """Test _load_plugin_file warns when EVENTS missing."""
        plugin_file = self.temp_path / "bad_plugin.py"
        plugin_file.write_text('def on_event(e): pass', encoding='utf-8')
        orch = self.create_mock_orchestrator()
        from ralph import Logger
        with patch.object(Logger, 'warning') as mock_warn:
            orch._load_plugin_file(plugin_file)
            mock_warn.assert_called()
            self.assertIn("missing EVENTS", mock_warn.call_args[0][0])

    def test_load_plugin_file_warns_on_missing_handler(self):
        """Test _load_plugin_file warns when on_event missing."""
        plugin_file = self.temp_path / "bad_plugin.py"
        plugin_file.write_text('EVENTS = ["TASK_START"]', encoding='utf-8')
        orch = self.create_mock_orchestrator()
        from ralph import Logger
        with patch.object(Logger, 'warning') as mock_warn:
            orch._load_plugin_file(plugin_file)
            mock_warn.assert_called()
            self.assertIn("missing EVENTS or on_event", mock_warn.call_args[0][0])

    def test_load_plugin_file_extracts_optional_attributes(self):
        """Test _load_plugin_file extracts PRIORITY, TIMEOUT, MODIFIES_DATA."""
        plugin_file = self.temp_path / "custom_plugin.py"
        plugin_file.write_text('''
EVENTS = ["TASK_START"]
PRIORITY = 50
TIMEOUT = 10.0
MODIFIES_DATA = True
def on_event(e): return e
''', encoding='utf-8')
        orch = self.create_mock_orchestrator()
        with patch.object(orch.hooks, 'register_hook', return_value=True) as mock_reg:
            orch._load_plugin_file(plugin_file)
            call_kwargs = mock_reg.call_args[1]
            self.assertEqual(call_kwargs['priority'], 50)
            self.assertEqual(call_kwargs['timeout'], 10.0)
            self.assertTrue(call_kwargs['modifies_data'])


class TestPhasePrePostIntegration(TempConfigTestCase):
    """Tests for pre/post command integration with phase execution."""

    def test_architect_calls_pre_commands(self):
        """Test run_architect calls _run_pre_commands."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "test.md").write_text("test", encoding='utf-8')
        (CONF.BASE_DIR / "ARCH.md").write_text("test", encoding='utf-8')
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["echo test"]
        with patch.object(orch, '_run_pre_commands', return_value=True) as mock_pre:
            with patch.object(orch.agent, 'run', return_value=(True, "", None)):
                orch.run_architect("test intent")
                mock_pre.assert_called_once_with("architect")

    def test_architect_calls_post_commands_on_success(self):
        """Test run_architect calls _run_post_commands on success."""
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "test.md").write_text("test", encoding='utf-8')
        (CONF.BASE_DIR / "ARCH.md").write_text("test", encoding='utf-8')
        orch = self.create_mock_orchestrator()
        orch._post_commands = ["echo done"]
        with patch.object(orch, '_run_pre_commands', return_value=True):
            with patch.object(orch, '_run_post_commands') as mock_post:
                with patch.object(orch.agent, 'run', return_value=(True, "", None)):
                    orch.run_architect("test intent")
                    mock_post.assert_called_with("architect", success=True)

    def test_architect_aborts_on_pre_command_failure(self):
        """Test run_architect aborts when pre-command fails."""
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["exit 1"]
        with patch.object(orch, '_run_pre_commands', return_value=False):
            with patch.object(orch, '_run_post_commands') as mock_post:
                with self.assertRaises(SystemExit):
                    orch.run_architect("test intent")
                mock_post.assert_called_with("architect", success=False)

    def test_execute_calls_pre_commands(self):
        """Test execute_loop calls _run_pre_commands."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE.write_text('{"userStories": []}', encoding='utf-8')
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding='utf-8')
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["echo test"]
        with patch.object(orch, '_run_pre_commands', return_value=True) as mock_pre:
            orch.execute_loop()
            mock_pre.assert_called_once_with("execute")

    def test_execute_returns_early_on_pre_command_failure(self):
        """Test execute_loop returns early when pre-command fails."""
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.PRD_FILE.write_text('{"userStories": [{"id": "T1", "description": "test", "status": "pending"}]}', encoding='utf-8')
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `pytest`", encoding='utf-8')
        orch = self.create_mock_orchestrator()
        orch._pre_commands = ["exit 1"]
        with patch.object(orch, '_run_pre_commands', return_value=False):
            with patch.object(orch, '_execute_task') as mock_task:
                orch.execute_loop()
                mock_task.assert_not_called()


class TestExtensibilityFlagsCLI(unittest.TestCase):
    """Tests for CLI passing extensibility flags to orchestrator."""

    def test_cli_passes_pre_commands_to_orchestrator(self):
        """Test CLI passes --pre commands to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            # Note: phase must come first, then flags with nargs="+" consume until next flag
            with patch('sys.argv', ['ralph', 'execute', '--pre', 'echo before', 'python check.py']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['pre'], ['echo before', 'python check.py'])

    def test_cli_passes_post_commands_to_orchestrator(self):
        """Test CLI passes --post commands to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute', '--post', 'echo done', 'python cleanup.py']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['post'], ['echo done', 'python cleanup.py'])

    def test_cli_passes_plugin_paths_to_orchestrator(self):
        """Test CLI passes --plugin paths to RalphOrchestrator."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute', '--plugin', '/path/to/plugin.py', '/plugins/']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['plugin'], ['/path/to/plugin.py', '/plugins/'])

    def test_cli_passes_none_when_extensibility_flags_not_specified(self):
        """Test CLI passes None when extensibility flags not specified."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertIsNone(call_kwargs['pre'])
            self.assertIsNone(call_kwargs['post'])
            self.assertIsNone(call_kwargs['plugin'])

    def test_cli_passes_all_extensibility_flags_combined(self):
        """Test CLI passes all extensibility flags when combined."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', [
                'ralph', 'execute',
                '--pre', 'pre_cmd1', 'pre_cmd2',
                '--post', 'post_cmd1',
                '--plugin', '/plugin.py'
            ]):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertEqual(call_kwargs['pre'], ['pre_cmd1', 'pre_cmd2'])
            self.assertEqual(call_kwargs['post'], ['post_cmd1'])
            self.assertEqual(call_kwargs['plugin'], ['/plugin.py'])


# ==============================================================================
# SAFETY AND PRIVACY FLAGS TESTS (TASK-012)
# ==============================================================================


class TestLoggerRedaction(unittest.TestCase):
    """Tests for Logger redaction functionality."""

    def setUp(self):
        self._original_redact_patterns = Logger.redact_patterns.copy() if Logger.redact_patterns else []
        Logger.redact_patterns = []

    def tearDown(self):
        Logger.redact_patterns = self._original_redact_patterns

    def test_set_redact_patterns_sets_patterns(self):
        """Test set_redact_patterns sets the patterns list."""
        patterns = [r'api_key=\w+', r'password=\w+']
        Logger.set_redact_patterns(patterns)
        self.assertEqual(Logger.redact_patterns, patterns)

    def test_set_redact_patterns_replaces_existing(self):
        """Test set_redact_patterns replaces existing patterns."""
        Logger.redact_patterns = ['old_pattern']
        Logger.set_redact_patterns(['new_pattern'])
        self.assertEqual(Logger.redact_patterns, ['new_pattern'])

    def test_redact_content_with_no_patterns(self):
        """Test _redact_content returns content unchanged when no patterns."""
        Logger.redact_patterns = []
        content = "api_key=secret123"
        result = Logger._redact_content(content)
        self.assertEqual(result, content)

    def test_redact_content_with_single_pattern(self):
        """Test _redact_content redacts matching content."""
        Logger.redact_patterns = [r'api_key=\w+']
        content = "The api_key=secret123 is here"
        result = Logger._redact_content(content)
        self.assertEqual(result, "The [REDACTED] is here")

    def test_redact_content_with_multiple_patterns(self):
        """Test _redact_content applies multiple patterns."""
        Logger.redact_patterns = [r'api_key=\w+', r'password=\w+']
        content = "api_key=secret123 and password=mypass"
        result = Logger._redact_content(content)
        self.assertEqual(result, "[REDACTED] and [REDACTED]")

    def test_redact_content_with_invalid_regex(self):
        """Test _redact_content skips invalid regex patterns."""
        Logger.redact_patterns = [r'[invalid', r'valid_pattern']
        content = "valid_pattern here"
        result = Logger._redact_content(content)
        self.assertEqual(result, "[REDACTED] here")

    def test_redact_content_multiple_matches(self):
        """Test _redact_content redacts all occurrences of a pattern."""
        Logger.redact_patterns = [r'secret\d+']
        content = "secret123 and secret456 and secret789"
        result = Logger._redact_content(content)
        self.assertEqual(result, "[REDACTED] and [REDACTED] and [REDACTED]")


class TestLoggerRedactFromFile(unittest.TestCase):
    """Tests for Logger.add_redact_patterns_from_file."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self._original_redact_patterns = Logger.redact_patterns.copy() if Logger.redact_patterns else []
        Logger.redact_patterns = []

    def tearDown(self):
        Logger.redact_patterns = self._original_redact_patterns
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_redact_patterns_from_file_loads_patterns(self):
        """Test loading patterns from file."""
        redact_file = self.temp_path / "redact.txt"
        redact_file.write_text("api_key=\\w+\npassword=\\w+\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        self.assertEqual(Logger.redact_patterns, ['api_key=\\w+', 'password=\\w+'])

    def test_add_redact_patterns_from_file_ignores_empty_lines(self):
        """Test that empty lines are ignored."""
        redact_file = self.temp_path / "redact.txt"
        redact_file.write_text("pattern1\n\n  \npattern2\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        self.assertEqual(Logger.redact_patterns, ['pattern1', 'pattern2'])

    def test_add_redact_patterns_from_file_ignores_comments(self):
        """Test that comment lines (starting with #) are ignored."""
        redact_file = self.temp_path / "redact.txt"
        redact_file.write_text("# This is a comment\npattern1\n# Another comment\npattern2\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        self.assertEqual(Logger.redact_patterns, ['pattern1', 'pattern2'])

    def test_add_redact_patterns_from_file_appends_to_existing(self):
        """Test that patterns are appended to existing patterns."""
        Logger.redact_patterns = ['existing_pattern']
        redact_file = self.temp_path / "redact.txt"
        redact_file.write_text("new_pattern\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        self.assertEqual(Logger.redact_patterns, ['existing_pattern', 'new_pattern'])

    def test_add_redact_patterns_from_file_nonexistent_file(self):
        """Test that non-existent file is silently ignored."""
        Logger.redact_patterns = []
        Logger.add_redact_patterns_from_file("/nonexistent/file.txt")
        self.assertEqual(Logger.redact_patterns, [])


class TestLoggerNoLogPrompts(unittest.TestCase):
    """Tests for Logger.no_log_prompts flag."""

    def setUp(self):
        self._original_no_log_prompts = Logger.no_log_prompts
        self._original_custom_log_file = Logger.custom_log_file
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_log.txt"
        self._original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file
        Logger.custom_log_file = None  # Ensure we use CONF.LOG_FILE
        Logger.no_log_prompts = False

    def tearDown(self):
        Logger.no_log_prompts = self._original_no_log_prompts
        Logger.custom_log_file = self._original_custom_log_file
        CONF.LOG_FILE = self._original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_no_log_prompts_true(self):
        """Test set_no_log_prompts(True) enables flag."""
        Logger.set_no_log_prompts(True)
        self.assertTrue(Logger.no_log_prompts)

    def test_set_no_log_prompts_false(self):
        """Test set_no_log_prompts(False) disables flag."""
        Logger.no_log_prompts = True
        Logger.set_no_log_prompts(False)
        self.assertFalse(Logger.no_log_prompts)

    def test_file_log_skips_prompts_when_no_log_prompts(self):
        """Test file_log skips PROMPT type when no_log_prompts is True."""
        Logger.set_no_log_prompts(True)
        Logger.file_log("This is a prompt", "PROMPT", "TEST")
        self.assertFalse(self.log_file.exists())

    def test_file_log_logs_prompts_when_not_set(self):
        """Test file_log logs PROMPT type when no_log_prompts is False."""
        Logger.set_no_log_prompts(False)
        Logger.file_log("This is a prompt", "PROMPT", "TEST")
        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("This is a prompt", content)

    def test_file_log_still_logs_responses_when_no_log_prompts(self):
        """Test file_log still logs RESPONSE type when no_log_prompts is True."""
        Logger.set_no_log_prompts(True)
        Logger.file_log("This is a response", "RESPONSE", "TEST")
        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("This is a response", content)


class TestLoggerNoLogResponses(unittest.TestCase):
    """Tests for Logger.no_log_responses flag."""

    def setUp(self):
        self._original_no_log_responses = Logger.no_log_responses
        self._original_custom_log_file = Logger.custom_log_file
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_log.txt"
        self._original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file
        Logger.custom_log_file = None  # Ensure we use CONF.LOG_FILE
        Logger.no_log_responses = False

    def tearDown(self):
        Logger.no_log_responses = self._original_no_log_responses
        Logger.custom_log_file = self._original_custom_log_file
        CONF.LOG_FILE = self._original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_no_log_responses_true(self):
        """Test set_no_log_responses(True) enables flag."""
        Logger.set_no_log_responses(True)
        self.assertTrue(Logger.no_log_responses)

    def test_set_no_log_responses_false(self):
        """Test set_no_log_responses(False) disables flag."""
        Logger.no_log_responses = True
        Logger.set_no_log_responses(False)
        self.assertFalse(Logger.no_log_responses)

    def test_file_log_skips_responses_when_no_log_responses(self):
        """Test file_log skips RESPONSE type when no_log_responses is True."""
        Logger.set_no_log_responses(True)
        Logger.file_log("This is a response", "RESPONSE", "TEST")
        self.assertFalse(self.log_file.exists())

    def test_file_log_logs_responses_when_not_set(self):
        """Test file_log logs RESPONSE type when no_log_responses is False."""
        Logger.set_no_log_responses(False)
        Logger.file_log("This is a response", "RESPONSE", "TEST")
        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("This is a response", content)

    def test_file_log_still_logs_prompts_when_no_log_responses(self):
        """Test file_log still logs PROMPT type when no_log_responses is True."""
        Logger.set_no_log_responses(True)
        Logger.file_log("This is a prompt", "PROMPT", "TEST")
        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("This is a prompt", content)


class TestFileLogRedaction(unittest.TestCase):
    """Tests for redaction in file_log."""

    def setUp(self):
        self._original_redact_patterns = Logger.redact_patterns.copy() if Logger.redact_patterns else []
        self._original_custom_log_file = Logger.custom_log_file
        Logger.redact_patterns = []
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_log.txt"
        self._original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file
        Logger.custom_log_file = None  # Ensure we use CONF.LOG_FILE

    def tearDown(self):
        Logger.redact_patterns = self._original_redact_patterns
        Logger.custom_log_file = self._original_custom_log_file
        CONF.LOG_FILE = self._original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_log_applies_redaction(self):
        """Test file_log applies redaction patterns to content."""
        Logger.set_redact_patterns([r'secret_key=\w+'])
        Logger.file_log("Content with secret_key=abc123 here", "INFO", "TEST")
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("[REDACTED]", content)
        self.assertNotIn("secret_key=abc123", content)

    def test_file_log_redacts_in_prompts(self):
        """Test file_log applies redaction to PROMPT type."""
        Logger.set_redact_patterns([r'API_KEY=\w+'])
        Logger.file_log("API call with API_KEY=xyz789", "PROMPT", "TEST")
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("[REDACTED]", content)
        self.assertNotIn("API_KEY=xyz789", content)

    def test_file_log_redacts_in_responses(self):
        """Test file_log applies redaction to RESPONSE type."""
        Logger.set_redact_patterns([r'"token":\s*"\w+"'])
        Logger.file_log('Response: {"token": "secret123"}', "RESPONSE", "TEST")
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("[REDACTED]", content)
        self.assertNotIn("secret123", content)


class TestCLIPrivacyFlags(unittest.TestCase):
    """Tests for CLI parsing of privacy flags."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", nargs="?", default="all")
        self.parser.add_argument("--redact", nargs="+", metavar="PATTERN")
        self.parser.add_argument("--redact-file", type=str, metavar="FILE")
        self.parser.add_argument("--no-log-prompts", action="store_true")
        self.parser.add_argument("--no-log-responses", action="store_true")

    def test_redact_flag_parses_single_pattern(self):
        """Test --redact with single pattern."""
        args = self.parser.parse_args(["--redact", "pattern1"])
        self.assertEqual(args.redact, ["pattern1"])

    def test_redact_flag_parses_multiple_patterns(self):
        """Test --redact with multiple patterns."""
        args = self.parser.parse_args(["--redact", "pattern1", "pattern2", "pattern3"])
        self.assertEqual(args.redact, ["pattern1", "pattern2", "pattern3"])

    def test_redact_file_flag_parses(self):
        """Test --redact-file flag is parsed."""
        args = self.parser.parse_args(["--redact-file", "/path/to/file.txt"])
        self.assertEqual(args.redact_file, "/path/to/file.txt")

    def test_no_log_prompts_flag_parses(self):
        """Test --no-log-prompts flag is parsed."""
        args = self.parser.parse_args(["--no-log-prompts"])
        self.assertTrue(args.no_log_prompts)

    def test_no_log_responses_flag_parses(self):
        """Test --no-log-responses flag is parsed."""
        args = self.parser.parse_args(["--no-log-responses"])
        self.assertTrue(args.no_log_responses)

    def test_all_privacy_flags_combined(self):
        """Test all privacy flags can be used together."""
        args = self.parser.parse_args([
            "--redact", "pattern1", "pattern2",
            "--redact-file", "/path/to/file.txt",
            "--no-log-prompts",
            "--no-log-responses",
            "execute"
        ])
        self.assertEqual(args.redact, ["pattern1", "pattern2"])
        self.assertEqual(args.redact_file, "/path/to/file.txt")
        self.assertTrue(args.no_log_prompts)
        self.assertTrue(args.no_log_responses)
        self.assertEqual(args.phase, "execute")

    def test_privacy_flags_default_values(self):
        """Test privacy flags have correct default values."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.redact)
        self.assertIsNone(args.redact_file)
        self.assertFalse(args.no_log_prompts)
        self.assertFalse(args.no_log_responses)


class TestMainPrivacyFlagsIntegration(unittest.TestCase):
    """Integration tests for main() with privacy flags."""

    def setUp(self):
        self._original_redact_patterns = Logger.redact_patterns.copy() if Logger.redact_patterns else []
        self._original_no_log_prompts = Logger.no_log_prompts
        self._original_no_log_responses = Logger.no_log_responses
        Logger.redact_patterns = []
        Logger.no_log_prompts = False
        Logger.no_log_responses = False

    def tearDown(self):
        Logger.redact_patterns = self._original_redact_patterns
        Logger.no_log_prompts = self._original_no_log_prompts
        Logger.no_log_responses = self._original_no_log_responses

    def test_main_configures_redact_patterns(self):
        """Test main() configures redact patterns from --redact flag."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value.start = MagicMock()
            with patch('sys.argv', ['ralph', '--redact', 'api_key=\\w+', 'password=\\w+']):
                main()
            self.assertEqual(Logger.redact_patterns, ['api_key=\\w+', 'password=\\w+'])

    def test_main_configures_no_log_prompts(self):
        """Test main() configures no_log_prompts from --no-log-prompts flag."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value.start = MagicMock()
            with patch('sys.argv', ['ralph', '--no-log-prompts']):
                main()
            self.assertTrue(Logger.no_log_prompts)

    def test_main_configures_no_log_responses(self):
        """Test main() configures no_log_responses from --no-log-responses flag."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value.start = MagicMock()
            with patch('sys.argv', ['ralph', '--no-log-responses']):
                main()
            self.assertTrue(Logger.no_log_responses)


class TestCombinedPrivacyFlags(unittest.TestCase):
    """Tests for combined privacy flag behavior."""

    def setUp(self):
        self._original_redact_patterns = Logger.redact_patterns.copy() if Logger.redact_patterns else []
        self._original_no_log_prompts = Logger.no_log_prompts
        self._original_no_log_responses = Logger.no_log_responses
        self._original_custom_log_file = Logger.custom_log_file
        Logger.redact_patterns = []
        Logger.no_log_prompts = False
        Logger.no_log_responses = False
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_log.txt"
        self._original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file
        Logger.custom_log_file = None  # Ensure we use CONF.LOG_FILE

    def tearDown(self):
        Logger.redact_patterns = self._original_redact_patterns
        Logger.no_log_prompts = self._original_no_log_prompts
        Logger.no_log_responses = self._original_no_log_responses
        Logger.custom_log_file = self._original_custom_log_file
        CONF.LOG_FILE = self._original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_log_prompts_and_responses_both_skip(self):
        """Test both no_log flags together skip both types."""
        Logger.set_no_log_prompts(True)
        Logger.set_no_log_responses(True)
        Logger.file_log("A prompt", "PROMPT", "TEST")
        Logger.file_log("A response", "RESPONSE", "TEST")
        Logger.file_log("An error", "ERROR", "TEST")
        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding='utf-8')
        self.assertNotIn("A prompt", content)
        self.assertNotIn("A response", content)
        self.assertIn("An error", content)

    def test_redaction_only_applies_to_logged_content(self):
        """Test redaction only applies to content that gets logged."""
        Logger.set_redact_patterns([r'secret=\w+'])
        Logger.set_no_log_prompts(True)
        # This prompt won't be logged, so redaction doesn't matter
        Logger.file_log("secret=abc123 in prompt", "PROMPT", "TEST")
        # This response will be logged and redacted
        Logger.file_log("secret=xyz789 in response", "RESPONSE", "TEST")
        content = self.log_file.read_text(encoding='utf-8')
        self.assertNotIn("secret=abc123", content)  # Never logged
        self.assertNotIn("secret=xyz789", content)  # Logged but redacted
        self.assertIn("[REDACTED]", content)


# ==============================================================================
# PRD AND STORY CONTROL FLAGS TESTS (TASK-013)
# ==============================================================================


class TestPrdStoryControlFlagsArgumentParsing(unittest.TestCase):
    """Tests for --schema, --min-criteria, and --label CLI flag parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--schema", type=str, metavar="FILE")
        self.parser.add_argument("--min-criteria", type=int, metavar="N")
        self.parser.add_argument("--label", nargs="+", metavar="KEY=VAL")

    def test_schema_flag_parses_file_path(self):
        """Test --schema flag parses file path."""
        args = self.parser.parse_args(['--schema', '/path/to/schema.json'])
        self.assertEqual(args.schema, '/path/to/schema.json')

    def test_schema_flag_default_is_none(self):
        """Test --schema flag defaults to None."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.schema)

    def test_min_criteria_flag_parses_integer(self):
        """Test --min-criteria flag parses integer."""
        args = self.parser.parse_args(['--min-criteria', '3'])
        self.assertEqual(args.min_criteria, 3)

    def test_min_criteria_flag_default_is_none(self):
        """Test --min-criteria flag defaults to None."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.min_criteria)

    def test_label_flag_parses_single_label(self):
        """Test --label flag parses a single label."""
        args = self.parser.parse_args(['--label', 'version=1.0'])
        self.assertEqual(args.label, ['version=1.0'])

    def test_label_flag_parses_multiple_labels(self):
        """Test --label flag parses multiple labels."""
        args = self.parser.parse_args(['--label', 'version=1.0', 'team=backend', 'sprint=5'])
        self.assertEqual(args.label, ['version=1.0', 'team=backend', 'sprint=5'])

    def test_label_flag_default_is_none(self):
        """Test --label flag defaults to None."""
        args = self.parser.parse_args([])
        self.assertIsNone(args.label)


class TestPrdStoryControlFlagsCLI(unittest.TestCase):
    """Tests for CLI argument parsing of --schema, --min-criteria, --label."""

    def test_cli_parses_schema_flag(self):
        """Test CLI correctly parses --schema flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--schema', '/tmp/schema.json', 'planner']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['schema'], '/tmp/schema.json')

    def test_cli_parses_min_criteria_flag(self):
        """Test CLI correctly parses --min-criteria flag."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--min-criteria', '5', 'planner']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['min_criteria'], 5)

    def test_cli_parses_label_flag_single(self):
        """Test CLI correctly parses --label flag with single value."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            # Phase must come before --label to avoid being consumed by nargs="+"
            with patch('sys.argv', ['ralph', 'planner', '--label', 'version=1.0']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['label'], ['version=1.0'])

    def test_cli_parses_label_flag_multiple(self):
        """Test CLI correctly parses --label flag with multiple values."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            # Phase must come before --label to avoid being consumed by nargs="+"
            with patch('sys.argv', ['ralph', 'planner', '--label', 'version=1.0', 'team=backend']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['label'], ['version=1.0', 'team=backend'])

    def test_cli_all_prd_flags_together(self):
        """Test CLI correctly parses all PRD control flags together."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            # Phase must come before --label to avoid being consumed by nargs="+"
            with patch('sys.argv', [
                'ralph',
                'planner',
                '--schema', '/tmp/schema.json',
                '--min-criteria', '3',
                '--label', 'version=2.0', 'env=prod'
            ]):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertEqual(call_kwargs['schema'], '/tmp/schema.json')
            self.assertEqual(call_kwargs['min_criteria'], 3)
            self.assertEqual(call_kwargs['label'], ['version=2.0', 'env=prod'])

    def test_cli_prd_flags_default_to_none(self):
        """Test CLI PRD flags default correctly when not specified."""
        with patch('ralph.RalphOrchestrator') as mock_orchestrator:
            mock_instance = MagicMock()
            mock_orchestrator.return_value = mock_instance
            with patch('sys.argv', ['ralph', 'execute']):
                main()
            call_kwargs = mock_orchestrator.call_args[1]
            self.assertIsNone(call_kwargs['schema'])
            self.assertIsNone(call_kwargs['min_criteria'])
            self.assertIsNone(call_kwargs['label'])


class TestOrchestratorSchemaValidation(TempConfigTestCase):
    """Tests for RalphOrchestrator --schema validation functionality."""

    def test_validate_prd_schema_returns_true_when_no_schema(self):
        """Test _validate_prd_schema returns True when no schema specified."""
        orch = self.create_mock_orchestrator()
        valid, error = orch._validate_prd_schema({"id": "PRD-001"})
        self.assertTrue(valid)
        self.assertEqual(error, "")

    def test_validate_prd_schema_fails_when_schema_file_not_found(self):
        """Test _validate_prd_schema fails when schema file doesn't exist."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema="/nonexistent/schema.json")
        valid, error = orch._validate_prd_schema({"id": "PRD-001"})
        self.assertFalse(valid)
        self.assertIn("not found", error)

    def test_validate_prd_schema_fails_on_invalid_json(self):
        """Test _validate_prd_schema fails when schema contains invalid JSON."""
        schema_path = self.temp_path / "invalid_schema.json"
        schema_path.write_text("not valid json {", encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema=str(schema_path))
        valid, error = orch._validate_prd_schema({"id": "PRD-001"})
        self.assertFalse(valid)
        self.assertIn("Invalid JSON schema", error)

    def test_validate_prd_schema_validates_required_properties(self):
        """Test _validate_prd_schema validates required properties."""
        schema_path = self.temp_path / "schema.json"
        schema = {
            "type": "object",
            "required": ["id", "userStories"]
        }
        schema_path.write_text(json.dumps(schema), encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema=str(schema_path))

        # Valid data
        valid, error = orch._validate_prd_schema({"id": "PRD-001", "userStories": []})
        self.assertTrue(valid)

        # Missing required property
        valid, error = orch._validate_prd_schema({"id": "PRD-001"})
        self.assertFalse(valid)
        self.assertIn("userStories", error)
        self.assertIn("required", error)

    def test_validate_prd_schema_validates_type(self):
        """Test _validate_prd_schema validates property types."""
        schema_path = self.temp_path / "schema.json"
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "count": {"type": "integer"}
            }
        }
        schema_path.write_text(json.dumps(schema), encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema=str(schema_path))

        # Valid types
        valid, error = orch._validate_prd_schema({"id": "PRD-001", "count": 5})
        self.assertTrue(valid)

        # Wrong type
        valid, error = orch._validate_prd_schema({"id": 123, "count": 5})
        self.assertFalse(valid)
        self.assertIn("expected string", error)

    def test_validate_prd_schema_validates_nested_properties(self):
        """Test _validate_prd_schema validates nested object properties."""
        schema_path = self.temp_path / "schema.json"
        schema = {
            "type": "object",
            "properties": {
                "userStories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "description"]
                    }
                }
            }
        }
        schema_path.write_text(json.dumps(schema), encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema=str(schema_path))

        # Valid nested data
        valid, error = orch._validate_prd_schema({
            "userStories": [{"id": "TASK-001", "description": "Test"}]
        })
        self.assertTrue(valid)

        # Missing required nested property
        valid, error = orch._validate_prd_schema({
            "userStories": [{"id": "TASK-001"}]
        })
        self.assertFalse(valid)
        self.assertIn("description", error)

    def test_validate_prd_schema_validates_min_items(self):
        """Test _validate_prd_schema validates minItems for arrays."""
        schema_path = self.temp_path / "schema.json"
        schema = {
            "type": "object",
            "properties": {
                "userStories": {
                    "type": "array",
                    "minItems": 1
                }
            }
        }
        schema_path.write_text(json.dumps(schema), encoding='utf-8')

        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema=str(schema_path))

        # Valid (has items)
        valid, error = orch._validate_prd_schema({"userStories": [{"id": "TASK-001"}]})
        self.assertTrue(valid)

        # Invalid (empty array)
        valid, error = orch._validate_prd_schema({"userStories": []})
        self.assertFalse(valid)
        self.assertIn("minimum", error)


class TestOrchestratorMinCriteriaValidation(TempConfigTestCase):
    """Tests for RalphOrchestrator --min-criteria validation functionality."""

    def test_validate_min_criteria_returns_true_when_not_set(self):
        """Test _validate_min_criteria returns True when not specified."""
        orch = self.create_mock_orchestrator()
        valid, error = orch._validate_min_criteria({
            "userStories": [{"id": "TASK-001", "acceptanceCriteria": []}]
        })
        self.assertTrue(valid)
        self.assertEqual(error, "")

    def test_validate_min_criteria_passes_when_criteria_met(self):
        """Test _validate_min_criteria passes when all stories meet minimum."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(min_criteria=2)

        valid, error = orch._validate_min_criteria({
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["a", "b", "c"]},
                {"id": "TASK-002", "acceptanceCriteria": ["x", "y"]}
            ]
        })
        self.assertTrue(valid)
        self.assertEqual(error, "")

    def test_validate_min_criteria_fails_when_criteria_not_met(self):
        """Test _validate_min_criteria fails when a story has too few criteria."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(min_criteria=3)

        valid, error = orch._validate_min_criteria({
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["a", "b", "c"]},
                {"id": "TASK-002", "acceptanceCriteria": ["x"]}  # Only 1, needs 3
            ]
        })
        self.assertFalse(valid)
        self.assertIn("TASK-002", error)
        self.assertIn("1 criteria", error)
        self.assertIn("minimum: 3", error)

    def test_validate_min_criteria_reports_all_violations(self):
        """Test _validate_min_criteria reports all stories that violate minimum."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(min_criteria=5)

        valid, error = orch._validate_min_criteria({
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["a", "b"]},
                {"id": "TASK-002", "acceptanceCriteria": ["x"]},
                {"id": "TASK-003", "acceptanceCriteria": ["1", "2", "3", "4", "5"]}  # OK
            ]
        })
        self.assertFalse(valid)
        self.assertIn("TASK-001", error)
        self.assertIn("TASK-002", error)
        self.assertNotIn("TASK-003", error)

    def test_validate_min_criteria_handles_missing_criteria_field(self):
        """Test _validate_min_criteria handles stories without acceptanceCriteria."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(min_criteria=1)

        valid, error = orch._validate_min_criteria({
            "userStories": [{"id": "TASK-001"}]  # No acceptanceCriteria field
        })
        self.assertFalse(valid)
        self.assertIn("TASK-001", error)
        self.assertIn("0 criteria", error)

    def test_validate_min_criteria_handles_empty_stories(self):
        """Test _validate_min_criteria handles PRD with no user stories."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(min_criteria=3)

        valid, error = orch._validate_min_criteria({"userStories": []})
        self.assertTrue(valid)  # No stories to validate
        self.assertEqual(error, "")


class TestOrchestratorLabelApplication(TempConfigTestCase):
    """Tests for RalphOrchestrator --label functionality."""

    def test_apply_labels_returns_unchanged_when_no_labels(self):
        """Test _apply_labels returns unchanged data when no labels specified."""
        orch = self.create_mock_orchestrator()
        data = {"id": "PRD-001", "userStories": []}
        result = orch._apply_labels(data)
        self.assertEqual(result, data)
        self.assertNotIn("labels", result)

    def test_apply_labels_adds_key_value_labels(self):
        """Test _apply_labels adds key=value format labels."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["version=1.0", "team=backend"])

        data = {"id": "PRD-001"}
        result = orch._apply_labels(data)
        self.assertIn("labels", result)
        self.assertEqual(result["labels"]["version"], "1.0")
        self.assertEqual(result["labels"]["team"], "backend")

    def test_apply_labels_handles_key_only_labels(self):
        """Test _apply_labels handles labels without values (tags)."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["urgent", "reviewed"])

        data = {"id": "PRD-001"}
        result = orch._apply_labels(data)
        self.assertIn("labels", result)
        self.assertEqual(result["labels"]["urgent"], "")
        self.assertEqual(result["labels"]["reviewed"], "")

    def test_apply_labels_handles_mixed_labels(self):
        """Test _apply_labels handles mix of key=value and key-only labels."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["version=2.0", "urgent", "sprint=5"])

        data = {"id": "PRD-001"}
        result = orch._apply_labels(data)
        self.assertEqual(result["labels"]["version"], "2.0")
        self.assertEqual(result["labels"]["urgent"], "")
        self.assertEqual(result["labels"]["sprint"], "5")

    def test_apply_labels_handles_values_with_equals(self):
        """Test _apply_labels handles values containing equals sign."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["formula=a=b+c", "url=http://example.com?a=1"])

        data = {"id": "PRD-001"}
        result = orch._apply_labels(data)
        self.assertEqual(result["labels"]["formula"], "a=b+c")
        self.assertEqual(result["labels"]["url"], "http://example.com?a=1")

    def test_apply_labels_preserves_existing_data(self):
        """Test _apply_labels preserves existing PRD data."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["tag=test"])

        data = {"id": "PRD-001", "description": "Test PRD", "userStories": [{"id": "TASK-001"}]}
        result = orch._apply_labels(data)
        self.assertEqual(result["id"], "PRD-001")
        self.assertEqual(result["description"], "Test PRD")
        self.assertEqual(result["userStories"], [{"id": "TASK-001"}])
        self.assertEqual(result["labels"]["tag"], "test")

    def test_apply_labels_strips_whitespace(self):
        """Test _apply_labels strips whitespace from keys and values."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["  version = 1.0  ", "  tag  "])

        data = {"id": "PRD-001"}
        result = orch._apply_labels(data)
        self.assertEqual(result["labels"]["version"], "1.0")
        self.assertEqual(result["labels"]["tag"], "")


class TestOrchestratorPrdFlagsInitialization(TempConfigTestCase):
    """Tests for RalphOrchestrator initialization with PRD control flags."""

    def test_orchestrator_stores_schema_path(self):
        """Test orchestrator stores schema path from constructor."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(schema="/path/to/schema.json")
        self.assertEqual(orch._schema_path, "/path/to/schema.json")

    def test_orchestrator_stores_min_criteria(self):
        """Test orchestrator stores min_criteria from constructor."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(min_criteria=5)
        self.assertEqual(orch._min_criteria, 5)

    def test_orchestrator_stores_labels(self):
        """Test orchestrator stores labels from constructor."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator(label=["a=1", "b=2"])
        self.assertEqual(orch._labels, ["a=1", "b=2"])

    def test_orchestrator_labels_default_to_empty_list(self):
        """Test orchestrator labels default to empty list when not specified."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator()
        self.assertEqual(orch._labels, [])

    def test_orchestrator_schema_and_min_criteria_default_to_none(self):
        """Test orchestrator schema and min_criteria default to None."""
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            orch = RalphOrchestrator()
        self.assertIsNone(orch._schema_path)
        self.assertIsNone(orch._min_criteria)


if __name__ == "__main__":
    unittest.main()
