#!/usr/bin/env python3
"""Shell module for Ralph.

This module contains the Shell class that provides subprocess execution
functionality for the Ralph CLI tool.
"""
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import CONF


@dataclass
class ExplorationResult:
    """Result from codebase exploration with metadata."""
    file_tree: str
    files_examined: int
    truncated: bool
    max_depth_reached: int


class Shell:
    """Safe wrapper for subprocess calls."""

    @staticmethod
    def run(command: str, timeout: int = 30) -> Tuple[str, str, int]:
        """
        Execute a shell command and capture its output.

        Args:
            command: The shell command to execute
            timeout: Maximum seconds to wait for command completion

        Returns:
            Tuple of (stdout, stderr, return_code)

        Security Note:
            This method uses shell=True which enables shell features (pipes,
            wildcards, variable expansion) but introduces command injection
            risks if `command` contains unsanitized user input.

            Safe usage (internal/trusted sources):
                - Hardcoded commands (e.g., "pytest", "tree -L 2")
                - Commands from configuration files controlled by the user
                - Agent-generated commands (trusted AI output)

            Unsafe usage (AVOID):
                - Commands built from external/untrusted input
                - Commands containing unvalidated user data

            This is acceptable here because:
                1. Commands originate from trusted sources (config, agents)
                2. The tool runs locally with user's own permissions
                3. Shell features (pipes, globs) are required for functionality
        """
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, encoding='utf-8', timeout=timeout
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command Timed Out", 1
        except Exception as e:
            return "", str(e), 1

    # Default exclusion patterns for file tree
    DEFAULT_TREE_IGNORE = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']

    @staticmethod
    def get_file_tree(depth: int = 2, ignore: Optional[List[str]] = None) -> str:
        """
        Generate a file tree representation of the project directory.

        Args:
            depth: Maximum directory depth to traverse (default: 2)
            ignore: List of directory/file patterns to exclude (default: node_modules, venv, .git, .ralph, __pycache__)

        Returns:
            String representation of the directory tree
        """
        if ignore is None:
            ignore = Shell.DEFAULT_TREE_IGNORE

        # Build the ignore pattern for tree command
        ignore_pattern = '|'.join(ignore) if ignore else ''

        # We explicitly list '.' to ensure we are looking at CWD
        cmd = f"tree -L {depth} --noreport"
        if ignore_pattern:
            cmd += f" -I '{ignore_pattern}'"
        stdout, _, code = Shell.run(cmd)
        if code == 0 and stdout.strip():
            return stdout

        # Fallback python walker using CWD
        ignore_set = set(ignore) if ignore else set()
        lines = []
        for path in CONF.BASE_DIR.glob('*'):
            if path.name not in ignore_set:
                lines.append(f"├── {path.name}")
        return "\n".join(lines)

    @staticmethod
    def explore_codebase(
        depth: Optional[int] = None,
        files_limit: Optional[int] = None,
        ignore: Optional[List[str]] = None,
        thorough: bool = False
    ) -> ExplorationResult:
        """
        Explore the codebase with configurable depth and file limits.

        Args:
            depth: Maximum directory traversal depth from project root.
                   None uses CONF.DEFAULT_EXPLORE_DEPTH.
            files_limit: Maximum number of files to examine.
                         None uses CONF.DEFAULT_EXPLORE_FILES_LIMIT.
            ignore: List of directory/file patterns to exclude.
            thorough: If True, disables all limits for full analysis.

        Returns:
            ExplorationResult containing file tree and metadata.
        """
        if ignore is None:
            ignore = Shell.DEFAULT_TREE_IGNORE

        # Apply defaults unless thorough mode
        if thorough:
            effective_depth = None
            effective_limit = None
        else:
            effective_depth = depth if depth is not None else CONF.DEFAULT_EXPLORE_DEPTH
            effective_limit = files_limit if files_limit is not None else CONF.DEFAULT_EXPLORE_FILES_LIMIT

        ignore_set = set(ignore)
        lines = []
        files_examined = 0
        max_depth_reached = 0
        truncated = False

        def _walk_directory(directory, current_depth: int, prefix: str = "") -> bool:
            """Walk directory tree, returning False if limit hit."""
            nonlocal files_examined, max_depth_reached, truncated

            if effective_depth is not None and current_depth > effective_depth:
                return True

            max_depth_reached = max(max_depth_reached, current_depth)

            try:
                entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError:
                return True
            except OSError:
                return True

            for i, entry in enumerate(entries):
                if entry.name in ignore_set:
                    continue

                if effective_limit is not None and files_examined >= effective_limit:
                    truncated = True
                    return False

                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{entry.name}")
                files_examined += 1

                if entry.is_dir():
                    extension = "    " if is_last else "│   "
                    if not _walk_directory(entry, current_depth + 1, prefix + extension):
                        return False

            return True

        # Start walking from BASE_DIR
        lines.append(".")
        _walk_directory(CONF.BASE_DIR, 1)

        file_tree = "\n".join(lines)

        return ExplorationResult(
            file_tree=file_tree,
            files_examined=files_examined,
            truncated=truncated,
            max_depth_reached=max_depth_reached
        )
