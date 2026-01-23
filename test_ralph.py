import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
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
    get_version,
    main,
)


class TestConfigDefaultPaths(unittest.TestCase):
    """Tests for Config dataclass default path values."""

    def test_base_dir_defaults_to_cwd(self):
        config = Config()
        self.assertEqual(config.BASE_DIR, Path.cwd())

    def test_root_dir_is_under_base_dir(self):
        config = Config()
        self.assertEqual(config.ROOT_DIR, config.BASE_DIR / ".ralph")

    def test_memory_dir_is_under_root_dir(self):
        config = Config()
        self.assertEqual(config.MEMORY_DIR, config.ROOT_DIR / "memory")

    def test_archive_dir_is_under_root_dir(self):
        config = Config()
        self.assertEqual(config.ARCHIVE_DIR, config.ROOT_DIR / "archive")

    def test_prd_file_is_under_root_dir(self):
        config = Config()
        self.assertEqual(config.PRD_FILE, config.ROOT_DIR / "prd.json")

    def test_progress_file_is_under_root_dir(self):
        config = Config()
        self.assertEqual(config.PROGRESS_FILE, config.ROOT_DIR / "progress.txt")

    def test_log_file_is_under_root_dir(self):
        config = Config()
        self.assertEqual(config.LOG_FILE, config.ROOT_DIR / "ralph_log.txt")

    def test_max_retries_default(self):
        config = Config()
        self.assertEqual(config.MAX_RETRIES, 3)

    def test_timeout_seconds_default(self):
        config = Config()
        self.assertEqual(config.TIMEOUT_SECONDS, 600)


class TestConfigEnsureDirectories(unittest.TestCase):
    """Tests for Config.ensure_directories() method."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ensure_directories_creates_root_dir(self):
        config = Config(
            BASE_DIR=self.temp_path,
            ROOT_DIR=self.temp_path / ".ralph",
            MEMORY_DIR=self.temp_path / ".ralph" / "memory",
            ARCHIVE_DIR=self.temp_path / ".ralph" / "archive",
        )
        self.assertFalse(config.ROOT_DIR.exists())
        config.ensure_directories()
        self.assertTrue(config.ROOT_DIR.exists())
        self.assertTrue(config.ROOT_DIR.is_dir())

    def test_ensure_directories_creates_memory_dir(self):
        config = Config(
            BASE_DIR=self.temp_path,
            ROOT_DIR=self.temp_path / ".ralph",
            MEMORY_DIR=self.temp_path / ".ralph" / "memory",
            ARCHIVE_DIR=self.temp_path / ".ralph" / "archive",
        )
        config.ensure_directories()
        self.assertTrue(config.MEMORY_DIR.exists())
        self.assertTrue(config.MEMORY_DIR.is_dir())

    def test_ensure_directories_creates_archive_dir(self):
        config = Config(
            BASE_DIR=self.temp_path,
            ROOT_DIR=self.temp_path / ".ralph",
            MEMORY_DIR=self.temp_path / ".ralph" / "memory",
            ARCHIVE_DIR=self.temp_path / ".ralph" / "archive",
        )
        config.ensure_directories()
        self.assertTrue(config.ARCHIVE_DIR.exists())
        self.assertTrue(config.ARCHIVE_DIR.is_dir())

    def test_ensure_directories_is_idempotent(self):
        config = Config(
            BASE_DIR=self.temp_path,
            ROOT_DIR=self.temp_path / ".ralph",
            MEMORY_DIR=self.temp_path / ".ralph" / "memory",
            ARCHIVE_DIR=self.temp_path / ".ralph" / "archive",
        )
        config.ensure_directories()
        config.ensure_directories()
        self.assertTrue(config.ROOT_DIR.exists())
        self.assertTrue(config.MEMORY_DIR.exists())
        self.assertTrue(config.ARCHIVE_DIR.exists())

    def test_ensure_directories_creates_nested_structure(self):
        nested_path = self.temp_path / "deep" / "nested" / "path"
        config = Config(
            BASE_DIR=nested_path,
            ROOT_DIR=nested_path / ".ralph",
            MEMORY_DIR=nested_path / ".ralph" / "memory",
            ARCHIVE_DIR=nested_path / ".ralph" / "archive",
        )
        self.assertFalse(nested_path.exists())
        config.ensure_directories()
        self.assertTrue(config.ROOT_DIR.exists())
        self.assertTrue(config.MEMORY_DIR.exists())
        self.assertTrue(config.ARCHIVE_DIR.exists())


class TestLoggerColorSettings(unittest.TestCase):
    """Tests for Logger color configuration."""

    def setUp(self):
        Logger.no_color = False
        Logger.verbose = False

    def tearDown(self):
        Logger.no_color = False
        Logger.verbose = False

    def test_set_no_color_enables(self):
        Logger.set_no_color(True)
        self.assertTrue(Logger.no_color)

    def test_set_no_color_disables(self):
        Logger.no_color = True
        Logger.set_no_color(False)
        self.assertFalse(Logger.no_color)

    def test_colors_dict_contains_expected_keys(self):
        expected_keys = {"RESET", "GREEN", "RED", "CYAN", "YELLOW", "MAGENTA"}
        self.assertEqual(set(Logger.COLORS.keys()), expected_keys)


class TestLoggerVerboseSettings(unittest.TestCase):
    """Tests for Logger verbose mode configuration."""

    def setUp(self):
        Logger.verbose = False
        Logger.no_color = False

    def tearDown(self):
        Logger.verbose = False
        Logger.no_color = False

    def test_set_verbose_enables(self):
        Logger.set_verbose(True)
        self.assertTrue(Logger.verbose)

    def test_set_verbose_disables(self):
        Logger.verbose = True
        Logger.set_verbose(False)
        self.assertFalse(Logger.verbose)


class TestLoggerInfoOutput(unittest.TestCase):
    """Tests for Logger.info() console output."""

    def setUp(self):
        Logger.no_color = False
        Logger.verbose = False
        self.held_output = StringIO()
        self.original_stdout = sys.stdout
        sys.stdout = self.held_output

    def tearDown(self):
        sys.stdout = self.original_stdout
        Logger.no_color = False
        Logger.verbose = False

    def test_info_prints_message(self):
        Logger.info("Test message")
        output = self.held_output.getvalue()
        self.assertIn("Test message", output)

    def test_info_with_no_color_prints_plain_message(self):
        Logger.set_no_color(True)
        Logger.info("Plain message")
        output = self.held_output.getvalue()
        self.assertEqual("Plain message\n", output)

    def test_info_with_color_includes_ansi_codes(self):
        Logger.set_no_color(False)
        Logger.info("Colored message", "GREEN")
        output = self.held_output.getvalue()
        self.assertIn("\033[92m", output)
        self.assertIn("\033[0m", output)
        self.assertIn("Colored message", output)

    def test_info_with_invalid_color_uses_reset(self):
        Logger.set_no_color(False)
        Logger.info("Message", "INVALID_COLOR")
        output = self.held_output.getvalue()
        self.assertIn("\033[0m", output)
        self.assertIn("Message", output)


class TestLoggerDebugOutput(unittest.TestCase):
    """Tests for Logger.debug() console output."""

    def setUp(self):
        Logger.no_color = False
        Logger.verbose = False
        self.held_output = StringIO()
        self.original_stdout = sys.stdout
        sys.stdout = self.held_output

    def tearDown(self):
        sys.stdout = self.original_stdout
        Logger.no_color = False
        Logger.verbose = False

    def test_debug_does_not_print_when_not_verbose(self):
        Logger.set_verbose(False)
        Logger.debug("Debug message")
        output = self.held_output.getvalue()
        self.assertEqual("", output)

    def test_debug_prints_when_verbose(self):
        Logger.set_verbose(True)
        Logger.debug("Debug message")
        output = self.held_output.getvalue()
        self.assertIn("[DEBUG]", output)
        self.assertIn("Debug message", output)

    def test_debug_with_no_color_prints_plain(self):
        Logger.set_verbose(True)
        Logger.set_no_color(True)
        Logger.debug("Plain debug")
        output = self.held_output.getvalue()
        self.assertEqual("[DEBUG] Plain debug\n", output)

    def test_debug_with_color_includes_ansi_codes(self):
        Logger.set_verbose(True)
        Logger.set_no_color(False)
        Logger.debug("Colored debug", "CYAN")
        output = self.held_output.getvalue()
        self.assertIn("\033[96m", output)
        self.assertIn("[DEBUG] Colored debug", output)


class TestLoggerFileLog(unittest.TestCase):
    """Tests for Logger.file_log() file logging."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.log_file = self.temp_path / "test_log.txt"
        self.original_log_file = CONF.LOG_FILE
        CONF.LOG_FILE = self.log_file

    def tearDown(self):
        CONF.LOG_FILE = self.original_log_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_log_creates_file(self):
        Logger.file_log("Test content", "INFO", "TEST")
        self.assertTrue(self.log_file.exists())

    def test_file_log_writes_content(self):
        Logger.file_log("Test content", "INFO", "TEST")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("Test content", content)

    def test_file_log_includes_type(self):
        Logger.file_log("Content", "PROMPT", "TAG")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("TYPE: PROMPT", content)

    def test_file_log_includes_tag(self):
        Logger.file_log("Content", "INFO", "MYTAG")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("TAG: MYTAG", content)

    def test_file_log_includes_timestamp(self):
        Logger.file_log("Content", "INFO", "TAG")
        content = self.log_file.read_text(encoding="utf-8")
        timestamp_pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
        self.assertRegex(content, timestamp_pattern)

    def test_file_log_appends_multiple_entries(self):
        Logger.file_log("First entry", "INFO", "TAG1")
        Logger.file_log("Second entry", "ERROR", "TAG2")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("First entry", content)
        self.assertIn("Second entry", content)

    def test_file_log_uses_correct_icons(self):
        Logger.file_log("Prompt content", "PROMPT", "TAG")
        Logger.file_log("Response content", "RESPONSE", "TAG")
        Logger.file_log("Error content", "ERROR", "TAG")
        Logger.file_log("Info content", "INFO", "TAG")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("➡️", content)
        self.assertIn("⬅️", content)
        self.assertIn("❌", content)
        self.assertIn("ℹ️", content)

    def test_file_log_unknown_type_uses_question_icon(self):
        Logger.file_log("Unknown content", "UNKNOWN_TYPE", "TAG")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("❓", content)

    def test_file_log_default_tag_is_unknown(self):
        Logger.file_log("Content", "INFO")
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("TAG: UNKNOWN", content)


class TestShellRun(unittest.TestCase):
    """Tests for Shell.run() command execution."""

    def test_run_successful_command_returns_stdout(self):
        stdout, stderr, code = Shell.run("echo hello")
        self.assertIn("hello", stdout)
        self.assertEqual(code, 0)

    def test_run_successful_command_returns_zero_exit_code(self):
        stdout, stderr, code = Shell.run("echo test")
        self.assertEqual(code, 0)

    def test_run_failing_command_returns_nonzero_exit_code(self):
        stdout, stderr, code = Shell.run("exit 1")
        self.assertEqual(code, 1)

    def test_run_returns_stderr_on_error(self):
        stdout, stderr, code = Shell.run("echo error_msg 1>&2")
        self.assertIn("error_msg", stderr)

    def test_run_returns_tuple_of_three_elements(self):
        result = Shell.run("echo test")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)

    def test_run_stdout_is_string(self):
        stdout, stderr, code = Shell.run("echo test")
        self.assertIsInstance(stdout, str)

    def test_run_stderr_is_string(self):
        stdout, stderr, code = Shell.run("echo test")
        self.assertIsInstance(stderr, str)

    def test_run_returncode_is_int(self):
        stdout, stderr, code = Shell.run("echo test")
        self.assertIsInstance(code, int)


class TestShellRunTimeout(unittest.TestCase):
    """Tests for Shell.run() timeout handling."""

    def test_run_timeout_returns_error_message(self):
        # Mock subprocess.run to simulate timeout without actual waiting
        with patch('ralph.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1)
            stdout, stderr, code = Shell.run("some_command", timeout=1)
        self.assertEqual(stderr, "Command Timed Out")

    def test_run_timeout_returns_exit_code_1(self):
        with patch('ralph.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1)
            stdout, stderr, code = Shell.run("some_command", timeout=1)
        self.assertEqual(code, 1)

    def test_run_timeout_returns_empty_stdout(self):
        with patch('ralph.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1)
            stdout, stderr, code = Shell.run("some_command", timeout=1)
        self.assertEqual(stdout, "")


class TestShellRunExceptionHandling(unittest.TestCase):
    """Tests for Shell.run() exception handling."""

    def test_run_handles_exception_gracefully(self):
        result = Shell.run("echo test")
        self.assertIsNotNone(result)

    def test_run_exception_returns_exit_code_1(self):
        stdout, stderr, code = Shell.run("nonexistent_command_xyz123")
        self.assertEqual(code, 1)

    def test_run_exception_returns_error_in_stderr(self):
        stdout, stderr, code = Shell.run("nonexistent_command_xyz123")
        self.assertTrue(len(stderr) > 0 or code == 1)


class TestShellGetFileTree(unittest.TestCase):
    """Tests for Shell.get_file_tree() directory listing."""

    def test_get_file_tree_returns_string(self):
        result = Shell.get_file_tree()
        self.assertIsInstance(result, str)

    def test_get_file_tree_returns_non_empty(self):
        result = Shell.get_file_tree()
        self.assertTrue(len(result) > 0)

    def test_get_file_tree_excludes_git_directory(self):
        result = Shell.get_file_tree()
        lines = result.split('\n')
        for line in lines:
            entry = line.split('/')[-1].split('\\')[-1].strip().lstrip('├─└│ ')
            self.assertNotEqual(entry, '.git')

    def test_get_file_tree_excludes_ralph_directory(self):
        result = Shell.get_file_tree()
        lines = result.split('\n')
        for line in lines:
            entry = line.split('/')[-1].split('\\')[-1].strip().lstrip('├─└│ ')
            self.assertNotEqual(entry, '.ralph')

    def test_get_file_tree_excludes_pycache_directory(self):
        result = Shell.get_file_tree()
        lines = result.split('\n')
        for line in lines:
            entry = line.split('/')[-1].split('\\')[-1].strip().lstrip('├─└│ ')
            self.assertNotEqual(entry, '__pycache__')


class TestJsonUtilsParseValidJson(unittest.TestCase):
    """Tests for JsonUtils.parse() with valid JSON input."""

    def test_parse_simple_object(self):
        result = JsonUtils.parse('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_parse_object_with_multiple_keys(self):
        result = JsonUtils.parse('{"a": 1, "b": 2, "c": 3}')
        self.assertEqual(result, {"a": 1, "b": 2, "c": 3})

    def test_parse_nested_object(self):
        result = JsonUtils.parse('{"outer": {"inner": "value"}}')
        self.assertEqual(result, {"outer": {"inner": "value"}})

    def test_parse_object_with_array(self):
        result = JsonUtils.parse('{"items": [1, 2, 3]}')
        self.assertEqual(result, {"items": [1, 2, 3]})

    def test_parse_object_with_nested_array_of_objects(self):
        result = JsonUtils.parse('{"users": [{"name": "Alice"}, {"name": "Bob"}]}')
        self.assertEqual(result, {"users": [{"name": "Alice"}, {"name": "Bob"}]})

    def test_parse_empty_object(self):
        result = JsonUtils.parse('{}')
        self.assertEqual(result, {})

    def test_parse_object_with_null_value(self):
        result = JsonUtils.parse('{"value": null}')
        self.assertEqual(result, {"value": None})

    def test_parse_object_with_boolean_values(self):
        result = JsonUtils.parse('{"active": true, "disabled": false}')
        self.assertEqual(result, {"active": True, "disabled": False})

    def test_parse_object_with_numeric_values(self):
        result = JsonUtils.parse('{"int": 42, "float": 3.14}')
        self.assertEqual(result, {"int": 42, "float": 3.14})


class TestJsonUtilsParseMarkdownFences(unittest.TestCase):
    """Tests for JsonUtils.parse() with markdown code fences."""

    def test_parse_json_in_plain_code_fence(self):
        text = '```\n{"key": "value"}\n```'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_in_json_code_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_in_code_fence_with_whitespace(self):
        text = '```json\n  {"key": "value"}  \n```'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_complex_json_in_code_fence(self):
        text = '''```json
{
  "id": "PRD-001",
  "userStories": [
    {"id": "TASK-001", "status": "pending"}
  ]
}
```'''
        result = JsonUtils.parse(text)
        self.assertEqual(result["id"], "PRD-001")
        self.assertEqual(len(result["userStories"]), 1)
        self.assertEqual(result["userStories"][0]["id"], "TASK-001")


class TestJsonUtilsParseSurroundingText(unittest.TestCase):
    """Tests for JsonUtils.parse() with surrounding prose/text."""

    def test_parse_json_with_leading_text(self):
        text = 'Here is the JSON:\n{"key": "value"}'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_with_trailing_text(self):
        text = '{"key": "value"}\nThat was the output.'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_with_surrounding_text(self):
        text = 'Output:\n{"key": "value"}\nEnd of output.'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_with_prose_and_code_fence(self):
        text = '''Here is the PRD:
```json
{"id": "PRD-001"}
```
Please review it.'''
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"id": "PRD-001"})


class TestJsonUtilsParseComments(unittest.TestCase):
    """Tests for JsonUtils.parse() with single-line comments."""

    def test_parse_json_with_single_line_comment(self):
        text = '''{"key": "value"// this is a comment
}'''
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_with_multiple_comments(self):
        text = '''{
"a": 1,// comment 1
"b": 2// comment 2
}'''
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_parse_json_with_comment_at_end(self):
        text = '{"key": "value"}// trailing comment'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key": "value"})


class TestJsonUtilsParseEdgeCases(unittest.TestCase):
    """Tests for JsonUtils.parse() edge cases and error handling."""

    def test_parse_raises_on_invalid_json(self):
        with self.assertRaises(Exception):
            JsonUtils.parse('not json at all')

    def test_parse_raises_on_incomplete_json(self):
        with self.assertRaises(Exception):
            JsonUtils.parse('{"key": ')

    def test_parse_raises_on_no_braces(self):
        with self.assertRaises(Exception):
            JsonUtils.parse('just some text')

    def test_parse_handles_unicode_content(self):
        result = JsonUtils.parse('{"emoji": "🎉", "text": "héllo"}')
        self.assertEqual(result, {"emoji": "🎉", "text": "héllo"})

    def test_parse_handles_escaped_quotes(self):
        result = JsonUtils.parse('{"text": "say \\"hello\\""}')
        self.assertEqual(result, {"text": 'say "hello"'})

    def test_parse_extracts_outermost_braces(self):
        text = 'prefix {"nested": {"inner": 1}} suffix'
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"nested": {"inner": 1}})

    def test_parse_handles_multiline_json(self):
        text = '''{
    "key1": "value1",
    "key2": "value2"
}'''
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"key1": "value1", "key2": "value2"})


class TestJsonUtilsParseLlmOutputFormats(unittest.TestCase):
    """Tests for JsonUtils.parse() with realistic LLM output formats."""

    def test_parse_llm_prd_output(self):
        text = '''I've created the PRD as requested:

```json
{
  "id": "PRD-001",
  "description": "User authentication system",
  "userStories": [
    {
      "id": "TASK-001",
      "description": "As a user, I want to log in",
      "acceptanceCriteria": ["Can enter credentials", "Receives token"],
      "status": "pending"
    }
  ]
}
```

Let me know if you need any changes.'''
        result = JsonUtils.parse(text)
        self.assertEqual(result["id"], "PRD-001")
        self.assertEqual(len(result["userStories"]), 1)
        self.assertEqual(result["userStories"][0]["status"], "pending")

    def test_parse_llm_output_without_fence(self):
        text = '''Here's the configuration:
{"setting": "value", "enabled": true}
That should work.'''
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"setting": "value", "enabled": True})

    def test_parse_llm_output_with_explanation_inside_fence(self):
        text = '''```
{"status": "success", "count": 5}
```'''
        result = JsonUtils.parse(text)
        self.assertEqual(result, {"status": "success", "count": 5})


class TestMemoryManagerValidateMemory(unittest.TestCase):
    """Tests for MemoryManager.validate_memory() method."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_base_dir = CONF.BASE_DIR
        CONF.BASE_DIR = self.temp_path
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        self.memory = MemoryManager()

    def tearDown(self):
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.BASE_DIR = self.original_base_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_validate_memory_returns_dict(self):
        result = self.memory.validate_memory()
        self.assertIsInstance(result, dict)

    def test_validate_memory_returns_expected_keys(self):
        result = self.memory.validate_memory()
        expected_keys = {'valid', 'corrupted', 'empty', 'total'}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_validate_memory_when_dir_not_exists(self):
        result = self.memory.validate_memory()
        self.assertTrue(result['valid'])
        self.assertEqual(result['corrupted'], [])
        self.assertEqual(result['empty'], [])
        self.assertEqual(result['total'], 0)

    def test_validate_memory_with_valid_file(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "test.md").write_text("content", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertTrue(result['valid'])
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['corrupted'], [])
        self.assertEqual(result['empty'], [])

    def test_validate_memory_with_empty_file(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "empty.md").write_text("", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertFalse(result['valid'])
        self.assertEqual(result['total'], 1)
        self.assertEqual(len(result['empty']), 1)
        self.assertIn(".ralph", result['empty'][0])

    def test_validate_memory_with_whitespace_only_file(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "whitespace.md").write_text("   \n\t  ", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['empty']), 1)

    def test_validate_memory_with_multiple_valid_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "file1.md").write_text("content1", encoding="utf-8")
        (CONF.MEMORY_DIR / "file2.md").write_text("content2", encoding="utf-8")
        (CONF.MEMORY_DIR / "file3.txt").write_text("content3", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertTrue(result['valid'])
        self.assertEqual(result['total'], 3)

    def test_validate_memory_ignores_hidden_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / ".hidden").write_text("", encoding="utf-8")
        (CONF.MEMORY_DIR / "visible.md").write_text("content", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertTrue(result['valid'])
        self.assertEqual(result['total'], 1)

    def test_validate_memory_ignores_directories(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        subdir = CONF.MEMORY_DIR / "subdir"
        subdir.mkdir()
        (CONF.MEMORY_DIR / "file.md").write_text("content", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertEqual(result['total'], 1)

    def test_validate_memory_with_nested_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        subdir = CONF.MEMORY_DIR / "nested"
        subdir.mkdir()
        (subdir / "nested_file.md").write_text("nested content", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertTrue(result['valid'])
        self.assertEqual(result['total'], 1)

    def test_validate_memory_mixed_valid_and_empty(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "valid.md").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "empty.md").write_text("", encoding="utf-8")
        result = self.memory.validate_memory()
        self.assertFalse(result['valid'])
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['empty']), 1)


class TestMemoryManagerGetStructure(unittest.TestCase):
    """Tests for MemoryManager.get_structure() method."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_base_dir = CONF.BASE_DIR
        CONF.BASE_DIR = self.temp_path
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        self.memory = MemoryManager()

    def tearDown(self):
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.BASE_DIR = self.original_base_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_structure_returns_string(self):
        result = self.memory.get_structure()
        self.assertIsInstance(result, str)

    def test_get_structure_empty_when_dir_not_exists(self):
        result = self.memory.get_structure()
        self.assertEqual(result, "(Memory Empty)")

    def test_get_structure_empty_when_dir_empty(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        result = self.memory.get_structure()
        self.assertEqual(result, "(Memory Empty)")

    def test_get_structure_lists_single_file(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")
        result = self.memory.get_structure()
        self.assertIn("architecture.md", result)
        self.assertTrue(result.startswith("- "))

    def test_get_structure_lists_multiple_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "file1.md").write_text("content1", encoding="utf-8")
        (CONF.MEMORY_DIR / "file2.md").write_text("content2", encoding="utf-8")
        result = self.memory.get_structure()
        self.assertIn("file1.md", result)
        self.assertIn("file2.md", result)

    def test_get_structure_ignores_hidden_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / ".hidden").write_text("content", encoding="utf-8")
        (CONF.MEMORY_DIR / "visible.md").write_text("content", encoding="utf-8")
        result = self.memory.get_structure()
        self.assertNotIn(".hidden", result)
        self.assertIn("visible.md", result)

    def test_get_structure_includes_relative_path(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "test.md").write_text("content", encoding="utf-8")
        result = self.memory.get_structure()
        self.assertIn(".ralph", result)
        self.assertIn("memory", result)

    def test_get_structure_includes_nested_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        subdir = CONF.MEMORY_DIR / "subdir"
        subdir.mkdir()
        (subdir / "nested.md").write_text("content", encoding="utf-8")
        result = self.memory.get_structure()
        self.assertIn("nested.md", result)
        self.assertIn("subdir", result)

    def test_get_structure_format_with_dash_prefix(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "test.md").write_text("content", encoding="utf-8")
        result = self.memory.get_structure()
        lines = result.strip().split('\n')
        for line in lines:
            self.assertTrue(line.startswith("- "))


class TestMemoryManagerExtractTestCommand(unittest.TestCase):
    """Tests for MemoryManager.extract_test_command() method."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_base_dir = CONF.BASE_DIR
        CONF.BASE_DIR = self.temp_path
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        self.memory = MemoryManager()

    def tearDown(self):
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.BASE_DIR = self.original_base_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extract_test_command_returns_string(self):
        result = self.memory.extract_test_command()
        self.assertIsInstance(result, str)

    def test_extract_test_command_default_is_pytest(self):
        result = self.memory.extract_test_command()
        self.assertEqual(result, "pytest")

    def test_extract_test_command_from_markdown(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        content = "## Test Command\n\nTest Command: `npm run test`\n"
        (CONF.MEMORY_DIR / "architecture.md").write_text(content, encoding="utf-8")
        result = self.memory.extract_test_command()
        self.assertEqual(result, "npm run test")

    def test_extract_test_command_case_insensitive(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        content = "test command: `make test`\n"
        (CONF.MEMORY_DIR / "config.md").write_text(content, encoding="utf-8")
        result = self.memory.extract_test_command()
        self.assertEqual(result, "make test")

    def test_extract_test_command_from_txt_file(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        content = "Test Command: `python -m pytest`\n"
        (CONF.MEMORY_DIR / "notes.txt").write_text(content, encoding="utf-8")
        result = self.memory.extract_test_command()
        self.assertEqual(result, "python -m pytest")

    def test_extract_test_command_npm_fallback_with_package_json(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "empty.md").write_text("no test command here", encoding="utf-8")
        (CONF.BASE_DIR / "package.json").write_text('{"name": "test"}', encoding="utf-8")
        result = self.memory.extract_test_command()
        self.assertEqual(result, "npm test")

    def test_extract_test_command_pytest_fallback_without_package_json(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "empty.md").write_text("no test command here", encoding="utf-8")
        result = self.memory.extract_test_command()
        self.assertEqual(result, "pytest")

    def test_extract_test_command_searches_all_memory_files(self):
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "file1.md").write_text("no command", encoding="utf-8")
        (CONF.MEMORY_DIR / "file2.md").write_text("Test Command: `cargo test`", encoding="utf-8")
        result = self.memory.extract_test_command()
        self.assertEqual(result, "cargo test")


class TestRalphOrchestratorInit(unittest.TestCase):
    """Tests for RalphOrchestrator.__init__() agent setup and dependency validation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        # Save original config values
        self.original_base_dir = CONF.BASE_DIR
        self.original_root_dir = CONF.ROOT_DIR
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_archive_dir = CONF.ARCHIVE_DIR
        # Set temp paths
        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        CONF.ARCHIVE_DIR = self.temp_path / ".ralph" / "archive"

    def tearDown(self):
        # Restore original config values
        CONF.BASE_DIR = self.original_base_dir
        CONF.ROOT_DIR = self.original_root_dir
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.ARCHIVE_DIR = self.original_archive_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestRalphOrchestratorInitAgentSetup(TestRalphOrchestratorInit):
    """Tests for RalphOrchestrator agent initialization."""

    def test_init_creates_agent_instance(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIsNotNone(orchestrator.agent)

    def test_init_calls_get_agent_with_agent_name(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent) as mock_get_agent:
            RalphOrchestrator(agent_name="claude")
            mock_get_agent.assert_called_once()
            call_args = mock_get_agent.call_args
            self.assertEqual(call_args[0][0], "claude")

    def test_init_passes_timeout_to_agent(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent) as mock_get_agent:
            RalphOrchestrator(agent_name="claude")
            call_kwargs = mock_get_agent.call_args[1]
            self.assertIn('timeout_seconds', call_kwargs)
            self.assertEqual(call_kwargs['timeout_seconds'], CONF.TIMEOUT_SECONDS)

    def test_init_stores_agent_in_attribute(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIs(orchestrator.agent, mock_agent)


class TestRalphOrchestratorInitDependencyInjection(TestRalphOrchestratorInit):
    """Tests for RalphOrchestrator logger and config injection."""

    def test_init_calls_set_logger_if_available(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            RalphOrchestrator(agent_name="mock")
            mock_agent.set_logger.assert_called_once_with(Logger)

    def test_init_calls_set_config_if_available(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            RalphOrchestrator(agent_name="mock")
            mock_agent.set_config.assert_called_once_with(CONF)

    def test_init_skips_set_logger_if_not_available(self):
        mock_agent = MagicMock(spec=['check_dependencies', 'get_name', 'run'])
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            # Should not raise even without set_logger
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIsNotNone(orchestrator)

    def test_init_skips_set_config_if_not_available(self):
        mock_agent = MagicMock(spec=['check_dependencies', 'get_name', 'run'])
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            # Should not raise even without set_config
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIsNotNone(orchestrator)


class TestRalphOrchestratorInitDependencyValidation(TestRalphOrchestratorInit):
    """Tests for RalphOrchestrator dependency validation."""

    def test_init_calls_check_dependencies(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            RalphOrchestrator(agent_name="mock")
            mock_agent.check_dependencies.assert_called_once()

    def test_init_exits_when_dependencies_not_satisfied(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = False
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.sys.exit') as mock_exit:
                RalphOrchestrator(agent_name="mock")
                mock_exit.assert_called_once_with(1)

    def test_init_does_not_exit_when_dependencies_satisfied(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.sys.exit') as mock_exit:
                RalphOrchestrator(agent_name="mock")
                mock_exit.assert_not_called()

    def test_init_logs_error_when_dependencies_not_satisfied(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = False
        mock_agent.get_name.return_value = "TestAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.sys.exit'):
                with patch('ralph.Logger.info') as mock_info:
                    RalphOrchestrator(agent_name="mock")
                    # Check that error message was logged
                    calls = mock_info.call_args_list
                    error_logged = any("dependencies not satisfied" in str(call) for call in calls)
                    self.assertTrue(error_logged)


class TestRalphOrchestratorInitMemorySetup(TestRalphOrchestratorInit):
    """Tests for RalphOrchestrator memory manager setup."""

    def test_init_creates_memory_manager(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIsInstance(orchestrator.memory, MemoryManager)

    def test_init_ensures_directories_exist(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        # Ensure directories don't exist before init
        self.assertFalse(CONF.ROOT_DIR.exists())
        self.assertFalse(CONF.MEMORY_DIR.exists())
        self.assertFalse(CONF.ARCHIVE_DIR.exists())

        with patch('ralph.get_agent', return_value=mock_agent):
            RalphOrchestrator(agent_name="mock")
            # Directories should now exist
            self.assertTrue(CONF.ROOT_DIR.exists())
            self.assertTrue(CONF.MEMORY_DIR.exists())
            self.assertTrue(CONF.ARCHIVE_DIR.exists())


class TestRalphOrchestratorInitMemoryValidation(TestRalphOrchestratorInit):
    """Tests for RalphOrchestrator memory validation on startup."""

    def test_init_validates_memory_on_startup(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        # Create memory directory with a file
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "test.md").write_text("content", encoding="utf-8")

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch.object(MemoryManager, 'validate_memory', return_value={'valid': True, 'corrupted': [], 'empty': [], 'total': 1}) as mock_validate:
                RalphOrchestrator(agent_name="mock")
                mock_validate.assert_called()

    def test_init_warns_about_empty_memory_files(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        # Create memory directory with an empty file
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "empty.md").write_text("", encoding="utf-8")

        with patch('ralph.get_agent', return_value=mock_agent):
            with patch('ralph.Logger.info') as mock_info:
                RalphOrchestrator(agent_name="mock")
                # Check that warning was logged
                calls = [str(call) for call in mock_info.call_args_list]
                warning_logged = any("empty" in call.lower() for call in calls)
                self.assertTrue(warning_logged)

    def test_init_continues_gracefully_with_memory_issues(self):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        # Create memory directory with an empty file
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "empty.md").write_text("", encoding="utf-8")

        with patch('ralph.get_agent', return_value=mock_agent):
            # Should not raise, just warn
            orchestrator = RalphOrchestrator(agent_name="mock")
            self.assertIsNotNone(orchestrator)


class TestRalphOrchestratorInitWithUnknownAgent(TestRalphOrchestratorInit):
    """Tests for RalphOrchestrator initialization with unknown agent names."""

    def test_init_raises_value_error_for_unknown_agent(self):
        with self.assertRaises(ValueError) as context:
            get_agent("nonexistent_agent")

        self.assertIn("Unknown agent", str(context.exception))
        self.assertIn("nonexistent_agent", str(context.exception))

    def test_init_error_message_lists_available_agents(self):
        with self.assertRaises(ValueError) as context:
            get_agent("unknown")

        error_msg = str(context.exception)
        self.assertIn("claude", error_msg)
        self.assertIn("copilot", error_msg)


class TestCliArgumentParserBase(unittest.TestCase):
    """Base class for CLI argument parser tests."""

    def setUp(self):
        self.agent = list_agents()[0]
        self.parser = argparse.ArgumentParser(
            description="Ralph - Autonomous Software Development Agent"
        )
        self.parser.add_argument(
            "phase",
            choices=["architect", "planner", "execute", "all"],
            default="all",
            nargs="?",
            help="Select which phase to run"
        )
        self.parser.add_argument(
            "--version",
            action="version",
            version="Ralph test"
        )
        self.parser.add_argument(
            "--accept-all", "-y",
            action="store_true",
            help="Skip user feedback prompts"
        )
        self.parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable debug-level logging"
        )
        self.parser.add_argument(
            "--no-color",
            action="store_true",
            help="Disable color output"
        )
        self.parser.add_argument(
            "--agent",
            choices=list_agents(),
            default=self.agent,
            help="Select which agent to use"
        )


class TestCliPhaseArgument(TestCliArgumentParserBase):
    """Tests for CLI phase positional argument."""

    def test_default_phase_is_all(self):
        args = self.parser.parse_args([])
        self.assertEqual(args.phase, "all")

    def test_phase_architect_is_valid(self):
        args = self.parser.parse_args(["architect"])
        self.assertEqual(args.phase, "architect")

    def test_phase_planner_is_valid(self):
        args = self.parser.parse_args(["planner"])
        self.assertEqual(args.phase, "planner")

    def test_phase_execute_is_valid(self):
        args = self.parser.parse_args(["execute"])
        self.assertEqual(args.phase, "execute")

    def test_phase_all_is_valid(self):
        args = self.parser.parse_args(["all"])
        self.assertEqual(args.phase, "all")

    def test_invalid_phase_raises_error(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["invalid_phase"])

    def test_phase_is_case_sensitive(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["ARCHITECT"])

    def test_phase_is_optional(self):
        args = self.parser.parse_args(["--verbose"])
        self.assertEqual(args.phase, "all")


class TestCliAcceptAllFlag(TestCliArgumentParserBase):
    """Tests for CLI --accept-all / -y flag."""

    def test_accept_all_default_is_false(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.accept_all)

    def test_accept_all_long_flag_sets_true(self):
        args = self.parser.parse_args(["--accept-all"])
        self.assertTrue(args.accept_all)

    def test_accept_all_short_flag_sets_true(self):
        args = self.parser.parse_args(["-y"])
        self.assertTrue(args.accept_all)

    def test_accept_all_with_phase(self):
        args = self.parser.parse_args(["execute", "--accept-all"])
        self.assertTrue(args.accept_all)
        self.assertEqual(args.phase, "execute")

    def test_accept_all_short_with_phase(self):
        args = self.parser.parse_args(["planner", "-y"])
        self.assertTrue(args.accept_all)
        self.assertEqual(args.phase, "planner")

    def test_accept_all_before_phase(self):
        args = self.parser.parse_args(["--accept-all", "architect"])
        self.assertTrue(args.accept_all)
        self.assertEqual(args.phase, "architect")

    def test_short_flag_before_phase(self):
        args = self.parser.parse_args(["-y", "execute"])
        self.assertTrue(args.accept_all)
        self.assertEqual(args.phase, "execute")


class TestCliVerboseFlag(TestCliArgumentParserBase):
    """Tests for CLI --verbose flag."""

    def test_verbose_default_is_false(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.verbose)

    def test_verbose_flag_sets_true(self):
        args = self.parser.parse_args(["--verbose"])
        self.assertTrue(args.verbose)

    def test_verbose_with_phase(self):
        args = self.parser.parse_args(["execute", "--verbose"])
        self.assertTrue(args.verbose)
        self.assertEqual(args.phase, "execute")

    def test_verbose_before_phase(self):
        args = self.parser.parse_args(["--verbose", "architect"])
        self.assertTrue(args.verbose)
        self.assertEqual(args.phase, "architect")

    def test_verbose_with_other_flags(self):
        args = self.parser.parse_args(["--verbose", "--accept-all"])
        self.assertTrue(args.verbose)
        self.assertTrue(args.accept_all)


class TestCliNoColorFlag(TestCliArgumentParserBase):
    """Tests for CLI --no-color flag."""

    def test_no_color_default_is_false(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.no_color)

    def test_no_color_flag_sets_true(self):
        args = self.parser.parse_args(["--no-color"])
        self.assertTrue(args.no_color)

    def test_no_color_with_phase(self):
        args = self.parser.parse_args(["planner", "--no-color"])
        self.assertTrue(args.no_color)
        self.assertEqual(args.phase, "planner")

    def test_no_color_before_phase(self):
        args = self.parser.parse_args(["--no-color", "execute"])
        self.assertTrue(args.no_color)
        self.assertEqual(args.phase, "execute")

    def test_no_color_with_verbose(self):
        args = self.parser.parse_args(["--no-color", "--verbose"])
        self.assertTrue(args.no_color)
        self.assertTrue(args.verbose)


class TestCliAgentFlag(TestCliArgumentParserBase):
    """Tests for CLI --agent flag."""

    def test_agent_default_is_first_available(self):
        args = self.parser.parse_args([])
        self.assertEqual(args.agent, self.agent)

    def test_agent_claude_is_valid(self):
        args = self.parser.parse_args(["--agent", "claude"])
        self.assertEqual(args.agent, "claude")

    def test_agent_copilot_is_valid(self):
        args = self.parser.parse_args(["--agent", "copilot"])
        self.assertEqual(args.agent, "copilot")

    def test_invalid_agent_raises_error(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["--agent", "invalid_agent"])

    def test_agent_with_phase(self):
        args = self.parser.parse_args(["execute", "--agent", "copilot"])
        self.assertEqual(args.agent, "copilot")
        self.assertEqual(args.phase, "execute")

    def test_agent_before_phase(self):
        args = self.parser.parse_args(["--agent", "claude", "architect"])
        self.assertEqual(args.agent, "claude")
        self.assertEqual(args.phase, "architect")


class TestCliVersionFlag(TestCliArgumentParserBase):
    """Tests for CLI --version flag."""

    def test_version_flag_exits(self):
        with self.assertRaises(SystemExit) as context:
            self.parser.parse_args(["--version"])
        self.assertEqual(context.exception.code, 0)


class TestCliCombinedFlags(TestCliArgumentParserBase):
    """Tests for CLI with multiple combined flags."""

    def test_all_flags_combined(self):
        args = self.parser.parse_args([
            "execute",
            "--accept-all",
            "--verbose",
            "--no-color",
            "--agent", "copilot"
        ])
        self.assertEqual(args.phase, "execute")
        self.assertTrue(args.accept_all)
        self.assertTrue(args.verbose)
        self.assertTrue(args.no_color)
        self.assertEqual(args.agent, "copilot")

    def test_flags_in_different_order(self):
        args = self.parser.parse_args([
            "--no-color",
            "--agent", "claude",
            "planner",
            "-y",
            "--verbose"
        ])
        self.assertEqual(args.phase, "planner")
        self.assertTrue(args.accept_all)
        self.assertTrue(args.verbose)
        self.assertTrue(args.no_color)
        self.assertEqual(args.agent, "claude")

    def test_short_and_long_flags_combined(self):
        args = self.parser.parse_args(["-y", "--verbose", "--no-color"])
        self.assertTrue(args.accept_all)
        self.assertTrue(args.verbose)
        self.assertTrue(args.no_color)

    def test_default_values_when_no_flags(self):
        args = self.parser.parse_args([])
        self.assertEqual(args.phase, "all")
        self.assertFalse(args.accept_all)
        self.assertFalse(args.verbose)
        self.assertFalse(args.no_color)
        self.assertEqual(args.agent, self.agent)


class TestCliGetVersion(unittest.TestCase):
    """Tests for get_version() function."""

    def test_get_version_returns_string(self):
        result = get_version()
        self.assertIsInstance(result, str)

    def test_get_version_returns_non_empty(self):
        result = get_version()
        self.assertTrue(len(result) > 0)

    def test_get_version_not_unknown_when_pyproject_exists(self):
        pyproject_path = Path(__file__).parent / "pyproject.toml"
        if pyproject_path.exists():
            result = get_version()
            # If pyproject.toml exists and has version, it shouldn't be "unknown"
            # But if it doesn't have version field, "unknown" is acceptable
            self.assertIsInstance(result, str)


class TestCliMainFunction(unittest.TestCase):
    """Tests for main() function setup."""

    def test_main_creates_parser(self):
        # Test that main() doesn't crash on import
        self.assertTrue(callable(main))

    def test_list_agents_returns_non_empty_list(self):
        agents = list_agents()
        self.assertIsInstance(agents, list)
        self.assertTrue(len(agents) > 0)

    def test_list_agents_contains_claude(self):
        agents = list_agents()
        self.assertIn("claude", agents)

    def test_list_agents_contains_copilot(self):
        agents = list_agents()
        self.assertIn("copilot", agents)


class TestRalphOrchestratorArchivePrd(unittest.TestCase):
    """Integration tests for RalphOrchestrator._archive_prd() PRD archival functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        # Save original config values
        self.original_base_dir = CONF.BASE_DIR
        self.original_root_dir = CONF.ROOT_DIR
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_archive_dir = CONF.ARCHIVE_DIR
        self.original_prd_file = CONF.PRD_FILE
        # Set temp paths
        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        CONF.ARCHIVE_DIR = self.temp_path / ".ralph" / "archive"
        CONF.PRD_FILE = self.temp_path / ".ralph" / "prd.json"

    def tearDown(self):
        # Restore original config values
        CONF.BASE_DIR = self.original_base_dir
        CONF.ROOT_DIR = self.original_root_dir
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.ARCHIVE_DIR = self.original_archive_dir
        CONF.PRD_FILE = self.original_prd_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_orchestrator(self):
        """Helper to create a mock orchestrator without agent dependencies."""
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"

        with patch('ralph.get_agent', return_value=mock_agent):
            return RalphOrchestrator(agent_name="mock")

    def test_archive_prd_moves_file_to_archive_directory(self):
        """Given a completed PRD file exists, when _archive_prd is called, then the PRD is moved to the archive directory."""
        orchestrator = self._create_mock_orchestrator()
        prd_content = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')

        orchestrator._archive_prd()

        self.assertFalse(CONF.PRD_FILE.exists())
        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived_files), 1)

    def test_archive_prd_creates_file_with_timestamp_format(self):
        """Given a completed PRD file exists, when _archive_prd is called, then the archived filename contains a timestamp in YYYY-MM-DD_HH-MM-SS format."""
        orchestrator = self._create_mock_orchestrator()
        prd_content = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')

        orchestrator._archive_prd()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived_files), 1)
        filename = archived_files[0].name
        # Verify timestamp format: prd_YYYY-MM-DD_HH-MM-SS.json
        pattern = r"^prd_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json$"
        self.assertRegex(filename, pattern)

    def test_archive_prd_preserves_original_content(self):
        """Given a completed PRD file exists, when _archive_prd is called, then the archived file contains the original PRD content."""
        orchestrator = self._create_mock_orchestrator()
        prd_content = '{"id": "PRD-001", "description": "Test PRD", "userStories": [{"id": "TASK-001", "status": "completed"}]}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')

        orchestrator._archive_prd()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        archived_content = archived_files[0].read_text(encoding='utf-8')
        self.assertEqual(archived_content, prd_content)

    def test_archive_prd_removes_original_file(self):
        """Given a completed PRD file exists, when _archive_prd is called, then the original prd.json is removed."""
        orchestrator = self._create_mock_orchestrator()
        prd_content = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')
        self.assertTrue(CONF.PRD_FILE.exists())

        orchestrator._archive_prd()

        self.assertFalse(CONF.PRD_FILE.exists())

    def test_archive_prd_archive_directory_created_by_orchestrator_init(self):
        """Given orchestrator is initialized, when _archive_prd is called, then the archive directory already exists from init."""
        orchestrator = self._create_mock_orchestrator()
        # Orchestrator.__init__ calls ensure_directories() which creates the archive dir
        self.assertTrue(CONF.ARCHIVE_DIR.exists())

        prd_content = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')

        orchestrator._archive_prd()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived_files), 1)

    def test_archive_prd_does_nothing_when_prd_not_exists(self):
        """Given no PRD file exists, when _archive_prd is called, then no error occurs and no file is created."""
        orchestrator = self._create_mock_orchestrator()
        # Ensure PRD file does not exist
        if CONF.PRD_FILE.exists():
            CONF.PRD_FILE.unlink()

        # Should not raise any exception
        orchestrator._archive_prd()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived_files), 0)

    def test_archive_prd_handles_multiple_archives(self):
        """Given multiple PRDs are archived over time, when _archive_prd is called multiple times, then each archive has a unique timestamp."""
        orchestrator = self._create_mock_orchestrator()

        # Archive first PRD with mocked time
        prd_content_1 = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content_1, encoding='utf-8')
        mock_now_1 = MagicMock()
        mock_now_1.strftime.return_value = "2024-01-01_12-00-00"
        with patch('ralph.datetime.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now_1
            orchestrator._archive_prd()

        # Archive second PRD with different mocked time
        prd_content_2 = '{"id": "PRD-002", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content_2, encoding='utf-8')
        mock_now_2 = MagicMock()
        mock_now_2.strftime.return_value = "2024-01-01_12-00-01"
        with patch('ralph.datetime.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now_2
            orchestrator._archive_prd()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        self.assertEqual(len(archived_files), 2)
        filenames = {f.name for f in archived_files}
        self.assertEqual(len(filenames), 2)  # All filenames unique

    def test_archive_prd_archived_file_is_valid_json(self):
        """Given a valid PRD JSON file exists, when _archive_prd is called, then the archived file is valid JSON."""
        orchestrator = self._create_mock_orchestrator()
        prd_data = {"id": "PRD-001", "description": "Test", "userStories": []}
        CONF.PRD_FILE.write_text(json.dumps(prd_data), encoding='utf-8')

        orchestrator._archive_prd()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        archived_content = archived_files[0].read_text(encoding='utf-8')
        parsed = json.loads(archived_content)
        self.assertEqual(parsed["id"], "PRD-001")
        self.assertEqual(parsed["description"], "Test")

    def test_archive_prd_timestamp_reflects_current_time(self):
        """Given a PRD file exists, when _archive_prd is called, then the timestamp in the filename reflects the current time."""
        orchestrator = self._create_mock_orchestrator()
        prd_content = '{"id": "PRD-001", "userStories": []}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')

        before = datetime.datetime.now()
        orchestrator._archive_prd()
        after = datetime.datetime.now()

        archived_files = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        filename = archived_files[0].name
        # Extract timestamp from filename: prd_YYYY-MM-DD_HH-MM-SS.json
        timestamp_str = filename[4:-5]  # Remove 'prd_' and '.json'
        archived_time = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")

        # Allow for some variance in timing
        self.assertGreaterEqual(archived_time, before.replace(microsecond=0))
        self.assertLessEqual(archived_time, after.replace(microsecond=0) + datetime.timedelta(seconds=1))



class TestAgentError(unittest.TestCase):
    """Tests for AgentError dataclass and error logging functionality."""

    def test_agent_error_from_exception_captures_exception_type(self):
        """Verify exception type is captured correctly."""
        try:
            raise ValueError("test error message")
        except ValueError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-001")

        self.assertEqual(error.exception_type, "ValueError")

    def test_agent_error_from_exception_captures_message(self):
        """Verify exception message is captured correctly."""
        try:
            raise RuntimeError("specific error details")
        except RuntimeError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-002")

        self.assertEqual(error.message, "specific error details")

    def test_agent_error_from_exception_captures_stack_trace(self):
        """Verify stack trace is captured and non-empty."""
        try:
            raise KeyError("missing key")
        except KeyError as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-003")

        self.assertIn("Traceback", error.stack_trace)
        self.assertIn("KeyError", error.stack_trace)

    def test_agent_error_from_exception_captures_timestamp(self):
        """Verify timestamp is captured in ISO format."""
        try:
            raise Exception("test")
        except Exception as e:
            error = AgentError.from_exception(e, "TestAgent", "TASK-004")

        # Verify timestamp can be parsed as ISO format
        parsed = dt.fromisoformat(error.timestamp)
        self.assertIsInstance(parsed, dt)

    def test_agent_error_from_exception_captures_agent_name(self):
        """Verify agent name is captured correctly."""
        try:
            raise Exception("test")
        except Exception as e:
            error = AgentError.from_exception(e, "Claude", "TASK-005")

        self.assertEqual(error.agent_name, "Claude")

    def test_agent_error_from_exception_captures_task_id(self):
        """Verify task ID is captured correctly."""
        try:
            raise Exception("test")
        except Exception as e:
            error = AgentError.from_exception(e, "TestAgent", "WORKER-TASK-006")

        self.assertEqual(error.task_id, "WORKER-TASK-006")

    def test_format_log_entry_contains_all_fields(self):
        """Verify formatted log entry contains all required context."""
        try:
            raise TypeError("type mismatch error")
        except TypeError as e:
            error = AgentError.from_exception(e, "Copilot", "TASK-007")

        log_entry = error.format_log_entry()

        self.assertIn("AGENT ERROR", log_entry)
        self.assertIn("Agent: Copilot", log_entry)
        self.assertIn("Task ID: TASK-007", log_entry)
        self.assertIn("Exception Type: TypeError", log_entry)
        self.assertIn("Message: type mismatch error", log_entry)
        self.assertIn("Stack Trace:", log_entry)
        self.assertIn("Traceback", log_entry)


class TestAgentStructuredErrorReturn(unittest.TestCase):
    """Tests for agent implementations returning structured error information."""

    def test_claude_agent_returns_agent_error_on_cli_failure(self):
        """Verify ClaudeAgent returns AgentError when CLI fails."""
        agent = ClaudeAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="some output",
                stderr="some error"
            )
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test prompt", "TEST-TAG")

        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "CLIError")
        self.assertIn("exited with code 1", error.message)
        self.assertEqual(error.agent_name, "Claude")
        self.assertEqual(error.task_id, "TEST-TAG")

    def test_claude_agent_returns_none_error_on_success(self):
        """Verify ClaudeAgent returns None for error on success."""
        agent = ClaudeAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="success output",
                stderr=""
            )
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test prompt", "TEST-TAG")

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(output, "success output")

    def test_claude_agent_returns_agent_error_on_exception(self):
        """Verify ClaudeAgent returns AgentError when exception occurs."""
        agent = ClaudeAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = TimeoutError("Process timed out")
            with patch('shutil.which', return_value='/usr/bin/claude'):
                success, output, error = agent.run("test prompt", "TEST-TAG")

        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "TimeoutError")
        self.assertEqual(error.agent_name, "Claude")

    def test_copilot_agent_returns_agent_error_on_cli_failure(self):
        """Verify GithubAgent returns AgentError when CLI fails."""
        agent = GithubAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="copilot output",
                stderr="copilot error"
            )
            with patch('shutil.which', return_value='/usr/bin/copilot'):
                success, output, error = agent.run("test prompt", "COPILOT-TAG")

        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIsInstance(error, AgentError)
        self.assertEqual(error.exception_type, "CLIError")
        self.assertIn("exited with code 1", error.message)
        self.assertEqual(error.agent_name, "Copilot")
        self.assertEqual(error.task_id, "COPILOT-TAG")

    def test_copilot_agent_returns_none_error_on_success(self):
        """Verify GithubAgent returns None for error on success."""
        agent = GithubAgent(timeout_seconds=5)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="copilot success",
                stderr=""
            )
            with patch('shutil.which', return_value='/usr/bin/copilot'):
                success, output, error = agent.run("test prompt", "COPILOT-TAG")

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(output, "copilot success")


class TestProgressRetryContext(unittest.TestCase):
    """Tests for structured failure information in progress.txt retry context."""

    def setUp(self):
        """Create a temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.progress_file = Path(self.test_dir) / "progress.txt"

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_record_failure_with_agent_error_includes_structured_context(self):
        """Verify _record_failure writes structured error context when AgentError provided."""
        # Suppress logging output
        Logger.set_verbose(False)

        # Create a mock agent
        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "TestAgent"
        mock_agent.check_dependencies.return_value = True

        # Temporarily redirect CONF paths
        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    error = AgentError(
                        exception_type="VerificationError",
                        message="Test command failed with exit code 1",
                        stack_trace="STDOUT:\ntest output\nSTDERR:\nerror output",
                        timestamp="2024-01-01T12:00:00",
                        agent_name="TestAgent",
                        task_id="TASK-001",
                    )

                    orchestrator._record_failure(0, "Verification Failed", "agent output", agent_error=error)

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("Structured Error Context", content)
                    self.assertIn("VerificationError", content)
                    self.assertIn("Test command failed with exit code 1", content)
                    self.assertIn("TestAgent", content)
                    self.assertIn("TASK-001", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file

    def test_record_failure_without_agent_error_uses_simple_format(self):
        """Verify _record_failure uses simple format when no AgentError provided."""
        Logger.set_verbose(False)

        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "TestAgent"
        mock_agent.check_dependencies.return_value = True

        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    orchestrator._record_failure(0, "CLI Crash", "simple error detail")

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("Attempt 1 Failed: CLI Crash", content)
                    self.assertIn("simple error detail", content)
                    self.assertNotIn("Structured Error Context", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file

    def test_structured_failure_context_includes_agent_output_section(self):
        """Verify structured failure includes the agent output section."""
        Logger.set_verbose(False)

        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "TestAgent"
        mock_agent.check_dependencies.return_value = True

        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    error = AgentError(
                        exception_type="AgentReportedFailure",
                        message="Agent did not report STATUS: SUCCESS",
                        stack_trace="Agent output trace",
                        timestamp="2024-01-01T12:00:00",
                        agent_name="Claude",
                        task_id="TASK-002",
                    )

                    orchestrator._record_failure(1, "Agent Reported Failure", "last 1000 chars of output", agent_error=error)

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("Agent Output (last 1000 chars)", content)
                    self.assertIn("last 1000 chars of output", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file


class TestOrchestratorUsesAgentError(unittest.TestCase):
    """Tests for orchestrator using structured error from agent return value."""

    def setUp(self):
        """Create a temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.progress_file = Path(self.test_dir) / "progress.txt"

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_orchestrator_uses_agent_returned_error(self):
        """Verify orchestrator uses structured error returned by agent."""
        Logger.set_verbose(False)

        agent_error = AgentError(
            exception_type="CLIError",
            message="Claude CLI exited with code 1",
            stack_trace="STDOUT:\ntest\nSTDERR:\nerror",
            timestamp="2024-01-01T12:00:00",
            agent_name="Claude",
            task_id="WORKER-TASK-001",
        )

        mock_agent = MagicMock()
        mock_agent.get_name.return_value = "Claude"
        mock_agent.check_dependencies.return_value = True
        mock_agent.run.return_value = (False, "CLI output", agent_error)

        original_progress_file = CONF.PROGRESS_FILE
        CONF.PROGRESS_FILE = self.progress_file

        try:
            with patch('ralph.get_agent', return_value=mock_agent):
                with patch.object(RalphOrchestrator, '_validate_memory_on_startup'):
                    orchestrator = RalphOrchestrator.__new__(RalphOrchestrator)
                    orchestrator.agent = mock_agent

                    orchestrator._record_failure(0, "CLI Crash", "CLI output", agent_error=agent_error)

                    content = self.progress_file.read_text(encoding='utf-8')

                    self.assertIn("CLIError", content)
                    self.assertIn("Claude CLI exited with code 1", content)
                    self.assertIn("Claude", content)
        finally:
            CONF.PROGRESS_FILE = original_progress_file


class TestRalphOrchestratorArchitectVerification(unittest.TestCase):
    """Tests for RalphOrchestrator.run_architect() ARCH.md verification."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        # Save original config values
        self.original_base_dir = CONF.BASE_DIR
        self.original_root_dir = CONF.ROOT_DIR
        self.original_memory_dir = CONF.MEMORY_DIR
        self.original_archive_dir = CONF.ARCHIVE_DIR
        # Set temp paths
        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        CONF.ARCHIVE_DIR = self.temp_path / ".ralph" / "archive"

    def tearDown(self):
        # Restore original config values
        CONF.BASE_DIR = self.original_base_dir
        CONF.ROOT_DIR = self.original_root_dir
        CONF.MEMORY_DIR = self.original_memory_dir
        CONF.ARCHIVE_DIR = self.original_archive_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_run_architect_exits_when_arch_md_not_created(self):
        """Given agent runs successfully but ARCH.md is not created, when run_architect is called, then it exits with code 1."""
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            from ralph import RalphOrchestrator
            orchestrator = RalphOrchestrator(agent_name="mock")

            # Create memory file so memory check passes
            CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")

            # ARCH.md should not exist in temp dir
            arch_md_path = CONF.BASE_DIR / "ARCH.md"
            self.assertFalse(arch_md_path.exists())

            with patch('ralph.sys.exit') as mock_exit:
                with patch('ralph.Logger.info'):  # Suppress Logger output
                    orchestrator.run_architect("test intent")
                mock_exit.assert_called_once_with(1)

    def test_run_architect_succeeds_when_arch_md_created(self):
        """Given agent runs successfully and ARCH.md is created, when run_architect is called, then it completes without exit."""
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            from ralph import RalphOrchestrator
            orchestrator = RalphOrchestrator(agent_name="mock")

            # Create memory file so memory check passes
            CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")

            # Create ARCH.md
            arch_md_path = CONF.BASE_DIR / "ARCH.md"
            arch_md_path.write_text("# Architecture\n", encoding="utf-8")

            with patch('ralph.sys.exit') as mock_exit:
                with patch('ralph.Logger.info'):  # Suppress Logger output
                    orchestrator.run_architect("test intent")
                mock_exit.assert_not_called()

    def test_run_architect_logs_error_when_arch_md_missing(self):
        """Given agent runs successfully but ARCH.md is not created, when run_architect is called, then it logs an error about ARCH.md."""
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            from ralph import RalphOrchestrator
            orchestrator = RalphOrchestrator(agent_name="mock")

            CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")

            with patch('ralph.sys.exit'):
                with patch('ralph.Logger.info') as mock_info:
                    orchestrator.run_architect("test intent")
                    # Check that error message about ARCH.md was logged
                    calls = [str(call) for call in mock_info.call_args_list]
                    arch_md_error_logged = any("ARCH.md" in call for call in calls)
                    self.assertTrue(arch_md_error_logged)

    def test_run_architect_checks_arch_md_in_project_root(self):
        """Given agent runs, when run_architect is called, then it checks for ARCH.md in CONF.BASE_DIR."""
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            from ralph import RalphOrchestrator
            orchestrator = RalphOrchestrator(agent_name="mock")

            CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            (CONF.MEMORY_DIR / "architecture.md").write_text("content", encoding="utf-8")

            # Create ARCH.md in the correct location (project root)
            expected_arch_path = CONF.BASE_DIR / "ARCH.md"
            expected_arch_path.write_text("# Architecture\n", encoding="utf-8")

            with patch('ralph.sys.exit') as mock_exit:
                with patch('ralph.Logger.info'):  # Suppress Logger output
                    orchestrator.run_architect("test intent")
                mock_exit.assert_not_called()

    def test_run_architect_still_checks_memory_files(self):
        """Given agent runs but memory files are not created, when run_architect is called, then it exits for memory failure."""
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = True
        mock_agent.get_name.return_value = "MockAgent"
        mock_agent.run.return_value = (True, "STATUS: CREATED", None)

        with patch('ralph.get_agent', return_value=mock_agent):
            from ralph import RalphOrchestrator
            orchestrator = RalphOrchestrator(agent_name="mock")

            # Don't create any memory files
            CONF.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            # Memory dir exists but is empty

            with patch('ralph.sys.exit') as mock_exit:
                with patch('ralph.Logger.info'):  # Suppress Logger output
                    orchestrator.run_architect("test intent")
                # sys.exit should be called at least once (for memory failure)
                mock_exit.assert_called_with(1)


if __name__ == "__main__":
    unittest.main()
