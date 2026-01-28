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
from pyralph.logger import Logger
from pyralph.orchestrator import RalphOrchestrator


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory using pytest's built-in tmp_path.

    The directory is automatically cleaned up by pytest after the test.
    Yields the Path object for the temporary directory.
    """
    yield tmp_path


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

    Yields a StringIO object that captures all stdout output.
    Restores original stdout after the test completes, even on exception.
    """
    captured = StringIO()
    original_stdout = sys.stdout
    sys.stdout = captured
    try:
        yield captured
    finally:
        sys.stdout = original_stdout


@pytest.fixture
def logger_reset():
    """Save and restore all Logger class attributes after each test.

    Saves all 10 Logger class attributes before the test and restores
    them after completion. Handles edge case where redact_patterns is
    None by providing an empty list fallback.

    This ensures complete test isolation for Logger state.
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

    try:
        yield
    finally:
        for attr, value in saved_state.items():
            setattr(Logger, attr, value)
