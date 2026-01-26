from .helpers import TempConfigTestCase


class TestHeadlessMode(TempConfigTestCase):
    def test_non_interactive_flag(self):
        orch = self.create_mock_orchestrator(non_interactive=True)
        assert orch._non_interactive

    def test_ci_mode_sets_flags(self):
        orch = self.create_mock_orchestrator(ci=True)
        assert orch._ci

    def test_status_check_flag(self):
        orch = self.create_mock_orchestrator(status_check=True)
        assert orch._status_check
