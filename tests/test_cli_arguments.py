import argparse
from unittest.mock import MagicMock, patch

import pytest

from pyralph import main, get_version, list_agents


class TestCliArguments:
    def setup_method(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?")
        self.parser.add_argument("--accept-all", "-y", action="store_true")
        self.parser.add_argument("-v", "--verbose", action="count", default=0)
        self.parser.add_argument("--quiet", "-q", action="store_true")
        self.parser.add_argument("--no-color", action="store_true")
        self.parser.add_argument("--no-emoji", action="store_true")
        self.parser.add_argument("--agent", choices=list_agents(), default=list_agents()[0])
        self.parser.add_argument("--no-hooks", action="store_true")
        self.parser.add_argument("--hooks", nargs="+")
        self.parser.add_argument("--intent", type=str)
        self.parser.add_argument("--intent-file", type=str)
        self.parser.add_argument("--enhance-intent", action="store_true")
        self.parser.add_argument("--enhance-intent-strict", action="store_true")
        self.parser.add_argument("--prompt-file", type=str)
        self.parser.add_argument("--tree-depth", type=int, default=2)
        self.parser.add_argument("--tree-ignore", nargs="+")
        self.parser.add_argument("--test-cmd", type=str)
        self.parser.add_argument("--skip-verify", action="store_true")
        self.parser.add_argument("--retries", type=int)
        self.parser.add_argument("--timeout", type=int)
        self.parser.add_argument("--only", nargs="+")
        self.parser.add_argument("--except", dest="except_tasks", nargs="+")
        self.parser.add_argument("--resume", type=str)
        self.parser.add_argument("--non-interactive", action="store_true")
        self.parser.add_argument("--ci", action="store_true")
        self.parser.add_argument("--status-check", action="store_true")
        self.parser.add_argument("--parallel", type=int)
        self.parser.add_argument("--batch-size", type=int)
        self.parser.add_argument("--pre", nargs="+")
        self.parser.add_argument("--post", nargs="+")
        self.parser.add_argument("--plugin", nargs="+")
        self.parser.add_argument("--redact", nargs="+")
        self.parser.add_argument("--redact-file", type=str)
        self.parser.add_argument("--no-log-prompts", action="store_true")
        self.parser.add_argument("--no-log-responses", action="store_true")
        self.parser.add_argument("--schema", type=str)
        self.parser.add_argument("--min-criteria", type=int)
        self.parser.add_argument("--label", nargs="+")

    def test_phases(self):
        for phase in ["architect", "planner", "execute", "all"]:
            assert self.parser.parse_args([phase]).phase == phase
        assert self.parser.parse_args([]).phase == "all"

    def test_verbosity(self):
        assert self.parser.parse_args(["-v"]).verbose == 1
        assert self.parser.parse_args(["-vv"]).verbose == 2
        assert self.parser.parse_args(["-vvv"]).verbose == 3

    def test_flags_combined(self):
        args = self.parser.parse_args([
            "-vvv", "-q", "--no-color", "--no-emoji", "-y",
            "--intent", "test", "--tree-depth", "5", "--test-cmd", "npm test",
            "--only", "T-1", "T-2", "--retries", "3", "execute"
        ])
        assert args.verbose == 3
        assert args.quiet
        assert args.no_color
        assert args.intent == "test"
        assert args.tree_depth == 5
        assert args.test_cmd == "npm test"
        assert args.only == ["T-1", "T-2"]
        assert args.retries == 3

    def test_get_version_and_list_agents(self):
        assert isinstance(get_version(), str)
        agents = list_agents()
        assert "claude" in agents
        assert "copilot" in agents


class TestMainIntentValidation:
    def test_rejects_both_intent_flags(self):
        with patch('sys.argv', ['ralph', '--intent', 'Test', '--intent-file', 'f.txt']):
            with patch('pyralph.orchestrator.sys.exit') as mock_exit, patch('pyralph.logger.Logger.error'), patch('pyralph.RalphOrchestrator'):
                main()
                mock_exit.assert_called_with(1)


class TestFlagConflictValidation:
    """Tests for CLI flag conflict detection and warnings."""

    def test_only_and_resume_raises_error(self):
        """Given --only and --resume are both specified, a ValueError is raised."""
        with patch('sys.argv', ['ralph', '--only', 'T-1', '--resume', 'T-2']):
            with patch('pyralph.cli.sys.exit') as mock_exit:
                with patch('pyralph.logger.Logger.error') as mock_error:
                    with patch('pyralph.RalphOrchestrator'):
                        main()
                        mock_error.assert_called()
                        assert '--only' in str(mock_error.call_args) and '--resume' in str(mock_error.call_args)
                        mock_exit.assert_called_with(1)

    def test_skip_verify_with_retries_warns(self):
        """Given --skip-verify and --retries are both specified, a warning is logged."""
        with patch('sys.argv', ['ralph', '--skip-verify', '--retries', '3']):
            with patch('pyralph.logger.Logger.warning') as mock_warning:
                with patch('pyralph.RalphOrchestrator') as mock_orch:
                    mock_orch.return_value = MagicMock()
                    main()
                    mock_warning.assert_called()
                    assert '--retries' in str(mock_warning.call_args)

    def test_ci_and_non_interactive_info(self):
        """Given --ci and --non-interactive are both specified, an info message is logged."""
        with patch('sys.argv', ['ralph', '--ci', '--non-interactive']):
            with patch('pyralph.logger.Logger.info') as mock_info:
                with patch('pyralph.RalphOrchestrator') as mock_orch:
                    mock_orch.return_value = MagicMock()
                    main()
                    # Check that the redundancy message was logged
                    info_calls = [str(call) for call in mock_info.call_args_list]
                    assert any('redundant' in call for call in info_calls)

    def test_no_warnings_without_conflicts(self):
        """Given no conflicting flags, no warnings are emitted."""
        with patch('sys.argv', ['ralph', '--retries', '3']):
            with patch('pyralph.logger.Logger.warning') as mock_warning:
                with patch('pyralph.RalphOrchestrator') as mock_orch:
                    mock_orch.return_value = MagicMock()
                    main()
                    # No warnings should be called about flag conflicts
                    for call in mock_warning.call_args_list:
                        assert '--retries has no effect' not in str(call)

    def test_negative_retries_raises_error(self):
        """Given --retries with negative value, a validation error occurs."""
        with patch('sys.argv', ['ralph', '--retries', '-1']):
            with patch('pyralph.cli.sys.exit') as mock_exit:
                with patch('pyralph.logger.Logger.error') as mock_error:
                    with patch('pyralph.RalphOrchestrator'):
                        main()
                        mock_error.assert_called()
                        assert 'non-negative' in str(mock_error.call_args)
                        mock_exit.assert_called_with(1)

    def test_only_without_resume_no_error(self):
        """Given --only alone, no error is raised."""
        with patch('sys.argv', ['ralph', '--only', 'T-1', 'T-2']):
            with patch('pyralph.RalphOrchestrator') as mock_orch:
                mock_orch.return_value = MagicMock()
                main()
                # Should not raise, orchestrator should be called
                mock_orch.assert_called()

    def test_resume_without_only_no_error(self):
        """Given --resume alone, no error is raised."""
        with patch('sys.argv', ['ralph', '--resume', 'T-3']):
            with patch('pyralph.RalphOrchestrator') as mock_orch:
                mock_orch.return_value = MagicMock()
                main()
                # Should not raise, orchestrator should be called
                mock_orch.assert_called()


class TestMainCLIPassthrough:
    FLAG_TESTS = [
        (['--intent', 'test'], {'intent': 'test'}),
        (['--tree-depth', '5'], {'tree_depth': 5}),
        (['--test-cmd', 'npm test'], {'test_cmd': 'npm test'}),
        (['--skip-verify'], {'skip_verify': True}),
        (['--retries', '3'], {'retries': 3}),
        (['--timeout', '300'], {'timeout': 300}),
        (['--only', 'T-1', 'T-2'], {'only': ['T-1', 'T-2']}),
        (['--resume', 'T-3'], {'resume': 'T-3'}),
        (['--non-interactive'], {'non_interactive': True}),
        (['--ci'], {'ci': True}),
        (['--status-check'], {'status_check': True}),
        (['execute', '--pre', 'echo', 'before'], {'pre': ['echo', 'before']}),
        (['execute', '--post', 'echo', 'done'], {'post': ['echo', 'done']}),
        (['--schema', '/schema.json'], {'schema': '/schema.json'}),
        (['--min-criteria', '3'], {'min_criteria': 3}),
        (['--label', 'type=bug'], {'label': ['type=bug']}),
    ]

    @pytest.mark.parametrize("cli_args,expected_kwargs", FLAG_TESTS)
    def test_cli_passes_flags(self, cli_args, expected_kwargs):
        with patch('pyralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph'] + cli_args):
                main()
            call_kwargs = mock_orch.call_args[1]
            for key, value in expected_kwargs.items():
                assert call_kwargs.get(key) == value
