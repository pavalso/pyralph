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
    Config, CONF, JsonUtils, Logger, MemoryManager, PRDManager, PromptFormatter,
    RalphOrchestrator, Shell, TemplateManager, get_version, main,
)
from hooks import Event, EventType, HookManager, PythonHook, ExecutableHook, FunctionHook


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

    def create_mock_orchestrator(self, agent_name="mock", mock_agent=None, **kwargs):
        if mock_agent is None:
            mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            return RalphOrchestrator(agent_name=agent_name, **kwargs)


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
        hook_file = self.hooks_dir / name
        hook_file.write_text(content, encoding='utf-8')
        return hook_file


class LoggerTestCase(unittest.TestCase):
    """Base test class for Logger tests with proper state reset."""

    def setUp(self):
        # Store internal descriptor values for proper state save/restore
        self._logger_state = {
            'no_color': Logger.no_color,
            '_verbose_value': Logger._verbose_value,
            '_verbosity_value': Logger._verbosity_value,
            'quiet': Logger.quiet, 'no_emoji': Logger.no_emoji, 'log_level': Logger.log_level,
            'json_output': Logger.json_output, 'ndjson_output': Logger.ndjson_output,
            'redact_patterns': Logger.redact_patterns.copy() if Logger.redact_patterns else [],
            'no_log_prompts': Logger.no_log_prompts, 'no_log_responses': Logger.no_log_responses,
        }
        # Reset to defaults - use descriptors for verbose/verbosity to ensure sync
        Logger.no_color = Logger.quiet = Logger.no_emoji = False
        Logger.verbosity = 0  # This syncs verbose via descriptor
        Logger.log_level = 20
        Logger.json_output = Logger.ndjson_output = Logger.no_log_prompts = Logger.no_log_responses = False
        Logger.redact_patterns = []
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "test_log.txt"
        self._original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file
        self.held_output = StringIO()
        self.original_stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self.original_stdout
        # Restore internal descriptor values directly to avoid sync side-effects
        for attr, value in self._logger_state.items():
            setattr(Logger, attr, value)
        CONF.LOG_FILE = self._original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)


# ==============================================================================
# CONFIG TESTS
# ==============================================================================


class TestConfig(unittest.TestCase):
    """Tests for Config dataclass."""

    PATH_CASES = [
        ("BASE_DIR", Path.cwd(), None), ("ROOT_DIR", ".ralph", "BASE_DIR"),
        ("MEMORY_DIR", "memory", "ROOT_DIR"), ("ARCHIVE_DIR", "archive", "ROOT_DIR"),
        ("TEMPLATES_DIR", "templates", "ROOT_DIR"), ("HOOKS_DIR", "hooks", "ROOT_DIR"),
        ("PRD_FILE", "prd.json", "ROOT_DIR"), ("PROGRESS_FILE", "progress.txt", "ROOT_DIR"),
        ("LOG_FILE", "ralph_log.txt", "ROOT_DIR"),
    ]
    SCALAR_CASES = [("MAX_RETRIES", 3), ("TIMEOUT_SECONDS", 600)]
    CREATED_DIRS = ["ROOT_DIR", "MEMORY_DIR", "ARCHIVE_DIR", "TEMPLATES_DIR", "HOOKS_DIR"]

    def test_defaults(self):
        config = Config()
        for attr, suffix, base in self.PATH_CASES:
            with self.subTest(attr=attr):
                expected = suffix if base is None else getattr(config, base) / suffix
                self.assertEqual(getattr(config, attr), expected)
        for attr, expected in self.SCALAR_CASES:
            with self.subTest(attr=attr):
                self.assertEqual(getattr(config, attr), expected)

    def test_ensure_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = Config(BASE_DIR=base, ROOT_DIR=base/".ralph", MEMORY_DIR=base/".ralph"/"memory",
                          ARCHIVE_DIR=base/".ralph"/"archive", TEMPLATES_DIR=base/".ralph"/"templates",
                          HOOKS_DIR=base/".ralph"/"hooks")
            for d in self.CREATED_DIRS:
                self.assertFalse(getattr(config, d).exists())
            config.ensure_directories()
            config.ensure_directories()  # Idempotent
            for d in self.CREATED_DIRS:
                self.assertTrue(getattr(config, d).is_dir())


# ==============================================================================
# LOGGER TESTS
# ==============================================================================


class TestLogger(LoggerTestCase):
    """Tests for Logger functionality."""

    def test_toggles(self):
        for setter, attr in [(Logger.set_no_color, 'no_color'), (Logger.set_verbose, 'verbose')]:
            setter(True)
            self.assertTrue(getattr(Logger, attr))
            setter(False)
            self.assertFalse(getattr(Logger, attr))

    def test_colors_keys(self):
        self.assertEqual(set(Logger.COLORS.keys()), {"RESET", "GREEN", "RED", "CYAN", "YELLOW", "MAGENTA"})

    def test_info_debug_output(self):
        cases = [
            (False, True, 'info', "Plain", None, ["Plain"], ["\033["]),
            (False, False, 'info', "Colored", "GREEN", ["\033[92m", "Colored"], []),
            (False, False, 'debug', "Debug", None, [], ["Debug"]),
            (True, True, 'debug', "Debug", None, ["[DEBUG] Debug"], ["\033["]),
        ]
        for verbose, no_color, method, msg, color, exp_in, exp_not in cases:
            with self.subTest(method=method, verbose=verbose):
                self.held_output = StringIO()
                sys.stdout = self.held_output
                Logger.set_verbose(verbose)
                Logger.set_no_color(no_color)
                getattr(Logger, method)(msg, color) if color else getattr(Logger, method)(msg)
                output = self.held_output.getvalue()
                for e in exp_in:
                    self.assertIn(e, output)
                for e in exp_not:
                    self.assertNotIn(e, output)
                sys.stdout = self.original_stdout

    def test_file_log(self):
        cases = [("PROMPT", "TAG", "➡️"), ("RESPONSE", "TAG", "⬅️"), ("ERROR", "TAG", "❌"), ("INFO", None, "ℹ️")]
        for log_type, tag, icon in cases:
            with self.subTest(log_type=log_type):
                if self.log_file.exists():
                    self.log_file.unlink()
                Logger.file_log("Content", log_type, tag) if tag else Logger.file_log("Content", log_type)
                self.assertIn(icon, self.log_file.read_text(encoding="utf-8"))

    def test_verbosity_levels(self):
        cases = [(0, 'debug', False), (1, 'debug', True), (0, 'trace', False), (2, 'trace', True),
                 (0, 'ultra', False), (3, 'ultra', True)]
        for verbosity, method, should_output in cases:
            with self.subTest(verbosity=verbosity, method=method):
                self.held_output = StringIO()
                sys.stdout = self.held_output
                Logger.set_verbosity(verbosity)
                Logger.set_no_color(True)
                getattr(Logger, method)("test")
                output = self.held_output.getvalue()
                self.assertEqual("test" in output, should_output)
                sys.stdout = self.original_stdout

    def test_verbosity_sync_and_clamp(self):
        Logger.set_verbosity(0)
        self.assertFalse(Logger.verbose)
        Logger.set_verbosity(1)
        self.assertTrue(Logger.verbose)
        Logger.set_verbosity(-5)
        self.assertEqual(Logger.verbosity, 0)
        Logger.set_verbosity(10)
        self.assertEqual(Logger.verbosity, 3)

    def test_verbosity_property_sync_direct_assignment(self):
        """Test that direct assignment to verbose/verbosity stays synchronized."""
        # Direct assignment to verbosity should sync verbose
        Logger.verbosity = 0
        self.assertFalse(Logger.verbose)
        self.assertEqual(Logger.verbosity, 0)

        Logger.verbosity = 2
        self.assertTrue(Logger.verbose)
        self.assertEqual(Logger.verbosity, 2)

        # Direct assignment to verbose should sync verbosity
        Logger.verbose = False
        self.assertFalse(Logger.verbose)
        self.assertEqual(Logger.verbosity, 0)

        Logger.verbose = True
        self.assertTrue(Logger.verbose)
        self.assertEqual(Logger.verbosity, 1)

        # Verify clamping works with direct assignment
        Logger.verbosity = -10
        self.assertEqual(Logger.verbosity, 0)
        self.assertFalse(Logger.verbose)

        Logger.verbosity = 100
        self.assertEqual(Logger.verbosity, 3)
        self.assertTrue(Logger.verbose)

    def test_quiet_mode(self):
        Logger.set_no_color(True)
        Logger.set_quiet(True)
        sys.stdout = self.held_output
        Logger.info("suppressed")
        self.assertEqual(self.held_output.getvalue(), "")
        Logger.warning("warning")
        Logger.error("error")
        output = self.held_output.getvalue()
        self.assertIn("warning", output)
        self.assertIn("error", output)

    def test_no_emoji(self):
        Logger.set_no_color(True)
        Logger.set_no_emoji(True)
        sys.stdout = self.held_output
        Logger.info("🤖 Robot ✅")
        output = self.held_output.getvalue()
        self.assertIn("[BOT]", output)
        self.assertNotIn("🤖", output)

    def test_strip_emoji_all(self):
        emojis = ["🤖", "🕵️", "🧠", "🚀", "✅", "❌", "⚠️", "▶️", "🔒", "🛑", "⏭️", "📋", "📦", "🎉", "➡️", "⬅️", "ℹ️", "❓"]
        for emoji in emojis:
            result = Logger._strip_emoji(f"Test {emoji} msg")
            self.assertNotIn(emoji, result)


class TestLoggerRedaction(LoggerTestCase):
    """Tests for Logger redaction."""

    def test_set_redact_patterns(self):
        Logger.set_redact_patterns(['p1', 'p2'])
        self.assertEqual(Logger.redact_patterns, ['p1', 'p2'])
        Logger.set_redact_patterns(['new'])
        self.assertEqual(Logger.redact_patterns, ['new'])

    def test_redact_content(self):
        cases = [
            ([], "api_key=secret", "api_key=secret"),
            ([r'api_key=\w+'], "The api_key=secret here", "The [REDACTED] here"),
            ([r'api_key=\w+', r'pwd=\w+'], "api_key=x pwd=y", "[REDACTED] [REDACTED]"),
            ([r'secret\d+'], "secret1 secret2 secret3", "[REDACTED] [REDACTED] [REDACTED]"),
        ]
        for patterns, content, expected in cases:
            Logger.redact_patterns = patterns
            self.assertEqual(Logger._redact_content(content), expected)

    def test_redact_from_file(self):
        redact_file = Path(self.temp_dir) / "redact.txt"
        redact_file.write_text("pattern1\n\n# comment\npattern2\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        self.assertEqual(Logger.redact_patterns, ['pattern1', 'pattern2'])

    def test_no_log_prompts_responses(self):
        for flag, log_type, other_type in [
            ('no_log_prompts', 'PROMPT', 'RESPONSE'),
            ('no_log_responses', 'RESPONSE', 'PROMPT'),
        ]:
            with self.subTest(flag=flag):
                if self.log_file.exists():
                    self.log_file.unlink()
                setattr(Logger, flag, True)
                Logger.file_log("skip", log_type, "T")
                self.assertFalse(self.log_file.exists())
                Logger.file_log("keep", other_type, "T")
                self.assertIn("keep", self.log_file.read_text(encoding='utf-8'))
                setattr(Logger, flag, False)

    def test_file_log_redaction(self):
        Logger.set_redact_patterns([r'secret_key=\w+'])
        Logger.file_log("secret_key=abc123", "INFO", "T")
        content = self.log_file.read_text(encoding='utf-8')
        self.assertIn("[REDACTED]", content)
        self.assertNotIn("abc123", content)


class TestLoggerJsonOutput(LoggerTestCase):
    """Tests for Logger JSON output modes."""

    def test_json_output_mode(self):
        Logger.json_output = True
        sys.stdout = self.held_output
        Logger.info("test message")
        output = self.held_output.getvalue().strip()
        parsed = json.loads(output)
        self.assertEqual(parsed["message"], "test message")
        self.assertEqual(parsed["level"], "info")

    def test_ndjson_output_mode(self):
        Logger.ndjson_output = True
        sys.stdout = self.held_output
        Logger.info("msg1")
        Logger.info("msg2")
        lines = [l for l in self.held_output.getvalue().strip().split('\n') if l]
        self.assertEqual(len(lines), 2)
        for line in lines:
            json.loads(line)

    def test_log_level_filtering(self):
        # Note: debug() uses verbosity control (tested in test_verbosity_levels)
        # This test focuses on log_level filtering for info/warning methods
        cases = [(20, 'info', True), (30, 'info', False), (30, 'warning', True), (40, 'warning', False)]
        for level, method, should_output in cases:
            with self.subTest(level=level, method=method):
                Logger.log_level = level
                Logger.set_no_color(True)
                Logger.set_verbosity(0)
                self.held_output = StringIO()
                sys.stdout = self.held_output
                getattr(Logger, method)("test")
                self.assertEqual("test" in self.held_output.getvalue(), should_output)
                sys.stdout = self.original_stdout


# ==============================================================================
# SHELL TESTS
# ==============================================================================


class TestShell(unittest.TestCase):
    """Tests for Shell class."""

    def test_run_basic(self):
        stdout, stderr, code = Shell.run("echo hello")
        self.assertIn("hello", stdout)
        self.assertEqual(code, 0)
        _, _, code = Shell.run("exit 1")
        self.assertEqual(code, 1)

    def test_run_timeout(self):
        with patch('ralph.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd="t", timeout=1)):
            stdout, stderr, code = Shell.run("cmd", timeout=1)
        self.assertEqual(stdout, "")
        self.assertIn("Timed Out", stderr)

    def test_get_file_tree(self):
        result = Shell.get_file_tree()
        self.assertIsInstance(result, str)
        for excluded in [".git", ".ralph", "__pycache__"]:
            for line in result.split('\n'):
                # Use Path.name for consistent cross-platform path parsing
                cleaned = line.strip().lstrip('├─└│ ')
                entry = Path(cleaned).name if cleaned else ''
                self.assertNotEqual(entry, excluded)

    def test_get_file_tree_params(self):
        with patch('ralph.Shell.run', return_value=("out", "", 0)) as mock:
            Shell.get_file_tree(depth=5, ignore=['build'])
            call_args = mock.call_args[0][0]
            self.assertIn("-L 5", call_args)
            self.assertIn("-I 'build'", call_args)


# ==============================================================================
# JSON UTILS TESTS
# ==============================================================================


class TestJsonUtils(unittest.TestCase):
    """Tests for JsonUtils.parse()."""

    CASES = [
        ('{"key": "value"}', {"key": "value"}),
        ('{}', {}),
        ('{"active": true}', {"active": True}),
        ('```\n{"key": "value"}\n```', {"key": "value"}),
        ('```json\n{"key": "value"}\n```', {"key": "value"}),
        ('Here is:\n{"key": "value"}', {"key": "value"}),
        ('{"key": "value"// comment\n}', {"key": "value"}),
    ]
    INVALID = ['not json', '{"key": ', 'text']

    def test_parse_valid(self):
        for input_text, expected in self.CASES:
            with self.subTest(input=input_text[:20]):
                self.assertEqual(JsonUtils.parse(input_text), expected)

    def test_parse_invalid(self):
        for input_text in self.INVALID:
            with self.subTest(input=input_text):
                with self.assertRaises(Exception):
                    JsonUtils.parse(input_text)


# ==============================================================================
# PRD MANAGER TESTS
# ==============================================================================


class TestPRDManager(TempConfigTestCase):
    """Tests for PRDManager class."""

    def setUp(self):
        super().setUp()
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        self.prd_path = CONF.PRD_FILE
        self.manager = PRDManager(self.prd_path)

    def test_exists_false_when_no_file(self):
        self.assertFalse(self.manager.exists())

    def test_exists_true_when_file_present(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        self.assertTrue(self.manager.exists())

    def test_load_parses_json(self):
        prd_data = {"id": "PRD-001", "userStories": []}
        self.prd_path.write_text(json.dumps(prd_data), encoding='utf-8')
        loaded = self.manager.load()
        self.assertEqual(loaded["id"], "PRD-001")
        self.assertEqual(loaded["userStories"], [])

    def test_load_caches_result(self):
        prd_data = {"id": "PRD-001"}
        self.prd_path.write_text(json.dumps(prd_data), encoding='utf-8')
        first_load = self.manager.load()
        # Modify file on disk
        self.prd_path.write_text('{"id": "PRD-002"}', encoding='utf-8')
        # Should still return cached data
        second_load = self.manager.load()
        self.assertEqual(first_load, second_load)
        self.assertEqual(second_load["id"], "PRD-001")

    def test_read_raw_returns_string(self):
        content = '{"id": "PRD-001", "description": "Test"}'
        self.prd_path.write_text(content, encoding='utf-8')
        raw = self.manager.read_raw()
        self.assertEqual(raw, content)

    def test_read_raw_caches_result(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        first_read = self.manager.read_raw()
        self.prd_path.write_text('{"id": "PRD-002"}', encoding='utf-8')
        second_read = self.manager.read_raw()
        self.assertEqual(first_read, second_read)

    def test_save_writes_to_disk(self):
        prd_data = {"id": "PRD-001", "userStories": [{"id": "T-001"}]}
        self.manager.save(prd_data)
        content = self.prd_path.read_text(encoding='utf-8')
        loaded = json.loads(content)
        self.assertEqual(loaded["id"], "PRD-001")

    def test_save_updates_cache(self):
        prd_data = {"id": "PRD-001"}
        self.manager.save(prd_data)
        # Load should return cached data without reading disk
        loaded = self.manager.load()
        self.assertEqual(loaded["id"], "PRD-001")

    def test_save_formats_with_indent(self):
        prd_data = {"id": "PRD-001"}
        self.manager.save(prd_data)
        content = self.prd_path.read_text(encoding='utf-8')
        # Check for indentation (pretty-printed JSON)
        self.assertIn('\n', content)
        self.assertIn('  ', content)

    def test_invalidate_cache_clears_both_caches(self):
        prd_data = {"id": "PRD-001"}
        self.prd_path.write_text(json.dumps(prd_data), encoding='utf-8')
        self.manager.load()
        self.manager.read_raw()
        self.assertIsNotNone(self.manager._cache)
        self.assertIsNotNone(self.manager._raw_cache)
        self.manager.invalidate_cache()
        self.assertIsNone(self.manager._cache)
        self.assertIsNone(self.manager._raw_cache)

    def test_invalidate_cache_forces_disk_read(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        first_load = self.manager.load()
        self.prd_path.write_text('{"id": "PRD-002"}', encoding='utf-8')
        self.manager.invalidate_cache()
        second_load = self.manager.load()
        self.assertEqual(first_load["id"], "PRD-001")
        self.assertEqual(second_load["id"], "PRD-002")

    def test_delete_removes_file(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        self.assertTrue(self.manager.exists())
        self.manager.delete()
        self.assertFalse(self.manager.exists())

    def test_delete_clears_cache(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        self.manager.load()
        self.manager.delete()
        self.assertIsNone(self.manager._cache)
        self.assertIsNone(self.manager._raw_cache)

    def test_delete_no_error_when_file_missing(self):
        self.assertFalse(self.manager.exists())
        # Should not raise
        self.manager.delete()

    def test_load_raises_on_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            self.manager.load()

    def test_load_raises_on_invalid_json(self):
        self.prd_path.write_text('not valid json', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            self.manager.load()


# ==============================================================================
# MEMORY MANAGER TESTS
# ==============================================================================


class TestMemoryManager(TempConfigTestCase):
    """Tests for MemoryManager."""

    def setUp(self):
        super().setUp()
        self.memory = MemoryManager()

    def test_validate_memory(self):
        result = self.memory.validate_memory()
        self.assertEqual(set(result.keys()), {'valid', 'corrupted', 'empty', 'total'})

    def test_validate_scenarios(self):
        cases = [
            (lambda: None, True, 0),
            (lambda: (CONF.MEMORY_DIR.mkdir(parents=True), (CONF.MEMORY_DIR/"t.md").write_text("c", encoding="utf-8")), True, 1),
            (lambda: (CONF.MEMORY_DIR.mkdir(parents=True), (CONF.MEMORY_DIR/"e.md").write_text("", encoding="utf-8")), False, 1),
        ]
        for setup, exp_valid, exp_total in cases:
            self.tearDown()
            self.setUp()
            setup()
            result = self.memory.validate_memory()
            self.assertEqual(result['valid'], exp_valid)
            self.assertEqual(result['total'], exp_total)

    def test_get_structure(self):
        self.assertEqual(self.memory.get_structure(), "(Memory Empty)")
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("c", encoding="utf-8")
        self.assertIn("arch.md", self.memory.get_structure())

    def test_extract_test_command(self):
        self.assertEqual(self.memory.extract_test_command(), "pytest")
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `npm test`\n", encoding="utf-8")
        self.assertEqual(self.memory.extract_test_command(), "npm test")

    def test_compile_patterns(self):
        patterns = ["*.md", "test_*"]
        compiled = MemoryManager._compile_patterns(patterns)
        self.assertEqual(len(compiled), 2)
        for p in compiled:
            self.assertIsNotNone(p.pattern)

    def test_matches_compiled_full_path(self):
        patterns = ["*.md"]
        compiled = MemoryManager._compile_patterns(patterns)
        CONF.MEMORY_DIR.mkdir(parents=True)
        test_file = CONF.MEMORY_DIR / "test.md"
        test_file.write_text("content", encoding="utf-8")
        self.assertTrue(MemoryManager._matches_compiled(test_file, compiled))

    def test_matches_compiled_no_match(self):
        patterns = ["*.txt"]
        compiled = MemoryManager._compile_patterns(patterns)
        CONF.MEMORY_DIR.mkdir(parents=True)
        test_file = CONF.MEMORY_DIR / "test.md"
        test_file.write_text("content", encoding="utf-8")
        self.assertFalse(MemoryManager._matches_compiled(test_file, compiled))

    def test_iter_memory_files_empty(self):
        files = list(MemoryManager._iter_memory_files())
        self.assertEqual(files, [])

    def test_iter_memory_files_with_content(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "a.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "b.md").write_text("b", encoding="utf-8")
        files = list(MemoryManager._iter_memory_files())
        self.assertEqual(len(files), 2)

    def test_iter_memory_files_skips_hidden(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / ".hidden").write_text("h", encoding="utf-8")
        (CONF.MEMORY_DIR / "visible.md").write_text("v", encoding="utf-8")
        files = list(MemoryManager._iter_memory_files())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "visible.md")

    def test_get_filtered_files_empty(self):
        files = MemoryManager.get_filtered_files()
        self.assertEqual(files, [])

    def test_get_filtered_files_include_pattern(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("n", encoding="utf-8")
        files = MemoryManager.get_filtered_files(include=["*.md"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "arch.md")

    def test_get_filtered_files_exclude_pattern(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("n", encoding="utf-8")
        files = MemoryManager.get_filtered_files(exclude=["*.txt"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "arch.md")

    def test_get_filtered_files_include_and_exclude(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "test_arch.md").write_text("t", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("n", encoding="utf-8")
        files = MemoryManager.get_filtered_files(include=["*.md"], exclude=["test_*"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "arch.md")

    def test_get_filtered_files_limit(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        for i in range(5):
            (CONF.MEMORY_DIR / f"file{i}.md").write_text(f"{i}", encoding="utf-8")
        files = MemoryManager.get_filtered_files(limit=2)
        self.assertEqual(len(files), 2)

    def test_get_filtered_files_sorted(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "zebra.md").write_text("z", encoding="utf-8")
        (CONF.MEMORY_DIR / "alpha.md").write_text("a", encoding="utf-8")
        files = MemoryManager.get_filtered_files()
        self.assertEqual(files[0].name, "alpha.md")
        self.assertEqual(files[1].name, "zebra.md")

    def test_matches_pattern_backward_compat(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        test_file = CONF.MEMORY_DIR / "test.md"
        test_file.write_text("content", encoding="utf-8")
        self.assertTrue(MemoryManager._matches_pattern(test_file, "*.md"))
        self.assertFalse(MemoryManager._matches_pattern(test_file, "*.txt"))


# ==============================================================================
# ORCHESTRATOR TESTS
# ==============================================================================


class TestRalphOrchestrator(TempConfigTestCase):
    """Tests for RalphOrchestrator."""

    def test_init(self):
        mock_agent = self.create_mock_agent()
        with patch('ralph.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            self.assertIsNotNone(orch.agent)
            self.assertIsInstance(orch.memory, MemoryManager)
            mock_agent.check_dependencies.assert_called_once()
            mock_agent.set_logger.assert_called_once_with(Logger)
            mock_agent.set_config.assert_called_once_with(CONF)

    def test_init_deps_fail_exits(self):
        mock_agent = self.create_mock_agent(check_deps=False)
        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.sys.exit') as mock_exit:
                RalphOrchestrator(agent_name="mock")
                mock_exit.assert_called_once_with(1)

    def test_init_ensures_directories(self):
        self.assertFalse(CONF.ROOT_DIR.exists())
        with patch('ralph.get_agent', return_value=self.create_mock_agent()):
            RalphOrchestrator(agent_name="mock")
            for p in [CONF.ROOT_DIR, CONF.MEMORY_DIR, CONF.ARCHIVE_DIR]:
                self.assertTrue(p.exists())

    def test_unknown_agent_raises(self):
        with self.assertRaises(ValueError):
            get_agent("nonexistent_agent")

    def test_archive_prd(self):
        orch = self.create_mock_orchestrator()
        prd_content = '{"id": "PRD-001"}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')
        orch._archive_prd()
        self.assertFalse(CONF.PRD_FILE.exists())
        archived = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(encoding='utf-8'), prd_content)

    def test_architect_requires_arch_md(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent)
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("c", encoding="utf-8")
        with patch('ralph.sys.exit') as mock_exit, patch('ralph.Logger.info'):
            orch.run_architect("test")
            mock_exit.assert_called_once_with(1)
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")
        with patch('ralph.sys.exit') as mock_exit, patch('ralph.Logger.info'):
            orch.run_architect("test")
            mock_exit.assert_not_called()


class TestOrchestratorFlags(TempConfigTestCase):
    """Tests for orchestrator flag storage."""

    FLAG_CASES = [
        # (kwarg, attr, value, default)
        ('tree_depth', '_tree_depth', 5, 2),
        ('tree_ignore', '_tree_ignore', ["build"], None),
        ('memory_out', '_memory_out', "/out.md", None),
        ('test_cmd', '_test_cmd_override', "npm test", None),
        ('skip_verify', '_skip_verify', True, False),
        ('retries', '_retries_override', 5, None),
        ('timeout', '_timeout_override', 300, None),
        ('only', '_only_tasks', ["T-1"], None),
        ('except_tasks', '_except_tasks', ["T-2"], None),
        ('resume', '_resume_from', "T-3", None),
        ('intent', '_intent', "Test intent", None),
        ('intent_file', '_intent_file', "/path", None),
        ('prompt_file', '_prompt_file_override', "/prompt", None),
        ('include', '_include_patterns', ["arch"], None),
        ('exclude', '_exclude_patterns', ["tasks"], None),
        ('non_interactive', '_non_interactive', True, False),
        ('ci', '_ci', True, False),
        ('status_check', '_status_check', True, False),
        ('pre', '_pre_commands', ["echo"], []),
        ('post', '_post_commands', ["done"], []),
        ('plugin', '_plugin_paths', ["/p.py"], []),
        ('schema', '_schema_path', "/schema.json", None),
        ('min_criteria', '_min_criteria', 3, None),
        ('label', '_labels', ["type=bug"], []),
    ]

    def test_flag_storage(self):
        for kwarg, attr, value, default in self.FLAG_CASES:
            with self.subTest(kwarg=kwarg):
                orch = self.create_mock_orchestrator(**{kwarg: value})
                self.assertEqual(getattr(orch, attr), value)

    def test_flag_defaults(self):
        orch = self.create_mock_orchestrator()
        for kwarg, attr, value, default in self.FLAG_CASES:
            if default is not None or attr in ('_tree_depth', '_skip_verify', '_git_enabled',
                                                '_non_interactive', '_ci', '_status_check', '_labels',
                                                '_pre_commands', '_post_commands', '_plugin_paths'):
                with self.subTest(attr=attr):
                    self.assertEqual(getattr(orch, attr), default)


class TestOrchestratorIntentHandling(TempConfigTestCase):
    """Tests for orchestrator intent handling."""

    def test_get_intent_inline(self):
        orch = self.create_mock_orchestrator(intent="Build CLI")
        self.assertEqual(orch._get_intent(), "Build CLI")

    def test_get_intent_from_file(self):
        intent_file = self.temp_path / "intent.txt"
        intent_file.write_text("Build webapp", encoding='utf-8')
        orch = self.create_mock_orchestrator(intent_file=str(intent_file))
        self.assertEqual(orch._get_intent(), "Build webapp")

    def test_get_intent_file_not_found_exits(self):
        orch = self.create_mock_orchestrator(intent_file="/nonexistent")
        with patch('ralph.sys.exit', side_effect=SystemExit(1)), patch('ralph.Logger.error'):
            with self.assertRaises(SystemExit):
                orch._get_intent()

    def test_get_intent_empty_file_exits(self):
        intent_file = self.temp_path / "empty.txt"
        intent_file.write_text("", encoding='utf-8')
        orch = self.create_mock_orchestrator(intent_file=str(intent_file))
        with patch('ralph.sys.exit', side_effect=SystemExit(1)), patch('ralph.Logger.error'):
            with self.assertRaises(SystemExit):
                orch._get_intent()


class TestOrchestratorExportMemory(TempConfigTestCase):
    """Tests for orchestrator memory export."""

    def test_export_memory(self):
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("# Arch\nContent", encoding="utf-8")
        (CONF.MEMORY_DIR / "tasks.md").write_text("Tasks", encoding="utf-8")
        (CONF.MEMORY_DIR / ".hidden").write_text("hidden", encoding="utf-8")
        orch = self.create_mock_orchestrator()
        output = self.temp_path / "out" / "ctx.md"
        orch._export_memory(str(output))
        content = output.read_text(encoding='utf-8')
        self.assertIn("Arch", content)
        self.assertIn("Tasks", content)
        self.assertNotIn("hidden", content)

    def test_export_memory_empty_warns(self):
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        orch = self.create_mock_orchestrator()
        with patch('ralph.Logger.warning') as mock_warn:
            orch._export_memory(str(self.temp_path / "out.md"))
            mock_warn.assert_called_with("No memory files to export.")


class TestOrchestratorValidation(TempConfigTestCase):
    """Tests for PRD schema and criteria validation."""

    def test_schema_validation(self):
        schema_file = self.temp_path / "schema.json"
        schema_file.write_text('{"type": "object", "required": ["title"]}', encoding='utf-8')
        orch = self.create_mock_orchestrator(schema=str(schema_file))
        valid, _ = orch._validate_prd_schema({"title": "Test"})
        self.assertTrue(valid)
        valid, _ = orch._validate_prd_schema({})
        self.assertFalse(valid)

    def test_schema_file_not_found(self):
        orch = self.create_mock_orchestrator(schema="/nonexistent.json")
        valid, _ = orch._validate_prd_schema({"title": "Test"})
        self.assertFalse(valid)

    def test_min_criteria_validation(self):
        orch = self.create_mock_orchestrator(min_criteria=3)
        prd_pass = {"userStories": [{"acceptanceCriteria": ["a", "b", "c"]}]}
        prd_fail = {"userStories": [{"acceptanceCriteria": ["a", "b"]}]}
        valid, _ = orch._validate_min_criteria(prd_pass)
        self.assertTrue(valid)
        valid, _ = orch._validate_min_criteria(prd_fail)
        self.assertFalse(valid)

    def test_label_application(self):
        orch = self.create_mock_orchestrator(label=["type=bug", "priority"])
        prd = {"id": "PRD-001"}
        result = orch._apply_labels(prd)
        self.assertEqual(result["labels"]["type"], "bug")
        self.assertIn("priority", result["labels"])
        self.assertEqual(result["labels"]["priority"], "")


# ==============================================================================
# CLI ARGUMENT TESTS
# ==============================================================================


class TestCliArguments(unittest.TestCase):
    """Tests for CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--accept-all", "-y", action="store_true")
        self.parser.add_argument("-v", "--verbose", action="count", default=0)
        self.parser.add_argument("--quiet", "-q", action="store_true")
        self.parser.add_argument("--no-color", action="store_true")
        self.parser.add_argument("--no-emoji", action="store_true")
        self.parser.add_argument("--agent", choices=list_agents(), default=list_agents()[0])
        self.parser.add_argument("--no-hooks", action="store_true")
        self.parser.add_argument("--hooks", nargs="+")
        self.parser.add_argument("--intent", type=str)
        self.parser.add_argument("--intent-file", type=str)
        self.parser.add_argument("--enhance-intent", action="store_true")
        self.parser.add_argument("--enhance-intent-strict", action="store_true")
        self.parser.add_argument("--prompt-file", type=str)
        self.parser.add_argument("--tree-depth", type=int, default=2)
        self.parser.add_argument("--tree-ignore", nargs="+")
        self.parser.add_argument("--memory-out", type=str)
        self.parser.add_argument("--test-cmd", type=str)
        self.parser.add_argument("--skip-verify", action="store_true")
        self.parser.add_argument("--retries", type=int)
        self.parser.add_argument("--timeout", type=int)
        self.parser.add_argument("--only", nargs="+")
        self.parser.add_argument("--except", dest="except_tasks", nargs="+")
        self.parser.add_argument("--resume", type=str)
        self.parser.add_argument("--include-memory", nargs="+")
        self.parser.add_argument("--exclude-memory", nargs="+")
        self.parser.add_argument("--non-interactive", action="store_true")
        self.parser.add_argument("--ci", action="store_true")
        self.parser.add_argument("--status-check", action="store_true")
        self.parser.add_argument("--parallel", type=int)
        self.parser.add_argument("--batch-size", type=int)
        self.parser.add_argument("--pre", nargs="+")
        self.parser.add_argument("--post", nargs="+")
        self.parser.add_argument("--plugin", nargs="+")
        self.parser.add_argument("--redact", nargs="+")
        self.parser.add_argument("--redact-file", type=str)
        self.parser.add_argument("--no-log-prompts", action="store_true")
        self.parser.add_argument("--no-log-responses", action="store_true")
        self.parser.add_argument("--schema", type=str)
        self.parser.add_argument("--min-criteria", type=int)
        self.parser.add_argument("--label", nargs="+")

    def test_phases(self):
        for phase in ["architect", "planner", "execute", "all"]:
            self.assertEqual(self.parser.parse_args([phase]).phase, phase)
        self.assertEqual(self.parser.parse_args([]).phase, "all")

    def test_verbosity(self):
        self.assertEqual(self.parser.parse_args(["-v"]).verbose, 1)
        self.assertEqual(self.parser.parse_args(["-vv"]).verbose, 2)
        self.assertEqual(self.parser.parse_args(["-vvv"]).verbose, 3)

    def test_flags_combined(self):
        args = self.parser.parse_args([
            "-vvv", "-q", "--no-color", "--no-emoji", "-y",
            "--intent", "test", "--tree-depth", "5", "--test-cmd", "npm test",
            "--only", "T-1", "T-2", "--retries", "3", "execute"
        ])
        self.assertEqual(args.verbose, 3)
        self.assertTrue(args.quiet)
        self.assertTrue(args.no_color)
        self.assertEqual(args.intent, "test")
        self.assertEqual(args.tree_depth, 5)
        self.assertEqual(args.test_cmd, "npm test")
        self.assertEqual(args.only, ["T-1", "T-2"])
        self.assertEqual(args.retries, 3)

    def test_get_version_and_list_agents(self):
        self.assertIsInstance(get_version(), str)
        agents = list_agents()
        self.assertIn("claude", agents)
        self.assertIn("copilot", agents)


class TestMainIntentValidation(unittest.TestCase):
    """Tests for main() validation."""

    def test_rejects_both_intent_flags(self):
        with patch('sys.argv', ['ralph', '--intent', 'Test', '--intent-file', 'f.txt']):
            with patch('ralph.sys.exit') as mock_exit, patch('ralph.Logger.error'), patch('ralph.RalphOrchestrator'):
                main()
                mock_exit.assert_called_with(1)


class TestMainCLIPassthrough(unittest.TestCase):
    """Tests for main() passing flags to orchestrator."""

    FLAG_TESTS = [
        (['--intent', 'test'], {'intent': 'test'}),
        (['--tree-depth', '5'], {'tree_depth': 5}),
        (['--test-cmd', 'npm test'], {'test_cmd': 'npm test'}),
        (['--skip-verify'], {'skip_verify': True}),
        (['--retries', '3'], {'retries': 3}),
        (['--timeout', '300'], {'timeout': 300}),
        (['--only', 'T-1', 'T-2'], {'only': ['T-1', 'T-2']}),
        (['--resume', 'T-3'], {'resume': 'T-3'}),
        (['--non-interactive'], {'non_interactive': True}),
        (['--ci'], {'ci': True}),
        (['--status-check'], {'status_check': True}),
        (['execute', '--pre', 'echo', 'before'], {'pre': ['echo', 'before']}),
        (['execute', '--post', 'echo', 'done'], {'post': ['echo', 'done']}),
        (['--schema', '/schema.json'], {'schema': '/schema.json'}),
        (['--min-criteria', '3'], {'min_criteria': 3}),
        (['--label', 'type=bug'], {'label': ['type=bug']}),
    ]

    def test_cli_passes_flags(self):
        for cli_args, expected_kwargs in self.FLAG_TESTS:
            with self.subTest(args=cli_args):
                with patch('ralph.RalphOrchestrator') as mock_orch:
                    mock_orch.return_value = MagicMock()
                    with patch('sys.argv', ['ralph'] + cli_args):
                        main()
                    call_kwargs = mock_orch.call_args[1]
                    for key, value in expected_kwargs.items():
                        self.assertEqual(call_kwargs.get(key), value)


# ==============================================================================
# AGENT ERROR TESTS
# ==============================================================================


class TestAgentError(unittest.TestCase):
    """Tests for AgentError."""

    def test_from_exception(self):
        try:
            raise ValueError("test error")
        except ValueError as e:
            error = AgentError.from_exception(e, "TestAgent", "T-001")
        self.assertEqual(error.exception_type, "ValueError")
        self.assertEqual(error.message, "test error")
        self.assertIn("Traceback", error.stack_trace)
        dt.fromisoformat(error.timestamp)

    def test_format_log_entry(self):
        try:
            raise TypeError("type error")
        except TypeError as e:
            error = AgentError.from_exception(e, "Copilot", "T-007")
        log = error.format_log_entry()
        for exp in ["AGENT ERROR", "Copilot", "T-007", "TypeError"]:
            self.assertIn(exp, log)

    def test_agent_error_handling(self):
        for AgentClass in [ClaudeAgent, GithubAgent]:
            with self.subTest(agent=AgentClass.__name__):
                agent = AgentClass(timeout_seconds=5)
                with patch('subprocess.run', return_value=MagicMock(returncode=1, stdout="", stderr="")):
                    with patch('shutil.which', return_value='/usr/bin/agent'):
                        success, output, error = agent.run("test", "TAG")
                self.assertFalse(success)
                self.assertIsInstance(error, AgentError)


# ==============================================================================
# EVENT TESTS
# ==============================================================================


class TestEvent(unittest.TestCase):
    """Tests for Event and EventType."""

    EVENTS = [
        "PHASE_START", "PHASE_END", "ARCHITECT_START", "ARCHITECT_SUCCESS", "ARCHITECT_FAILURE",
        "PLANNER_START", "PLANNER_SUCCESS", "PLANNER_FAILURE", "EXECUTE_START", "EXECUTE_END",
        "TASK_START", "TASK_SUCCESS", "TASK_FAILURE", "TASK_RETRY",
        "VERIFICATION_START", "VERIFICATION_SUCCESS", "VERIFICATION_FAILURE",
        "PRD_CREATED", "PRD_ARCHIVED", "ERROR",
    ]

    def test_event_types_exist(self):
        for name in self.EVENTS:
            self.assertTrue(hasattr(EventType, name))
        # 20 original + 11 IssueWatcher events
        # 20 original + 11 IssueWatcher events + 3 Intent Enhancement events = 34
        self.assertEqual(len(EventType), 34)

    def test_event_creation_serialization(self):
        event = Event(EventType.TASK_SUCCESS, phase="execute", task_id="T-001", metadata={"k": "v"})
        self.assertEqual(event.phase, "execute")
        dt.fromisoformat(event.timestamp)
        result = event.to_dict()
        self.assertEqual(result["event_type"], "TASK_SUCCESS")
        parsed = json.loads(event.to_json())
        self.assertEqual(parsed["event_type"], "TASK_SUCCESS")

    def test_all_event_types_serialize(self):
        for et in EventType:
            parsed = json.loads(Event(et).to_json())
            self.assertEqual(parsed["event_type"], et.name)


# ==============================================================================
# HOOK MANAGER TESTS
# ==============================================================================


class TestHookManager(TempHooksTestCase):
    """Tests for HookManager."""

    def test_enable_disable(self):
        self.assertTrue(self.manager.is_enabled)
        self.manager.disable()
        self.assertFalse(self.manager.is_enabled)
        self.manager.enable()
        self.assertTrue(self.manager.is_enabled)

    def test_discover(self):
        self.assertEqual(self.manager.discover(), 0)
        self.create_hook_file("_private.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        self.assertEqual(self.manager.discover(), 0)
        self.create_hook_file("valid.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        self.assertEqual(HookManager(self.hooks_dir).discover(), 1)

    def test_discover_rejects_invalid(self):
        for name, content in [("no_ev.py", 'def on_event(e): pass'), ("no_h.py", 'EVENTS = ["TASK_START"]')]:
            self.tearDown()
            self.setUp()
            self.create_hook_file(name, content)
            self.assertEqual(HookManager(self.hooks_dir, MagicMock()).discover(), 0)

    def test_emit_priority_order(self):
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
        self.manager.set_enabled_hooks(["a"])
        self.assertTrue(self.manager.is_hook_enabled("a"))
        self.assertFalse(self.manager.is_hook_enabled("b"))


class TestHookModification(TempHooksTestCase):
    """Tests for hook modification."""

    def test_modifying_hook(self):
        self.create_hook_file("mod.py", '''
from hooks import Event
EVENTS = ["TASK_START"]
MODIFIES_DATA = True
def on_event(e):
    return Event(event_type=e.event_type, task_id="MOD-" + (e.task_id or ""))
''')
        self.manager.discover()
        result = self.manager.emit(Event(EventType.TASK_START, task_id="T-001"))
        self.assertEqual(result.task_id, "MOD-T-001")

    def test_non_modifying_ignored(self):
        self.create_hook_file("obs.py", '''
from hooks import Event
EVENTS = ["TASK_START"]
def on_event(e):
    return Event(event_type=e.event_type, task_id="IGNORED")
''')
        self.manager.discover()
        result = self.manager.emit(Event(EventType.TASK_START, task_id="ORIG"))
        self.assertEqual(result.task_id, "ORIG")


class TestHookTypes(TempHooksTestCase):
    """Tests for hook types."""

    def test_python_hook_parse_events(self):
        self.assertEqual(PythonHook._parse_events(["TASK_START", "task_success"]),
                        {EventType.TASK_START, EventType.TASK_SUCCESS})
        self.assertEqual(PythonHook._parse_events(["INVALID"]), set())

    def test_executable_hook(self):
        hook = ExecutableHook(self.temp_path/"t.sh", {EventType.TASK_START}, priority=50, timeout=10.0, modifies_data=True)
        self.assertEqual(hook.name, "t.sh")
        self.assertEqual(hook.priority, 50)
        self.assertTrue(hook.modifies_data)

    def test_executable_hook_parse_modified(self):
        orig = Event(EventType.TASK_START, task_id="T-001", phase="execute")
        result = ExecutableHook._parse_modified_event('{"task_id": "MOD"}', orig)
        self.assertEqual(result.task_id, "MOD")
        self.assertIsNone(ExecutableHook._parse_modified_event("bad", orig))

    def test_function_hook(self):
        called = []
        hook = FunctionHook("t", lambda e: called.append(e.task_id), {EventType.TASK_START})
        hook.execute(Event(EventType.TASK_START, task_id="T-001"))
        self.assertEqual(called, ["T-001"])

    def test_function_hook_modification(self):
        hook = FunctionHook("t", lambda e: Event(e.event_type, task_id="MOD"),
                           {EventType.TASK_START}, modifies_data=True)
        result = hook.execute(Event(EventType.TASK_START, task_id="ORIG"))
        self.assertEqual(result.task_id, "MOD")


class TestHookBehavior(TempHooksTestCase):
    """Tests for hook isolation and concurrency."""

    def test_exception_isolation(self):
        log = []
        self.manager.register_hook("fail", lambda e: (_ for _ in ()).throw(RuntimeError()), ["TASK_START"], priority=10)
        self.manager.register_hook("ok", lambda e: log.append("ok"), ["TASK_START"], priority=20)
        self.manager.emit(Event(EventType.TASK_START))
        self.assertIn("ok", log)

    def test_concurrent_emit(self):
        results = []
        lock = threading.Lock()
        self.manager.register_hook("h", lambda e: (lock.acquire(), results.append(e.task_id), lock.release()), ["TASK_START"])
        threads = [threading.Thread(target=lambda i=i: self.manager.emit(Event(EventType.TASK_START, task_id=f"T-{i}")))
                  for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 10)


# ==============================================================================
# TEMPLATE MANAGER TESTS
# ==============================================================================


class TestTemplateManager(unittest.TestCase):
    """Tests for TemplateManager."""

    def test_template_load(self):
        template = TemplateManager.load("developer.txt")
        self.assertIn("# ROLE", template)
        self.assertIn("Developer", template)
        self.assertIn("{{task_id}}", template)

    def test_template_render(self):
        result = TemplateManager.render("architect.txt", user_intent="Test", file_tree="tree")
        self.assertIn("Test", result)
        self.assertIn("tree", result)

    def test_default_templates_exist(self):
        for name in ["architect.txt", "planner.txt", "developer.txt"]:
            template = TemplateManager.load(name)
            self.assertIsInstance(template, str)
            self.assertTrue(len(template) > 0)


class TestFormatAcceptanceCriteria(TempConfigTestCase):
    """Tests for acceptance criteria formatting (TASK-003)."""

    def test_format_acceptance_criteria_with_criteria(self):
        """Should format acceptance criteria as bulleted list."""
        orch = self.create_mock_orchestrator()
        task = {
            'id': 'TASK-001',
            'description': 'Test task',
            'acceptanceCriteria': ['Criterion 1', 'Criterion 2', 'Criterion 3']
        }
        result = orch._format_acceptance_criteria(task)
        self.assertIn("- Criterion 1", result)
        self.assertIn("- Criterion 2", result)
        self.assertIn("- Criterion 3", result)

    def test_format_acceptance_criteria_empty(self):
        """Should return default message when no criteria."""
        orch = self.create_mock_orchestrator()
        task = {
            'id': 'TASK-001',
            'description': 'Test task',
            'acceptanceCriteria': []
        }
        result = orch._format_acceptance_criteria(task)
        self.assertIn("No acceptance criteria specified", result)

    def test_format_acceptance_criteria_missing(self):
        """Should return default message when criteria field missing."""
        orch = self.create_mock_orchestrator()
        task = {
            'id': 'TASK-001',
            'description': 'Test task'
        }
        result = orch._format_acceptance_criteria(task)
        self.assertIn("No acceptance criteria specified", result)


# ==============================================================================
# HEADLESS/CI MODE TESTS
# ==============================================================================


class TestHeadlessMode(TempConfigTestCase):
    """Tests for headless and CI mode behavior."""

    def test_non_interactive_flag(self):
        orch = self.create_mock_orchestrator(non_interactive=True)
        self.assertTrue(orch._non_interactive)

    def test_ci_mode_sets_flags(self):
        orch = self.create_mock_orchestrator(ci=True)
        self.assertTrue(orch._ci)

    def test_status_check_flag(self):
        orch = self.create_mock_orchestrator(status_check=True)
        self.assertTrue(orch._status_check)


# ==============================================================================
# EXTENSIBILITY FLAGS TESTS
# ==============================================================================


class TestExtensibilityFlags(TempConfigTestCase):
    """Tests for extensibility flags."""

    def test_pre_post_commands(self):
        orch = self.create_mock_orchestrator(pre=["echo before"], post=["echo after"])
        self.assertEqual(orch._pre_commands, ["echo before"])
        self.assertEqual(orch._post_commands, ["echo after"])

    def test_plugin_paths_stored(self):
        orch = self.create_mock_orchestrator(plugin=["/path/to/plugin.py"])
        self.assertEqual(orch._plugin_paths, ["/path/to/plugin.py"])


# ==============================================================================
# PRD STORY CONTROL FLAGS TESTS
# ==============================================================================


class TestPrdStoryControlFlags(unittest.TestCase):
    """Tests for PRD story control flag parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--schema", type=str)
        self.parser.add_argument("--min-criteria", type=int)
        self.parser.add_argument("--label", nargs="+")

    def test_flag_parsing(self):
        args = self.parser.parse_args(["--schema", "/s.json", "--min-criteria", "3", "--label", "t=bug", "p"])
        self.assertEqual(args.schema, "/s.json")
        self.assertEqual(args.min_criteria, 3)
        self.assertEqual(args.label, ["t=bug", "p"])

    def test_defaults(self):
        args = self.parser.parse_args([])
        self.assertIsNone(args.schema)
        self.assertIsNone(args.min_criteria)
        self.assertIsNone(args.label)


# ==============================================================================
# PROGRESS AND LIFECYCLE TESTS
# ==============================================================================


class TestEventLifecycle(TempConfigTestCase):
    """Tests for event lifecycle."""

    def test_phase_events_emitted(self):
        events = []
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent)
        orch.hooks.register_hook("capture", lambda e: events.append(e.event_type), ["PHASE_START", "PHASE_END"])
        CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("c", encoding="utf-8")
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")
        with patch('ralph.Logger.info'), patch('ralph.Shell.get_file_tree', return_value="tree"):
            orch.run_architect("test")
        self.assertIn(EventType.PHASE_START, events)
        self.assertIn(EventType.PHASE_END, events)


# ==============================================================================
# ISSUE WATCHER TESTS (TASK-006)
# ==============================================================================

from fetch_ready_issues import (
    Issue, IssueStore, IssueStoreError, StoredIssue,
    ProcessingQueue, ProcessingQueueError, QueueItem,
    PromptTransformer, PromptTransformerError, TransformedPrompt,
    IssueWatcher, WatcherConfig, WatcherStatus, IssueWatcherError,
    create_watcher_parser, watcher_main,
    GitHubPoller, PollerConfig,
)


class IssueWatcherTestCase(unittest.TestCase):
    """Base test class for IssueWatcher tests with temp directories."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.store_dir = str(self.temp_path / "issues")
        self.queue_dir = str(self.temp_path / "queue")
        self.pid_file = str(self.temp_path / "watcher.pid")
        self.log_file = str(self.temp_path / "watcher.log")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_sample_issue(self, number=1, title="Test Issue", body="Test body"):
        return Issue(
            number=number,
            title=title,
            body=body,
            url=f"https://github.com/test/repo/issues/{number}",
            labels=["ready"]
        )


class TestIssueStore(IssueWatcherTestCase):
    """Tests for IssueStore class."""

    def test_save_and_get(self):
        store = IssueStore(self.store_dir)
        issue = self.create_sample_issue()
        stored = store.save(issue)
        self.assertEqual(stored.number, 1)
        self.assertEqual(stored.title, "Test Issue")
        self.assertEqual(stored.status, "pending")
        retrieved = store.get(1)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.number, 1)

    def test_exists(self):
        store = IssueStore(self.store_dir)
        self.assertFalse(store.exists(1))
        store.save(self.create_sample_issue())
        self.assertTrue(store.exists(1))

    def test_delete(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue())
        self.assertTrue(store.delete(1))
        self.assertFalse(store.exists(1))
        self.assertFalse(store.delete(999))

    def test_list_issues(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue(1))
        store.save(self.create_sample_issue(2))
        issues = store.list_issues()
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].number, 1)
        self.assertEqual(issues[1].number, 2)

    def test_list_issues_by_status(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue(1), status="pending")
        store.save(self.create_sample_issue(2), status="completed")
        pending = store.list_issues(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].number, 1)

    def test_update_status(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue())
        updated = store.update_status(1, "completed")
        self.assertEqual(updated.status, "completed")
        retrieved = store.get(1)
        self.assertEqual(retrieved.status, "completed")

    def test_count(self):
        store = IssueStore(self.store_dir)
        self.assertEqual(store.count(), 0)
        store.save(self.create_sample_issue(1))
        store.save(self.create_sample_issue(2))
        self.assertEqual(store.count(), 2)

    def test_clear(self):
        store = IssueStore(self.store_dir)
        store.save(self.create_sample_issue(1))
        store.save(self.create_sample_issue(2))
        deleted = store.clear()
        self.assertEqual(deleted, 2)
        self.assertEqual(store.count(), 0)


class TestProcessingQueue(IssueWatcherTestCase):
    """Tests for ProcessingQueue class."""

    def test_enqueue_and_dequeue(self):
        queue = ProcessingQueue(self.queue_dir)
        item = queue.enqueue(42)
        self.assertEqual(item.issue_number, 42)
        self.assertEqual(item.status, "pending")
        dequeued = queue.dequeue()
        self.assertEqual(dequeued.issue_number, 42)
        self.assertEqual(dequeued.status, "processing")

    def test_enqueue_idempotent(self):
        queue = ProcessingQueue(self.queue_dir)
        item1 = queue.enqueue(42)
        item2 = queue.enqueue(42)
        self.assertEqual(item1.issue_number, item2.issue_number)
        self.assertEqual(queue.count(), 1)

    def test_priority_ordering(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(1, priority=10)
        queue.enqueue(2, priority=1)
        queue.enqueue(3, priority=5)
        item = queue.dequeue()
        self.assertEqual(item.issue_number, 2)  # Lowest priority value first

    def test_mark_completed(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(42)
        queue.dequeue()
        completed = queue.mark_completed(42)
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(completed.completed_at)

    def test_mark_failed(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(42)
        queue.dequeue()
        failed = queue.mark_failed(42, error="Test error")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error, "Test error")

    def test_retry(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(42)
        queue.dequeue()
        queue.mark_failed(42)
        retried = queue.retry(42)
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.retry_count, 1)

    def test_list_items(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(1)
        queue.enqueue(2)
        queue.dequeue()  # Mark first as processing
        pending = queue.list_items(status="pending")
        processing = queue.list_items(status="processing")
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(processing), 1)

    def test_reset_processing(self):
        queue = ProcessingQueue(self.queue_dir)
        queue.enqueue(1)
        queue.enqueue(2)
        queue.dequeue()
        queue.dequeue()
        reset_count = queue.reset_processing()
        self.assertEqual(reset_count, 2)
        self.assertEqual(queue.count(status="pending"), 2)


class TestWatcherConfig(IssueWatcherTestCase):
    """Tests for WatcherConfig dataclass."""

    def test_defaults(self):
        config = WatcherConfig()
        self.assertEqual(config.label, "ready")
        self.assertEqual(config.poll_interval, 60.0)
        self.assertEqual(config.agent_name, "claude")
        self.assertTrue(config.enable_hooks)
        self.assertTrue(config.auto_process)

    def test_custom_values(self):
        config = WatcherConfig(
            label="bug",
            poll_interval=30.0,
            agent_name="copilot",
            auto_process=False
        )
        self.assertEqual(config.label, "bug")
        self.assertEqual(config.poll_interval, 30.0)
        self.assertEqual(config.agent_name, "copilot")
        self.assertFalse(config.auto_process)


class TestWatcherStatus(IssueWatcherTestCase):
    """Tests for WatcherStatus dataclass."""

    def test_to_dict(self):
        status = WatcherStatus(
            running=True,
            pid=12345,
            issues_stored=10,
            issues_pending=3,
            issues_processing=1,
            issues_completed=5,
            issues_failed=1
        )
        d = status.to_dict()
        self.assertTrue(d["running"])
        self.assertEqual(d["pid"], 12345)
        self.assertEqual(d["issues_stored"], 10)


class TestIssueWatcher(IssueWatcherTestCase):
    """Tests for IssueWatcher class."""

    def test_init_default_config(self):
        watcher = IssueWatcher()
        self.assertIsNotNone(watcher.config)
        self.assertEqual(watcher.config.label, "ready")

    def test_init_custom_config(self):
        config = WatcherConfig(
            label="bug",
            poll_interval=30.0,
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            pid_file=self.pid_file,
            log_file=self.log_file
        )
        watcher = IssueWatcher(config)
        self.assertEqual(watcher.config.label, "bug")
        self.assertEqual(watcher.config.poll_interval, 30.0)

    def test_get_status_not_running(self):
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            pid_file=self.pid_file
        )
        watcher = IssueWatcher(config)
        status = watcher.get_status()
        self.assertFalse(status.running)
        self.assertIsNone(status.pid)

    def test_store_and_queue_access(self):
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir
        )
        watcher = IssueWatcher(config)
        self.assertIsNotNone(watcher.store)
        self.assertIsNotNone(watcher.queue)


class TestGitHubPoller(IssueWatcherTestCase):
    """Tests for GitHubPoller class."""

    def test_init_default_config(self):
        poller = GitHubPoller()
        self.assertEqual(poller.config.label, "ready")
        self.assertEqual(poller.config.interval, 60.0)

    def test_init_custom_config(self):
        config = PollerConfig(label="bug", interval=30.0)
        poller = GitHubPoller(config)
        self.assertEqual(poller.config.label, "bug")
        self.assertEqual(poller.config.interval, 30.0)

    def test_interval_property(self):
        poller = GitHubPoller()
        self.assertEqual(poller.interval, 60.0)
        poller.interval = 30.0
        self.assertEqual(poller.interval, 30.0)

    def test_interval_validation(self):
        poller = GitHubPoller()
        with self.assertRaises(ValueError):
            poller.interval = 0
        with self.assertRaises(ValueError):
            poller.interval = -10

    def test_is_running_initial(self):
        poller = GitHubPoller()
        self.assertFalse(poller.is_running)

    def test_seen_issues(self):
        poller = GitHubPoller()
        self.assertEqual(poller.seen_issues, set())

    def test_reset_seen_issues(self):
        poller = GitHubPoller()
        poller._seen_issue_numbers.add(1)
        poller._seen_issue_numbers.add(2)
        poller.reset_seen_issues()
        self.assertEqual(poller.seen_issues, set())


class TestWatcherCLI(IssueWatcherTestCase):
    """Tests for watcher CLI argument parsing."""

    def test_parser_creation(self):
        parser = create_watcher_parser()
        self.assertIsNotNone(parser)
        self.assertEqual(parser.prog, "ralph-watch")

    def test_start_command_defaults(self):
        parser = create_watcher_parser()
        args = parser.parse_args(["start"])
        self.assertEqual(args.command, "start")
        self.assertEqual(args.label, "ready")
        self.assertEqual(args.interval, 60.0)
        self.assertEqual(args.agent, "claude")

    def test_start_command_custom(self):
        parser = create_watcher_parser()
        args = parser.parse_args([
            "start",
            "--label", "bug",
            "--interval", "30",
            "--agent", "copilot",
            "--no-hooks",
            "--mark-issues"
        ])
        self.assertEqual(args.label, "bug")
        self.assertEqual(args.interval, 30.0)
        self.assertEqual(args.agent, "copilot")
        self.assertTrue(args.no_hooks)
        self.assertTrue(args.mark_issues)

    def test_stop_command(self):
        parser = create_watcher_parser()
        args = parser.parse_args(["stop"])
        self.assertEqual(args.command, "stop")

    def test_status_command(self):
        parser = create_watcher_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_status_json_flag(self):
        parser = create_watcher_parser()
        args = parser.parse_args(["status", "--json"])
        self.assertTrue(args.json_output)

    def test_poll_command(self):
        parser = create_watcher_parser()
        args = parser.parse_args(["poll", "--label", "bug"])
        self.assertEqual(args.command, "poll")
        self.assertEqual(args.label, "bug")

    def test_process_command(self):
        parser = create_watcher_parser()
        args = parser.parse_args(["process", "--agent", "copilot", "--no-hooks"])
        self.assertEqual(args.command, "process")
        self.assertEqual(args.agent, "copilot")
        self.assertTrue(args.no_hooks)

    def test_no_command_returns_1(self):
        with patch('sys.stdout', new_callable=StringIO):
            result = watcher_main([])
        self.assertEqual(result, 1)


class TestStoredIssue(IssueWatcherTestCase):
    """Tests for StoredIssue dataclass."""

    def test_to_dict(self):
        stored = StoredIssue(
            number=42,
            title="Test",
            body="Body",
            url="https://github.com/test/issues/42",
            labels=["ready"],
            stored_at="2024-01-01T00:00:00Z",
            status="pending"
        )
        d = stored.to_dict()
        self.assertEqual(d["number"], 42)
        self.assertEqual(d["title"], "Test")
        self.assertEqual(d["status"], "pending")

    def test_from_dict(self):
        data = {
            "number": 42,
            "title": "Test",
            "body": "Body",
            "url": "https://github.com/test/issues/42",
            "labels": ["ready"],
            "stored_at": "2024-01-01T00:00:00Z",
            "status": "pending"
        }
        stored = StoredIssue.from_dict(data)
        self.assertEqual(stored.number, 42)
        self.assertEqual(stored.title, "Test")

    def test_from_issue(self):
        issue = self.create_sample_issue(42)
        stored = StoredIssue.from_issue(issue)
        self.assertEqual(stored.number, 42)
        self.assertEqual(stored.status, "pending")
        self.assertIsNotNone(stored.stored_at)


class TestQueueItem(IssueWatcherTestCase):
    """Tests for QueueItem dataclass."""

    def test_to_dict(self):
        item = QueueItem(issue_number=42, priority=5)
        d = item.to_dict()
        self.assertEqual(d["issue_number"], 42)
        self.assertEqual(d["priority"], 5)
        self.assertEqual(d["status"], "pending")

    def test_from_dict(self):
        data = {
            "issue_number": 42,
            "priority": 5,
            "added_at": "2024-01-01T00:00:00Z",
            "status": "processing"
        }
        item = QueueItem.from_dict(data)
        self.assertEqual(item.issue_number, 42)
        self.assertEqual(item.priority, 5)
        self.assertEqual(item.status, "processing")

    def test_auto_timestamp(self):
        item = QueueItem(issue_number=42)
        self.assertIsNotNone(item.added_at)
        self.assertNotEqual(item.added_at, "")


class TestIssueWatcherHooks(IssueWatcherTestCase):
    """Tests for IssueWatcher hook event emission."""

    def setUp(self):
        super().setUp()
        self.hooks_dir = str(self.temp_path / "hooks")
        Path(self.hooks_dir).mkdir(parents=True, exist_ok=True)
        self.emitted_events = []

        # Create a mock HookManager that records emitted events
        from hooks import HookManager, Event, EventType
        self.hook_manager = HookManager(Path(self.hooks_dir))

        # Register a function hook to capture events
        def capture_event(event):
            self.emitted_events.append(event)

        # Subscribe to all watcher events
        watcher_events = [
            "WATCHER_START", "WATCHER_STOP",
            "ISSUE_DETECTED", "ISSUE_STORED", "ISSUE_QUEUED",
            "ISSUE_PROCESSING_START", "ISSUE_PROCESSING_SUCCESS", "ISSUE_PROCESSING_FAILURE",
            "POLL_START", "POLL_SUCCESS", "POLL_ERROR"
        ]
        self.hook_manager.register_hook(
            "test_capture",
            capture_event,
            watcher_events
        )

    def test_watcher_has_hooks_property(self):
        """IssueWatcher should expose hooks property."""
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            hooks_dir=self.hooks_dir
        )
        watcher = IssueWatcher(config, hooks=self.hook_manager)
        self.assertIsNotNone(watcher.hooks)
        self.assertEqual(watcher.hooks, self.hook_manager)

    def test_watcher_init_with_hooks_disabled(self):
        """IssueWatcher should have None hooks when disabled."""
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            enable_hooks=False
        )
        watcher = IssueWatcher(config)
        self.assertIsNone(watcher.hooks)

    def test_watcher_init_creates_hook_manager(self):
        """IssueWatcher should create HookManager when enabled."""
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            hooks_dir=self.hooks_dir,
            enable_hooks=True
        )
        watcher = IssueWatcher(config)
        self.assertIsNotNone(watcher.hooks)

    def test_on_new_issues_emits_events(self):
        """_on_new_issues should emit ISSUE_DETECTED, ISSUE_STORED, ISSUE_QUEUED events."""
        from hooks import EventType

        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            auto_process=False  # Disable auto-processing for this test
        )
        watcher = IssueWatcher(config, hooks=self.hook_manager)

        # Call _on_new_issues directly
        issue = self.create_sample_issue(number=42, title="Test Issue")
        watcher._on_new_issues([issue])

        # Check emitted events
        event_types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.ISSUE_DETECTED, event_types)
        self.assertIn(EventType.ISSUE_STORED, event_types)
        self.assertIn(EventType.ISSUE_QUEUED, event_types)

        # Check event details
        detected_event = next(e for e in self.emitted_events if e.event_type == EventType.ISSUE_DETECTED)
        self.assertEqual(detected_event.issue_number, 42)
        self.assertEqual(detected_event.issue_title, "Test Issue")
        self.assertEqual(detected_event.issues_count, 1)

    def test_on_poll_error_emits_event(self):
        """_on_poll_error should emit POLL_ERROR event."""
        from hooks import EventType

        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir
        )
        watcher = IssueWatcher(config, hooks=self.hook_manager)

        # Call _on_poll_error directly
        watcher._on_poll_error(Exception("Test error"))

        # Check emitted event
        event_types = [e.event_type for e in self.emitted_events]
        self.assertIn(EventType.POLL_ERROR, event_types)

        error_event = next(e for e in self.emitted_events if e.event_type == EventType.POLL_ERROR)
        self.assertEqual(error_event.error, "Test error")

    def test_emit_with_disabled_hooks(self):
        """_emit should do nothing when hooks is None."""
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            enable_hooks=False
        )
        watcher = IssueWatcher(config)

        # This should not raise
        from hooks import Event, EventType
        watcher._emit(Event(EventType.WATCHER_START))

    def test_event_has_issue_fields(self):
        """Events should include issue-related fields."""
        from hooks import Event, EventType

        event = Event(
            EventType.ISSUE_DETECTED,
            issue_number=42,
            issue_title="Test",
            issue_url="https://example.com",
            issues_count=1
        )

        d = event.to_dict()
        self.assertEqual(d["issue_number"], 42)
        self.assertEqual(d["issue_title"], "Test")
        self.assertEqual(d["issue_url"], "https://example.com")
        self.assertEqual(d["issues_count"], 1)

    def test_watcher_event_types_exist(self):
        """All IssueWatcher event types should exist in EventType enum."""
        from hooks import EventType

        expected_events = [
            "WATCHER_START", "WATCHER_STOP",
            "ISSUE_DETECTED", "ISSUE_STORED", "ISSUE_QUEUED",
            "ISSUE_PROCESSING_START", "ISSUE_PROCESSING_SUCCESS", "ISSUE_PROCESSING_FAILURE",
            "POLL_START", "POLL_SUCCESS", "POLL_ERROR"
        ]

        for event_name in expected_events:
            self.assertTrue(
                hasattr(EventType, event_name),
                f"EventType.{event_name} should exist"
            )


# ==============================================================================
# INTENT ENHANCEMENT TESTS (TASK-001)
# ==============================================================================


class TestEnhanceIntentCLI(unittest.TestCase):
    """Tests for --enhance-intent CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--enhance-intent", action="store_true")
        self.parser.add_argument("--enhance-intent-strict", action="store_true")
        self.parser.add_argument("--intent", type=str)

    def test_enhance_intent_flag_default(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.enhance_intent)
        self.assertFalse(args.enhance_intent_strict)

    def test_enhance_intent_flag_enabled(self):
        args = self.parser.parse_args(["--enhance-intent"])
        self.assertTrue(args.enhance_intent)

    def test_enhance_intent_strict_flag(self):
        args = self.parser.parse_args(["--enhance-intent", "--enhance-intent-strict"])
        self.assertTrue(args.enhance_intent)
        self.assertTrue(args.enhance_intent_strict)

    def test_combined_flags(self):
        args = self.parser.parse_args(["--enhance-intent", "--intent", "test"])
        self.assertTrue(args.enhance_intent)
        self.assertEqual(args.intent, "test")


class TestEnhanceIntentCLIPassthrough(unittest.TestCase):
    """Tests for --enhance-intent flags passed to orchestrator."""

    def test_enhance_intent_passed(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-intent']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('enhance_intent'))

    def test_enhance_intent_strict_passed(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-intent', '--enhance-intent-strict']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('enhance_intent'))
            self.assertTrue(call_kwargs.get('enhance_intent_strict'))


class TestEnhanceIntentOrchestrator(TempConfigTestCase):
    """Tests for intent enhancement in RalphOrchestrator."""

    def test_orchestrator_stores_enhance_intent_flag(self):
        orch = self.create_mock_orchestrator(enhance_intent=True)
        self.assertTrue(orch._enhance_intent)

    def test_orchestrator_stores_enhance_intent_strict_flag(self):
        orch = self.create_mock_orchestrator(enhance_intent=True, enhance_intent_strict=True)
        self.assertTrue(orch._enhance_intent)
        self.assertTrue(orch._enhance_intent_strict)

    def test_orchestrator_defaults_to_no_enhancement(self):
        orch = self.create_mock_orchestrator()
        self.assertFalse(orch._enhance_intent)
        self.assertFalse(orch._enhance_intent_strict)


class TestEnhanceIntentMethod(TempConfigTestCase):
    """Tests for _enhance_intent_impl method."""

    def test_empty_intent_exits(self):
        orch = self.create_mock_orchestrator(enhance_intent=True)
        with patch('ralph.Logger.error'):
            with self.assertRaises(SystemExit) as cm:
                orch._enhance_intent_impl("")
            self.assertEqual(cm.exception.code, 1)

    def test_whitespace_intent_exits(self):
        orch = self.create_mock_orchestrator(enhance_intent=True)
        with patch('ralph.Logger.error'):
            with self.assertRaises(SystemExit) as cm:
                orch._enhance_intent_impl("   ")
            self.assertEqual(cm.exception.code, 1)

    def test_success_returns_enhanced_intent(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<ENHANCED_INTENT>Enhanced version</ENHANCED_INTENT>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            result = orch._enhance_intent_impl("original")
        self.assertEqual(result, "Enhanced version")

    def test_failure_falls_back_to_original(self):
        mock_agent = self.create_mock_agent()
        from agents.base import AgentError
        error = AgentError("TestError", "test message", "", "", "", "")
        mock_agent.run.return_value = (False, "", error)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            result = orch._enhance_intent_impl("original")
        self.assertEqual(result, "original")

    def test_failure_strict_mode_exits(self):
        mock_agent = self.create_mock_agent()
        from agents.base import AgentError
        error = AgentError("TestError", "test message", "", "", "", "")
        mock_agent.run.return_value = (False, "", error)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True, enhance_intent_strict=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'), patch('ralph.Logger.error'):
            with self.assertRaises(SystemExit) as cm:
                orch._enhance_intent_impl("original")
            self.assertEqual(cm.exception.code, 1)

    def test_empty_response_falls_back(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<ENHANCED_INTENT>   </ENHANCED_INTENT>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            result = orch._enhance_intent_impl("original")
        self.assertEqual(result, "original")

    def test_empty_response_strict_mode_exits(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<ENHANCED_INTENT>   </ENHANCED_INTENT>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True, enhance_intent_strict=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'), patch('ralph.Logger.error'):
            with self.assertRaises(SystemExit) as cm:
                orch._enhance_intent_impl("original")
            self.assertEqual(cm.exception.code, 1)


class TestParseEnhancedIntent(TempConfigTestCase):
    """Tests for _parse_enhanced_intent method."""

    def test_parses_tagged_response(self):
        orch = self.create_mock_orchestrator()
        result = orch._parse_enhanced_intent(
            "Some text\n<ENHANCED_INTENT>The enhanced content</ENHANCED_INTENT>\nMore text",
            "fallback"
        )
        self.assertEqual(result, "The enhanced content")

    def test_parses_multiline_response(self):
        orch = self.create_mock_orchestrator()
        result = orch._parse_enhanced_intent(
            "<ENHANCED_INTENT>\nLine 1\nLine 2\nLine 3\n</ENHANCED_INTENT>",
            "fallback"
        )
        self.assertEqual(result, "Line 1\nLine 2\nLine 3")

    def test_falls_back_on_missing_tags(self):
        orch = self.create_mock_orchestrator()
        with patch('ralph.Logger.warning'):
            result = orch._parse_enhanced_intent("No tags here", "fallback")
        self.assertEqual(result, "fallback")

    def test_strips_whitespace(self):
        orch = self.create_mock_orchestrator()
        result = orch._parse_enhanced_intent(
            "<ENHANCED_INTENT>  content with spaces  </ENHANCED_INTENT>",
            "fallback"
        )
        self.assertEqual(result, "content with spaces")


class TestGetAndEnhanceIntent(TempConfigTestCase):
    """Tests for _get_and_enhance_intent method."""

    def test_without_enhance_flag_returns_original(self):
        orch = self.create_mock_orchestrator(intent="original intent", enhance_intent=False)
        with patch('ralph.Logger.info'):
            result = orch._get_and_enhance_intent()
        self.assertEqual(result, "original intent")

    def test_with_enhance_flag_calls_enhancement(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<ENHANCED_INTENT>enhanced</ENHANCED_INTENT>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, intent="original", enhance_intent=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            result = orch._get_and_enhance_intent()
        self.assertEqual(result, "enhanced")

    def test_uses_provided_intent(self):
        orch = self.create_mock_orchestrator(enhance_intent=False)
        with patch('ralph.Logger.info'):
            result = orch._get_and_enhance_intent("passed intent")
        self.assertEqual(result, "passed intent")


class TestEnhanceIntentEvents(TempConfigTestCase):
    """Tests for intent enhancement event emission."""

    def test_emits_start_event(self):
        events = []
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<ENHANCED_INTENT>enhanced</ENHANCED_INTENT>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True)
        orch.hooks.register_hook("capture", lambda e: events.append(e.event_type), ["INTENT_ENHANCE_START"])
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            orch._enhance_intent_impl("original")
        self.assertIn(EventType.INTENT_ENHANCE_START, events)

    def test_emits_success_event(self):
        events = []
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<ENHANCED_INTENT>enhanced</ENHANCED_INTENT>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True)
        orch.hooks.register_hook("capture", lambda e: events.append(e.event_type), ["INTENT_ENHANCE_SUCCESS"])
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            orch._enhance_intent_impl("original")
        self.assertIn(EventType.INTENT_ENHANCE_SUCCESS, events)

    def test_emits_failure_event_on_agent_error(self):
        events = []
        mock_agent = self.create_mock_agent()
        from agents.base import AgentError
        error = AgentError("TestError", "test message", "", "", "", "")
        mock_agent.run.return_value = (False, "", error)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, enhance_intent=True)
        orch.hooks.register_hook("capture", lambda e: events.append(e.event_type), ["INTENT_ENHANCE_FAILURE"])
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            orch._enhance_intent_impl("original")
        self.assertIn(EventType.INTENT_ENHANCE_FAILURE, events)


class TestEnhanceIntentTemplate(unittest.TestCase):
    """Tests for enhance_intent.txt template."""

    def test_template_exists(self):
        self.assertIn("enhance_intent.txt", TemplateManager.DEFAULT_TEMPLATES)

    def test_template_renders_intent(self):
        template = TemplateManager.render("enhance_intent.txt", original_intent="Build a web app")
        self.assertIn("Build a web app", template)
        self.assertIn("ORIGINAL_INTENT", template)
        self.assertIn("ENHANCED_INTENT", template)


class TestIntentEnhanceEventTypes(unittest.TestCase):
    """Tests for intent enhancement event types."""

    def test_intent_enhance_event_types_exist(self):
        expected_events = [
            "INTENT_ENHANCE_START",
            "INTENT_ENHANCE_SUCCESS",
            "INTENT_ENHANCE_FAILURE"
        ]
        for event_name in expected_events:
            self.assertTrue(
                hasattr(EventType, event_name),
                f"EventType.{event_name} should exist"
            )


if __name__ == '__main__':
    unittest.main()
