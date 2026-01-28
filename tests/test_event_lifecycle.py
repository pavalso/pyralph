from unittest.mock import patch

from pyralph.config import CONF
from pyralph.hooks import EventType


class TestEventLifecycle:
    def test_phase_events_emitted(self, temp_config):
        events = []
        mock_agent = temp_config.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED ARCHITECTURE.md", None)
        orch = temp_config.create_mock_orchestrator(mock_agent=mock_agent)
        orch.hooks.register_hook("capture", lambda e: events.append(e.event_type), ["PHASE_START", "PHASE_END"])
        (CONF.BASE_DIR / "ARCH.md").write_text("c", encoding="utf-8")
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")
        with patch('pyralph.logger.Logger.info'), patch('pyralph.shell.Shell.get_file_tree', return_value="tree"):
            orch.run_architect("test")
        assert EventType.PHASE_START in events
        assert EventType.PHASE_END in events
