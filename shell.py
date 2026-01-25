#!/usr/bin/env python3
"""Shell module for Ralph.

This module contains the Shell class that provides subprocess execution
functionality for the Ralph CLI tool.
"""
import subprocess
from typing import List, Optional, Tuple

from config import CONF


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
