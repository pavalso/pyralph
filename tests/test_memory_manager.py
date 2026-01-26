from pathlib import Path

from pyralph.memory import MemoryManager

from .helpers import TempConfigTestCase


class TestMemoryManager(TempConfigTestCase):
    def setUp(self):
        super().setUp()
        self.memory = MemoryManager()

    def test_validate_memory(self):
        result = self.memory.validate_memory()
        assert set(result.keys()) == {'valid', 'corrupted', 'empty', 'total'}

    def test_validate_scenarios(self):
        cases = [
            (lambda: None, True, 0),
            (lambda: (CONF.MEMORY_DIR.mkdir(parents=True), (CONF.MEMORY_DIR/"t.md").write_text("c", encoding="utf-8")), True, 1),
            (lambda: (CONF.MEMORY_DIR.mkdir(parents=True), (CONF.MEMORY_DIR/"e.md").write_text("", encoding="utf-8")), False, 1),
        ]
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        for setup, exp_valid, exp_total in cases:
            self.tearDown()
            self.setUp()
            setup()
            result = self.memory.validate_memory()
            assert result['valid'] == exp_valid
            assert result['total'] == exp_total

    def test_get_structure(self):
        assert self.memory.get_structure() == "(Memory Empty)"
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("c", encoding="utf-8")
        assert "arch.md" in self.memory.get_structure()

    def test_extract_test_command(self):
        assert self.memory.extract_test_command() == "pytest"
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("Test Command: `npm test`\n", encoding="utf-8")
        assert self.memory.extract_test_command() == "npm test"

    def test_compile_patterns(self):
        patterns = ["*.md", "test_*"]
        compiled = MemoryManager._compile_patterns(patterns)
        assert len(compiled) == 2
        for p in compiled:
            assert p.pattern is not None

    def test_matches_compiled_full_path(self):
        patterns = ["*.md"]
        compiled = MemoryManager._compile_patterns(patterns)
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        test_file = CONF.MEMORY_DIR / "test.md"
        test_file.write_text("content", encoding="utf-8")
        assert MemoryManager._matches_compiled(test_file, compiled)

    def test_matches_compiled_no_match(self):
        patterns = ["*.txt"]
        compiled = MemoryManager._compile_patterns(patterns)
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        test_file = CONF.MEMORY_DIR / "test.md"
        test_file.write_text("content", encoding="utf-8")
        assert not MemoryManager._matches_compiled(test_file, compiled)

    def test_iter_memory_files_empty(self):
        files = list(MemoryManager._iter_memory_files())
        assert files == []

    def test_iter_memory_files_with_content(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "a.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "b.md").write_text("b", encoding="utf-8")
        files = list(MemoryManager._iter_memory_files())
        assert len(files) == 2

    def test_iter_memory_files_skips_hidden(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / ".hidden").write_text("h", encoding="utf-8")
        (CONF.MEMORY_DIR / "visible.md").write_text("v", encoding="utf-8")
        files = list(MemoryManager._iter_memory_files())
        assert len(files) == 1
        assert files[0].name == "visible.md"

    def test_get_filtered_files_empty(self):
        files = MemoryManager.get_filtered_files()
        assert files == []

    def test_get_filtered_files_include_pattern(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("n", encoding="utf-8")
        files = MemoryManager.get_filtered_files(include=["*.md"])
        assert len(files) == 1
        assert files[0].name == "arch.md"

    def test_get_filtered_files_exclude_pattern(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("n", encoding="utf-8")
        files = MemoryManager.get_filtered_files(exclude=["*.txt"])
        assert len(files) == 1
        assert files[0].name == "arch.md"

    def test_get_filtered_files_include_and_exclude(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "arch.md").write_text("a", encoding="utf-8")
        (CONF.MEMORY_DIR / "test_arch.md").write_text("t", encoding="utf-8")
        (CONF.MEMORY_DIR / "notes.txt").write_text("n", encoding="utf-8")
        files = MemoryManager.get_filtered_files(include=["*.md"], exclude=["test_*"])
        assert len(files) == 1
        assert files[0].name == "arch.md"

    def test_get_filtered_files_limit(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        for i in range(5):
            (CONF.MEMORY_DIR / f"file{i}.md").write_text(f"{i}", encoding="utf-8")
        files = MemoryManager.get_filtered_files(limit=2)
        assert len(files) == 2

    def test_get_filtered_files_sorted(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        (CONF.MEMORY_DIR / "zebra.md").write_text("z", encoding="utf-8")
        (CONF.MEMORY_DIR / "alpha.md").write_text("a", encoding="utf-8")
        files = MemoryManager.get_filtered_files()
        assert files[0].name == "alpha.md"
        assert files[1].name == "zebra.md"

    def test_matches_pattern_backward_compat(self):
        CONF = __import__("pyralph.config", fromlist=["CONF"]).CONF
        CONF.MEMORY_DIR.mkdir(parents=True)
        test_file = CONF.MEMORY_DIR / "test.md"
        test_file.write_text("content", encoding="utf-8")
        assert MemoryManager._matches_pattern(test_file, "*.md")
        assert not MemoryManager._matches_pattern(test_file, "*.txt")
