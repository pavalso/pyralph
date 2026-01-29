#!/usr/bin/env python3
"""Exploration context module for Ralph.

This module contains the ExplorationContextManager class for managing
structured exploration findings that feed into PRD generation.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class ExplorationContextManager:
    """Manager for exploration context artifacts.

    Handles reading, writing, and validating exploration_context.json files
    that contain structured findings from codebase exploration.
    """

    # Required fields for a valid exploration context
    REQUIRED_FIELDS = ['version', 'timestamp', 'file_tree', 'exploration_summary']

    def __init__(self, context_path: Path):
        """Initialize exploration context manager.

        Args:
            context_path: Path to the exploration_context.json file
        """
        self._path = context_path
        self._cache: Optional[Dict[str, Any]] = None

    def exists(self) -> bool:
        """Check if exploration context file exists on disk."""
        return self._path.exists()

    def invalidate_cache(self) -> None:
        """Clear cached context data, forcing next read from disk."""
        self._cache = None

    def load(self) -> Dict[str, Any]:
        """Load and parse exploration context from disk with caching.

        Returns:
            Parsed exploration context as dictionary

        Raises:
            FileNotFoundError: If context file does not exist
            json.JSONDecodeError: If context contains invalid JSON
            ValueError: If context is corrupted (missing required fields or invalid hash)
        """
        if self._cache is None:
            raw_content = self._path.read_text(encoding='utf-8')
            data = json.loads(raw_content)
            self._validate_context(data)
            self._cache = data
        return self._cache

    def _validate_context(self, data: Dict[str, Any]) -> None:
        """Validate that context contains required fields and valid hash.

        Args:
            data: Parsed context data to validate

        Raises:
            ValueError: If context is missing required fields or has invalid hash
        """
        for field in self.REQUIRED_FIELDS:
            if field not in data:
                raise ValueError(f"Corrupted exploration context: missing required field '{field}'")

        # Validate content hash if present
        if 'content_hash' in data:
            expected_hash = data['content_hash']
            actual_hash = self._compute_hash(data)
            if expected_hash != actual_hash:
                raise ValueError(
                    "Corrupted exploration context: content hash mismatch "
                    f"(expected {expected_hash[:8]}..., got {actual_hash[:8]}...)"
                )

    def _compute_hash(self, data: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of context content (excluding the hash field).

        Args:
            data: Context data to hash

        Returns:
            Hex-encoded SHA-256 hash string
        """
        # Create a copy without the hash field for hashing
        hashable_data = {k: v for k, v in data.items() if k != 'content_hash'}
        content = json.dumps(hashable_data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def save(self, data: Dict[str, Any]) -> None:
        """Save exploration context to disk and update cache.

        Automatically adds version, timestamp, and content hash if not present.

        Args:
            data: Exploration context data to write
        """
        # Ensure required metadata
        if 'version' not in data:
            data['version'] = '1.0'
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now().isoformat()

        # Compute and add content hash
        data['content_hash'] = self._compute_hash(data)

        content = json.dumps(data, indent=2)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(content, encoding='utf-8')
        self._cache = data

    def delete(self) -> None:
        """Delete exploration context file from disk and clear cache."""
        if self._path.exists():
            self._path.unlink()
        self.invalidate_cache()

    def is_valid(self) -> bool:
        """Check if exploration context exists and is valid.

        Returns:
            True if context exists and passes validation, False otherwise
        """
        if not self.exists():
            return False
        try:
            self.invalidate_cache()  # Force fresh read
            self.load()
            return True
        except (json.JSONDecodeError, ValueError):
            return False


def create_exploration_context(
    file_tree: str,
    exploration_summary: Dict[str, Any],
    incomplete_paths: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    truncated: bool = False,
    files_examined: Optional[int] = None
) -> Dict[str, Any]:
    """Create a structured exploration context dictionary.

    Args:
        file_tree: String representation of the project file tree
        exploration_summary: Dictionary containing exploration findings
        incomplete_paths: Optional list of paths that could not be explored
        metadata: Optional additional metadata
        truncated: Whether exploration was truncated due to limits
        files_examined: Number of files examined during exploration

    Returns:
        Structured exploration context dictionary
    """
    context = {
        'version': '1.0',
        'timestamp': datetime.now().isoformat(),
        'file_tree': file_tree,
        'exploration_summary': exploration_summary,
        'incomplete_paths': incomplete_paths or [],
        'metadata': metadata or {},
        'truncated': truncated,
        'files_examined': files_examined
    }
    return context
