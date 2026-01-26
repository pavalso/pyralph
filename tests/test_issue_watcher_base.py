from pathlib import Path

from pyralph.hooks import HookManager

from .helpers import IssueWatcherTestCase


class IssueWatcherBase(IssueWatcherTestCase):
    """Common setup for issue watcher tests."""

    def setup_hooks(self):
        self.hooks_dir = str(self.temp_path / "hooks")
        Path(self.hooks_dir).mkdir(parents=True, exist_ok=True)
        self.emitted_events = []
        self.hook_manager = HookManager(Path(self.hooks_dir))
        def capture_event(event):
            self.emitted_events.append(event)
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
