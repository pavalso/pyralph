#!/usr/bin/env python3
"""Configuration module for Ralph.

This module contains the Config dataclass and CONF singleton that define
all path and limit configurations for the Ralph CLI tool.
"""
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Config:
    BASE_DIR: Path = Path.cwd()
    ROOT_DIR: Path = BASE_DIR / ".ralph"
    ARCHIVE_DIR: Path = ROOT_DIR / "archive"
    TEMPLATES_DIR: Path = ROOT_DIR / "templates"
    HOOKS_DIR: Path = ROOT_DIR / "hooks"
    QA_RULES_DIR: Path = ROOT_DIR / "qa"
    PRD_FILE: Path = ROOT_DIR / "prd.json"
    PROGRESS_FILE: Path = ROOT_DIR / "progress.txt"
    LOG_FILE: Path = ROOT_DIR / "ralph_log.txt"

    # Limits
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 600

    def ensure_directories(self) -> None:
        for path in [self.ROOT_DIR, self.ARCHIVE_DIR, self.TEMPLATES_DIR, self.HOOKS_DIR, self.QA_RULES_DIR]:
            path.mkdir(exist_ok=True, parents=True)


CONF = Config()
