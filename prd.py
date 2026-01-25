#!/usr/bin/env python3
"""PRD module for Ralph.

This module contains the PRDManager class for PRD file operations with caching
and CRUD capabilities, and the JsonUtils class for robust JSON parsing of LLM outputs.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional


class PRDManager:
    """Consolidated manager for PRD file operations.

    Provides caching to avoid repeated disk reads and centralizes
    all PRD read/write operations in one place.
    """

    def __init__(self, prd_path: Path):
        """Initialize PRD manager with path to PRD file.

        Args:
            prd_path: Path to the PRD JSON file
        """
        self._path = prd_path
        self._cache: Optional[Dict[str, Any]] = None
        self._raw_cache: Optional[str] = None

    def exists(self) -> bool:
        """Check if PRD file exists on disk."""
        return self._path.exists()

    def invalidate_cache(self) -> None:
        """Clear cached PRD data, forcing next read from disk."""
        self._cache = None
        self._raw_cache = None

    def read_raw(self) -> str:
        """Read raw PRD content as string.

        Returns:
            Raw JSON string from PRD file

        Raises:
            FileNotFoundError: If PRD file does not exist
        """
        if self._raw_cache is None:
            self._raw_cache = self._path.read_text(encoding='utf-8')
        return self._raw_cache

    def load(self) -> Dict[str, Any]:
        """Load and parse PRD from disk with caching.

        Returns:
            Parsed PRD data as dictionary

        Raises:
            FileNotFoundError: If PRD file does not exist
            json.JSONDecodeError: If PRD contains invalid JSON
        """
        if self._cache is None:
            self._cache = json.loads(self.read_raw())
        return self._cache

    def save(self, data: Dict[str, Any]) -> None:
        """Save PRD data to disk and update cache.

        Args:
            data: PRD data to write
        """
        content = json.dumps(data, indent=2)
        self._path.write_text(content, encoding='utf-8')
        self._cache = data
        self._raw_cache = content

    def delete(self) -> None:
        """Delete PRD file from disk and clear cache."""
        if self._path.exists():
            self._path.unlink()
        self.invalidate_cache()


class JsonUtils:
    """Robust JSON parsing for LLM outputs."""

    @staticmethod
    def parse(text: str) -> Dict[str, Any]:
        """
        Parse JSON from LLM output, handling markdown fences and comments.

        Args:
            text: Raw text potentially containing JSON with markdown fences

        Returns:
            Parsed JSON as a dictionary

        Raises:
            json.JSONDecodeError: If the text cannot be parsed as valid JSON
        """
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end+1]
        text = re.sub(r"//.*", "", text)
        return json.loads(text)
