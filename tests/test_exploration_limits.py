"""Tests for the exploration depth and file limit functionality.

Tests for --explore-depth, --explore-files-limit, and --explore-thorough flags
that control codebase exploration behavior.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyralph.config import CONF
from pyralph.shell import Shell, ExplorationResult
from pyralph.exploration_context import (
    ExplorationContextManager,
    create_exploration_context,
)


class TestExplorationResult:
    """Tests for ExplorationResult dataclass."""

    def test_exploration_result_fields(self):
        """Test ExplorationResult has expected fields."""
        result = ExplorationResult(
            file_tree="├── src",
            files_examined=10,
            truncated=False,
            max_depth_reached=3
        )
        assert result.file_tree == "├── src"
        assert result.files_examined == 10
        assert result.truncated is False
        assert result.max_depth_reached == 3

    def test_exploration_result_truncated(self):
        """Test ExplorationResult with truncated flag."""
        result = ExplorationResult(
            file_tree=".",
            files_examined=1000,
            truncated=True,
            max_depth_reached=5
        )
        assert result.truncated is True
        assert result.files_examined == 1000


class TestShellExploreCodebase:
    """Tests for Shell.explore_codebase method."""

    @pytest.fixture(autouse=True)
    def setup(self, temp_config):
        """Set up test environment."""
        self.temp_config = temp_config
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)

    def _create_directory_structure(self, structure, base_path=None):
        """Helper to create a directory structure for testing.

        Args:
            structure: Dict mapping paths to 'dir' or 'file'
            base_path: Base directory (defaults to CONF.BASE_DIR)
        """
        if base_path is None:
            base_path = CONF.BASE_DIR

        for path, ptype in structure.items():
            full_path = base_path / path
            if ptype == 'dir':
                full_path.mkdir(parents=True, exist_ok=True)
            else:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(f"# {path}", encoding='utf-8')

    def test_explore_with_default_limits(self):
        """GIVEN no limits passed WHEN explore_codebase runs THEN defaults apply."""
        self._create_directory_structure({
            'src/main.py': 'file',
            'tests/test_main.py': 'file',
        })

        result = Shell.explore_codebase()

        assert isinstance(result, ExplorationResult)
        assert result.files_examined >= 2
        assert result.truncated is False

    def test_explore_depth_limit_stops_traversal(self):
        """GIVEN --explore-depth 1 WHEN exploration runs THEN stops at depth 1."""
        # Create nested structure
        self._create_directory_structure({
            'level1/level2/level3/deep.py': 'file',
            'level1/shallow.py': 'file',
            'top.py': 'file',
        })

        result = Shell.explore_codebase(depth=1)

        # Should only reach depth 1
        assert result.max_depth_reached <= 1
        # Deep files should not be in tree
        assert 'level3' not in result.file_tree
        assert 'deep.py' not in result.file_tree

    def test_explore_files_limit_stops_at_m_files(self):
        """GIVEN --explore-files-limit 5 WHEN exploration runs THEN stops after 5 files."""
        # Create many files
        self._create_directory_structure({
            f'file{i}.txt': 'file' for i in range(20)
        })

        result = Shell.explore_codebase(files_limit=5)

        assert result.files_examined == 5
        assert result.truncated is True

    def test_explore_thorough_disables_limits(self):
        """GIVEN --explore-thorough WHEN exploration runs THEN limits are disabled."""
        # Create structure that would hit default limits
        self._create_directory_structure({
            f'file{i}.txt': 'file' for i in range(50)
        })

        result = Shell.explore_codebase(
            depth=1,
            files_limit=10,
            thorough=True
        )

        # Should examine all files despite low limits
        assert result.files_examined >= 50
        assert result.truncated is False

    def test_explore_respects_ignore_patterns(self):
        """GIVEN ignore patterns WHEN exploration runs THEN ignores those paths."""
        self._create_directory_structure({
            'src/main.py': 'file',
            'node_modules/dep.js': 'file',
            '.git/config': 'file',
        })

        result = Shell.explore_codebase(
            ignore=['node_modules', '.git']
        )

        assert 'node_modules' not in result.file_tree
        assert 'dep.js' not in result.file_tree
        assert '.git' not in result.file_tree

    def test_explore_returns_truncated_metadata(self):
        """GIVEN files limit hit WHEN results compiled THEN truncated is True."""
        self._create_directory_structure({
            f'file{i}.txt': 'file' for i in range(100)
        })

        result = Shell.explore_codebase(files_limit=10)

        assert result.truncated is True
        assert result.files_examined == 10


class TestExplorationContextWithLimits:
    """Tests for exploration context with truncation metadata."""

    @pytest.fixture(autouse=True)
    def setup(self, temp_config):
        """Set up test environment."""
        self.temp_config = temp_config
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        self.context_path = CONF.EXPLORATION_CONTEXT_FILE
        self.manager = ExplorationContextManager(self.context_path)

    def test_create_context_with_truncation(self):
        """Test create_exploration_context includes truncation fields."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'},
            truncated=True,
            files_examined=1000
        )

        assert context['truncated'] is True
        assert context['files_examined'] == 1000

    def test_create_context_defaults_truncated_false(self):
        """Test truncated defaults to False."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )

        assert context['truncated'] is False
        assert context['files_examined'] is None

    def test_save_and_load_with_truncation(self):
        """Test save/load preserves truncation metadata."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'},
            truncated=True,
            files_examined=500
        )
        self.manager.save(context)

        loaded = self.manager.load()

        assert loaded['truncated'] is True
        assert loaded['files_examined'] == 500


class TestConfigDefaults:
    """Tests for default exploration configuration values."""

    def test_default_explore_depth(self):
        """GIVEN no flags WHEN checking defaults THEN depth=10."""
        assert CONF.DEFAULT_EXPLORE_DEPTH == 10

    def test_default_explore_files_limit(self):
        """GIVEN no flags WHEN checking defaults THEN files_limit=1000."""
        assert CONF.DEFAULT_EXPLORE_FILES_LIMIT == 1000


class TestCLIExploreFlags:
    """Tests for CLI argument parsing of exploration flags."""

    def test_explore_depth_passed_to_orchestrator(self):
        """GIVEN --explore-depth 5 WHEN main runs THEN orchestrator receives explore_depth=5."""
        from pyralph import main

        with patch('pyralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--explore-depth', '5']):
                main()

            call_kwargs = mock_orch.call_args[1]
            assert call_kwargs.get('explore_depth') == 5

    def test_explore_files_limit_passed_to_orchestrator(self):
        """GIVEN --explore-files-limit 500 WHEN main runs THEN orchestrator receives it."""
        from pyralph import main

        with patch('pyralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--explore-files-limit', '500']):
                main()

            call_kwargs = mock_orch.call_args[1]
            assert call_kwargs.get('explore_files_limit') == 500

    def test_explore_thorough_passed_to_orchestrator(self):
        """GIVEN --explore-thorough WHEN main runs THEN orchestrator receives explore_thorough=True."""
        from pyralph import main

        with patch('pyralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph', '--explore-thorough']):
                main()

            call_kwargs = mock_orch.call_args[1]
            assert call_kwargs.get('explore_thorough') is True

    def test_default_explore_flags_are_none(self):
        """GIVEN no explore flags WHEN main runs THEN orchestrator receives None."""
        from pyralph import main

        with patch('pyralph.RalphOrchestrator') as mock_orch:
            mock_orch.return_value = MagicMock()
            with patch('sys.argv', ['ralph']):
                main()

            call_kwargs = mock_orch.call_args[1]
            assert call_kwargs.get('explore_depth') is None
            assert call_kwargs.get('explore_files_limit') is None
            assert call_kwargs.get('explore_thorough') is False


class TestPhaseRunnerExploration:
    """Tests for PhaseRunner exploration with limits."""

    @pytest.fixture(autouse=True)
    def setup(self, temp_config):
        """Set up test environment."""
        self.temp_config = temp_config
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)

    def test_phase_runner_uses_explore_depth(self):
        """GIVEN explore_depth=3 WHEN PhaseRunner explores THEN Shell receives depth=3."""
        from pyralph.phase_runner import PhaseRunner

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_shell = MagicMock()
        mock_shell.DEFAULT_TREE_IGNORE = ['node_modules', '.git']
        mock_shell.explore_codebase.return_value = ExplorationResult(
            file_tree="├── src",
            files_examined=10,
            truncated=False,
            max_depth_reached=3
        )

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=MagicMock(),
            shell_module=mock_shell,
            prd_manager=MagicMock(),
            json_utils=MagicMock(),
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=MagicMock(),
            event_type_class=MagicMock(),
            config=CONF,
            explore_depth=3,
        )

        runner._explore_codebase()

        mock_shell.explore_codebase.assert_called_once()
        call_kwargs = mock_shell.explore_codebase.call_args[1]
        assert call_kwargs['depth'] == 3

    def test_phase_runner_uses_explore_files_limit(self):
        """GIVEN explore_files_limit=100 WHEN PhaseRunner explores THEN Shell receives it."""
        from pyralph.phase_runner import PhaseRunner

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_shell = MagicMock()
        mock_shell.DEFAULT_TREE_IGNORE = ['node_modules', '.git']
        mock_shell.explore_codebase.return_value = ExplorationResult(
            file_tree="├── src",
            files_examined=100,
            truncated=True,
            max_depth_reached=5
        )

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=MagicMock(),
            shell_module=mock_shell,
            prd_manager=MagicMock(),
            json_utils=MagicMock(),
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=MagicMock(),
            event_type_class=MagicMock(),
            config=CONF,
            explore_files_limit=100,
        )

        runner._explore_codebase()

        mock_shell.explore_codebase.assert_called_once()
        call_kwargs = mock_shell.explore_codebase.call_args[1]
        assert call_kwargs['files_limit'] == 100

    def test_phase_runner_uses_explore_thorough(self):
        """GIVEN explore_thorough=True WHEN PhaseRunner explores THEN Shell receives it."""
        from pyralph.phase_runner import PhaseRunner

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_shell = MagicMock()
        mock_shell.DEFAULT_TREE_IGNORE = ['node_modules', '.git']
        mock_shell.explore_codebase.return_value = ExplorationResult(
            file_tree="├── src",
            files_examined=5000,
            truncated=False,
            max_depth_reached=15
        )

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=MagicMock(),
            shell_module=mock_shell,
            prd_manager=MagicMock(),
            json_utils=MagicMock(),
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=MagicMock(),
            event_type_class=MagicMock(),
            config=CONF,
            explore_thorough=True,
        )

        runner._explore_codebase()

        mock_shell.explore_codebase.assert_called_once()
        call_kwargs = mock_shell.explore_codebase.call_args[1]
        assert call_kwargs['thorough'] is True

    def test_phase_runner_saves_truncation_metadata(self):
        """GIVEN truncated exploration WHEN context saved THEN includes truncated flag."""
        from pyralph.phase_runner import PhaseRunner

        mock_agent = MagicMock()
        mock_hooks = MagicMock()
        mock_logger = MagicMock()
        mock_shell = MagicMock()
        mock_shell.DEFAULT_TREE_IGNORE = ['node_modules', '.git']
        mock_shell.explore_codebase.return_value = ExplorationResult(
            file_tree="├── src",
            files_examined=1000,
            truncated=True,
            max_depth_reached=5
        )

        runner = PhaseRunner(
            agent=mock_agent,
            hooks=mock_hooks,
            logger=mock_logger,
            template_manager=MagicMock(),
            shell_module=mock_shell,
            prd_manager=MagicMock(),
            json_utils=MagicMock(),
            command_runner=MagicMock(),
            prd_processor=MagicMock(),
            event_class=MagicMock(),
            event_type_class=MagicMock(),
            config=CONF,
            explore_files_limit=1000,
        )

        file_tree, incomplete_paths, truncated, files_examined = runner._explore_codebase()

        assert truncated is True
        assert files_examined == 1000
