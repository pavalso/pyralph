#!/usr/bin/env python3
"""Memory module for Ralph.

This module contains the MemoryManager class that provides context and memory
file operations including filtering, pattern matching, and validation.
"""
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import CONF
from logger import Logger


class MemoryManager:
    """Manager for memory file operations with filtering and pattern matching."""

    @staticmethod
    def validate_memory() -> Dict[str, Any]:
        """Validate all memory files for corruption and emptiness.

        Returns:
            Dictionary with validation results:
            - valid: True if all files are readable and non-empty
            - corrupted: List of paths to files with encoding errors
            - empty: List of paths to empty files
            - total: Total number of files checked
        """
        result = {'valid': True, 'corrupted': [], 'empty': [], 'total': 0}
        if not CONF.MEMORY_DIR.exists():
            return result
        for path in CONF.MEMORY_DIR.rglob('*'):
            if not path.is_file() or path.name.startswith('.'):
                continue
            result['total'] += 1
            try:
                if not path.read_text(encoding='utf-8').strip():
                    result['empty'].append(str(path.relative_to(CONF.BASE_DIR)))
                    result['valid'] = False
            except (OSError, UnicodeDecodeError) as e:
                Logger.debug(f"Failed to read memory file {path}: {type(e).__name__}: {e}")
                result['corrupted'].append(str(path.relative_to(CONF.BASE_DIR)))
                result['valid'] = False
        return result

    @staticmethod
    def _compile_patterns(patterns: List[str]) -> List['re.Pattern']:
        """
        Compile glob patterns into regex patterns for efficient repeated matching.

        For each pattern, compiles three variants for matching:
        1. Full path pattern
        2. Filename-only pattern
        3. Partial path pattern (*/{pattern})

        Args:
            patterns: List of glob patterns to compile

        Returns:
            List of compiled regex patterns
        """
        import fnmatch
        import re
        compiled = []
        for pattern in patterns:
            regex_full = fnmatch.translate(pattern)
            regex_name = fnmatch.translate(pattern)
            regex_partial = fnmatch.translate(f"*/{pattern}")
            combined = f"({regex_full})|({regex_name})|({regex_partial})"
            compiled.append(re.compile(combined))
        return compiled

    @staticmethod
    def _matches_compiled(path: Path, compiled_patterns: List['re.Pattern']) -> bool:
        """
        Check if a path matches any of the pre-compiled patterns.

        Args:
            path: Path to check
            compiled_patterns: List of compiled regex patterns

        Returns:
            True if path matches any pattern
        """
        from pathlib import PurePosixPath
        path_posix = PurePosixPath(path.as_posix())
        path_str = str(path_posix)
        name = path.name
        test_str = f"{path_str}\n{name}\n{path_str}"
        return any(p.search(test_str) for p in compiled_patterns)

    @staticmethod
    def _matches_pattern(path: Path, pattern: str) -> bool:
        """Check if a path matches a glob pattern."""
        from fnmatch import fnmatch
        from pathlib import PurePosixPath
        path_posix = PurePosixPath(path.as_posix())
        path_str = str(path_posix)
        name = path.name
        return fnmatch(path_str, pattern) or fnmatch(name, pattern) or fnmatch(path_str, f"*/{pattern}")

    @staticmethod
    def _iter_memory_files() -> 'Iterator[Path]':
        """
        Generator that yields memory files lazily.

        Yields:
            Path objects for each valid memory file
        """
        if not CONF.MEMORY_DIR.exists():
            return
        for p in CONF.MEMORY_DIR.rglob('*'):
            if p.is_file() and not p.name.startswith('.'):
                yield p

    @staticmethod
    def get_filtered_files(include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                           limit: Optional[int] = None) -> List[Path]:
        """
        Get filtered list of memory files based on include/exclude patterns and limit.

        Optimized for large file sets with:
        - Pre-compiled patterns for O(1) pattern matching per file
        - Single-pass filtering combining include/exclude checks
        - Lazy file enumeration via generator
        - Early termination when limit is reached (after sorting)

        Args:
            include: Glob patterns to include (if specified, only matching files are included)
            exclude: Glob patterns to exclude (matching files are removed)
            limit: Maximum number of files to return

        Returns:
            List of filtered file paths
        """
        if not CONF.MEMORY_DIR.exists():
            return []

        include_compiled = MemoryManager._compile_patterns(include) if include else None
        exclude_compiled = MemoryManager._compile_patterns(exclude) if exclude else None

        files = []
        for p in MemoryManager._iter_memory_files():
            if include_compiled and not MemoryManager._matches_compiled(p, include_compiled):
                continue
            if exclude_compiled and MemoryManager._matches_compiled(p, exclude_compiled):
                continue
            files.append(p)

        files.sort(key=lambda p: str(p))

        if limit is not None and limit > 0:
            files = files[:limit]

        return files

    @staticmethod
    def get_structure(include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                      limit: Optional[int] = None) -> str:
        """
        Get a formatted list of memory files with optional filtering.

        Args:
            include: Glob patterns to include (if specified, only matching files are included)
            exclude: Glob patterns to exclude (matching files are removed)
            limit: Maximum number of files to include

        Returns:
            Formatted string listing memory files, or "(Memory Empty)" if none found
        """
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            return "(Memory Empty)"

        filtered_files = MemoryManager.get_filtered_files(include, exclude, limit)
        if not filtered_files:
            return "(No matching memory files)"

        output = []
        for p in filtered_files:
            try:
                output.append(f"- {p.relative_to(CONF.BASE_DIR)}")
            except ValueError as e:
                Logger.debug(f"Path {p} not relative to {CONF.BASE_DIR}: {e}")
                continue
        if output:
            return "\n".join(output)
        return "(No matching memory files)"

    @staticmethod
    def extract_test_command() -> str:
        """Extract test command from memory files.

        Searches for 'Test Command' pattern in memory files and returns
        the command, or falls back to default test runners based on project type.

        Returns:
            Test command string (e.g., 'pytest', 'npm test')
        """
        texts = []
        for path in CONF.MEMORY_DIR.rglob('*'):
            if path.suffix in ('.md', '.txt'):
                try:
                    texts.append(path.read_text(encoding='utf-8'))
                except (OSError, UnicodeDecodeError) as e:
                    Logger.debug(f"Failed to read {path}: {type(e).__name__}: {e}")
                    continue
        full_text = ''.join(texts)
        match = re.search(r"Test Command.*?`([^`]+)`", full_text, re.IGNORECASE)
        if match:
            return match.group(1)
        if (CONF.BASE_DIR / "package.json").exists():
            return "npm test"
        return "pytest"
