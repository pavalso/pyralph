"""Tests for the exploration context module.

Tests for ExplorationContextManager class that manages exploration_context.json
artifacts containing structured exploration findings.
"""
import json

import pytest

from pyralph.config import CONF
from pyralph.exploration_context import (
    ExplorationContextManager,
    create_exploration_context,
)


class TestExplorationContextManager:
    """Tests for ExplorationContextManager class."""

    @pytest.fixture(autouse=True)
    def setup(self, temp_config):
        """Set up test environment with temp_config fixture."""
        self.temp_config = temp_config
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        self.context_path = CONF.EXPLORATION_CONTEXT_FILE
        self.manager = ExplorationContextManager(self.context_path)

    def test_exists_false_when_no_file(self):
        """Test exists() returns False when file doesn't exist."""
        assert not self.manager.exists()

    def test_exists_true_when_file_present(self):
        """Test exists() returns True when file exists."""
        context = create_exploration_context(
            file_tree="├── src\n├── tests",
            exploration_summary={'user_intent': 'test'}
        )
        self.manager.save(context)
        assert self.manager.exists()

    def test_save_creates_file(self):
        """Test save() creates the context file."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)
        assert self.context_path.exists()

    def test_save_adds_version_if_missing(self):
        """Test save() adds version if not present."""
        context = {
            'timestamp': '2026-01-29T12:00:00',
            'file_tree': '├── src',
            'exploration_summary': {}
        }
        self.manager.save(context)
        loaded = self.manager.load()
        assert loaded['version'] == '1.0'

    def test_save_adds_timestamp_if_missing(self):
        """Test save() adds timestamp if not present."""
        context = {
            'version': '1.0',
            'file_tree': '├── src',
            'exploration_summary': {}
        }
        self.manager.save(context)
        loaded = self.manager.load()
        assert 'timestamp' in loaded

    def test_save_adds_content_hash(self):
        """Test save() computes and adds content hash."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)
        loaded = self.manager.load()
        assert 'content_hash' in loaded
        assert len(loaded['content_hash']) == 64  # SHA-256 hex length

    def test_load_returns_valid_context(self):
        """Test load() returns the saved context."""
        context = create_exploration_context(
            file_tree="├── src\n├── tests",
            exploration_summary={'user_intent': 'build feature'}
        )
        self.manager.save(context)
        loaded = self.manager.load()
        assert loaded['file_tree'] == "├── src\n├── tests"
        assert loaded['exploration_summary']['user_intent'] == 'build feature'

    def test_load_caches_result(self):
        """Test load() caches and returns same object."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)
        first_load = self.manager.load()
        # Modify file directly
        self.context_path.write_text('{"invalid": true}', encoding='utf-8')
        second_load = self.manager.load()
        assert first_load is second_load

    def test_load_raises_on_missing_file(self):
        """Test load() raises FileNotFoundError when file missing."""
        with pytest.raises(FileNotFoundError):
            self.manager.load()

    def test_load_raises_on_invalid_json(self):
        """Test load() raises JSONDecodeError on invalid JSON."""
        self.context_path.write_text('not valid json', encoding='utf-8')
        with pytest.raises(json.JSONDecodeError):
            self.manager.load()

    def test_load_raises_on_missing_required_field(self):
        """Test load() raises ValueError when required field is missing."""
        invalid_context = {
            'version': '1.0',
            'timestamp': '2026-01-29T12:00:00',
            # Missing 'file_tree' and 'exploration_summary'
        }
        self.context_path.write_text(json.dumps(invalid_context), encoding='utf-8')
        with pytest.raises(ValueError) as exc_info:
            self.manager.load()
        assert "missing required field" in str(exc_info.value)

    def test_load_raises_on_corrupted_hash(self):
        """Test load() raises ValueError when content hash doesn't match."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)

        # Tamper with the file content
        loaded = json.loads(self.context_path.read_text(encoding='utf-8'))
        loaded['file_tree'] = "TAMPERED"
        self.context_path.write_text(json.dumps(loaded), encoding='utf-8')

        self.manager.invalidate_cache()
        with pytest.raises(ValueError) as exc_info:
            self.manager.load()
        assert "content hash mismatch" in str(exc_info.value)

    def test_invalidate_cache_clears_cache(self):
        """Test invalidate_cache() clears the cached data."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)
        self.manager.load()
        assert self.manager._cache is not None
        self.manager.invalidate_cache()
        assert self.manager._cache is None

    def test_invalidate_cache_forces_disk_read(self):
        """Test invalidate_cache() causes next load() to read from disk."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'version': 'v1'}
        )
        self.manager.save(context)
        first_load = self.manager.load()

        # Create new context
        context2 = create_exploration_context(
            file_tree="├── src\n├── tests",
            exploration_summary={'version': 'v2'}
        )
        self.manager.save(context2)
        self.manager.invalidate_cache()
        second_load = self.manager.load()

        assert first_load['exploration_summary']['version'] == 'v1'
        assert second_load['exploration_summary']['version'] == 'v2'

    def test_delete_removes_file(self):
        """Test delete() removes the context file."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={}
        )
        self.manager.save(context)
        assert self.manager.exists()
        self.manager.delete()
        assert not self.manager.exists()

    def test_delete_clears_cache(self):
        """Test delete() clears the cached data."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={}
        )
        self.manager.save(context)
        self.manager.load()
        self.manager.delete()
        assert self.manager._cache is None

    def test_delete_no_error_when_file_missing(self):
        """Test delete() doesn't raise error when file doesn't exist."""
        assert not self.manager.exists()
        self.manager.delete()  # Should not raise

    def test_is_valid_true_for_valid_context(self):
        """Test is_valid() returns True for valid context."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)
        assert self.manager.is_valid()

    def test_is_valid_false_when_file_missing(self):
        """Test is_valid() returns False when file doesn't exist."""
        assert not self.manager.is_valid()

    def test_is_valid_false_for_invalid_json(self):
        """Test is_valid() returns False for invalid JSON."""
        self.context_path.write_text('not json', encoding='utf-8')
        assert not self.manager.is_valid()

    def test_is_valid_false_for_corrupted_hash(self):
        """Test is_valid() returns False for corrupted content."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={'test': 'data'}
        )
        self.manager.save(context)

        # Tamper with content
        loaded = json.loads(self.context_path.read_text(encoding='utf-8'))
        loaded['file_tree'] = "TAMPERED"
        self.context_path.write_text(json.dumps(loaded), encoding='utf-8')

        assert not self.manager.is_valid()


class TestCreateExplorationContext:
    """Tests for create_exploration_context helper function."""

    def test_creates_basic_context(self):
        """Test creates context with required fields."""
        context = create_exploration_context(
            file_tree="├── src\n├── tests",
            exploration_summary={'user_intent': 'test'}
        )
        assert context['file_tree'] == "├── src\n├── tests"
        assert context['exploration_summary']['user_intent'] == 'test'
        assert context['version'] == '1.0'
        assert 'timestamp' in context

    def test_includes_incomplete_paths(self):
        """Test includes incomplete_paths when provided."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={},
            incomplete_paths=['/path/to/inaccessible', '/another/path']
        )
        assert context['incomplete_paths'] == ['/path/to/inaccessible', '/another/path']

    def test_defaults_incomplete_paths_to_empty_list(self):
        """Test defaults incomplete_paths to empty list."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={}
        )
        assert context['incomplete_paths'] == []

    def test_includes_metadata(self):
        """Test includes metadata when provided."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={},
            metadata={'base_dir': '/project', 'custom_key': 'value'}
        )
        assert context['metadata']['base_dir'] == '/project'
        assert context['metadata']['custom_key'] == 'value'

    def test_defaults_metadata_to_empty_dict(self):
        """Test defaults metadata to empty dict."""
        context = create_exploration_context(
            file_tree="├── src",
            exploration_summary={}
        )
        assert context['metadata'] == {}
