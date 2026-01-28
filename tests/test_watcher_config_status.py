from pyralph.fetch_ready_issues import WatcherConfig, WatcherStatus


class TestWatcherConfig:
    def test_defaults(self):
        config = WatcherConfig()
        assert config.label == "ready"
        assert config.poll_interval == 60.0
        assert config.agent_name == "claude"
        assert config.enable_hooks
        assert config.auto_process

    def test_custom_values(self):
        config = WatcherConfig(
            label="bug",
            poll_interval=30.0,
            agent_name="copilot",
            auto_process=False
        )
        assert config.label == "bug"
        assert config.poll_interval == 30.0
        assert config.agent_name == "copilot"
        assert not config.auto_process


class TestWatcherStatus:
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
        assert d["running"]
        assert d["pid"] == 12345
        assert d["issues_stored"] == 10
