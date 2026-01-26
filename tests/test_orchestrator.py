from unittest.mock import patch

import pytest

from pyralph import RalphOrchestrator
from pyralph.agents import get_agent
from pyralph.logger import Logger

from .helpers import TempConfigTestCase


class TestRalphOrchestrator(TempConfigTestCase):
    def test_init(self):
        mock_agent = self.create_mock_agent()
        with patch('pyralph.orchestrator.get_agent', return_value=mock_agent):
            orch = RalphOrchestrator(agent_name="mock")
            assert orch.agent is not None
            mock_agent.check_dependencies.assert_called_once()
            mock_agent.set_logger.assert_called_once_with(Logger)
            mock_agent.set_config.assert_called_once_with(__import__("pyralph.config", fromlist=["CONF"]).CONF)

    def test_init_deps_fail_exits(self):
        mock_agent = self.create_mock_agent(check_deps=False)
        with patch('pyralph.orchestrator.get_agent', return_value=mock_agent):
            with patch('pyralph.orchestrator.sys.exit') as mock_exit:
                RalphOrchestrator(agent_name="mock")
                mock_exit.assert_called_once_with(1)

    def test_init_ensures_directories(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        assert not CONF.ROOT_DIR.exists()
        with patch('pyralph.orchestrator.get_agent', return_value=self.create_mock_agent()):
            RalphOrchestrator(agent_name="mock")
            for p in [CONF.ROOT_DIR, CONF.ARCHIVE_DIR]:
                assert p.exists()

    def test_unknown_agent_raises(self):
        with pytest.raises(ValueError):
            get_agent("nonexistent_agent")

    def test_archive_prd(self):
        orch = self.create_mock_orchestrator()
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        prd_content = '{"id": "PRD-001"}'
        CONF.PRD_FILE.write_text(prd_content, encoding='utf-8')
        orch._archive_prd()
        assert not CONF.PRD_FILE.exists()
        archived = list(CONF.ARCHIVE_DIR.glob("prd_*.json"))
        assert len(archived) == 1
        assert archived[0].read_text(encoding='utf-8') == prd_content

    def test_architect_requires_arch_md(self):
        mock_agent = self.create_mock_agent()
        mock_agent.run.return_value = (True, "STATUS: CREATED ARCHITECTURE.md", None)
        orch = self.create_mock_orchestrator(mock_agent=mock_agent)
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        (CONF.BASE_DIR / "ARCH.md").unlink(missing_ok=True)
        with patch('pyralph.orchestrator.sys.exit') as mock_exit, patch('pyralph.logger.Logger.info'):
            orch.run_architect("test")
            mock_exit.assert_called_once_with(1)
        (CONF.BASE_DIR / "ARCH.md").write_text("# Arch", encoding="utf-8")
        with patch('pyralph.orchestrator.sys.exit') as mock_exit, patch('pyralph.logger.Logger.info'):
            orch.run_architect("test")
            mock_exit.assert_not_called()

class TestOrchestratorFlags(TempConfigTestCase):
    FLAG_CASES = [
        ('tree_depth', '_tree_depth', 5, 2),
        ('tree_ignore', '_tree_ignore', ["build"], None),
        ('test_cmd', '_test_cmd_override', "npm test", None),
        ('skip_verify', '_skip_verify', True, False),
        ('retries', '_retries_override', 5, None),
        ('timeout', '_timeout_override', 300, None),
        ('only', '_only_tasks', ["T-1"], None),
        ('except_tasks', '_except_tasks', ["T-2"], None),
        ('resume', '_resume_from', "T-3", None),
        ('intent', '_intent', "Test intent", None),
        ('intent_file', '_intent_file_override', "/prompt", None),
        ('include', '_include_patterns', ["arch"], None),
        ('exclude', '_exclude_patterns', ["tasks"], None),
        ('non_interactive', '_non_interactive', True, False),
        ('ci', '_ci', True, False),
        ('status_check', '_status_check', True, False),
        ('pre', '_pre_commands', ["echo"], []),
        ('post', '_post_commands', ["done"], []),
        ('plugin', '_plugin_paths', ["/p.py"], []),
        ('schema', '_schema_path', "/schema.json", None),
        ('min_criteria', '_min_criteria', 3, None),
        ('label', '_labels', ["type=bug"], []),
    ]

    def test_flag_storage(self):
        for kwarg, attr, value, default in self.FLAG_CASES:
            orch = self.create_mock_orchestrator(**{kwarg: value})
            assert getattr(orch, attr) == value

    def test_flag_defaults(self):
        orch = self.create_mock_orchestrator()
        for kwarg, attr, value, default in self.FLAG_CASES:
            if default is not None or attr in ('_tree_depth', '_skip_verify', '_git_enabled',
                                               '_non_interactive', '_ci', '_status_check', '_labels',
                                               '_pre_commands', '_post_commands', '_plugin_paths'):
                assert getattr(orch, attr) == default


class TestOrchestratorIntentHandling(TempConfigTestCase):
    def test_get_intent_inline(self):
        orch = self.create_mock_orchestrator(intent="Build CLI")
        assert orch._get_intent() == "Build CLI"

    def test_get_intent_from_file(self):
        intent_file = self.temp_path / "intent.txt"
        intent_file.write_text("Build webapp", encoding='utf-8')
        orch = self.create_mock_orchestrator(intent_file=str(intent_file))
        assert orch._get_intent() == "Build webapp"

    def test_get_intent_file_not_found_exits(self):
        orch = self.create_mock_orchestrator(intent_file="/nonexistent")
        with patch('pyralph.orchestrator.sys.exit', side_effect=SystemExit(1)), patch('pyralph.logger.Logger.error'):
            with pytest.raises(SystemExit):
                orch._get_intent()

    def test_get_intent_empty_file_exits(self):
        intent_file = self.temp_path / "empty.txt"
        intent_file.write_text("", encoding='utf-8')
        orch = self.create_mock_orchestrator(intent_file=str(intent_file))
        with patch('pyralph.orchestrator.sys.exit', side_effect=SystemExit(1)), patch('pyralph.logger.Logger.error'):
            with pytest.raises(SystemExit):
                orch._get_intent()
