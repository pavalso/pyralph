import tempfile
from pathlib import Path

from pyralph.config import Config


class TestConfig:
    PATH_CASES = [
        ("BASE_DIR", Path.cwd(), None), ("ROOT_DIR", ".ralph", "BASE_DIR"),
        ("ARCHIVE_DIR", "archive", "ROOT_DIR"),
        ("TEMPLATES_DIR", "templates", "ROOT_DIR"), ("HOOKS_DIR", "hooks", "ROOT_DIR"),
        ("PRD_FILE", "prd.json", "ROOT_DIR"), ("PROGRESS_FILE", "progress.txt", "ROOT_DIR"),
        ("LOG_FILE", "ralph_log.txt", "ROOT_DIR"),
    ]
    SCALAR_CASES = [("MAX_RETRIES", 3), ("TIMEOUT_SECONDS", 600)]
    CREATED_DIRS = ["ROOT_DIR", "ARCHIVE_DIR", "TEMPLATES_DIR", "HOOKS_DIR"]

    def test_defaults(self):
        config = Config()
        for attr, suffix, base in self.PATH_CASES:
            expected = suffix if base is None else getattr(config, base) / suffix
            assert getattr(config, attr) == expected
        for attr, expected in self.SCALAR_CASES:
            assert getattr(config, attr) == expected

    def test_ensure_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = Config(BASE_DIR=base, ROOT_DIR=base/".ralph",
                            ARCHIVE_DIR=base/".ralph"/"archive", TEMPLATES_DIR=base/".ralph"/"templates",
                            HOOKS_DIR=base/".ralph"/"hooks")
            for d in self.CREATED_DIRS:
                assert not getattr(config, d).exists()
            config.ensure_directories()
            config.ensure_directories()
            for d in self.CREATED_DIRS:
                assert getattr(config, d).is_dir()
