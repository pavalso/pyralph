"""Centralized pytest fixtures for shared test infrastructure.

This module provides reusable fixtures to eliminate redundant setUp/tearDown
code across test files and improve test maintainability.
"""
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyralph.config import CONF
from pyralph.fetch_ready_issues import Issue
from pyralph.hooks import HookManager
from pyralph.logger import Logger
from pyralph.orchestrator import RalphOrchestrator
from tests.helpers import CaptureStdout


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory using pytest's built-in tmp_path.

    The directory is automatically cleaned up by pytest after the test.
    Yields the Path object for the temporary directory.
    """
    yield tmp_path


@pytest.fixture
def temp_config(tmp_path):
    """Provide temporary directory with CONF management for config-dependent tests.

    Saves and restores CONF state after each test to prevent test pollution.
    Sets up CONF to use temporary directories for isolation.

    Yields a namespace object with:
    - temp_path: Path to temporary directory
    - temp_dir: String path to temporary directory (for backwards compatibility)
    - create_mock_agent: Factory for creating mock agents
    - create_mock_orchestrator: Factory for creating mock orchestrators

    Raises:
        ValueError: If tmp_path fixture is unavailable (should not happen
            when using pytest correctly).
    """
    if tmp_path is None:
        raise ValueError("tmp_path fixture is required for temp_config")

    config_attrs = ('BASE_DIR', 'ROOT_DIR', 'ARCHIVE_DIR', 'PRD_FILE')
    original_conf = {attr: getattr(CONF, attr) for attr in config_attrs if hasattr(CONF, attr)}

    CONF.BASE_DIR = tmp_path
    CONF.ROOT_DIR = tmp_path / ".ralph"
    CONF.ARCHIVE_DIR = tmp_path / ".ralph" / "archive"
    CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"

    class TempConfigEnv:
        """Environment for config-dependent tests."""

        def __init__(self, path: Path):
            self.temp_path = path
            self.temp_dir = str(path)

        def create_mock_agent(self, name="MockAgent", check_deps=True):
            """Create a mock agent with standard interface methods."""
            agent = MagicMock()
            agent.check_dependencies.return_value = check_deps
            agent.get_name.return_value = name
            return agent

        def create_mock_orchestrator(self, agent_name="mock", mock_agent=None, **kwargs):
            """Create an orchestrator with a mock agent."""
            if mock_agent is None:
                mock_agent = self.create_mock_agent()
            with patch('pyralph.orchestrator.get_agent', return_value=mock_agent):
                return RalphOrchestrator(agent_name=agent_name, **kwargs)

    try:
        yield TempConfigEnv(tmp_path)
    finally:
        for attr, value in original_conf.items():
            setattr(CONF, attr, value)


class TempHooksEnv:
    """Environment for hook-related tests with temp directories.

    Provides paths and utilities for testing HookManager functionality.
    """

    def __init__(self, tmp_path: Path):
        if tmp_path is None:
            raise ValueError("tmp_path fixture is required for TempHooksEnv")
        self.temp_path = tmp_path
        self.temp_dir = str(tmp_path)
        self.hooks_dir = tmp_path / "hooks"
        self.hooks_dir.mkdir(parents=True)
        self.manager = HookManager(self.hooks_dir)

    def create_hook_file(self, name: str, content: str) -> Path:
        """Create a hook file with the given name and content."""
        hook_file = self.hooks_dir / name
        hook_file.write_text(content, encoding='utf-8')
        return hook_file


@pytest.fixture
def temp_hooks(tmp_path):
    """Provide temporary directory with hooks setup for hook-related tests.

    Creates a hooks directory and initializes a HookManager. The directory
    is automatically cleaned up by pytest after the test.

    Yields a TempHooksEnv object with:
    - temp_path: Path to temporary directory
    - temp_dir: String path to temporary directory
    - hooks_dir: Path to hooks directory
    - manager: HookManager instance for the hooks directory
    - create_hook_file: Method to create hook files

    Raises:
        ValueError: If tmp_path fixture is unavailable (should not happen
            when using pytest correctly).
    """
    return TempHooksEnv(tmp_path)


@pytest.fixture
def mock_agent():
    """Create a mock agent with standard interface methods.

    Returns a MagicMock configured with:
    - check_dependencies() returning True
    - get_name() returning "MockAgent"
    """
    agent = MagicMock()
    agent.check_dependencies.return_value = True
    agent.get_name.return_value = "MockAgent"
    return agent


@pytest.fixture
def mock_orchestrator(tmp_path, mock_agent):
    """Create a mock orchestrator with proper CONF state management.

    Saves and restores CONF state after each test to prevent test pollution.
    Sets up CONF to use temporary directories for isolation.

    Yields a factory function that creates orchestrator instances with
    optional keyword arguments.
    """
    config_attrs = ('BASE_DIR', 'ROOT_DIR', 'ARCHIVE_DIR', 'PRD_FILE',
                    'TEMPLATES_DIR', 'HOOKS_DIR', 'PROGRESS_FILE', 'LOG_FILE')
    original_conf = {attr: getattr(CONF, attr) for attr in config_attrs}

    CONF.BASE_DIR = tmp_path
    CONF.ROOT_DIR = tmp_path / ".ralph"
    CONF.ARCHIVE_DIR = tmp_path / ".ralph" / "archive"
    CONF.TEMPLATES_DIR = tmp_path / ".ralph" / "templates"
    CONF.HOOKS_DIR = tmp_path / ".ralph" / "hooks"
    CONF.PRD_FILE = tmp_path / ".ralph" / "prd.json"
    CONF.PROGRESS_FILE = tmp_path / ".ralph" / "progress.txt"
    CONF.LOG_FILE = tmp_path / ".ralph" / "ralph_log.txt"

    def _create_orchestrator(agent_name="mock", custom_agent=None, **kwargs):
        """Factory to create orchestrator instances.

        Args:
            agent_name: Name of the agent (default: "mock")
            custom_agent: Optional custom mock agent to use
            **kwargs: Additional keyword arguments for RalphOrchestrator

        Returns:
            RalphOrchestrator instance
        """
        agent = custom_agent if custom_agent is not None else mock_agent
        with patch('pyralph.orchestrator.get_agent', return_value=agent):
            return RalphOrchestrator(agent_name=agent_name, **kwargs)

    try:
        yield _create_orchestrator
    finally:
        for attr, value in original_conf.items():
            setattr(CONF, attr, value)


@pytest.fixture
def capture_stdout():
    """Capture stdout during test execution.

    Yields a CaptureStdout context manager that captures all stdout output.
    Restores original stdout after the test completes, even on exception.

    Usage:
        def test_example(capture_stdout):
            with capture_stdout as captured:
                print("hello")
                assert captured.getvalue() == "hello\\n"

    Edge cases:
        - Empty stdout capture returns empty string without error
        - Nested capture_stdout calls raise RuntimeError with clear message
    """
    capture = CaptureStdout()
    with capture:
        yield capture


@pytest.fixture
def logger_reset():
    """Save and restore all Logger class attributes after each test.

    Saves all 12 Logger class attributes before the test and restores
    them after completion. Handles edge case where redact_patterns is
    None by providing an empty list fallback.

    Also sets Logger to clean initial state before the test:
    - no_color, quiet, no_emoji, json_output, ndjson_output: False
    - verbosity: 0
    - log_level: 20 (info)
    - redact_patterns: []
    - no_log_prompts, no_log_responses: False

    This ensures complete test isolation for Logger state.
    State restoration occurs even on test failure via try/finally.
    """
    saved_state = {
        '_verbosity_value': Logger._verbosity_value,
        'no_color': Logger.no_color,
        'quiet': Logger.quiet,
        'no_emoji': Logger.no_emoji,
        'log_level': Logger.log_level,
        'json_output': Logger.json_output,
        'ndjson_output': Logger.ndjson_output,
        'custom_log_file': Logger.custom_log_file,
        'non_interactive': Logger.non_interactive,
        'redact_patterns': (Logger.redact_patterns.copy()
                           if Logger.redact_patterns is not None else []),
        'no_log_prompts': Logger.no_log_prompts,
        'no_log_responses': Logger.no_log_responses,
    }

    # Set clean initial Logger state
    Logger.no_color = False
    Logger.quiet = False
    Logger.no_emoji = False
    Logger._verbosity_value = 0
    Logger.log_level = 20
    Logger.json_output = False
    Logger.ndjson_output = False
    Logger.redact_patterns = []
    Logger.no_log_prompts = False
    Logger.no_log_responses = False

    try:
        yield
    finally:
        for attr, value in saved_state.items():
            setattr(Logger, attr, value)


@pytest.fixture
def logger_test_env(tmp_path, logger_reset):
    """Provide a complete Logger test environment with temp log file.

    Combines logger_reset fixture for Logger state management with
    temporary log file setup and CONF.LOG_FILE management.

    Sets Logger to clean initial state:
    - no_color, quiet, no_emoji, json_output, ndjson_output: False
    - verbosity: 0
    - log_level: 20 (info)
    - redact_patterns: []
    - no_log_prompts, no_log_responses: False

    Yields a dict with:
    - temp_dir: Path to temporary directory
    - log_file: Path to temporary log file

    CONF.LOG_FILE is restored after the test even on failure.
    """
    original_log_file = CONF.LOG_FILE
    log_file = tmp_path / "test_log.txt"
    CONF.LOG_FILE = log_file

    # Set clean initial Logger state
    Logger.no_color = False
    Logger.quiet = False
    Logger.no_emoji = False
    Logger.verbosity = 0
    Logger.log_level = 20
    Logger.json_output = False
    Logger.ndjson_output = False
    Logger.redact_patterns = []
    Logger.no_log_prompts = False
    Logger.no_log_responses = False

    try:
        yield {'temp_dir': tmp_path, 'log_file': log_file}
    finally:
        CONF.LOG_FILE = original_log_file


class IssueWatcherEnv:
    """Environment for IssueWatcher tests with lazy directory creation.

    Provides paths for store_dir, queue_dir, pid_file, log_file, and hooks_dir.
    Directories are created lazily only when accessed via ensure_* methods.
    """

    def __init__(self, tmp_path: Path):
        if tmp_path is None:
            raise ValueError("tmp_path fixture is required for IssueWatcherEnv")
        self._tmp_path = tmp_path

    @property
    def store_dir(self) -> str:
        """Path to the issue store directory (not created until ensure_store_dir)."""
        return str(self._tmp_path / "issues")

    @property
    def queue_dir(self) -> str:
        """Path to the issue queue directory (not created until ensure_queue_dir)."""
        return str(self._tmp_path / "queue")

    @property
    def pid_file(self) -> str:
        """Path to the watcher PID file."""
        return str(self._tmp_path / "watcher.pid")

    @property
    def log_file(self) -> str:
        """Path to the watcher log file."""
        return str(self._tmp_path / "watcher.log")

    @property
    def hooks_dir(self) -> str:
        """Path to the hooks directory (not created until ensure_hooks_dir)."""
        return str(self._tmp_path / "hooks")

    def ensure_store_dir(self) -> Path:
        """Create store directory if it doesn't exist and return its Path."""
        path = Path(self.store_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_queue_dir(self) -> Path:
        """Create queue directory if it doesn't exist and return its Path."""
        path = Path(self.queue_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_hooks_dir(self) -> Path:
        """Create hooks directory if it doesn't exist and return its Path."""
        path = Path(self.hooks_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_sample_issue(self, number=1, title="Test Issue", body="Test body"):
        """Create a sample Issue for testing."""
        return Issue(
            number=number,
            title=title,
            body=body,
            url=f"https://github.com/test/repo/issues/{number}",
            labels=["ready"]
        )

    def setup_hooks(self):
        """Set up hooks directory and HookManager with event capture.

        Returns a tuple of (hook_manager, emitted_events list).
        """
        hooks_path = self.ensure_hooks_dir()
        emitted_events = []
        hook_manager = HookManager(hooks_path)

        def capture_event(event):
            emitted_events.append(event)

        watcher_events = [
            "WATCHER_START", "WATCHER_STOP",
            "ISSUE_DETECTED", "ISSUE_STORED", "ISSUE_QUEUED",
            "ISSUE_PROCESSING_START", "ISSUE_PROCESSING_SUCCESS", "ISSUE_PROCESSING_FAILURE",
            "POLL_START", "POLL_SUCCESS", "POLL_ERROR"
        ]
        hook_manager.register_hook("test_capture", capture_event, watcher_events)
        return hook_manager, emitted_events


@pytest.fixture
def issue_watcher_env(tmp_path):
    """Provide an IssueWatcher test environment with automatic cleanup.

    Creates an IssueWatcherEnv instance that provides paths for:
    - store_dir: directory for storing issues
    - queue_dir: directory for queued issues
    - pid_file: path for the watcher PID file
    - log_file: path for the watcher log file
    - hooks_dir: directory for hooks

    Directories are created lazily via ensure_* methods to avoid creating
    empty directories that aren't needed by the test.

    Uses pytest's tmp_path fixture for automatic cleanup after each test.

    Raises:
        ValueError: If tmp_path fixture is missing (should not happen
            when using pytest correctly).
    """
    return IssueWatcherEnv(tmp_path)
