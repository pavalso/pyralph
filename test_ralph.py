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
    QAChecklistCorruptedError, QAChecklistError, QAChecklistManager, QARequirement,
    QAFinding, QAFindingsAnalyzer, QAFindingType,
    RalphOrchestrator, Shell, TemplateManager, get_version, main,
)
from hooks import Event, EventType, HookManager, PythonHook, ExecutableHook, FunctionHook, QAChecklistAgent, FinalQAValidator, FinalQAReport


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
# QA CHECKLIST MANAGER TESTS
# ==============================================================================


class TestQARequirement(unittest.TestCase):
    """Tests for QARequirement dataclass."""

    def test_default_values(self):
        req = QARequirement(id="REQ-001", description="Test requirement")
        self.assertEqual(req.id, "REQ-001")
        self.assertEqual(req.description, "Test requirement")
        self.assertEqual(req.status, "pending")
        self.assertIsNone(req.lastChecked)
        self.assertEqual(req.linkedTasks, [])

    def test_to_dict(self):
        req = QARequirement(
            id="REQ-001",
            description="Test requirement",
            status="passed",
            lastChecked="2024-01-01T00:00:00",
            linkedTasks=["TASK-001", "TASK-002"]
        )
        data = req.to_dict()
        self.assertEqual(data["id"], "REQ-001")
        self.assertEqual(data["description"], "Test requirement")
        self.assertEqual(data["status"], "passed")
        self.assertEqual(data["lastChecked"], "2024-01-01T00:00:00")
        self.assertEqual(data["linkedTasks"], ["TASK-001", "TASK-002"])

    def test_from_dict_with_all_fields(self):
        data = {
            "id": "REQ-001",
            "description": "Test requirement",
            "status": "failed",
            "lastChecked": "2024-01-01T00:00:00",
            "linkedTasks": ["TASK-001"]
        }
        req = QARequirement.from_dict(data)
        self.assertEqual(req.id, "REQ-001")
        self.assertEqual(req.description, "Test requirement")
        self.assertEqual(req.status, "failed")
        self.assertEqual(req.lastChecked, "2024-01-01T00:00:00")
        self.assertEqual(req.linkedTasks, ["TASK-001"])

    def test_from_dict_with_minimal_fields(self):
        data = {"id": "REQ-001", "description": "Test requirement"}
        req = QARequirement.from_dict(data)
        self.assertEqual(req.id, "REQ-001")
        self.assertEqual(req.description, "Test requirement")
        self.assertEqual(req.status, "pending")
        self.assertIsNone(req.lastChecked)
        self.assertEqual(req.linkedTasks, [])

    def test_from_dict_missing_id_raises(self):
        data = {"description": "Test requirement"}
        with self.assertRaises(KeyError):
            QARequirement.from_dict(data)

    def test_from_dict_missing_description_raises(self):
        data = {"id": "REQ-001"}
        with self.assertRaises(KeyError):
            QARequirement.from_dict(data)


class TestQAChecklistManager(TempConfigTestCase):
    """Tests for QAChecklistManager class."""

    def setUp(self):
        super().setUp()
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        CONF.QA_CHECKLIST_FILE = self.temp_path / ".ralph" / "qa-checklist.json"
        self.checklist_path = CONF.QA_CHECKLIST_FILE
        self.prd_manager = PRDManager(CONF.PRD_FILE)
        self.manager = QAChecklistManager(self.checklist_path, self.prd_manager)

    def _write_checklist(self, data):
        """Helper to write checklist JSON to disk."""
        self.checklist_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def _write_prd(self, data):
        """Helper to write PRD JSON to disk."""
        CONF.PRD_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # --- Basic existence tests ---

    def test_exists_false_when_no_file(self):
        self.assertFalse(self.manager.exists())

    def test_exists_true_when_file_present(self):
        self._write_checklist({"requirements": []})
        self.assertTrue(self.manager.exists())

    def test_path_property(self):
        self.assertEqual(self.manager.path, self.checklist_path)

    # --- Load tests ---

    def test_load_parses_valid_checklist(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Requirement 1", "status": "pending"},
                {"id": "REQ-002", "description": "Requirement 2", "status": "passed", "linkedTasks": ["TASK-001"]}
            ]
        }
        self._write_checklist(checklist)
        requirements = self.manager.load()
        self.assertEqual(len(requirements), 2)
        self.assertIn("REQ-001", requirements)
        self.assertIn("REQ-002", requirements)
        self.assertEqual(requirements["REQ-001"].status, "pending")
        self.assertEqual(requirements["REQ-002"].linkedTasks, ["TASK-001"])

    def test_load_raises_on_missing_file_without_auto_create(self):
        manager = QAChecklistManager(self.checklist_path, prd_manager=None)
        with self.assertRaises(FileNotFoundError):
            manager.load(auto_create=False)

    def test_load_auto_creates_from_prd_when_missing(self):
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {
                    "id": "TASK-001",
                    "acceptanceCriteria": [
                        "First acceptance criterion",
                        "Second acceptance criterion"
                    ]
                },
                {
                    "id": "TASK-002",
                    "acceptanceCriteria": [
                        "Third acceptance criterion"
                    ]
                }
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        self.assertEqual(len(requirements), 3)
        self.assertIn("TASK-001-AC01", requirements)
        self.assertIn("TASK-001-AC02", requirements)
        self.assertIn("TASK-002-AC01", requirements)
        self.assertEqual(requirements["TASK-001-AC01"].description, "First acceptance criterion")
        self.assertEqual(requirements["TASK-002-AC01"].description, "Third acceptance criterion")
        # Verify file was created
        self.assertTrue(self.checklist_path.exists())

    def test_load_no_auto_create_without_prd_manager(self):
        manager = QAChecklistManager(self.checklist_path, prd_manager=None)
        with self.assertRaises(FileNotFoundError):
            manager.load(auto_create=True)

    # --- Corruption detection and recovery tests ---

    def test_load_handles_invalid_json(self):
        self.checklist_path.write_text('not valid json {', encoding='utf-8')
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["Criterion 1"]}
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        self.assertEqual(len(requirements), 1)
        # Verify backup was created
        backup_files = list(self.checklist_path.parent.glob("*.corrupted.*.json"))
        self.assertEqual(len(backup_files), 1)

    def test_load_handles_missing_requirements_field(self):
        self._write_checklist({"invalid": "structure"})
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["Criterion 1"]}
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        self.assertEqual(len(requirements), 1)
        backup_files = list(self.checklist_path.parent.glob("*.corrupted.*.json"))
        self.assertEqual(len(backup_files), 1)

    def test_load_handles_requirements_not_a_list(self):
        self._write_checklist({"requirements": "not a list"})
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["Criterion 1"]}
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        self.assertEqual(len(requirements), 1)

    def test_load_handles_requirement_missing_id(self):
        self._write_checklist({
            "requirements": [{"description": "Missing id"}]
        })
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["Criterion 1"]}
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        # Should regenerate from PRD
        self.assertIn("TASK-001-AC01", requirements)

    def test_load_handles_requirement_missing_description(self):
        self._write_checklist({
            "requirements": [{"id": "REQ-001"}]
        })
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["Criterion 1"]}
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        self.assertIn("TASK-001-AC01", requirements)

    def test_load_handles_invalid_status(self):
        self._write_checklist({
            "requirements": [{"id": "REQ-001", "description": "Test", "status": "invalid_status"}]
        })
        prd_data = {
            "id": "PRD-001",
            "userStories": [
                {"id": "TASK-001", "acceptanceCriteria": ["Criterion 1"]}
            ]
        }
        self._write_prd(prd_data)

        requirements = self.manager.load()
        self.assertIn("TASK-001-AC01", requirements)

    def test_load_corrupted_raises_when_no_prd(self):
        self.checklist_path.write_text('corrupted', encoding='utf-8')
        manager = QAChecklistManager(self.checklist_path, prd_manager=None)
        with self.assertRaises(QAChecklistCorruptedError):
            manager.load()

    # --- Save tests ---

    def test_save_creates_file(self):
        self._write_prd({"id": "PRD-001", "userStories": []})
        self.manager.load()
        self.manager._requirements["NEW-001"] = QARequirement(
            id="NEW-001", description="New requirement"
        )
        self.manager.save()

        content = json.loads(self.checklist_path.read_text(encoding='utf-8'))
        self.assertEqual(len(content["requirements"]), 1)
        self.assertEqual(content["requirements"][0]["id"], "NEW-001")

    def test_save_creates_parent_directories(self):
        nested_path = self.temp_path / "deep" / "nested" / "qa-checklist.json"
        manager = QAChecklistManager(nested_path, self.prd_manager)
        manager._requirements = {"REQ-001": QARequirement(id="REQ-001", description="Test")}
        manager.save()
        self.assertTrue(nested_path.exists())

    # --- CRUD operation tests ---

    def test_get_requirement_returns_existing(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Test requirement"}
            ]
        }
        self._write_checklist(checklist)
        req = self.manager.get_requirement("REQ-001")
        self.assertIsNotNone(req)
        self.assertEqual(req.id, "REQ-001")

    def test_get_requirement_returns_none_for_missing(self):
        checklist = {"requirements": []}
        self._write_checklist(checklist)
        req = self.manager.get_requirement("NONEXISTENT")
        self.assertIsNone(req)

    def test_get_all_requirements(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "First"},
                {"id": "REQ-002", "description": "Second"}
            ]
        }
        self._write_checklist(checklist)
        all_reqs = self.manager.get_all_requirements()
        self.assertEqual(len(all_reqs), 2)

    def test_update_requirement_status(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Test", "status": "pending"}
            ]
        }
        self._write_checklist(checklist)

        result = self.manager.update_requirement("REQ-001", status="passed")
        self.assertTrue(result)

        req = self.manager.get_requirement("REQ-001")
        self.assertEqual(req.status, "passed")
        self.assertIsNotNone(req.lastChecked)

    def test_update_requirement_links_task(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Test", "linkedTasks": []}
            ]
        }
        self._write_checklist(checklist)

        self.manager.update_requirement("REQ-001", linked_task="TASK-001")
        req = self.manager.get_requirement("REQ-001")
        self.assertIn("TASK-001", req.linkedTasks)

    def test_update_requirement_does_not_duplicate_task(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Test", "linkedTasks": ["TASK-001"]}
            ]
        }
        self._write_checklist(checklist)

        self.manager.update_requirement("REQ-001", linked_task="TASK-001")
        req = self.manager.get_requirement("REQ-001")
        self.assertEqual(req.linkedTasks.count("TASK-001"), 1)

    def test_update_requirement_returns_false_for_missing(self):
        checklist = {"requirements": []}
        self._write_checklist(checklist)
        result = self.manager.update_requirement("NONEXISTENT", status="passed")
        self.assertFalse(result)

    def test_update_requirement_invalid_status_raises(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Test"}
            ]
        }
        self._write_checklist(checklist)
        with self.assertRaises(ValueError):
            self.manager.update_requirement("REQ-001", status="invalid")

    def test_update_requirement_persists_to_disk(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Test", "status": "pending"}
            ]
        }
        self._write_checklist(checklist)

        self.manager.update_requirement("REQ-001", status="passed")

        # Reload from disk
        fresh_manager = QAChecklistManager(self.checklist_path, self.prd_manager)
        req = fresh_manager.get_requirement("REQ-001")
        self.assertEqual(req.status, "passed")

    def test_add_requirement(self):
        checklist = {"requirements": []}
        self._write_checklist(checklist)
        self.manager.load()

        new_req = QARequirement(id="REQ-001", description="New requirement")
        self.manager.add_requirement(new_req)

        req = self.manager.get_requirement("REQ-001")
        self.assertIsNotNone(req)
        self.assertEqual(req.description, "New requirement")

    def test_add_requirement_duplicate_raises(self):
        checklist = {
            "requirements": [
                {"id": "REQ-001", "description": "Existing"}
            ]
        }
        self._write_checklist(checklist)
        self.manager.load()

        new_req = QARequirement(id="REQ-001", description="Duplicate")
        with self.assertRaises(ValueError):
            self.manager.add_requirement(new_req)

    # --- Incremental update tests ---

    def test_incremental_update_preserves_history(self):
        """Verify updates don't lose previous validation history."""
        checklist = {
            "requirements": [
                {
                    "id": "REQ-001",
                    "description": "Test",
                    "status": "passed",
                    "lastChecked": "2024-01-01T00:00:00",
                    "linkedTasks": ["TASK-001"]
                },
                {
                    "id": "REQ-002",
                    "description": "Another",
                    "status": "pending",
                    "linkedTasks": []
                }
            ]
        }
        self._write_checklist(checklist)

        # Update only REQ-002
        self.manager.update_requirement("REQ-002", status="passed", linked_task="TASK-002")

        # Verify REQ-001 is unchanged
        req1 = self.manager.get_requirement("REQ-001")
        self.assertEqual(req1.status, "passed")
        self.assertEqual(req1.linkedTasks, ["TASK-001"])
        self.assertEqual(req1.lastChecked, "2024-01-01T00:00:00")

        # Verify REQ-002 is updated
        req2 = self.manager.get_requirement("REQ-002")
        self.assertEqual(req2.status, "passed")
        self.assertIn("TASK-002", req2.linkedTasks)


# ==============================================================================
# QA CHECKLIST AGENT TESTS
# ==============================================================================


class TestQAChecklistAgent(TempConfigTestCase):
    """Tests for QAChecklistAgent class."""

    def setUp(self):
        super().setUp()
        CONF.QA_CHECKLIST_FILE = self.temp_path / ".ralph" / "qa-checklist.json"
        self.checklist_path = CONF.QA_CHECKLIST_FILE
        self.prd_manager = MagicMock()
        self.prd_manager.exists.return_value = True
        self.manager = QAChecklistManager(self.checklist_path, self.prd_manager)
        self.hooks_dir = self.temp_path / ".ralph" / "hooks"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        self.hook_manager = HookManager(self.hooks_dir)
        self.agent = QAChecklistAgent(self.manager)

    def _write_checklist(self, data):
        """Helper to write checklist JSON to disk."""
        self.checklist_path.parent.mkdir(parents=True, exist_ok=True)
        self.checklist_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def test_register_succeeds(self):
        """Test QA agent registration with HookManager."""
        result = self.agent.register(self.hook_manager)
        self.assertTrue(result)
        hooks = self.hook_manager.get_all_hooks()
        hook_names = [h.name for h in hooks]
        self.assertIn("qa_checklist_agent", hook_names)

    def test_register_fails_if_already_registered(self):
        """Test that double registration fails."""
        self.agent.register(self.hook_manager)
        result = self.agent.register(self.hook_manager)
        self.assertFalse(result)

    def test_unregister_succeeds(self):
        """Test QA agent unregistration."""
        self.agent.register(self.hook_manager)
        result = self.agent.unregister(self.hook_manager)
        self.assertTrue(result)
        hooks = self.hook_manager.get_all_hooks()
        hook_names = [h.name for h in hooks]
        self.assertNotIn("qa_checklist_agent", hook_names)

    def test_unregister_fails_if_not_registered(self):
        """Test unregistration without registration fails."""
        result = self.agent.unregister(self.hook_manager)
        self.assertFalse(result)

    def test_on_task_success_updates_requirements(self):
        """Test that TASK_SUCCESS event updates requirements for the task."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Test criteria 1", "status": "pending"},
                {"id": "TASK-001-AC02", "description": "Test criteria 2", "status": "pending"},
                {"id": "TASK-002-AC01", "description": "Other task", "status": "pending"},
            ]
        }
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001", task_description="Test task")
        self.hook_manager.emit(event)

        # Verify TASK-001 requirements are updated
        req1 = self.manager.get_requirement("TASK-001-AC01")
        self.assertEqual(req1.status, "passed")
        self.assertIn("TASK-001", req1.linkedTasks)
        self.assertIsNotNone(req1.lastChecked)

        req2 = self.manager.get_requirement("TASK-001-AC02")
        self.assertEqual(req2.status, "passed")
        self.assertIn("TASK-001", req2.linkedTasks)

        # Verify TASK-002 requirement is unchanged
        req3 = self.manager.get_requirement("TASK-002-AC01")
        self.assertEqual(req3.status, "pending")
        self.assertEqual(req3.linkedTasks, [])

    def test_on_task_success_no_requirements_does_not_error(self):
        """Test that task with no matching requirements doesn't error."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Test criteria", "status": "pending"},
            ]
        }
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-999", task_description="Unknown task")

        # Should not raise
        self.hook_manager.emit(event)

        # Original requirement unchanged
        req = self.manager.get_requirement("TASK-001-AC01")
        self.assertEqual(req.status, "pending")

    def test_on_task_success_without_task_id_does_not_error(self):
        """Test that event without task_id is handled gracefully."""
        checklist = {"requirements": []}
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id=None)

        # Should not raise
        self.hook_manager.emit(event)

    def test_checklist_manager_error_does_not_block(self):
        """Test that checklist errors don't block task completion."""
        # Create agent with a mock manager that raises
        mock_manager = MagicMock()
        mock_manager.get_all_requirements.side_effect = Exception("DB Error")
        agent = QAChecklistAgent(mock_manager)
        agent.register(self.hook_manager)

        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        # Should not raise, even though manager errors
        self.hook_manager.emit(event)

    def test_agent_logs_warnings_on_error(self):
        """Test that agent logs warnings when errors occur."""
        mock_logger = MagicMock()
        mock_manager = MagicMock()
        mock_manager.get_all_requirements.side_effect = Exception("Test error")

        agent = QAChecklistAgent(mock_manager, logger=mock_logger)
        agent.register(self.hook_manager)

        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        self.hook_manager.emit(event)

        mock_logger.warning.assert_called()

    def test_requirement_status_set_to_passed_on_success(self):
        """Test that requirements are marked as passed on TASK_SUCCESS."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria", "status": "pending"},
            ]
        }
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001", task_description="Completed task")
        self.hook_manager.emit(event)

        req = self.manager.get_requirement("TASK-001-AC01")
        self.assertEqual(req.status, "passed")

    def test_linked_task_added_to_requirement(self):
        """Test that task ID is added to linkedTasks array."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria", "status": "pending", "linkedTasks": []},
            ]
        }
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        self.hook_manager.emit(event)

        req = self.manager.get_requirement("TASK-001-AC01")
        self.assertIn("TASK-001", req.linkedTasks)

    def test_linked_task_not_duplicated(self):
        """Test that task ID is not added twice to linkedTasks."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria", "status": "passed", "linkedTasks": ["TASK-001"]},
            ]
        }
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        self.hook_manager.emit(event)

        req = self.manager.get_requirement("TASK-001-AC01")
        self.assertEqual(req.linkedTasks.count("TASK-001"), 1)

    def test_last_checked_timestamp_updated(self):
        """Test that lastChecked timestamp is updated."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria", "status": "pending", "lastChecked": None},
            ]
        }
        self._write_checklist(checklist)

        self.agent.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        self.hook_manager.emit(event)

        req = self.manager.get_requirement("TASK-001-AC01")
        self.assertIsNotNone(req.lastChecked)

    def test_get_requirements_for_task_finds_matching(self):
        """Test _get_requirements_for_task finds correct requirements."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "pending"},
                {"id": "TASK-001-AC02", "description": "Criteria 2", "status": "pending"},
                {"id": "TASK-002-AC01", "description": "Other", "status": "pending"},
            ]
        }
        self._write_checklist(checklist)
        self.manager.load()

        reqs = self.agent._get_requirements_for_task("TASK-001")
        self.assertEqual(len(reqs), 2)
        req_ids = [r[0] for r in reqs]
        self.assertIn("TASK-001-AC01", req_ids)
        self.assertIn("TASK-001-AC02", req_ids)
        self.assertNotIn("TASK-002-AC01", req_ids)

    def test_evaluate_requirement_returns_passed(self):
        """Test _evaluate_requirement returns passed status."""
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Test criteria", "status": "pending"},
            ]
        }
        self._write_checklist(checklist)
        self.manager.load()

        req = self.manager.get_requirement("TASK-001-AC01")
        status, reasoning = self.agent._evaluate_requirement(req, "TASK-001", "Test task")

        self.assertEqual(status, "passed")
        self.assertIn("TASK-001", reasoning)
        self.assertIn("successfully", reasoning.lower())


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
        # 20 original + 11 IssueWatcher events + 3 Intent Enhancement events + 3 PRD Revision events + 4 QA Review events + 2 PRD completion events = 43
        self.assertEqual(len(EventType), 43)

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


# ==============================================================================
# REVISE PRD TESTS
# ==============================================================================


class TestRevisePrdCLI(unittest.TestCase):
    """Tests for --revise-prd CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--revise-prd", action="store_true")

    def test_revise_prd_flag_default(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.revise_prd)

    def test_revise_prd_flag_enabled(self):
        args = self.parser.parse_args(["--revise-prd"])
        self.assertTrue(args.revise_prd)


class TestRevisePrdCLIPassthrough(unittest.TestCase):
    """Tests for --revise-prd flag passed to orchestrator."""

    def test_revise_prd_passed(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--revise-prd']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('revise_prd'))


class TestRevisePrdOrchestrator(TempConfigTestCase):
    """Tests for PRD revision in RalphOrchestrator."""

    def test_orchestrator_stores_revise_prd_flag(self):
        orch = self.create_mock_orchestrator(revise_prd=True)
        self.assertTrue(orch._revise_prd)

    def test_orchestrator_defaults_to_no_revision(self):
        orch = self.create_mock_orchestrator()
        self.assertFalse(orch._revise_prd)


class TestRevisePrdMethod(TempConfigTestCase):
    """Tests for _revise_prd_impl method."""

    def test_success_returns_revised_prd(self):
        mock_agent = self.create_mock_agent()
        revised_prd = {"userStories": [{"id": "TASK-001", "description": "Revised"}]}
        mock_agent.run.return_value = (
            True,
            f'<REVISED_PRD>{json.dumps(revised_prd)}</REVISED_PRD><REVISION_SUMMARY>Minor improvements</REVISION_SUMMARY>',
            None
        )
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            result = orch._revise_prd_impl({"userStories": []})
        self.assertEqual(result["userStories"][0]["id"], "TASK-001")

    def test_failure_falls_back_to_original(self):
        mock_agent = self.create_mock_agent()
        error = AgentError("TestError", "test message", "", "", "", "")
        mock_agent.run.return_value = (False, "", error)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        original_prd = {"userStories": [{"id": "TASK-001"}]}
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            result = orch._revise_prd_impl(original_prd)
        self.assertEqual(result, original_prd)

    def test_invalid_json_falls_back_to_original(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "<REVISED_PRD>invalid json</REVISED_PRD>", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        original_prd = {"userStories": [{"id": "TASK-001"}]}
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            result = orch._revise_prd_impl(original_prd)
        self.assertEqual(result, original_prd)

    def test_missing_user_stories_falls_back_to_original(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<REVISED_PRD>{"invalid": "prd"}</REVISED_PRD>', None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        original_prd = {"userStories": [{"id": "TASK-001"}]}
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            result = orch._revise_prd_impl(original_prd)
        self.assertEqual(result, original_prd)

    def test_no_revision_needed_logs_message(self):
        mock_agent = self.create_mock_agent()
        original_prd = {"userStories": [{"id": "TASK-001"}]}
        mock_agent.run.return_value = (
            True,
            f'<REVISED_PRD>{json.dumps(original_prd)}</REVISED_PRD><REVISION_SUMMARY>No revision needed - PRD already optimal</REVISION_SUMMARY>',
            None
        )
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        with patch('ralph.Logger.info') as mock_info:
            result = orch._revise_prd_impl(original_prd)
        # Check that the "already optimal" message was logged
        info_calls = [str(c) for c in mock_info.call_args_list]
        self.assertTrue(any('optimal' in str(c).lower() for c in info_calls))


class TestParseRevisedPrd(TempConfigTestCase):
    """Tests for _parse_revised_prd method."""

    def test_parses_valid_prd(self):
        orch = self.create_mock_orchestrator()
        prd = {"userStories": [{"id": "TASK-001"}]}
        response = f'<REVISED_PRD>{json.dumps(prd)}</REVISED_PRD><REVISION_SUMMARY>Changes made</REVISION_SUMMARY>'
        with patch('ralph.Logger.warning'):
            result, summary = orch._parse_revised_prd(response, {})
        self.assertIsNotNone(result)
        self.assertEqual(result["userStories"][0]["id"], "TASK-001")
        self.assertEqual(summary, "Changes made")

    def test_returns_none_for_missing_tags(self):
        orch = self.create_mock_orchestrator()
        response = 'No tags here'
        with patch('ralph.Logger.warning'):
            result, summary = orch._parse_revised_prd(response, {})
        self.assertIsNone(result)

    def test_returns_none_for_invalid_json(self):
        orch = self.create_mock_orchestrator()
        response = '<REVISED_PRD>not valid json</REVISED_PRD>'
        with patch('ralph.Logger.warning'):
            result, summary = orch._parse_revised_prd(response, {})
        self.assertIsNone(result)


class TestRevisePrdSchemaValidation(TempConfigTestCase):
    """Tests for schema validation of revised PRD."""

    def setUp(self):
        super().setUp()
        # Create a simple schema file
        self.schema_file = self.temp_path / "schema.json"
        schema = {
            "type": "object",
            "required": ["userStories"],
            "properties": {
                "userStories": {"type": "array"}
            }
        }
        self.schema_file.write_text(json.dumps(schema), encoding='utf-8')

    def test_revised_prd_validated_against_schema(self):
        mock_agent = self.create_mock_agent()
        # Return a PRD that doesn't have userStories
        invalid_prd = {"invalid": "structure"}
        mock_agent.run.return_value = (
            True,
            f'<REVISED_PRD>{json.dumps(invalid_prd)}</REVISED_PRD><REVISION_SUMMARY>Done</REVISION_SUMMARY>',
            None
        )
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent,
            revise_prd=True,
            schema=str(self.schema_file)
        )
        original_prd = {"userStories": [{"id": "TASK-001"}]}
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            # The method checks for userStories key first, so it will fall back
            result = orch._revise_prd_impl(original_prd)
        # Should fall back to original since revised PRD is invalid
        self.assertEqual(result, original_prd)


class TestRevisePrdEvents(TempConfigTestCase):
    """Tests for PRD revision events."""

    def test_emits_start_event(self):
        mock_agent = self.create_mock_agent()
        prd = {"userStories": []}
        mock_agent.run.return_value = (
            True,
            f'<REVISED_PRD>{json.dumps(prd)}</REVISED_PRD><REVISION_SUMMARY>Done</REVISION_SUMMARY>',
            None
        )
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            orch._revise_prd_impl(prd)
        self.assertIn(EventType.PRD_REVISE_START, events)

    def test_emits_success_event(self):
        mock_agent = self.create_mock_agent()
        prd = {"userStories": []}
        mock_agent.run.return_value = (
            True,
            f'<REVISED_PRD>{json.dumps(prd)}</REVISED_PRD><REVISION_SUMMARY>Done</REVISION_SUMMARY>',
            None
        )
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
            orch._revise_prd_impl(prd)
        self.assertIn(EventType.PRD_REVISE_SUCCESS, events)

    def test_emits_failure_event_on_agent_failure(self):
        mock_agent = self.create_mock_agent()
        error = AgentError("TestError", "test message", "", "", "", "")
        mock_agent.run.return_value = (False, "", error)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, revise_prd=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
            orch._revise_prd_impl({"userStories": []})
        self.assertIn(EventType.PRD_REVISE_FAILURE, events)


class TestRevisePrdTemplate(unittest.TestCase):
    """Tests for revise_prd.txt template existence."""

    def test_template_exists_in_defaults(self):
        from ralph import TemplateManager
        self.assertIn("revise_prd.txt", TemplateManager.DEFAULT_TEMPLATES)

    def test_template_has_required_placeholders(self):
        from ralph import TemplateManager
        template = TemplateManager.DEFAULT_TEMPLATES["revise_prd.txt"]
        self.assertIn("{{original_prd}}", template)


class TestPrdReviseEventTypes(unittest.TestCase):
    """Tests for PRD revision event type existence."""

    def test_prd_revise_event_types_exist(self):
        expected_events = [
            "PRD_REVISE_START",
            "PRD_REVISE_SUCCESS",
            "PRD_REVISE_FAILURE"
        ]
        for event_name in expected_events:
            self.assertTrue(
                hasattr(EventType, event_name),
                f"EventType.{event_name} should exist"
            )


# ==============================================================================
# QA REVIEW TESTS
# ==============================================================================


class TestQAReviewCLI(unittest.TestCase):
    """Tests for --qa-review CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--qa-review", action="store_true")
        self.parser.add_argument("--qa-strict", action="store_true")

    def test_qa_review_flag_default(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.qa_review)
        self.assertFalse(args.qa_strict)

    def test_qa_review_flag_enabled(self):
        args = self.parser.parse_args(["--qa-review"])
        self.assertTrue(args.qa_review)

    def test_qa_strict_flag(self):
        args = self.parser.parse_args(["--qa-review", "--qa-strict"])
        self.assertTrue(args.qa_review)
        self.assertTrue(args.qa_strict)


class TestQAReviewCLIPassthrough(unittest.TestCase):
    """Tests for --qa-review flags passed to orchestrator."""

    def test_qa_review_passed(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--qa-review']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('qa_review'))

    def test_qa_strict_passed(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--qa-review', '--qa-strict']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('qa_review'))
            self.assertTrue(call_kwargs.get('qa_strict'))


class TestQAReviewOrchestrator(TempConfigTestCase):
    """Tests for QA review in RalphOrchestrator."""

    def test_orchestrator_stores_qa_review_flag(self):
        orch = self.create_mock_orchestrator(qa_review=True)
        self.assertTrue(orch._qa_review)

    def test_orchestrator_stores_qa_strict_flag(self):
        orch = self.create_mock_orchestrator(qa_review=True, qa_strict=True)
        self.assertTrue(orch._qa_review)
        self.assertTrue(orch._qa_strict)

    def test_orchestrator_defaults_to_no_qa_review(self):
        orch = self.create_mock_orchestrator()
        self.assertFalse(orch._qa_review)
        self.assertFalse(orch._qa_strict)


class TestQAReviewMethod(TempConfigTestCase):
    """Tests for _run_qa_review method."""

    def test_skips_when_disabled(self):
        orch = self.create_mock_orchestrator(qa_review=False)
        task = {"id": "TASK-001", "description": "Test task"}
        passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)
        self.assertIsNone(findings)

    def test_skips_when_no_code_changes(self):
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="(No code changes detected)"):
            with patch('ralph.Logger.info'):
                passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)
        self.assertIsNone(findings)
        mock_agent.run.assert_not_called()

    def test_passes_with_no_critical_issues(self):
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "PASS",
  "critical_issues": [],
  "warnings": [],
  "suggestions": [],
  "passed_checks": ["Error handling", "Security"]
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)
        self.assertIsNotNone(findings)
        self.assertEqual(findings["summary"], "PASS")

    def test_passes_with_critical_issues_non_strict(self):
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "severity": "critical", "description": "SQL injection"}],
  "warnings": [],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_strict=False)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=False):
                with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                    passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)  # Non-strict mode continues despite critical issues
        self.assertIsNotNone(findings)
        self.assertEqual(len(findings["critical_issues"]), 1)

    def test_fails_with_critical_issues_strict(self):
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "severity": "critical", "description": "SQL injection"}],
  "warnings": [],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_strict=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                passed, findings = orch._run_qa_review(task)
        self.assertFalse(passed)  # Strict mode fails on critical issues
        self.assertIsNotNone(findings)

    def test_continues_on_agent_failure(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (False, "", AgentError("TestError", "test", "", "", "", ""))
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
                passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)  # Continues despite agent failure
        self.assertIsNone(findings)

    def test_continues_on_parse_failure(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "Invalid response without QA_FINDINGS tags", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.warning'), patch('ralph.Logger.debug'):
                passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)  # Continues despite parse failure
        self.assertIsNone(findings)

    def test_continues_on_agent_exception(self):
        """Test that QA review gracefully handles agent exceptions."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.side_effect = Exception("Unexpected agent crash")
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
                passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)  # Continues despite exception
        self.assertIsNone(findings)

    def test_skips_when_unable_to_detect_changes(self):
        """Test that QA review skips when unable to detect code changes."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="(Unable to detect code changes)"):
            with patch('ralph.Logger.info'):
                passed, findings = orch._run_qa_review(task)
        self.assertTrue(passed)
        self.assertIsNone(findings)
        mock_agent.run.assert_not_called()


class TestParseQAFindings(TempConfigTestCase):
    """Tests for _parse_qa_findings method."""

    def test_parses_valid_findings(self):
        orch = self.create_mock_orchestrator()
        response = """Some preamble text
<QA_FINDINGS>
{
  "summary": "WARN",
  "critical_issues": [],
  "warnings": [{"category": "style", "severity": "warning", "description": "Long function"}],
  "suggestions": [],
  "passed_checks": ["Security"]
}
</QA_FINDINGS>
Some trailing text"""
        findings = orch._parse_qa_findings(response)
        self.assertIsNotNone(findings)
        self.assertEqual(findings["summary"], "WARN")
        self.assertEqual(len(findings["warnings"]), 1)

    def test_returns_none_for_missing_tags(self):
        orch = self.create_mock_orchestrator()
        response = "No tags here at all"
        with patch('ralph.Logger.debug'):
            findings = orch._parse_qa_findings(response)
        self.assertIsNone(findings)

    def test_returns_none_for_invalid_json(self):
        orch = self.create_mock_orchestrator()
        response = "<QA_FINDINGS>not valid json</QA_FINDINGS>"
        with patch('ralph.Logger.debug'):
            findings = orch._parse_qa_findings(response)
        self.assertIsNone(findings)


class TestQAReviewTemplate(unittest.TestCase):
    """Tests for QA review template."""

    def test_template_exists(self):
        from ralph import TemplateManager
        self.assertIn("qa_review.txt", TemplateManager.DEFAULT_TEMPLATES)

    def test_template_has_required_placeholders(self):
        from ralph import TemplateManager
        template = TemplateManager.DEFAULT_TEMPLATES["qa_review.txt"]
        required_placeholders = [
            "{{task_id}}",
            "{{task_description}}",
            "{{acceptance_criteria}}",
            "{{code_changes}}",
            "{{memory_map}}"
        ]
        for placeholder in required_placeholders:
            self.assertIn(placeholder, template)


class TestQAReviewEventTypes(unittest.TestCase):
    """Tests for QA review event type existence."""

    def test_qa_review_event_types_exist(self):
        expected_events = [
            "QA_REVIEW_START",
            "QA_REVIEW_SUCCESS",
            "QA_REVIEW_FAILURE",
            "QA_REVIEW_SKIPPED"
        ]
        for event_name in expected_events:
            self.assertTrue(
                hasattr(EventType, event_name),
                f"EventType.{event_name} should exist"
            )


class TestQAReviewEvents(TempConfigTestCase):
    """Tests for QA review event emissions."""

    def test_emits_start_event(self):
        """Test that QA review emits start event."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{"summary": "PASS", "critical_issues": [], "warnings": [], "suggestions": [], "passed_checks": []}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                orch._run_qa_review(task)
        self.assertIn(EventType.QA_REVIEW_START, events)

    def test_emits_success_event(self):
        """Test that QA review emits success event on pass."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{"summary": "PASS", "critical_issues": [], "warnings": [], "suggestions": [], "passed_checks": []}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                orch._run_qa_review(task)
        self.assertIn(EventType.QA_REVIEW_SUCCESS, events)

    def test_emits_skipped_event_on_no_changes(self):
        """Test that QA review emits skipped event when no code changes."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="(No code changes detected)"):
            with patch('ralph.Logger.info'):
                orch._run_qa_review(task)
        self.assertIn(EventType.QA_REVIEW_SKIPPED, events)

    def test_emits_failure_event_on_agent_failure(self):
        """Test that QA review emits failure event when agent fails."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (False, "", AgentError("TestError", "test", "", "", "", ""))
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
                orch._run_qa_review(task)
        self.assertIn(EventType.QA_REVIEW_FAILURE, events)

    def test_emits_failure_event_on_parse_failure(self):
        """Test that QA review emits failure event when parsing fails."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "Invalid response", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.warning'), patch('ralph.Logger.debug'):
                orch._run_qa_review(task)
        self.assertIn(EventType.QA_REVIEW_FAILURE, events)

    def test_emits_failure_event_on_agent_exception(self):
        """Test that QA review emits failure event when agent raises exception."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.side_effect = Exception("Unexpected crash")
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch('ralph.Logger.info'), patch('ralph.Logger.warning'):
                orch._run_qa_review(task)
        self.assertIn(EventType.QA_REVIEW_FAILURE, events)


# ==============================================================================
# QA REVIEW PRD PROMPT TESTS (TASK-003)
# ==============================================================================


class TestQAReviewPRDPrompt(TempConfigTestCase):
    """Tests for PRD generation prompt after QA review findings."""

    def test_prompt_returns_true_for_yes_response(self):
        """Test that _prompt_for_prd_generation returns True for 'y' input."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        with patch('builtins.input', return_value='y'):
            result = orch._prompt_for_prd_generation(findings)
        self.assertTrue(result)

    def test_prompt_returns_true_for_yes_word_response(self):
        """Test that _prompt_for_prd_generation returns True for 'yes' input."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        with patch('builtins.input', return_value='yes'):
            result = orch._prompt_for_prd_generation(findings)
        self.assertTrue(result)

    def test_prompt_returns_false_for_no_response(self):
        """Test that _prompt_for_prd_generation returns False for 'n' input."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        with patch('builtins.input', return_value='n'):
            result = orch._prompt_for_prd_generation(findings)
        self.assertFalse(result)

    def test_prompt_returns_false_for_no_word_response(self):
        """Test that _prompt_for_prd_generation returns False for 'no' input."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        with patch('builtins.input', return_value='no'):
            result = orch._prompt_for_prd_generation(findings)
        self.assertFalse(result)

    def test_prompt_skipped_in_non_interactive_mode(self):
        """Test that prompt is skipped in non-interactive mode."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, non_interactive=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        with patch('ralph.Logger.info') as mock_info:
            result = orch._prompt_for_prd_generation(findings)
        self.assertFalse(result)
        mock_info.assert_called()

    def test_prompt_skipped_in_ci_mode(self):
        """Test that prompt is skipped in CI mode (which implies non-interactive)."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, non_interactive=True, ci=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        with patch('ralph.Logger.info'):
            result = orch._prompt_for_prd_generation(findings)
        self.assertFalse(result)

    def test_prompt_reprompts_on_invalid_input(self):
        """Test that invalid input triggers re-prompt."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        findings = {"critical_issues": [{"description": "test"}], "warnings": []}
        # First input is invalid, second is valid
        with patch('builtins.input', side_effect=['invalid', 'maybe', 'y']):
            with patch('ralph.Logger.info') as mock_info:
                result = orch._prompt_for_prd_generation(findings)
        self.assertTrue(result)
        # Should have shown invalid input warning twice
        self.assertEqual(mock_info.call_count, 2)


class TestQAReviewPRDGeneration(TempConfigTestCase):
    """Tests for PRD generation from QA findings."""

    def test_generate_prd_creates_valid_prd_structure(self):
        """Test that _generate_prd_from_qa_findings creates a valid PRD structure."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": [{"category": "style", "description": "Inconsistent naming"}]
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        # Verify PRD was created with valid structure
        self.assertTrue(CONF.PRD_FILE.exists())
        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        self.assertIn("id", prd_data)
        self.assertIn("description", prd_data)
        self.assertIn("userStories", prd_data)
        self.assertEqual(len(prd_data["userStories"]), 2)  # One per finding

    def test_generate_prd_from_findings_includes_task_context(self):
        """Test that generated PRD includes task ID and description."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Implement login feature"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        self.assertIn("TASK-001", prd_data["id"])
        self.assertIn("TASK-001", prd_data["description"])

    def test_generate_prd_user_story_format(self):
        """Test that user stories have proper format with TASK-XXX IDs."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        story = prd_data["userStories"][0]
        self.assertRegex(story["id"], r"TASK-\d{3}")
        self.assertIn("As a developer", story["description"])
        self.assertIn("acceptanceCriteria", story)
        self.assertIsInstance(story["acceptanceCriteria"], list)
        self.assertGreater(len(story["acceptanceCriteria"]), 0)

    def test_generate_prd_single_finding_creates_single_story(self):
        """Test that a single finding creates a single-story PRD."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": [],
            "suggestions": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        self.assertEqual(len(prd_data["userStories"]), 1)

    def test_generate_prd_many_findings_groups_by_category(self):
        """Test that 50+ findings are grouped by category."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}

        # Create 60 findings across 3 categories
        findings = {
            "critical_issues": [{"category": "security", "description": f"Issue {i}"} for i in range(20)],
            "warnings": [{"category": "style", "description": f"Warning {i}"} for i in range(20)],
            "suggestions": [{"category": "performance", "description": f"Suggestion {i}"} for i in range(20)]
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        # Should be grouped into 3 stories (one per category)
        self.assertEqual(len(prd_data["userStories"]), 3)
        # Each story should mention the count
        for story in prd_data["userStories"]:
            self.assertIn("20 findings", story["description"])

    def test_generate_prd_error_handling_invalid_findings(self):
        """Test error handling when findings data is invalid."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        # Empty findings - no valid data
        findings = {
            "critical_issues": [],
            "warnings": [],
            "suggestions": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error') as mock_error:
            orch._generate_prd_from_qa_findings(task, findings)

        # Should log an error
        mock_error.assert_called()
        error_call = str(mock_error.call_args)
        self.assertIn("PRD generation failed", error_call)

    def test_generate_prd_schema_validation(self):
        """Test that generated PRD passes schema validation when --schema is provided."""
        mock_agent = self.create_mock_agent()
        # Create a schema file
        schema_path = CONF.ROOT_DIR / "prd_schema.json"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        schema = {
            "type": "object",
            "required": ["id", "description", "userStories"],
            "properties": {
                "id": {"type": "string"},
                "description": {"type": "string"},
                "userStories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "description", "acceptanceCriteria"],
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "acceptanceCriteria": {"type": "array"}
                        }
                    }
                }
            }
        }
        schema_path.write_text(json.dumps(schema), encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, schema=str(schema_path))
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        # PRD should be created (passes validation)
        self.assertTrue(CONF.PRD_FILE.exists())

    def test_generate_prd_priority_based_on_severity(self):
        """Test that story priority is based on finding severity."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "Critical issue"}],
            "warnings": [{"category": "style", "description": "Warning issue"}],
            "suggestions": [{"category": "docs", "description": "Suggestion"}]
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        priorities = [story["priority"] for story in prd_data["userStories"]]
        self.assertIn("Must Have", priorities)  # Critical
        self.assertIn("Should Have", priorities)  # Warning
        self.assertIn("Could Have", priorities)  # Suggestion


# ==============================================================================
# STANDALONE QA REVIEW WORKFLOW TESTS (TASK-006)
# ==============================================================================


class TestStandaloneQAReviewCLI(unittest.TestCase):
    """Tests for --qa-path CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--qa-review", action="store_true")
        self.parser.add_argument("--qa-path", type=str)

    def test_qa_path_flag_parses(self):
        args = self.parser.parse_args(["--qa-review", "--qa-path", "/some/path"])
        self.assertTrue(args.qa_review)
        self.assertEqual(args.qa_path, "/some/path")

    def test_qa_path_without_qa_review_parses(self):
        """Test that --qa-path can be parsed without --qa-review (validation is in main)."""
        args = self.parser.parse_args(["--qa-path", "/some/path"])
        self.assertFalse(args.qa_review)
        self.assertEqual(args.qa_path, "/some/path")


class TestStandaloneQAReviewCLIValidation(unittest.TestCase):
    """Tests for --qa-path CLI argument validation."""

    def test_qa_path_requires_qa_review_flag(self):
        """Test that --qa-path without --qa-review errors in main()."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--qa-path', '/some/path']):
                with patch('ralph.Logger.error') as mock_error:
                    with self.assertRaises(SystemExit) as ctx:
                        main()
                    self.assertEqual(ctx.exception.code, 1)
                    mock_error.assert_called()
                    # Verify the error message mentions --qa-review requirement
                    call_args = mock_error.call_args[0][0]
                    self.assertIn("--qa-review", call_args)

    def test_qa_path_incompatible_with_phase(self):
        """Test that --qa-path with phase argument errors in main()."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', 'execute', '--qa-review', '--qa-path', '/some/path']):
                with patch('ralph.Logger.error') as mock_error:
                    with self.assertRaises(SystemExit) as ctx:
                        main()
                    self.assertEqual(ctx.exception.code, 1)
                    mock_error.assert_called()


class TestStandaloneQAReviewCLIPassthrough(unittest.TestCase):
    """Tests for --qa-path flag passed to orchestrator."""

    def test_qa_path_passed_to_orchestrator(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_instance = MagicMock()
            mock_orch.return_value = mock_instance
            with patch('sys.argv', ['ralph', '--qa-review', '--qa-path', '/test/path']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('qa_review'))
            self.assertEqual(call_kwargs.get('qa_path'), '/test/path')


class TestStandaloneQAReviewOrchestrator(TempConfigTestCase):
    """Tests for standalone QA review in RalphOrchestrator."""

    def test_orchestrator_stores_qa_path_flag(self):
        orch = self.create_mock_orchestrator(qa_review=True, qa_path="/test/path")
        self.assertTrue(orch._qa_review)
        self.assertEqual(orch._qa_path, "/test/path")

    def test_orchestrator_defaults_to_no_qa_path(self):
        orch = self.create_mock_orchestrator()
        self.assertIsNone(orch._qa_path)


class TestStandaloneQAReviewMethod(TempConfigTestCase):
    """Tests for _run_standalone_qa_review method."""

    def test_review_path_not_exists_exits_with_error(self):
        """Test that non-existent path causes exit with error."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path="/nonexistent/path")
        with patch('ralph.Logger.error') as mock_error:
            with self.assertRaises(SystemExit) as ctx:
                orch._run_standalone_qa_review()
            self.assertEqual(ctx.exception.code, 1)
            mock_error.assert_called()

    def test_review_single_file(self):
        """Test standalone QA review of a single file."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "PASS", "critical_issues": [], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        # Create a test file
        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        with patch('ralph.Logger.info'):
            orch._run_standalone_qa_review()

        mock_agent.run.assert_called_once()

    def test_review_directory(self):
        """Test standalone QA review of a directory."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "PASS", "critical_issues": [], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        # Create a test directory with files outside .ralph (which is excluded)
        test_dir = CONF.BASE_DIR / "test_src"
        test_dir.mkdir(parents=True, exist_ok=True)
        (test_dir / "file1.py").write_text("print('file1')", encoding='utf-8')
        (test_dir / "file2.py").write_text("print('file2')", encoding='utf-8')

        try:
            orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_dir))
            with patch('ralph.Logger.info'):
                orch._run_standalone_qa_review()

            mock_agent.run.assert_called_once()
        finally:
            # Cleanup
            import shutil
            if test_dir.exists():
                shutil.rmtree(test_dir)

    def test_non_interactive_skips_prd_prompt(self):
        """Test that non-interactive mode skips PRD generation prompt."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "WARN", "critical_issues": [{"category": "security", "description": "test"}], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file), non_interactive=True)
        with patch('ralph.Logger.info') as mock_info:
            with patch.object(orch, '_prompt_for_prd_generation') as mock_prompt:
                orch._run_standalone_qa_review()
                # Should not call prompt in non-interactive mode
                mock_prompt.assert_not_called()

    def test_findings_trigger_prd_prompt(self):
        """Test that findings in interactive mode trigger PRD generation prompt."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "WARN", "critical_issues": [{"category": "security", "description": "test"}], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        with patch('ralph.Logger.info'):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=False) as mock_prompt:
                orch._run_standalone_qa_review()
                mock_prompt.assert_called_once()

    def test_agent_failure_exits_with_error(self):
        """Test that agent failure causes exit with error."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (False, None, "Agent failed")

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        with patch('ralph.Logger.error'):
            with patch('ralph.Logger.info'):
                with self.assertRaises(SystemExit) as ctx:
                    orch._run_standalone_qa_review()
                self.assertEqual(ctx.exception.code, 1)

    def test_parse_failure_exits_with_error(self):
        """Test that parse failure causes exit with error."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "Invalid response without QA_FINDINGS tags", None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        with patch('ralph.Logger.error'):
            with patch('ralph.Logger.info'):
                with self.assertRaises(SystemExit) as ctx:
                    orch._run_standalone_qa_review()
                self.assertEqual(ctx.exception.code, 1)


class TestStandaloneQAReviewEvents(TempConfigTestCase):
    """Tests for standalone QA review event emissions."""

    def test_emits_start_event(self):
        """Test that standalone QA review emits start event."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "PASS", "critical_issues": [], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        with patch('ralph.Logger.info'):
            orch._run_standalone_qa_review()
        self.assertIn(EventType.QA_REVIEW_START, events)

    def test_emits_success_event(self):
        """Test that standalone QA review emits success event."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "PASS", "critical_issues": [], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        events = []
        orch.hooks.emit = lambda e: events.append(e.event_type)
        with patch('ralph.Logger.info'):
            orch._run_standalone_qa_review()
        self.assertIn(EventType.QA_REVIEW_SUCCESS, events)


class TestStandaloneQAReviewTemplate(unittest.TestCase):
    """Tests for standalone QA review template."""

    def test_template_exists(self):
        from ralph import TemplateManager
        self.assertIn("qa_standalone_review.txt", TemplateManager.DEFAULT_TEMPLATES)

    def test_template_contains_required_variables(self):
        from ralph import TemplateManager
        template = TemplateManager.DEFAULT_TEMPLATES["qa_standalone_review.txt"]
        self.assertIn("{{review_path}}", template)
        self.assertIn("{{codebase_files}}", template)
        self.assertIn("{{memory_map}}", template)


class TestStandaloneQAReviewPRDGeneration(TempConfigTestCase):
    """Tests for PRD generation from standalone QA review findings."""

    def test_generates_prd_when_user_accepts(self):
        """Test that PRD is generated when user accepts prompt."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "WARN", "critical_issues": [{"category": "security", "description": "test"}], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file))
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=True):
                orch._run_standalone_qa_review()

        # Check that PRD was created
        self.assertTrue(CONF.PRD_FILE.exists())

    def test_prd_out_flag_saves_to_custom_location(self):
        """Test that --prd-out saves PRD to specified location."""
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, '<QA_FINDINGS>{"summary": "WARN", "critical_issues": [{"category": "security", "description": "test"}], "warnings": [], "suggestions": [], "passed_checks": []}</QA_FINDINGS>', None)

        test_file = CONF.ROOT_DIR / "test_file.py"
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')", encoding='utf-8')

        custom_prd_path = CONF.ROOT_DIR / "custom_prd.json"
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_path=str(test_file), prd_out=str(custom_prd_path))
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=True):
                orch._run_standalone_qa_review()

        # Check that PRD was saved to custom location
        self.assertTrue(custom_prd_path.exists())


class TestPRDFileSaving(TempConfigTestCase):
    """Tests for PRD file saving with overwrite protection and error handling."""

    def test_save_prd_to_default_location(self):
        """Test that PRD is saved to default .ralph/prd.json location."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        self.assertTrue(CONF.PRD_FILE.exists())
        prd_data = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        self.assertIn("id", prd_data)

    def test_save_prd_to_custom_location_with_prd_out(self):
        """Test that --prd-out flag saves PRD to custom location."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "custom" / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }
        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        self.assertTrue(custom_path.exists())
        prd_data = json.loads(custom_path.read_text(encoding='utf-8'))
        self.assertIn("id", prd_data)

    def test_save_prd_creates_directory_if_not_exists(self):
        """Test that output directory is created automatically."""
        mock_agent = self.create_mock_agent()
        nested_path = self.temp_path / "deep" / "nested" / "path" / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(nested_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }
        self.assertFalse(nested_path.parent.exists())

        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        self.assertTrue(nested_path.exists())

    def test_overwrite_error_in_non_interactive_mode(self):
        """Test that existing file causes error in non-interactive mode."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "existing.json"
        custom_path.write_text('{"existing": "content"}', encoding='utf-8')

        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path), non_interactive=True
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info') as mock_info, patch('ralph.Logger.error') as mock_error:
            with patch('builtins.print') as mock_print:
                orch._generate_prd_from_qa_findings(task, findings)

        # Should show error about existing file
        error_calls = [str(call) for call in mock_error.call_args_list]
        self.assertTrue(any("already exists" in call for call in error_calls))
        # Should print PRD to stdout as fallback
        mock_print.assert_called()
        # Original file should remain unchanged
        self.assertEqual(custom_path.read_text(encoding='utf-8'), '{"existing": "content"}')

    def test_overwrite_confirmation_accepted_in_interactive_mode(self):
        """Test that overwrite confirmation works in interactive mode."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "existing.json"
        custom_path.write_text('{"existing": "content"}', encoding='utf-8')

        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path), non_interactive=False
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            with patch('builtins.input', return_value='y'):
                orch._generate_prd_from_qa_findings(task, findings)

        # File should be overwritten
        prd_data = json.loads(custom_path.read_text(encoding='utf-8'))
        self.assertIn("userStories", prd_data)

    def test_overwrite_confirmation_declined_in_interactive_mode(self):
        """Test that declining overwrite prints PRD to stdout."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "existing.json"
        custom_path.write_text('{"existing": "content"}', encoding='utf-8')

        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path), non_interactive=False
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            with patch('builtins.input', return_value='n'):
                with patch('builtins.print') as mock_print:
                    orch._generate_prd_from_qa_findings(task, findings)

        # Original file should remain unchanged
        self.assertEqual(custom_path.read_text(encoding='utf-8'), '{"existing": "content"}')
        # PRD should be printed to stdout
        mock_print.assert_called()

    def test_save_prd_displays_file_path_on_success(self):
        """Test that confirmation message shows file path."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info') as mock_info, patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        # Check for file path in info messages
        info_calls = [str(call) for call in mock_info.call_args_list]
        self.assertTrue(any("output.json" in call for call in info_calls))

    def test_save_prd_valid_json_output(self):
        """Test that saved PRD file contains valid JSON matching schema."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": [{"category": "style", "description": "Naming issue"}]
        }

        with patch('ralph.Logger.info'), patch('ralph.Logger.error'):
            orch._generate_prd_from_qa_findings(task, findings)

        prd_data = json.loads(custom_path.read_text(encoding='utf-8'))
        # Validate PRD schema
        self.assertIn("id", prd_data)
        self.assertIn("description", prd_data)
        self.assertIn("userStories", prd_data)
        self.assertIsInstance(prd_data["userStories"], list)
        for story in prd_data["userStories"]:
            self.assertIn("id", story)
            self.assertIn("description", story)
            self.assertIn("acceptanceCriteria", story)
            self.assertIn("status", story)

    def test_save_prd_permission_error_fallback_to_stdout(self):
        """Test that permission errors fall back to stdout."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info'), patch('ralph.Logger.error') as mock_error:
            with patch('builtins.print') as mock_print:
                with patch.object(Path, 'write_text', side_effect=PermissionError("Access denied")):
                    orch._generate_prd_from_qa_findings(task, findings)

        # Should show permission error
        error_calls = [str(call) for call in mock_error.call_args_list]
        self.assertTrue(any("Permission denied" in call for call in error_calls))
        # Should print PRD to stdout
        mock_print.assert_called()

    def test_save_prd_oserror_fallback_to_stdout(self):
        """Test that OS errors (disk full, etc.) fall back to stdout."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info'), patch('ralph.Logger.error') as mock_error:
            with patch('builtins.print') as mock_print:
                with patch.object(Path, 'write_text', side_effect=OSError("Disk full")):
                    orch._generate_prd_from_qa_findings(task, findings)

        # Should show OS error
        error_calls = [str(call) for call in mock_error.call_args_list]
        self.assertTrue(any("Failed to write PRD" in call for call in error_calls))
        # Should print PRD to stdout
        mock_print.assert_called()

    def test_overwrite_prompt_retries_on_invalid_input(self):
        """Test that overwrite prompt retries on invalid input."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "existing.json"
        custom_path.write_text('{"existing": "content"}', encoding='utf-8')

        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path), non_interactive=False
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info') as mock_info, patch('ralph.Logger.error'):
            with patch('builtins.input', side_effect=['invalid', 'maybe', 'y']):
                orch._generate_prd_from_qa_findings(task, findings)

        # Should have shown invalid input warning twice
        info_calls = [str(call) for call in mock_info.call_args_list]
        invalid_warnings = [c for c in info_calls if "Invalid input" in c]
        self.assertEqual(len(invalid_warnings), 2)
        # File should be overwritten after 'y'
        prd_data = json.loads(custom_path.read_text(encoding='utf-8'))
        self.assertIn("userStories", prd_data)

    def test_save_prd_directory_creation_failure_fallback(self):
        """Test fallback when directory creation fails."""
        mock_agent = self.create_mock_agent()
        custom_path = self.temp_path / "protected" / "output.json"
        orch = self.create_mock_orchestrator(
            mock_agent=mock_agent, qa_review=True, prd_out=str(custom_path)
        )
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        task = {"id": "TASK-001", "description": "Test task"}
        findings = {
            "critical_issues": [{"category": "security", "description": "SQL injection"}],
            "warnings": []
        }

        with patch('ralph.Logger.info'), patch('ralph.Logger.error') as mock_error:
            with patch('builtins.print') as mock_print:
                with patch.object(Path, 'mkdir', side_effect=OSError("Cannot create directory")):
                    orch._generate_prd_from_qa_findings(task, findings)

        # Should show directory creation error
        error_calls = [str(call) for call in mock_error.call_args_list]
        self.assertTrue(any("Failed to create output directory" in call for call in error_calls))
        # Should print PRD to stdout
        mock_print.assert_called()


class TestQAReviewWithPRDPromptIntegration(TempConfigTestCase):
    """Integration tests for QA review with PRD prompt workflow."""

    def test_no_prompt_when_no_findings(self):
        """Test that no prompt is shown when QA review has no findings."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "PASS",
  "critical_issues": [],
  "warnings": [],
  "suggestions": [],
  "passed_checks": ["All checks passed"]
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation') as mock_prompt:
                with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                    orch._run_qa_review(task)
        mock_prompt.assert_not_called()

    def test_prompt_shown_when_critical_issues_found(self):
        """Test that prompt is shown when critical issues are found."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "description": "SQL injection"}],
  "warnings": [],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=False) as mock_prompt:
                with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                    orch._run_qa_review(task)
        mock_prompt.assert_called_once()

    def test_prompt_shown_when_only_warnings_found(self):
        """Test that prompt is shown when only warnings are found."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "WARN",
  "critical_issues": [],
  "warnings": [{"category": "style", "description": "Inconsistent naming"}],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=False) as mock_prompt:
                with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                    orch._run_qa_review(task)
        mock_prompt.assert_called_once()

    def test_no_prompt_in_strict_mode_with_critical_issues(self):
        """Test that prompt is NOT shown in strict mode (task fails instead)."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "description": "SQL injection"}],
  "warnings": [],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True, qa_strict=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation') as mock_prompt:
                with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                    passed, _ = orch._run_qa_review(task)
        mock_prompt.assert_not_called()
        self.assertFalse(passed)

    def test_prd_generated_when_user_accepts(self):
        """Test that PRD is generated when user accepts the prompt."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "description": "SQL injection"}],
  "warnings": [],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=True):
                with patch.object(orch, '_generate_prd_from_qa_findings') as mock_gen:
                    with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                        orch._run_qa_review(task)
        mock_gen.assert_called_once()

    def test_prd_not_generated_when_user_declines(self):
        """Test that PRD is NOT generated when user declines the prompt."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "description": "SQL injection"}],
  "warnings": [],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=False):
                with patch.object(orch, '_generate_prd_from_qa_findings') as mock_gen:
                    with patch('ralph.Logger.info'), patch('ralph.Logger.debug'):
                        orch._run_qa_review(task)
        mock_gen.assert_not_called()

    def test_graceful_exit_message_when_user_declines(self):
        """Test that a summary message is shown when user declines PRD generation."""
        mock_agent = self.create_mock_agent()
        qa_response = """<QA_FINDINGS>
{
  "summary": "FAIL",
  "critical_issues": [{"category": "security", "description": "SQL injection"}],
  "warnings": [{"category": "style", "description": "warning"}],
  "suggestions": [],
  "passed_checks": []
}
</QA_FINDINGS>"""
        mock_agent.run.return_value = (True, qa_response, None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)
        task = {"id": "TASK-001", "description": "Test task"}
        with patch.object(orch, '_get_code_changes', return_value="diff --git a/file.py"):
            with patch.object(orch, '_prompt_for_prd_generation', return_value=False):
                with patch('ralph.Logger.info') as mock_info, patch('ralph.Logger.debug'):
                    orch._run_qa_review(task)
        # Check that the summary message was logged
        calls = [str(call) for call in mock_info.call_args_list]
        found_summary = any("2 finding(s)" in str(call) and "Continuing without PRD generation" in str(call) for call in calls)
        self.assertTrue(found_summary, f"Expected summary message not found in calls: {calls}")


# ==============================================================================
# ENHANCE-ALL TESTS
# ==============================================================================


class TestEnhanceAllCLI(unittest.TestCase):
    """Tests for --enhance-all CLI argument parsing."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--enhance-all", action="store_true")
        self.parser.add_argument("--enhance-intent", action="store_true")
        self.parser.add_argument("--no-enhance-intent", action="store_true")
        self.parser.add_argument("--revise-prd", action="store_true")
        self.parser.add_argument("--no-revise-prd", action="store_true")
        self.parser.add_argument("--qa-review", action="store_true")
        self.parser.add_argument("--no-qa-review", action="store_true")

    def test_enhance_all_flag_default(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.enhance_all)

    def test_enhance_all_flag_enabled(self):
        args = self.parser.parse_args(["--enhance-all"])
        self.assertTrue(args.enhance_all)

    def test_no_enhance_intent_flag(self):
        args = self.parser.parse_args(["--no-enhance-intent"])
        self.assertTrue(args.no_enhance_intent)

    def test_no_revise_prd_flag(self):
        args = self.parser.parse_args(["--no-revise-prd"])
        self.assertTrue(args.no_revise_prd)

    def test_no_qa_review_flag(self):
        args = self.parser.parse_args(["--no-qa-review"])
        self.assertTrue(args.no_qa_review)

    def test_enhance_all_with_override_flags(self):
        args = self.parser.parse_args(["--enhance-all", "--no-qa-review"])
        self.assertTrue(args.enhance_all)
        self.assertTrue(args.no_qa_review)


class TestEnhanceAllCLIPassthrough(unittest.TestCase):
    """Tests for --enhance-all flags passed to orchestrator."""

    def test_enhance_all_enables_all_features(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-all']):
                with patch('ralph.Logger.info'):
                    main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('enhance_intent'))
            self.assertTrue(call_kwargs.get('revise_prd'))
            self.assertTrue(call_kwargs.get('qa_review'))

    def test_enhance_all_with_no_enhance_intent_override(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-all', '--no-enhance-intent']):
                with patch('ralph.Logger.info'):
                    main()
            call_kwargs = mock_orch.call_args[1]
            self.assertFalse(call_kwargs.get('enhance_intent'))
            self.assertTrue(call_kwargs.get('revise_prd'))
            self.assertTrue(call_kwargs.get('qa_review'))

    def test_enhance_all_with_no_revise_prd_override(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-all', '--no-revise-prd']):
                with patch('ralph.Logger.info'):
                    main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('enhance_intent'))
            self.assertFalse(call_kwargs.get('revise_prd'))
            self.assertTrue(call_kwargs.get('qa_review'))

    def test_enhance_all_with_no_qa_review_override(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-all', '--no-qa-review']):
                with patch('ralph.Logger.info'):
                    main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('enhance_intent'))
            self.assertTrue(call_kwargs.get('revise_prd'))
            self.assertFalse(call_kwargs.get('qa_review'))

    def test_enhance_all_with_multiple_overrides(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-all', '--no-enhance-intent', '--no-qa-review']):
                with patch('ralph.Logger.info'):
                    main()
            call_kwargs = mock_orch.call_args[1]
            self.assertFalse(call_kwargs.get('enhance_intent'))
            self.assertTrue(call_kwargs.get('revise_prd'))
            self.assertFalse(call_kwargs.get('qa_review'))

    def test_explicit_flags_work_without_enhance_all(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-intent', '--qa-review']):
                main()
            call_kwargs = mock_orch.call_args[1]
            self.assertTrue(call_kwargs.get('enhance_intent'))
            self.assertFalse(call_kwargs.get('revise_prd'))
            self.assertTrue(call_kwargs.get('qa_review'))

    def test_explicit_positive_flag_overrides_no_flag(self):
        """Explicit positive flags should work even when --no-* flags are specified."""
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--enhance-intent', '--no-enhance-intent']):
                main()
            call_kwargs = mock_orch.call_args[1]
            # Explicit positive flag should win since --no-* is for --enhance-all overrides
            self.assertTrue(call_kwargs.get('enhance_intent'))


class TestEnhanceAllLogging(unittest.TestCase):
    """Tests for --enhance-all logging of enabled features."""

    def test_logs_enabled_features_when_all_enabled(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('ralph.Logger.info') as mock_log:
                with patch('sys.argv', ['ralph', '--enhance-all']):
                    main()
            # Check that enabled features are logged
            log_calls = [str(call) for call in mock_log.call_args_list]
            enabled_log = [c for c in log_calls if 'enabled' in c.lower()]
            self.assertTrue(len(enabled_log) > 0)

    def test_logs_disabled_features_when_override_used(self):
        with patch('ralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('ralph.Logger.info') as mock_log:
                with patch('sys.argv', ['ralph', '--enhance-all', '--no-qa-review']):
                    main()
            # Check that disabled features are logged
            log_calls = [str(call) for call in mock_log.call_args_list]
            disabled_log = [c for c in log_calls if 'disabled' in c.lower()]
            self.assertTrue(len(disabled_log) > 0)


# ==============================================================================
# QA FINDINGS ANALYSIS TESTS
# ==============================================================================


class TestQAFindingType(unittest.TestCase):
    """Tests for QAFindingType enum."""

    def test_finding_types_exist(self):
        """Test all expected finding types are defined."""
        self.assertTrue(hasattr(QAFindingType, 'ERROR'))
        self.assertTrue(hasattr(QAFindingType, 'WARNING'))
        self.assertTrue(hasattr(QAFindingType, 'SUGGESTION'))
        self.assertTrue(hasattr(QAFindingType, 'INFO'))

    def test_severity_ordering(self):
        """Test that finding types are ordered by severity (ERROR is highest)."""
        self.assertLess(QAFindingType.ERROR, QAFindingType.WARNING)
        self.assertLess(QAFindingType.WARNING, QAFindingType.SUGGESTION)
        self.assertLess(QAFindingType.SUGGESTION, QAFindingType.INFO)


class TestQAFinding(unittest.TestCase):
    """Tests for QAFinding dataclass."""

    def test_from_dict_basic(self):
        """Test creating a finding from a valid dictionary."""
        data = {
            "category": "security",
            "description": "SQL injection vulnerability",
            "location": "src/db.py:42",
            "recommendation": "Use parameterized queries"
        }
        finding = QAFinding.from_dict(data, QAFindingType.ERROR)
        self.assertEqual(finding.finding_type, QAFindingType.ERROR)
        self.assertEqual(finding.category, "security")
        self.assertEqual(finding.description, "SQL injection vulnerability")
        self.assertEqual(finding.file_path, "src/db.py")
        self.assertEqual(finding.line_number, 42)
        self.assertEqual(finding.recommendation, "Use parameterized queries")
        self.assertFalse(finding.has_missing_data)

    def test_from_dict_without_line_number(self):
        """Test creating a finding with file path but no line number."""
        data = {
            "category": "style",
            "description": "Inconsistent naming",
            "location": "src/utils.py"
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertEqual(finding.file_path, "src/utils.py")
        self.assertIsNone(finding.line_number)
        self.assertFalse(finding.has_missing_data)

    def test_from_dict_missing_location(self):
        """Test creating a finding without location information."""
        data = {
            "category": "testing",
            "description": "Missing unit tests"
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertIsNone(finding.file_path)
        self.assertIsNone(finding.line_number)
        self.assertTrue(finding.has_missing_data)
        self.assertIn("location", finding.missing_data_note)

    def test_from_dict_missing_category(self):
        """Test creating a finding without category."""
        data = {
            "description": "Some issue",
            "location": "file.py:10"
        }
        finding = QAFinding.from_dict(data, QAFindingType.SUGGESTION)
        self.assertEqual(finding.category, "unknown")
        self.assertTrue(finding.has_missing_data)
        self.assertIn("category", finding.missing_data_note)

    def test_from_dict_missing_description(self):
        """Test creating a finding without description."""
        data = {
            "category": "error_handling",
            "location": "main.py:1"
        }
        finding = QAFinding.from_dict(data, QAFindingType.ERROR)
        self.assertEqual(finding.description, "No description provided")
        self.assertTrue(finding.has_missing_data)
        self.assertIn("description", finding.missing_data_note)

    def test_from_dict_all_missing(self):
        """Test creating a finding from empty dictionary."""
        data = {}
        finding = QAFinding.from_dict(data, QAFindingType.INFO)
        self.assertEqual(finding.category, "unknown")
        self.assertEqual(finding.description, "No description provided")
        self.assertIsNone(finding.file_path)
        self.assertTrue(finding.has_missing_data)

    def test_from_dict_windows_path(self):
        """Test parsing Windows-style file paths."""
        data = {
            "category": "security",
            "description": "Issue found",
            "location": "C:\\Users\\test\\file.py:100"
        }
        finding = QAFinding.from_dict(data, QAFindingType.ERROR)
        self.assertEqual(finding.file_path, "C:\\Users\\test\\file.py")
        self.assertEqual(finding.line_number, 100)

    def test_from_dict_non_numeric_after_colon(self):
        """Test location with colon but no line number."""
        data = {
            "category": "documentation",
            "description": "Missing docs",
            "location": "general:module_level"
        }
        finding = QAFinding.from_dict(data, QAFindingType.SUGGESTION)
        self.assertEqual(finding.file_path, "general:module_level")
        self.assertIsNone(finding.line_number)

    def test_format_location_with_line(self):
        """Test format_location with file and line number."""
        finding = QAFinding(
            finding_type=QAFindingType.ERROR,
            category="test",
            description="test",
            file_path="src/main.py",
            line_number=42
        )
        self.assertEqual(finding.format_location(), "src/main.py:42")

    def test_format_location_without_line(self):
        """Test format_location with file but no line number."""
        finding = QAFinding(
            finding_type=QAFindingType.WARNING,
            category="test",
            description="test",
            file_path="src/utils.py"
        )
        self.assertEqual(finding.format_location(), "src/utils.py")

    def test_format_location_empty(self):
        """Test format_location with no location info."""
        finding = QAFinding(
            finding_type=QAFindingType.INFO,
            category="test",
            description="test"
        )
        self.assertEqual(finding.format_location(), "")


class TestQAFindingsAnalyzer(unittest.TestCase):
    """Tests for QAFindingsAnalyzer class."""

    def test_basic_parsing(self):
        """Test basic parsing of findings dictionary."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [
                {"category": "security", "description": "SQL injection", "location": "db.py:10"}
            ],
            "warnings": [
                {"category": "style", "description": "Long line", "location": "utils.py:5"}
            ],
            "suggestions": [],
            "passed_checks": ["Error handling", "Documentation"]
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        self.assertEqual(analyzer.summary, "WARN")
        self.assertEqual(len(analyzer.all_findings), 2)
        self.assertEqual(len(analyzer.passed_checks), 2)

    def test_categorization_by_type(self):
        """Test findings are correctly categorized by type."""
        findings_dict = {
            "summary": "FAIL",
            "critical_issues": [
                {"category": "security", "description": "Issue 1", "location": "a.py:1"}
            ],
            "warnings": [
                {"category": "style", "description": "Issue 2", "location": "b.py:2"},
                {"category": "testing", "description": "Issue 3", "location": "c.py:3"}
            ],
            "suggestions": [
                {"category": "documentation", "description": "Issue 4", "location": "d.py:4"}
            ],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)

        errors = analyzer.get_findings_by_type(QAFindingType.ERROR)
        warnings = analyzer.get_findings_by_type(QAFindingType.WARNING)
        suggestions = analyzer.get_findings_by_type(QAFindingType.SUGGESTION)
        info = analyzer.get_findings_by_type(QAFindingType.INFO)

        self.assertEqual(len(errors), 1)
        self.assertEqual(len(warnings), 2)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(len(info), 0)

    def test_sorting_by_severity(self):
        """Test all_findings returns findings sorted by severity."""
        findings_dict = {
            "summary": "FAIL",
            "critical_issues": [
                {"category": "security", "description": "Error", "location": "z.py:1"}
            ],
            "warnings": [
                {"category": "style", "description": "Warning", "location": "a.py:1"}
            ],
            "suggestions": [
                {"category": "docs", "description": "Suggestion", "location": "b.py:1"}
            ],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        all_findings = analyzer.all_findings

        self.assertEqual(all_findings[0].finding_type, QAFindingType.ERROR)
        self.assertEqual(all_findings[1].finding_type, QAFindingType.WARNING)
        self.assertEqual(all_findings[2].finding_type, QAFindingType.SUGGESTION)

    def test_grouping_by_file(self):
        """Test findings are correctly grouped by file."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [
                {"category": "security", "description": "Issue 1", "location": "file_a.py:10"},
                {"category": "security", "description": "Issue 2", "location": "file_b.py:20"}
            ],
            "warnings": [
                {"category": "style", "description": "Issue 3", "location": "file_a.py:30"}
            ],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        grouped = analyzer.get_findings_grouped_by_file()

        self.assertIn("file_a.py", grouped)
        self.assertIn("file_b.py", grouped)
        self.assertEqual(len(grouped["file_a.py"]), 2)
        self.assertEqual(len(grouped["file_b.py"]), 1)

    def test_grouping_sorted_by_severity_within_file(self):
        """Test findings within each file group are sorted by severity."""
        findings_dict = {
            "summary": "FAIL",
            "critical_issues": [
                {"category": "security", "description": "Error", "location": "file.py:100"}
            ],
            "warnings": [
                {"category": "style", "description": "Warning", "location": "file.py:10"}
            ],
            "suggestions": [
                {"category": "docs", "description": "Suggestion", "location": "file.py:50"}
            ],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        grouped = analyzer.get_findings_grouped_by_file()
        file_findings = grouped["file.py"]

        self.assertEqual(file_findings[0].finding_type, QAFindingType.ERROR)
        self.assertEqual(file_findings[1].finding_type, QAFindingType.WARNING)
        self.assertEqual(file_findings[2].finding_type, QAFindingType.SUGGESTION)

    def test_findings_without_file(self):
        """Test findings without file path are grouped under special key."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [],
            "warnings": [
                {"category": "style", "description": "General warning"}
            ],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        grouped = analyzer.get_findings_grouped_by_file()

        self.assertIn("(no file)", grouped)
        self.assertEqual(len(grouped["(no file)"]), 1)

    def test_counts(self):
        """Test get_counts returns correct statistics."""
        findings_dict = {
            "summary": "FAIL",
            "critical_issues": [
                {"category": "a", "description": "1", "location": "f1.py:1"},
                {"category": "b", "description": "2", "location": "f2.py:1"}
            ],
            "warnings": [
                {"category": "c", "description": "3", "location": "f1.py:2"}
            ],
            "suggestions": [
                {"category": "d", "description": "4", "location": "f3.py:1"},
                {"category": "e", "description": "5", "location": "f3.py:2"},
                {"category": "f", "description": "6", "location": "f3.py:3"}
            ],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        counts = analyzer.get_counts()

        self.assertEqual(counts['errors'], 2)
        self.assertEqual(counts['warnings'], 1)
        self.assertEqual(counts['suggestions'], 3)
        self.assertEqual(counts['total'], 6)
        self.assertEqual(counts['files'], 3)

    def test_large_output_detection(self):
        """Test is_large_output flag for many files."""
        # Create findings spanning >50 files
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [],
            "warnings": [
                {"category": "style", "description": f"Issue {i}", "location": f"file_{i}.py:1"}
                for i in range(60)
            ],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        self.assertTrue(analyzer.is_large_output)
        self.assertEqual(analyzer.total_files, 60)

    def test_small_output_detection(self):
        """Test is_large_output is False for small outputs."""
        findings_dict = {
            "summary": "PASS",
            "critical_issues": [],
            "warnings": [
                {"category": "style", "description": "Issue", "location": "file.py:1"}
            ],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        self.assertFalse(analyzer.is_large_output)

    def test_summary_report(self):
        """Test get_summary_report returns structured data."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [
                {"category": "security", "description": "Issue", "location": "file.py:1"}
            ],
            "warnings": [],
            "suggestions": [],
            "passed_checks": ["Test 1", "Test 2"]
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        report = analyzer.get_summary_report()

        self.assertEqual(report['summary'], "WARN")
        self.assertIn('counts', report)
        self.assertIn('files', report)
        self.assertEqual(report['passed_checks'], ["Test 1", "Test 2"])

    def test_format_for_display(self):
        """Test format_for_display returns formatted string."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [
                {"category": "security", "description": "SQL injection", "location": "db.py:10"}
            ],
            "warnings": [],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        output = analyzer.format_for_display()

        self.assertIn("WARN", output)
        self.assertIn("db.py", output)
        self.assertIn("SQL injection", output)

    def test_malformed_finding_non_dict(self):
        """Test handling of non-dict finding entries."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [],
            "warnings": ["This is a string, not a dict", 12345, None],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        warnings = analyzer.get_findings_by_type(QAFindingType.WARNING)

        self.assertEqual(len(warnings), 3)
        for w in warnings:
            self.assertTrue(w.has_missing_data)

    def test_malformed_findings_list(self):
        """Test handling when findings list is not a list."""
        findings_dict = {
            "summary": "PASS",
            "critical_issues": "not a list",
            "warnings": 42,
            "suggestions": None,
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        self.assertEqual(len(analyzer.all_findings), 0)

    def test_empty_findings(self):
        """Test handling of empty findings dictionary."""
        findings_dict = {
            "summary": "PASS",
            "critical_issues": [],
            "warnings": [],
            "suggestions": [],
            "passed_checks": ["All checks passed"]
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        self.assertEqual(len(analyzer.all_findings), 0)
        self.assertEqual(analyzer.get_counts()['total'], 0)

    def test_missing_keys_in_findings_dict(self):
        """Test handling when some keys are missing from findings dict."""
        findings_dict = {
            "summary": "PASS"
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        self.assertEqual(len(analyzer.all_findings), 0)
        self.assertEqual(analyzer.passed_checks, [])


class TestQAFindingsAnalyzerPagination(unittest.TestCase):
    """Tests for QAFindingsAnalyzer pagination functionality."""

    def test_pagination_respects_page_size(self):
        """Test that pagination limits output to page_size."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [],
            "warnings": [
                {"category": "style", "description": f"Issue {i}", "location": f"file_{i}.py:1"}
                for i in range(100)
            ],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict, page_size=5)
        report = analyzer.get_summary_report()

        self.assertLessEqual(len(report['files']), 5)
        self.assertGreater(report['remaining_files'], 0)

    def test_custom_page_size(self):
        """Test custom page_size parameter."""
        findings_dict = {
            "summary": "WARN",
            "critical_issues": [],
            "warnings": [
                {"category": "style", "description": f"Issue {i}", "location": f"file_{i}.py:1"}
                for i in range(100)
            ],
            "suggestions": [],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict, page_size=20)
        report = analyzer.get_summary_report()

        self.assertLessEqual(len(report['files']), 20)


class TestQAFindingsEdgeCases(unittest.TestCase):
    """Tests for edge cases in QA findings analysis."""

    def test_empty_location_string(self):
        """Test handling of empty location string."""
        data = {
            "category": "test",
            "description": "Issue",
            "location": ""
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertIsNone(finding.file_path)
        self.assertTrue(finding.has_missing_data)

    def test_whitespace_only_location(self):
        """Test handling of whitespace-only location."""
        data = {
            "category": "test",
            "description": "Issue",
            "location": "   "
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertEqual(finding.file_path, "")

    def test_location_with_multiple_colons(self):
        """Test handling of location with multiple colons."""
        data = {
            "category": "test",
            "description": "Issue",
            "location": "src/module:class:method:42"
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertEqual(finding.file_path, "src/module:class:method")
        self.assertEqual(finding.line_number, 42)

    def test_non_string_category(self):
        """Test handling of non-string category."""
        data = {
            "category": 123,
            "description": "Issue",
            "location": "file.py:1"
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertEqual(finding.category, "unknown")
        self.assertTrue(finding.has_missing_data)

    def test_non_string_description(self):
        """Test handling of non-string description."""
        data = {
            "category": "test",
            "description": ["This", "is", "a", "list"],
            "location": "file.py:1"
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertEqual(finding.description, "No description provided")
        self.assertTrue(finding.has_missing_data)

    def test_very_large_line_number(self):
        """Test handling of very large line numbers."""
        data = {
            "category": "test",
            "description": "Issue",
            "location": "file.py:999999999"
        }
        finding = QAFinding.from_dict(data, QAFindingType.WARNING)
        self.assertEqual(finding.line_number, 999999999)

    def test_info_finding_type(self):
        """Test info finding type is handled."""
        findings_dict = {
            "summary": "PASS",
            "critical_issues": [],
            "warnings": [],
            "suggestions": [],
            "info": [
                {"category": "note", "description": "Informational message", "location": "readme.md"}
            ],
            "passed_checks": []
        }
        analyzer = QAFindingsAnalyzer(findings_dict)
        info = analyzer.get_findings_by_type(QAFindingType.INFO)
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0].description, "Informational message")


class TestQAReportFindingsIntegration(TempConfigTestCase):
    """Integration tests for _report_qa_findings using QAFindingsAnalyzer."""

    def test_report_qa_findings_uses_analyzer(self):
        """Test that _report_qa_findings properly uses QAFindingsAnalyzer."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)

        findings = {
            "summary": "WARN",
            "critical_issues": [
                {"category": "security", "description": "SQL injection", "location": "db.py:10"}
            ],
            "warnings": [
                {"category": "style", "description": "Long line", "location": "utils.py:5"}
            ],
            "suggestions": [],
            "passed_checks": ["Error handling"]
        }

        task = {"id": "TASK-001", "description": "Test task"}

        with patch('ralph.Logger.info'), patch('ralph.Logger.debug'), patch('ralph.Logger.file_log'):
            orch._report_qa_findings(task, findings)

    def test_report_qa_findings_json_output(self):
        """Test JSON output includes analyzer data."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)

        findings = {
            "summary": "WARN",
            "critical_issues": [
                {"category": "security", "description": "Issue", "location": "file.py:10"}
            ],
            "warnings": [],
            "suggestions": [],
            "passed_checks": []
        }

        task = {"id": "TASK-001", "description": "Test task"}

        original_json = Logger.json_output
        try:
            Logger.json_output = True
            with patch('ralph.Logger.file_log'):
                with patch('builtins.print') as mock_print:
                    orch._report_qa_findings(task, findings)

            mock_print.assert_called_once()
            output = json.loads(mock_print.call_args[0][0])
            self.assertIn('counts', output)
            self.assertIn('findings_by_file', output)
            self.assertEqual(output['counts']['errors'], 1)
        finally:
            Logger.json_output = original_json

    def test_report_qa_findings_groups_by_file(self):
        """Test that text output groups findings by file."""
        mock_agent = self.create_mock_agent()
        orch = self.create_mock_orchestrator(mock_agent=mock_agent, qa_review=True)

        findings = {
            "summary": "FAIL",
            "critical_issues": [
                {"category": "security", "description": "Issue 1", "location": "file_a.py:10"},
                {"category": "security", "description": "Issue 2", "location": "file_a.py:20"}
            ],
            "warnings": [],
            "suggestions": [],
            "passed_checks": []
        }

        task = {"id": "TASK-001", "description": "Test task"}
        logged_messages = []

        def capture_info(msg, color=None):
            logged_messages.append(msg)

        # Ensure text output mode (not JSON)
        original_json = Logger.json_output
        original_ndjson = Logger.ndjson_output
        try:
            Logger.json_output = False
            Logger.ndjson_output = False
            with patch('ralph.Logger.info', side_effect=capture_info):
                with patch('ralph.Logger.debug'), patch('ralph.Logger.file_log'):
                    orch._report_qa_findings(task, findings)

            # Check that file_a.py appears in output
            file_a_logged = any("file_a.py" in msg for msg in logged_messages)
            self.assertTrue(file_a_logged)
        finally:
            Logger.json_output = original_json
            Logger.ndjson_output = original_ndjson


# ==============================================================================
# FINAL QA VALIDATOR TESTS
# ==============================================================================


class TestFinalQAValidator(TempConfigTestCase):
    """Tests for FinalQAValidator class."""

    def setUp(self):
        super().setUp()
        CONF.QA_CHECKLIST_FILE = self.temp_path / ".ralph" / "qa-checklist.json"
        self.checklist_path = CONF.QA_CHECKLIST_FILE
        self.prd_path = self.temp_path / ".ralph" / "prd.json"
        self.prd_manager = PRDManager(self.prd_path)
        self.checklist_manager = QAChecklistManager(self.checklist_path, self.prd_manager)
        self.hooks_dir = self.temp_path / ".ralph" / "hooks"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        self.hook_manager = HookManager(self.hooks_dir)
        self.validator = FinalQAValidator(
            self.checklist_manager,
            self.prd_manager,
            self.hook_manager
        )

    def _write_prd(self, data):
        """Helper to write PRD JSON to disk."""
        self.prd_path.parent.mkdir(parents=True, exist_ok=True)
        self.prd_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def _write_checklist(self, data):
        """Helper to write checklist JSON to disk."""
        self.checklist_path.parent.mkdir(parents=True, exist_ok=True)
        self.checklist_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def test_validate_all_passed(self):
        """Test validation when all requirements pass."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"}
            ]
        }
        self._write_prd(prd)
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "passed"},
                {"id": "TASK-001-AC02", "description": "Criteria 2", "status": "passed"},
            ]
        }
        self._write_checklist(checklist)

        report = self.validator.validate()

        self.assertTrue(report.all_passed)
        self.assertEqual(report.total_requirements, 2)
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.coverage_percentage, 100.0)
        self.assertEqual(report.failed_requirements, [])

    def test_validate_some_failed(self):
        """Test validation when some requirements fail."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"}
            ]
        }
        self._write_prd(prd)
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "passed"},
                {"id": "TASK-001-AC02", "description": "Criteria 2", "status": "failed"},
                {"id": "TASK-001-AC03", "description": "Criteria 3", "status": "pending"},
            ]
        }
        self._write_checklist(checklist)

        report = self.validator.validate()

        self.assertFalse(report.all_passed)
        self.assertEqual(report.total_requirements, 3)
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 2)
        self.assertAlmostEqual(report.coverage_percentage, 33.33, places=1)
        self.assertEqual(len(report.failed_requirements), 2)
        failed_ids = [r[0] for r in report.failed_requirements]
        self.assertIn("TASK-001-AC02", failed_ids)
        self.assertIn("TASK-001-AC03", failed_ids)

    def test_validate_emits_prd_complete_event(self):
        """Test that PRD_COMPLETE event is emitted when all pass."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"}
            ]
        }
        self._write_prd(prd)
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "passed"},
            ]
        }
        self._write_checklist(checklist)

        events_received = []
        def capture_event(event):
            events_received.append(event)
            return None

        self.hook_manager.register_hook(
            name="test_capture",
            handler=capture_event,
            events=["PRD_COMPLETE"]
        )

        self.validator.validate()

        prd_complete_events = [e for e in events_received if e.event_type == EventType.PRD_COMPLETE]
        self.assertEqual(len(prd_complete_events), 1)
        self.assertIn("report", prd_complete_events[0].metadata)

    def test_validate_emits_prd_incomplete_event(self):
        """Test that PRD_INCOMPLETE event is emitted when some fail."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"}
            ]
        }
        self._write_prd(prd)
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "failed"},
            ]
        }
        self._write_checklist(checklist)

        events_received = []
        def capture_event(event):
            events_received.append(event)
            return None

        self.hook_manager.register_hook(
            name="test_capture",
            handler=capture_event,
            events=["PRD_INCOMPLETE"]
        )

        self.validator.validate()

        prd_incomplete_events = [e for e in events_received if e.event_type == EventType.PRD_INCOMPLETE]
        self.assertEqual(len(prd_incomplete_events), 1)
        self.assertIn("failed_requirements", prd_incomplete_events[0].metadata)

    def test_edge_case_checklist_never_created(self):
        """Test validation when checklist was never created - generates and evaluates in one pass."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {
                    "id": "TASK-001",
                    "description": "Task 1",
                    "status": "completed",
                    "acceptanceCriteria": ["Criteria 1", "Criteria 2"]
                }
            ]
        }
        self._write_prd(prd)
        # No checklist file exists

        report = self.validator.validate()

        # Checklist should be generated and all requirements should be pending (failed)
        self.assertFalse(report.all_passed)
        self.assertEqual(report.total_requirements, 2)
        self.assertEqual(report.passed, 0)
        self.assertEqual(report.failed, 2)

    def test_edge_case_empty_requirements(self):
        """Test validation with no requirements in checklist."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": []
        }
        self._write_prd(prd)
        checklist = {"requirements": []}
        self._write_checklist(checklist)

        report = self.validator.validate()

        self.assertTrue(report.all_passed)
        self.assertEqual(report.total_requirements, 0)
        self.assertEqual(report.coverage_percentage, 100.0)

    def test_catastrophic_failure_preserves_partial_results(self):
        """Test that catastrophic failure preserves partial results."""
        mock_checklist_manager = MagicMock()
        mock_checklist_manager.exists.return_value = True
        mock_checklist_manager.get_all_requirements.side_effect = Exception("Database crash")

        mock_prd_manager = MagicMock()
        validator = FinalQAValidator(mock_checklist_manager, mock_prd_manager)

        report = validator.validate()

        self.assertFalse(report.all_passed)
        self.assertIsNotNone(report.error)
        self.assertIn("Database crash", report.error)
        self.assertIsNotNone(report.partial_results)

    def test_are_all_tasks_completed_true(self):
        """Test _are_all_tasks_completed returns True when all complete."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"},
                {"id": "TASK-002", "description": "Task 2", "status": "completed"},
            ]
        }
        self._write_prd(prd)

        self.assertTrue(self.validator._are_all_tasks_completed())

    def test_are_all_tasks_completed_false_pending(self):
        """Test _are_all_tasks_completed returns False with pending tasks."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"},
                {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            ]
        }
        self._write_prd(prd)

        self.assertFalse(self.validator._are_all_tasks_completed())

    def test_are_all_tasks_completed_false_no_prd(self):
        """Test _are_all_tasks_completed returns False when no PRD."""
        self.assertFalse(self.validator._are_all_tasks_completed())

    def test_check_and_validate_triggers_when_all_complete(self):
        """Test check_and_validate triggers validation when all tasks complete."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"}
            ]
        }
        self._write_prd(prd)
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "passed"},
            ]
        }
        self._write_checklist(checklist)

        report = self.validator.check_and_validate()

        self.assertIsNotNone(report)
        self.assertTrue(report.all_passed)

    def test_check_and_validate_returns_none_when_incomplete(self):
        """Test check_and_validate returns None when tasks not complete."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "pending"}
            ]
        }
        self._write_prd(prd)

        report = self.validator.check_and_validate()

        self.assertIsNone(report)

    def test_register_succeeds(self):
        """Test validator registration with HookManager."""
        result = self.validator.register(self.hook_manager)
        self.assertTrue(result)
        hooks = self.hook_manager.get_all_hooks()
        hook_names = [h.name for h in hooks]
        self.assertIn("final_qa_validator", hook_names)

    def test_register_fails_if_already_registered(self):
        """Test that double registration fails."""
        self.validator.register(self.hook_manager)
        result = self.validator.register(self.hook_manager)
        self.assertFalse(result)

    def test_unregister_succeeds(self):
        """Test validator unregistration."""
        self.validator.register(self.hook_manager)
        result = self.validator.unregister(self.hook_manager)
        self.assertTrue(result)
        hooks = self.hook_manager.get_all_hooks()
        hook_names = [h.name for h in hooks]
        self.assertNotIn("final_qa_validator", hook_names)

    def test_unregister_fails_if_not_registered(self):
        """Test unregistration without registration fails."""
        result = self.validator.unregister(self.hook_manager)
        self.assertFalse(result)

    def test_on_task_success_triggers_validation_when_all_complete(self):
        """Test that TASK_SUCCESS triggers validation when all tasks complete."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"}
            ]
        }
        self._write_prd(prd)
        checklist = {
            "requirements": [
                {"id": "TASK-001-AC01", "description": "Criteria 1", "status": "passed"},
            ]
        }
        self._write_checklist(checklist)

        events_received = []
        def capture_event(event):
            events_received.append(event)
            return None

        self.hook_manager.register_hook(
            name="test_capture",
            handler=capture_event,
            events=["PRD_COMPLETE"]
        )

        self.validator.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        self.hook_manager.emit(event)

        prd_complete_events = [e for e in events_received if e.event_type == EventType.PRD_COMPLETE]
        self.assertEqual(len(prd_complete_events), 1)

    def test_on_task_success_does_not_trigger_when_incomplete(self):
        """Test that TASK_SUCCESS does not trigger validation when tasks incomplete."""
        prd = {
            "id": "PRD-001",
            "description": "Test PRD",
            "userStories": [
                {"id": "TASK-001", "description": "Task 1", "status": "completed"},
                {"id": "TASK-002", "description": "Task 2", "status": "pending"},
            ]
        }
        self._write_prd(prd)

        events_received = []
        def capture_event(event):
            events_received.append(event)
            return None

        self.hook_manager.register_hook(
            name="test_capture",
            handler=capture_event,
            events=["PRD_COMPLETE", "PRD_INCOMPLETE"]
        )

        self.validator.register(self.hook_manager)
        event = Event(EventType.TASK_SUCCESS, task_id="TASK-001")
        self.hook_manager.emit(event)

        prd_events = [e for e in events_received if e.event_type in (EventType.PRD_COMPLETE, EventType.PRD_INCOMPLETE)]
        self.assertEqual(len(prd_events), 0)

    def test_report_to_dict(self):
        """Test FinalQAReport.to_dict serialization."""
        report = FinalQAReport(
            total_requirements=5,
            passed=3,
            failed=2,
            coverage_percentage=60.0,
            failed_requirements=[("REQ-001", "Desc 1"), ("REQ-002", "Desc 2")],
            all_passed=False,
            error=None,
            partial_results=None
        )

        d = report.to_dict()

        self.assertEqual(d["total_requirements"], 5)
        self.assertEqual(d["passed"], 3)
        self.assertEqual(d["failed"], 2)
        self.assertEqual(d["coverage_percentage"], 60.0)
        self.assertEqual(len(d["failed_requirements"]), 2)
        self.assertEqual(d["failed_requirements"][0]["id"], "REQ-001")
        self.assertFalse(d["all_passed"])

    def test_validator_logs_on_error(self):
        """Test that validator logs warnings on error."""
        mock_logger = MagicMock()
        mock_checklist_manager = MagicMock()
        mock_checklist_manager.exists.return_value = True
        mock_checklist_manager.get_all_requirements.side_effect = Exception("Test error")

        validator = FinalQAValidator(
            mock_checklist_manager,
            MagicMock(),
            logger=mock_logger
        )
        validator.validate()

        mock_logger.error.assert_called()


class TestFinalQAValidatorEventTypes(unittest.TestCase):
    """Tests for PRD_COMPLETE and PRD_INCOMPLETE event types."""

    def test_prd_complete_event_exists(self):
        """Test that PRD_COMPLETE event type exists."""
        self.assertTrue(hasattr(EventType, 'PRD_COMPLETE'))

    def test_prd_incomplete_event_exists(self):
        """Test that PRD_INCOMPLETE event type exists."""
        self.assertTrue(hasattr(EventType, 'PRD_INCOMPLETE'))

    def test_event_types_are_unique(self):
        """Test that PRD_COMPLETE and PRD_INCOMPLETE have different values."""
        self.assertNotEqual(EventType.PRD_COMPLETE, EventType.PRD_INCOMPLETE)


if __name__ == '__main__':
    unittest.main()
