from pathlib import Path
from io import StringIO
from unittest.mock import patch

from pyralph.fetch_ready_issues import IssueWatcher, WatcherConfig
from pyralph.hooks import EventType

from .helpers import IssueWatcherTestCase
from .test_issue_watcher_base import IssueWatcherBase


class TestIssueWatcher(IssueWatcherTestCase):
    def test_init_default_config(self):
        watcher = IssueWatcher()
        assert watcher.config is not None
        assert watcher.config.label == "ready"

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
        assert watcher.config.label == "bug"
        assert watcher.config.poll_interval == 30.0

    def test_get_status_not_running(self):
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir,
            pid_file=self.pid_file
        )
        watcher = IssueWatcher(config)
        status = watcher.get_status()
        assert not status.running
        assert status.pid is None

    def test_store_and_queue_access(self):
        config = WatcherConfig(
            store_dir=self.store_dir,
            queue_dir=self.queue_dir
        )
        watcher = IssueWatcher(config)
        assert watcher.store is not None
        assert watcher.queue is not None


class TestGitHubPoller(IssueWatcherTestCase):
    def test_init_default_config(self):
        from pyralph.fetch_ready_issues import GitHubPoller
        poller = GitHubPoller()
        assert poller.config.label == "ready"
        assert poller.config.interval == 60.0

    def test_init_custom_config(self):
        from pyralph.fetch_ready_issues import PollerConfig, GitHubPoller
        config = PollerConfig(label="bug", interval=30.0)
        poller = GitHubPoller(config)
        assert poller.config.label == "bug"
        assert poller.config.interval == 30.0

    def test_interval_property(self):
        from pyralph.fetch_ready_issues import GitHubPoller
        poller = GitHubPoller()
        assert poller.interval == 60.0
        poller.interval = 30.0
        assert poller.interval == 30.0

    def test_interval_validation(self):
        from pyralph.fetch_ready_issues import GitHubPoller
        poller = GitHubPoller()
        import pytest
        with pytest.raises(ValueError):
            poller.interval = 0
        with pytest.raises(ValueError):
            poller.interval = -10

    def test_is_running_initial(self):
        from pyralph.fetch_ready_issues import GitHubPoller
        poller = GitHubPoller()
        assert not poller.is_running

    def test_seen_issues(self):
        from pyralph.fetch_ready_issues import GitHubPoller
        poller = GitHubPoller()
        assert poller.seen_issues == set()

    def test_reset_seen_issues(self):
        from pyralph.fetch_ready_issues import GitHubPoller
        poller = GitHubPoller()
        poller._seen_issue_numbers.add(1)
        poller._seen_issue_numbers.add(2)
        poller.reset_seen_issues()
        assert poller.seen_issues == set()


class TestWatcherCLI(IssueWatcherTestCase):
    def test_parser_creation(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        parser = create_watcher_parser()
        assert parser is not None
        assert parser.prog == "ralph-watch"

    def test_start_command_defaults(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = parser = create_watcher_parser().parse_args(["start"])
        assert args.command == "start"
        assert args.label == "ready"
        assert args.interval == 60.0
        assert args.agent == "claude"

    def test_start_command_custom(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = create_watcher_parser().parse_args([
            "start",
            "--label", "bug",
            "--interval", "30",
            "--agent", "copilot",
            "--no-hooks",
            "--mark-issues"
        ])
        assert args.label == "bug"
        assert args.interval == 30.0
        assert args.agent == "copilot"
        assert args.no_hooks
        assert args.mark_issues

    def test_stop_command(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = create_watcher_parser().parse_args(["stop"])
        assert args.command == "stop"

    def test_status_command(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = create_watcher_parser().parse_args(["status"])
        assert args.command == "status"

    def test_status_json_flag(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = create_watcher_parser().parse_args(["status", "--json"])
        assert args.json_output

    def test_poll_command(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = create_watcher_parser().parse_args(["poll", "--label", "bug"])
        assert args.command == "poll"
        assert args.label == "bug"

    def test_process_command(self):
        from pyralph.fetch_ready_issues import create_watcher_parser
        args = create_watcher_parser().parse_args(["process", "--agent", "copilot", "--no-hooks"])
        assert args.command == "process"
        assert args.agent == "copilot"
        assert args.no_hooks

    def test_no_command_returns_1(self):
        from pyralph.fetch_ready_issues import watcher_main
        with patch('sys.stdout', new_callable=StringIO):
            result = watcher_main([])
        assert result == 1
