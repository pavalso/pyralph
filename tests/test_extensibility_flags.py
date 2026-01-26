from .helpers import TempConfigTestCase


class TestExtensibilityFlags(TempConfigTestCase):
    def test_pre_post_commands(self):
        orch = self.create_mock_orchestrator(pre=["echo before"], post=["echo after"])
        assert orch._pre_commands == ["echo before"]
        assert orch._post_commands == ["echo after"]

    def test_plugin_paths_stored(self):
        orch = self.create_mock_orchestrator(plugin=["/path/to/plugin.py"])
        assert orch._plugin_paths == ["/path/to/plugin.py"]
