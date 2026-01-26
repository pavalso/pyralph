import json
from pathlib import Path

from pyralph.prd import PRDManager

from .helpers import TempConfigTestCase


class TestPRDManager(TempConfigTestCase):
    def setUp(self):
        super().setUp()
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.ROOT_DIR.mkdir(parents=True, exist_ok=True)
        self.prd_path = CONF.PRD_FILE
        self.manager = PRDManager(self.prd_path)

    def test_exists_false_when_no_file(self):
        assert not self.manager.exists()

    def test_exists_true_when_file_present(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        assert self.manager.exists()

    def test_load_parses_json(self):
        prd_data = {"id": "PRD-001", "userStories": []}
        self.prd_path.write_text(json.dumps(prd_data), encoding='utf-8')
        loaded = self.manager.load()
        assert loaded["id"] == "PRD-001"
        assert loaded["userStories"] == []

    def test_load_caches_result(self):
        prd_data = {"id": "PRD-001"}
        self.prd_path.write_text(json.dumps(prd_data), encoding='utf-8')
        first_load = self.manager.load()
        self.prd_path.write_text('{"id": "PRD-002"}', encoding='utf-8')
        second_load = self.manager.load()
        assert first_load == second_load
        assert second_load["id"] == "PRD-001"

    def test_read_raw_returns_string(self):
        content = '{"id": "PRD-001", "description": "Test"}'
        self.prd_path.write_text(content, encoding='utf-8')
        raw = self.manager.read_raw()
        assert raw == content

    def test_read_raw_caches_result(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        first_read = self.manager.read_raw()
        self.prd_path.write_text('{"id": "PRD-002"}', encoding='utf-8')
        second_read = self.manager.read_raw()
        assert first_read == second_read

    def test_save_writes_to_disk(self):
        prd_data = {"id": "PRD-001", "userStories": [{"id": "T-001"}]}
        self.manager.save(prd_data)
        content = self.prd_path.read_text(encoding='utf-8')
        loaded = json.loads(content)
        assert loaded["id"] == "PRD-001"

    def test_save_updates_cache(self):
        prd_data = {"id": "PRD-001"}
        self.manager.save(prd_data)
        loaded = self.manager.load()
        assert loaded["id"] == "PRD-001"

    def test_save_formats_with_indent(self):
        prd_data = {"id": "PRD-001"}
        self.manager.save(prd_data)
        content = self.prd_path.read_text(encoding='utf-8')
        assert '\n' in content
        assert '  ' in content

    def test_invalidate_cache_clears_both_caches(self):
        prd_data = {"id": "PRD-001"}
        self.prd_path.write_text(json.dumps(prd_data), encoding='utf-8')
        self.manager.load()
        self.manager.read_raw()
        assert self.manager._cache is not None
        assert self.manager._raw_cache is not None
        self.manager.invalidate_cache()
        assert self.manager._cache is None
        assert self.manager._raw_cache is None

    def test_invalidate_cache_forces_disk_read(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        first_load = self.manager.load()
        self.prd_path.write_text('{"id": "PRD-002"}', encoding='utf-8')
        self.manager.invalidate_cache()
        second_load = self.manager.load()
        assert first_load["id"] == "PRD-001"
        assert second_load["id"] == "PRD-002"

    def test_delete_removes_file(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        assert self.manager.exists()
        self.manager.delete()
        assert not self.manager.exists()

    def test_delete_clears_cache(self):
        self.prd_path.write_text('{"id": "PRD-001"}', encoding='utf-8')
        self.manager.load()
        self.manager.delete()
        assert self.manager._cache is None
        assert self.manager._raw_cache is None

    def test_delete_no_error_when_file_missing(self):
        assert not self.manager.exists()
        self.manager.delete()

    def test_load_raises_on_missing_file(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            self.manager.load()

    def test_load_raises_on_invalid_json(self):
        import pytest
        self.prd_path.write_text('not valid json', encoding='utf-8')
        with pytest.raises(json.JSONDecodeError):
            self.manager.load()
