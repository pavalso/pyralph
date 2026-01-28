import shutil
import sys
import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyralph.config import CONF
from pyralph.hooks import HookManager
from pyralph.orchestrator import RalphOrchestrator


# Thread-local storage to track active captures and detect nesting
_capture_state = threading.local()


class CaptureStdout:
    """Context manager for capturing stdout with proper restoration.

    Captures all stdout output during the context and provides access
    to the captured content. Restores original stdout even on exception.

    Raises:
        RuntimeError: If nested capture is attempted (not supported).
    """

    def __init__(self):
        self._captured = StringIO()
        self._original_stdout = None

    def __enter__(self):
        # Check for nested capture
        if getattr(_capture_state, 'active', False):
            raise RuntimeError(
                "Nested stdout capture is not supported. "
                "Ensure previous capture context is closed before starting a new one."
            )
        _capture_state.active = True
        self._original_stdout = sys.stdout
        sys.stdout = self._captured
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._original_stdout
        _capture_state.active = False
        return False  # Don't suppress exceptions

    def getvalue(self):
        """Return captured stdout content as string.

        Returns empty string if nothing was captured.
        """
        return self._captured.getvalue()

    @property
    def output(self):
        """Alias for getvalue() for convenience."""
        return self.getvalue()


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

    config_attrs = ('BASE_DIR', 'ROOT_DIR', 'ARCHIVE_DIR', 'PRD_FILE')

    def setUp(self):
        super().setUp()
        self._original_conf = {attr: getattr(CONF, attr) for attr in self.config_attrs if hasattr(CONF, attr)}
        CONF.BASE_DIR = self.temp_path
        CONF.ROOT_DIR = self.temp_path / ".ralph"
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
