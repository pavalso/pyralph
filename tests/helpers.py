import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyralph.config import CONF
from pyralph.fetch_ready_issues import Issue
from pyralph.hooks import HookManager
from pyralph.logger import Logger
from pyralph.orchestrator import RalphOrchestrator


class TempDirectoryMixin:
    """Mixin providing temporary directory setup/teardown."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        super().tearDown()


class TempConfigTestCase(TempDirectoryMixin, unittest.TestCase):
    """Base test class providing temporary directory and CONF management."""

    config_attrs = ('BASE_DIR', 'ROOT_DIR', 'MEMORY_DIR', 'ARCHIVE_DIR', 'PRD_FILE')

    def setUp(self):
        super().setUp()
        self._original_conf = {attr: getattr(CONF, attr) for attr in self.config_attrs if hasattr(CONF, attr)}
        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
        CONF.MEMORY_DIR = self.temp_path / ".ralph" / "memory"
        CONF.ARCHIVE_DIR = self.temp_path / ".ralph" / "archive"
        CONF.PRD_FILE = self.temp_path / ".ralph" / "prd.json"

    def tearDown(self):
        for attr, value in self._original_conf.items():
            setattr(CONF, attr, value)
        super().tearDown()

    def create_mock_agent(self, name="MockAgent", check_deps=True):
        mock_agent = MagicMock()
        mock_agent.check_dependencies.return_value = check_deps
        mock_agent.get_name.return_value = name
        return mock_agent

    def create_mock_orchestrator(self, agent_name="mock", mock_agent=None, **kwargs):
        if mock_agent is None:
            mock_agent = self.create_mock_agent()
        with patch('pyralph.orchestrator.get_agent', return_value=mock_agent):
            return RalphOrchestrator(agent_name=agent_name, **kwargs)


class TempHooksTestCase(TempDirectoryMixin, unittest.TestCase):
    """Base test class for hook-related tests with temp directories."""

    def setUp(self):
        super().setUp()
        self.hooks_dir = self.temp_path / "hooks"
        self.hooks_dir.mkdir(parents=True)
        self.manager = HookManager(self.hooks_dir)

    def tearDown(self):
        super().tearDown()

    def create_hook_file(self, name, content):
        hook_file = self.hooks_dir / name
        hook_file.write_text(content, encoding='utf-8')
        return hook_file


class LoggerTestCase(TempDirectoryMixin, unittest.TestCase):
    """Base test class for Logger tests with proper state reset."""

    def setUp(self):
        super().setUp()
        self._logger_state = {
            'no_color': Logger.no_color,
            '_verbose_value': Logger._verbose_value,
            '_verbosity_value': Logger._verbosity_value,
            'quiet': Logger.quiet, 'no_emoji': Logger.no_emoji, 'log_level': Logger.log_level,
            'json_output': Logger.json_output, 'ndjson_output': Logger.ndjson_output,
            'redact_patterns': Logger.redact_patterns.copy() if Logger.redact_patterns else [],
            'no_log_prompts': Logger.no_log_prompts, 'no_log_responses': Logger.no_log_responses,
        }
        Logger.no_color = Logger.quiet = Logger.no_emoji = False
        Logger.verbosity = 0
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
        for attr, value in self._logger_state.items():
            setattr(Logger, attr, value)
        CONF.LOG_FILE = self._original_log_file
        super().tearDown()


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
