#!/usr/bin/env python3
import json
import subprocess
import sys
import re
import shutil
import datetime
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Import agents
from agents import get_agent, list_agents
from agents.base import AgentError

# Import hooks
from hooks import HookManager, Event, EventType

# ==============================================================================
# CONFIGURATION
# ==============================================================================

@dataclass
class Config:
    BASE_DIR: Path = Path.cwd()
    ROOT_DIR: Path = BASE_DIR / ".ralph"
    MEMORY_DIR: Path = ROOT_DIR / "memory"
    ARCHIVE_DIR: Path = ROOT_DIR / "archive"
    TEMPLATES_DIR: Path = ROOT_DIR / "templates"
    HOOKS_DIR: Path = ROOT_DIR / "hooks"
    PRD_FILE: Path = ROOT_DIR / "prd.json"
    PROGRESS_FILE: Path = ROOT_DIR / "progress.txt"
    LOG_FILE: Path = ROOT_DIR / "ralph_log.txt"

    # Limits
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 600

    def ensure_directories(self) -> None:
        for path in [self.ROOT_DIR, self.MEMORY_DIR, self.ARCHIVE_DIR, self.TEMPLATES_DIR, self.HOOKS_DIR]:
            path.mkdir(exist_ok=True, parents=True)

CONF = Config()

# ==============================================================================
# UTILITIES & LOGGING
# ==============================================================================

class _LoggerMeta(type):
    """Metaclass for Logger to provide property-based synchronization of verbose/verbosity.

    This metaclass enables class-level properties that keep Logger.verbose and
    Logger.verbosity synchronized automatically, even with direct assignment.
    """

    @property
    def verbosity(cls) -> int:
        """Get verbosity level (0=normal, 1=verbose, 2=very verbose, 3=debug)."""
        return cls._verbosity_value

    @verbosity.setter
    def verbosity(cls, value: int) -> None:
        """Set verbosity level, clamping to [0, 3] and syncing verbose."""
        clamped = max(0, min(3, value))
        cls._verbosity_value = clamped
        cls._verbose_value = clamped >= 1
        # Auto-set log_level to debug when verbosity is enabled
        if clamped >= 1:
            cls.log_level = cls.LOG_LEVELS["debug"]

    @property
    def verbose(cls) -> bool:
        """Get verbose mode (True if verbosity >= 1)."""
        return cls._verbose_value

    @verbose.setter
    def verbose(cls, value: bool) -> None:
        """Set verbose mode, syncing verbosity to 1 or 0."""
        cls._verbose_value = bool(value)
        cls._verbosity_value = 1 if value else 0
        # Auto-set log_level to debug when verbose is enabled
        if value:
            cls.log_level = cls.LOG_LEVELS["debug"]


class Logger(metaclass=_LoggerMeta):
    COLORS = {"RESET": "\033[0m", "GREEN": "\033[92m", "RED": "\033[91m",
              "CYAN": "\033[96m", "YELLOW": "\033[93m", "MAGENTA": "\033[95m"}
    # Verbosity levels: 0=normal, 1=verbose (-v), 2=very verbose (-vv), 3=debug (-vvv)
    # These are synchronized via metaclass properties - setting one updates the other
    _verbosity_value = 0
    _verbose_value = False
    no_color = False
    quiet = False
    no_emoji = False
    # Log level control: debug=10, info=20, warn=30, error=40
    LOG_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
    log_level = 20  # Default: info
    # Output format control
    json_output = False
    ndjson_output = False
    # Custom log file path (None = use default CONF.LOG_FILE)
    custom_log_file: Optional[Path] = None
    # Non-interactive mode (disables all interactive prompts)
    non_interactive = False
    # Redaction patterns for sensitive data
    redact_patterns: List[str] = []
    # Log control flags
    no_log_prompts = False
    no_log_responses = False

    @staticmethod
    def set_no_color(enabled: bool) -> None:
        Logger.no_color = enabled

    @staticmethod
    def set_verbose(enabled: bool) -> None:
        """Set verbose mode (backwards compatible, sets verbosity to 1 or 0).

        This method is provided for backwards compatibility. The verbose and
        verbosity attributes are automatically synchronized via descriptors.
        """
        Logger.verbose = enabled

    @staticmethod
    def set_verbosity(level: int) -> None:
        """Set verbosity level (0=normal, 1=verbose, 2=very verbose, 3=debug).

        When verbosity >= 1, log_level is automatically set to debug to allow
        debug/trace/ultra messages to appear. This maintains backwards compatibility
        with existing -v/-vv/-vvv behavior.

        The verbose and verbosity attributes are automatically synchronized via
        descriptors, so setting verbosity will update verbose accordingly.
        """
        Logger.verbosity = level

    @staticmethod
    def set_quiet(enabled: bool) -> None:
        """Set quiet mode (suppresses all non-error output)."""
        Logger.quiet = enabled

    @staticmethod
    def set_no_emoji(enabled: bool) -> None:
        """Set no-emoji mode (replaces emojis with text equivalents)."""
        Logger.no_emoji = enabled

    @staticmethod
    def set_log_level(level: str) -> None:
        """Set log level (debug, info, warn, error)."""
        if level in Logger.LOG_LEVELS:
            Logger.log_level = Logger.LOG_LEVELS[level]

    @staticmethod
    def set_json_output(enabled: bool) -> None:
        """Enable JSON output format."""
        Logger.json_output = enabled

    @staticmethod
    def set_ndjson_output(enabled: bool) -> None:
        """Enable newline-delimited JSON output format."""
        Logger.ndjson_output = enabled

    @staticmethod
    def set_log_file(path: Optional[str]) -> None:
        """Set custom log file path."""
        Logger.custom_log_file = Path(path) if path else None

    @staticmethod
    def set_non_interactive(enabled: bool) -> None:
        """Set non-interactive mode (disables all interactive prompts)."""
        Logger.non_interactive = enabled

    @staticmethod
    def set_redact_patterns(patterns: List[str]) -> None:
        """Set patterns to redact from logs.

        Args:
            patterns: List of regex patterns to redact from log output
        """
        Logger.redact_patterns = patterns

    @staticmethod
    def add_redact_patterns_from_file(file_path: str) -> None:
        """Load redaction patterns from a file (one pattern per line).

        Args:
            file_path: Path to file containing patterns (one per line)
        """
        try:
            path = Path(file_path)
            if path.exists():
                lines = path.read_text(encoding='utf-8').splitlines()
                patterns = [
                    line.strip()
                    for line in lines
                    if line.strip() and not line.strip().startswith('#')
                ]
                Logger.redact_patterns.extend(patterns)
        except (OSError, UnicodeDecodeError) as e:
            Logger.debug(f"Failed to load redact patterns from {file_path}: {type(e).__name__}: {e}")

    @staticmethod
    def set_no_log_prompts(enabled: bool) -> None:
        """Disable logging of prompts to log file.

        Args:
            enabled: If True, prompts will not be written to logs
        """
        Logger.no_log_prompts = enabled

    @staticmethod
    def set_no_log_responses(enabled: bool) -> None:
        """Disable logging of responses to log file.

        Args:
            enabled: If True, responses will not be written to logs
        """
        Logger.no_log_responses = enabled

    @staticmethod
    def _redact_content(content: str) -> str:
        """Apply redaction patterns to content.

        Args:
            content: The content to redact

        Returns:
            Content with sensitive patterns replaced with [REDACTED]
        """
        if not Logger.redact_patterns:
            return content
        redacted = content
        for pattern in Logger.redact_patterns:
            try:
                redacted = re.sub(pattern, '[REDACTED]', redacted)
            except re.error as e:
                Logger.debug(f"Invalid redact pattern '{pattern}': {e}")
        return redacted

    @staticmethod
    def get_log_file() -> Path:
        """Get the effective log file path (custom or default)."""
        if Logger.custom_log_file:
            return Logger.custom_log_file
        return CONF.LOG_FILE

    @staticmethod
    def _should_log(level: int) -> bool:
        """Check if a message at the given level should be logged."""
        return level >= Logger.log_level

    @staticmethod
    def _format_json_message(msg: str, level: str, **kwargs) -> str:
        """Format a log message as JSON."""
        data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "level": level,
            "message": msg,
            **kwargs
        }
        return json.dumps(data)

    _EMOJI_MAP = {
        "🤖": "[BOT]", "🕵️": "[ARCH]", "🧠": "[PLAN]", "🚀": "[EXEC]",
        "✅": "[OK]", "❌": "[FAIL]", "⚠️": "[WARN]", "▶️": "[>]",
        "🔒": "[VERIFY]", "🛑": "[STOP]", "⏭️": "[SKIP]", "📋": "[LIST]",
        "📦": "[PKG]", "🎉": "[DONE]", "➡️": "[->]", "⬅️": "[<-]",
        "ℹ️": "[INFO]", "❓": "[?]",
    }
    _EMOJI_PATTERN = re.compile('|'.join(re.escape(e) for e in _EMOJI_MAP.keys()))

    @classmethod
    def _strip_emoji(cls, msg: str) -> str:
        """Replace emojis with text equivalents."""
        return cls._EMOJI_PATTERN.sub(lambda m: cls._EMOJI_MAP[m.group()], msg)

    @staticmethod
    def _print_colored(msg: str, color: str = "RESET", prefix: str = ""):
        if Logger.no_emoji:
            msg = Logger._strip_emoji(msg)
        text = f"{prefix}{msg}" if prefix else msg
        if Logger.no_color:
            output = text
        else:
            output = f"{Logger.COLORS.get(color, Logger.COLORS['RESET'])}{text}{Logger.COLORS['RESET']}"
        try:
            print(output)
        except UnicodeEncodeError:
            print(output.encode('ascii', errors='replace').decode('ascii'))

    @staticmethod
    def info(msg: str, color: str = "RESET") -> None:
        """Print info message (suppressed in quiet mode or if log level > info)."""
        if not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["info"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "info"))
            else:
                Logger._print_colored(msg, color)

    @staticmethod
    def debug(msg: str, color: str = "RESET") -> None:
        """Print debug message (requires verbosity >= 1 and log level <= debug)."""
        if Logger.verbosity >= 1 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "debug"))
            else:
                Logger._print_colored(msg, color, prefix="[DEBUG] ")

    @staticmethod
    def trace(msg: str, color: str = "RESET") -> None:
        """Print trace message (requires verbosity >= 2 and log level <= debug)."""
        if Logger.verbosity >= 2 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "trace"))
            else:
                Logger._print_colored(msg, color, prefix="[TRACE] ")

    @staticmethod
    def ultra(msg: str, color: str = "RESET") -> None:
        """Print ultra-verbose message (requires verbosity >= 3 and log level <= debug)."""
        if Logger.verbosity >= 3 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "ultra"))
            else:
                Logger._print_colored(msg, color, prefix="[ULTRA] ")

    @staticmethod
    def warning(msg: str) -> None:
        """Print warning message (shown even in quiet mode, respects log level)."""
        if Logger._should_log(Logger.LOG_LEVELS["warn"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "warn"))
            else:
                Logger._print_colored(msg, "YELLOW", prefix="[WARNING] ")

    @staticmethod
    def error(msg: str) -> None:
        """Print error message (always shown, respects log level)."""
        if Logger._should_log(Logger.LOG_LEVELS["error"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "error"))
            else:
                Logger._print_colored(msg, "RED", prefix="[ERROR] ")

    @staticmethod
    def file_log(content: str, type: str, tag: str = "UNKNOWN") -> None:
        """Append a timestamped entry to the persistent log file.

        Respects the following privacy flags:
        - --no-log-prompts: Skip logging when type is PROMPT
        - --no-log-responses: Skip logging when type is RESPONSE
        - --redact / --redact-file: Apply redaction patterns to content
        """
        # Skip logging prompts if --no-log-prompts is set
        if Logger.no_log_prompts and type == "PROMPT":
            return
        # Skip logging responses if --no-log-responses is set
        if Logger.no_log_responses and type == "RESPONSE":
            return
        # Apply redaction patterns to content
        redacted_content = Logger._redact_content(content)
        icons = {"PROMPT": "➡️", "RESPONSE": "⬅️", "ERROR": "❌", "INFO": "ℹ️"}
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_file = Logger.get_log_file()
        entry = f"\n{'='*60}\n{icons.get(type, '❓')} [{ts}] TYPE: {type} | TAG: {tag}\n{'='*60}\n{redacted_content}\n"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except OSError as e:
            print(f"⚠️ Log Error: {type(e).__name__}: {e}")

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

# ==============================================================================
# CORE COMPONENTS
# ==============================================================================

class MemoryManager:
    @staticmethod
    def validate_memory() -> Dict[str, Any]:
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


class PromptFormatter:
    """Utility class for consistent prompt formatting with delimiters."""

    # Standard delimiters for variable content
    DELIMITERS = {
        'user_intent': ('USER_INTENT', 'User-provided intent/goal'),
        'file_tree': ('FILE_TREE', 'Project directory structure'),
        'memory_map': ('MEMORY_MAP', 'Available memory files'),
        'task_id': ('TASK_ID', 'Task identifier'),
        'task_description': ('TASK_DESC', 'Task description'),
        'acceptance_criteria': ('ACCEPTANCE_CRITERIA', 'Task acceptance criteria'),
        'user_context': ('USER_CONTEXT', 'User preferences and instructions'),
        'memory_tree': ('MEMORY_TREE', 'Memory file contents'),
        'prev_errors': ('PREV_ERRORS', 'Previous error messages'),
        'test_cmd': ('TEST_CMD', 'Verification command'),
    }

    @staticmethod
    def wrap(content: str, delimiter_key: str) -> str:
        """Wrap content in XML-style delimiters for clear boundaries."""
        if delimiter_key not in PromptFormatter.DELIMITERS:
            return content
        tag, _ = PromptFormatter.DELIMITERS[delimiter_key]
        return f"<{tag}>\n{content}\n</{tag}>"

    @staticmethod
    def format_list(items: list, prefix: str = "- ") -> str:
        """Format a list with consistent prefix."""
        if not items:
            return "(none)"
        return "\n".join(f"{prefix}{item}" for item in items)

    @staticmethod
    def format_code_block(content: str, language: str = "") -> str:
        """Format content as a fenced code block."""
        return f"```{language}\n{content}\n```"


class TemplateManager:
    DEFAULT_TEMPLATES = {
        "architect.txt": """# ROLE
Senior Software Architect

# OBJECTIVE
Analyze the project structure and initialize architecture documentation.

# CONTEXT

## User Intent
<USER_INTENT>
{{user_intent}}
</USER_INTENT>

## Project File Tree
<FILE_TREE>
{{file_tree}}
</FILE_TREE>

# CONSTRAINTS
- You MUST create exactly two files: .ralph/memory/architecture.md and ARCH.md
- You MUST use the exact YAML frontmatter format specified below
- You MUST include ALL required sections in the exact order specified
- You MUST detect the actual test command from the project (pytest, npm test, etc.)
- You MUST NOT invent or assume technologies not evident in the file tree
- Keep descriptions concise and factual

# OUTPUT SPECIFICATION

## File 1: .ralph/memory/architecture.md
Create this file with the following structure:

```markdown
---
type: wiki
title: Architecture
---

# Architecture

## Tech Stack
- **Language**: [detected language and version]
- **Testing**: [detected test framework]
- **Build**: [detected build tool]
- [additional relevant technologies]

## Overview
[2-3 sentence description of the project purpose and architecture]

## Key Components
| Component | Description |
|-----------|-------------|
| [path/file] | [brief description] |
[list 3-6 key components]

## Test Command
Test Command: `[actual test command]`
```

## File 2: ARCH.md (Project Root)
Create a copy of the architecture documentation in the project root for git tracking.

# RESPONSE FORMAT
After creating the files, output EXACTLY:
```
STATUS: CREATED .ralph/memory/architecture.md
```
""",
        "planner.txt": """# ROLE
Product Manager

# OBJECTIVE
Create a comprehensive Product Requirements Document (PRD) in JSON format.

# CONTEXT

## User Intent
<USER_INTENT>
{{user_intent}}
</USER_INTENT>

## Available Memory Files
<MEMORY_MAP>
{{memory_map}}
</MEMORY_MAP>

# CHAIN-OF-THOUGHT REASONING
Before generating the PRD, think through the following steps:

1. **Scope Analysis**: What is the user trying to build or achieve?
2. **Feature Decomposition**: What distinct features or tasks are needed?
3. **User Story Mapping**: For each feature, who is the user and what value do they get?
4. **Acceptance Criteria**: What specific, testable conditions define "done" for each story?
5. **Dependencies**: Are there any ordering constraints between tasks?

# OUTPUT SPECIFICATION

## JSON Schema
You MUST output valid JSON matching this exact schema:

```json
{
  "id": "PRD-001",
  "description": "Brief description of the overall PRD",
  "userStories": [
    {
      "id": "TASK-001",
      "description": "As a <role>, I want <feature> so that <benefit>",
      "acceptanceCriteria": [
        "Given <context>, when <action>, then <expected result>",
        "The feature must <specific requirement>",
        "Error handling: <error case> should <expected behavior>"
      ],
      "status": "pending"
    }
  ]
}
```

## Schema Field Requirements
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | Yes | Unique PRD identifier (format: PRD-XXX) |
| description | string | Yes | Clear, concise PRD summary (1-2 sentences) |
| userStories | array | Yes | List of user stories (minimum 1) |
| userStories[].id | string | Yes | Unique task ID (format: TASK-XXX) |
| userStories[].description | string | Yes | User story in "As a... I want... so that..." format |
| userStories[].acceptanceCriteria | array | Yes | Testable conditions (minimum 2 per story) |
| userStories[].status | string | Yes | Must be "pending" for new stories |

# CONSTRAINTS
- Output ONLY valid JSON - no markdown fences, no explanatory text before or after
- Each user story MUST follow the "As a <role>, I want <feature> so that <benefit>" format
- Each acceptance criterion MUST be specific and testable
- Task IDs MUST be sequential (TASK-001, TASK-002, etc.)
- Status MUST always be "pending" for new stories
- Minimum 2 acceptance criteria per user story
- Do NOT include any text outside the JSON object

# RESPONSE FORMAT
Output the raw JSON object directly. Example:
{"id":"PRD-001","description":"...","userStories":[...]}
""",
        "developer.txt": """# ROLE
Developer

# OBJECTIVE
Implement the assigned task following the acceptance criteria and verification requirements.

# TASK CONTEXT

## Task ID
<TASK_ID>
{{task_id}}
</TASK_ID>

## Task Description
<TASK_DESC>
{{task_description}}
</TASK_DESC>

## Acceptance Criteria
<ACCEPTANCE_CRITERIA>
{{acceptance_criteria}}
</ACCEPTANCE_CRITERIA>

# MANDATORY INSTRUCTIONS
The following user preferences are REQUIRED. You MUST strictly adhere to these instructions:
<USER_CONTEXT>
{{user_context}}
</USER_CONTEXT>

# AVAILABLE CONTEXT

## Memory Files
<MEMORY_TREE>
{{memory_tree}}
</MEMORY_TREE>

## Previous Errors (if any)
<PREV_ERRORS>
{{prev_errors}}
</PREV_ERRORS>

# EXECUTION WORKFLOW

## Phase 1: Planning
1. Analyze the task requirements and acceptance criteria
2. Identify files that need to be created or modified
3. Consider edge cases and potential issues
4. Plan the implementation order

## Phase 2: Implementation
1. Make changes incrementally
2. Follow existing code patterns and conventions
3. Keep changes minimal and focused on the task
4. Do NOT add features beyond what is specified

## Phase 3: Verification
1. Run the verification command: `{{test_cmd}}`
2. If tests fail, analyze the error output
3. Fix any issues and re-run verification
4. Only proceed to completion when tests pass

# ERROR HANDLING GUIDANCE

## If Tests Fail
1. Read the error message carefully
2. Identify the root cause (syntax error, logic error, missing import, etc.)
3. Fix the specific issue - do not make unrelated changes
4. Re-run verification to confirm the fix

## Self-Correction Rules
- If you encounter the same error twice, try a different approach
- If verification fails 3+ times, step back and re-analyze the requirements
- Do NOT modify test files unless the task explicitly requires it
- Do NOT skip or disable failing tests

# OUTPUT SPECIFICATION

## On Success
When all acceptance criteria are met and verification passes, output EXACTLY:
```
STATUS: SUCCESS
```

## On Failure
If you cannot complete the task, output EXACTLY:
```
STATUS: FAILURE - <specific reason>
```

Include a clear explanation of:
1. What was attempted
2. What failed
3. What might be needed to resolve it

# CONSTRAINTS
- You MUST run verification (`{{test_cmd}}`) before reporting success
- You MUST NOT report SUCCESS if verification fails
- You MUST follow the acceptance criteria exactly
- You MUST keep changes minimal and focused
- You MUST NOT modify unrelated files"""
    }

    @staticmethod
    def ensure_templates():
        CONF.TEMPLATES_DIR.mkdir(exist_ok=True, parents=True)
        for name, content in TemplateManager.DEFAULT_TEMPLATES.items():
            path = CONF.TEMPLATES_DIR / name
            if not path.exists(): path.write_text(content, encoding='utf-8')

    @staticmethod
    def load(template_name: str) -> str:
        path = CONF.TEMPLATES_DIR / template_name
        if not path.exists():
            if template_name in TemplateManager.DEFAULT_TEMPLATES:
                return TemplateManager.DEFAULT_TEMPLATES[template_name]
            raise FileNotFoundError(f"Template not found: {template_name}")
        return path.read_text(encoding='utf-8')

    @staticmethod
    def render(template_name: str, **variables) -> str:
        template = TemplateManager.load(template_name)
        for key, value in variables.items():
            template = template.replace("{{" + key + "}}", str(value))
        return template


# ==============================================================================
# ORCHESTRATOR
# ==============================================================================

class RalphOrchestrator:
    def __init__(self, agent_name: str = "claude", enable_hooks: bool = True, enabled_hook_names: Optional[List[str]] = None,
                 intent: Optional[str] = None, intent_file: Optional[str] = None, prompt_file: Optional[str] = None,
                 tree_depth: int = 2, tree_ignore: Optional[List[str]] = None, memory_out: Optional[str] = None,
                 test_cmd: Optional[str] = None, skip_verify: bool = False, retries: Optional[int] = None,
                 timeout: Optional[int] = None, only: Optional[List[str]] = None, except_tasks: Optional[List[str]] = None,
                 resume: Optional[str] = None, include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                 context_limit: Optional[int] = None,
                 model: Optional[str] = None, temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None, seed: Optional[int] = None,
                 log_file: Optional[str] = None, log_level: Optional[str] = None,
                 json_output: bool = False, ndjson_output: bool = False,
                 print_prd: bool = False, prd_out: Optional[str] = None, archive: bool = True,
                 non_interactive: bool = False, ci: bool = False, status_check: bool = False,
                 pre: Optional[List[str]] = None, post: Optional[List[str]] = None,
                 plugin: Optional[List[str]] = None,
                 schema: Optional[str] = None, min_criteria: Optional[int] = None,
                 label: Optional[List[str]] = None) -> None:
        # Use --timeout override if provided, otherwise use config default
        agent_timeout = timeout if timeout is not None else CONF.TIMEOUT_SECONDS
        self.agent = get_agent(agent_name, timeout_seconds=agent_timeout,
                               model=model, temperature=temperature,
                               max_tokens=max_tokens, seed=seed)
        if hasattr(self.agent, 'set_logger'):
            self.agent.set_logger(Logger)
        if hasattr(self.agent, 'set_config'):
            self.agent.set_config(CONF)
        if not self.agent.check_dependencies():
            Logger.info(f"❌ Agent '{self.agent.get_name()}' dependencies not satisfied.", "RED")
            sys.exit(1)
        self.memory = MemoryManager()
        CONF.ensure_directories()
        self._validate_memory_on_startup()
        # Initialize hook system
        self.hooks = HookManager(CONF.HOOKS_DIR, Logger)
        if not enable_hooks:
            self.hooks.disable()
        elif enabled_hook_names is not None:
            self.hooks.set_enabled_hooks(enabled_hook_names)
        # Store intent flags for non-interactive runs
        self._intent = intent
        self._intent_file = intent_file
        self._prompt_file_override = prompt_file
        # Store architect control flags
        self._tree_depth = tree_depth
        self._tree_ignore = tree_ignore
        self._memory_out = memory_out
        # Store execution and verification flags
        self._test_cmd_override = test_cmd
        self._skip_verify = skip_verify
        self._retries_override = retries
        self._timeout_override = timeout
        self._only_tasks = only
        self._except_tasks = except_tasks
        self._resume_from = resume
        # Store context and memory control flags
        self._include_patterns = include
        self._exclude_patterns = exclude
        self._context_limit = context_limit
        # Store I/O, logging and output flags
        self._log_file = log_file
        self._log_level = log_level
        self._json_output = json_output
        self._ndjson_output = ndjson_output
        self._print_prd_flag = print_prd
        self._prd_out = prd_out
        self._archive = archive
        # Store headless operation flags
        self._non_interactive = non_interactive
        self._ci = ci
        self._status_check = status_check
        # Store extensibility and hook flags
        self._pre_commands = pre or []
        self._post_commands = post or []
        self._plugin_paths = plugin or []
        # Load plugins if specified
        self._load_plugins()
        # Store PRD and story control flags
        self._schema_path = schema
        self._min_criteria = min_criteria
        self._labels = label or []
        # Initialize PRD manager for consolidated file operations
        self._prd = PRDManager(CONF.PRD_FILE)

    def _load_plugins(self) -> None:
        """
        Load plugins from specified paths.

        Plugins are Python files or directories containing hook definitions.
        Each plugin can register hooks programmatically via the HookManager API.
        """
        for plugin_path_str in self._plugin_paths:
            plugin_path = Path(plugin_path_str)
            if not plugin_path.exists():
                Logger.warning(f"Plugin path not found: {plugin_path}")
                continue

            if plugin_path.is_file() and plugin_path.suffix == '.py':
                self._load_plugin_file(plugin_path)
            elif plugin_path.is_dir():
                # Load all .py files in the directory
                for py_file in plugin_path.glob('*.py'):
                    if not py_file.name.startswith('_'):
                        self._load_plugin_file(py_file)
            else:
                Logger.warning(f"Invalid plugin path (must be .py file or directory): {plugin_path}")

    def _load_plugin_file(self, path: Path) -> None:
        """
        Load a single plugin file.

        The plugin file should define:
        - EVENTS: List of event names to subscribe to
        - on_event(event): Handler function
        - Optional: PRIORITY, TIMEOUT, MODIFIES_DATA

        Args:
            path: Path to the plugin Python file
        """
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                Logger.warning(f"Could not load plugin: {path}")
                return

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Check if plugin has required attributes
            if not hasattr(module, 'EVENTS') or not hasattr(module, 'on_event'):
                Logger.warning(f"Plugin '{path.name}' missing EVENTS or on_event")
                return

            # Register the plugin as a hook
            events = getattr(module, 'EVENTS', [])
            priority = getattr(module, 'PRIORITY', 100)
            timeout = getattr(module, 'TIMEOUT', 5.0)
            modifies_data = getattr(module, 'MODIFIES_DATA', False)
            handler = getattr(module, 'on_event')

            success = self.hooks.register_hook(
                name=f"plugin_{path.stem}",
                handler=handler,
                events=events,
                priority=priority,
                timeout=timeout,
                modifies_data=modifies_data
            )
            if success:
                Logger.debug(f"Loaded plugin: {path.name}")
            else:
                Logger.warning(f"Failed to register plugin: {path.name}")

        except Exception as e:
            Logger.warning(f"Error loading plugin '{path.name}': {e}")

    def _run_pre_commands(self, phase: str) -> bool:
        """
        Run pre-execution commands before a phase.

        Pre-commands are shell commands executed before each phase.
        If any command fails (non-zero exit code), the phase is aborted.

        Args:
            phase: The phase about to run (architect, planner, execute)

        Returns:
            True if all commands succeeded, False if any failed
        """
        if not self._pre_commands:
            return True

        Logger.debug(f"Running {len(self._pre_commands)} pre-command(s) for {phase} phase")
        for cmd in self._pre_commands:
            Logger.debug(f"  Pre-command: {cmd}")
            stdout, stderr, code = Shell.run(cmd, timeout=60)
            if code != 0:
                Logger.error(f"Pre-command failed: {cmd}")
                Logger.error(f"  Exit code: {code}")
                if stderr:
                    Logger.error(f"  Stderr: {stderr[:500]}")
                self.hooks.emit(Event(
                    EventType.ERROR,
                    phase=phase,
                    metadata={"reason": "pre_command_failed", "command": cmd, "exit_code": code}
                ))
                return False
            if stdout and Logger.verbosity >= 2:
                Logger.trace(f"  Output: {stdout[:200]}")
        return True

    def _run_post_commands(self, phase: str, success: bool) -> None:
        """
        Run post-execution commands after a phase.

        Post-commands are shell commands executed after each phase completes.
        They receive the phase result via environment variables.

        Args:
            phase: The phase that just completed (architect, planner, execute)
            success: Whether the phase completed successfully

        Security Note:
            Uses shell=True for command execution. Commands are sourced from
            user-controlled configuration (--post-command flag), so command
            injection risk is accepted as the user controls their own config.
            Environment variables RALPH_PHASE and RALPH_SUCCESS are set with
            sanitized values (fixed strings and booleans only).
        """
        if not self._post_commands:
            return

        import os
        # Set environment variables for post-commands
        env = os.environ.copy()
        env['RALPH_PHASE'] = phase
        env['RALPH_SUCCESS'] = '1' if success else '0'

        Logger.debug(f"Running {len(self._post_commands)} post-command(s) for {phase} phase")
        for cmd in self._post_commands:
            Logger.debug(f"  Post-command: {cmd}")
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True,
                    text=True, encoding='utf-8', timeout=60,
                    env=env
                )
                if result.returncode != 0:
                    Logger.warning(f"Post-command failed: {cmd} (exit code: {result.returncode})")
                elif result.stdout and Logger.verbosity >= 2:
                    Logger.trace(f"  Output: {result.stdout[:200]}")
            except subprocess.TimeoutExpired:
                Logger.warning(f"Post-command timed out: {cmd}")
            except Exception as e:
                Logger.warning(f"Post-command error: {cmd} ({e})")

    def run_architect(self, user_intent: str) -> None:
        """
        Run the architect phase to initialize project memory.

        Creates both .ralph/memory/architecture.md (internal memory) and
        ARCH.md (git-tracked documentation) with project structure,
        tech stack, and test command configuration.

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🕵️  Architect: Initializing Memory...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="architect"))
        self.hooks.emit(Event(EventType.ARCHITECT_START, phase="architect"))

        # Run pre-commands before phase execution
        if not self._run_pre_commands("architect"):
            Logger.info("⚠️ Architect aborted: pre-command failed.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            self._run_post_commands("architect", success=False)
            sys.exit(1)

        # Generate file tree with customizable depth and ignore patterns
        file_tree = Shell.get_file_tree(depth=self._tree_depth, ignore=self._tree_ignore)

        prompt = TemplateManager.render(
            "architect.txt",
            user_intent=user_intent,
            file_tree=file_tree
        )

        success, _, _ = self.agent.run(prompt, "ARCHITECT")
        if not success or not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("⚠️ Architect failed.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            self._run_post_commands("architect", success=False)
            sys.exit(1)

        arch_md_path = CONF.BASE_DIR / "ARCH.md"
        if not arch_md_path.exists():
            Logger.info("⚠️ Architect failed: ARCH.md was not created.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            self._run_post_commands("architect", success=False)
            sys.exit(1)

        # Export memory to --memory-out path if specified
        if self._memory_out:
            self._export_memory(self._memory_out)

        Logger.info("✅ Memory Initialized.", "GREEN")
        self.hooks.emit(Event(EventType.ARCHITECT_SUCCESS, phase="architect"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
        self._run_post_commands("architect", success=True)

    def _validate_prd_schema(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate PRD data against a JSON schema file.

        Args:
            data: The PRD data to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        if not self._schema_path:
            return True, ""

        schema_path = Path(self._schema_path)
        if not schema_path.exists():
            return False, f"Schema file not found: {self._schema_path}"

        try:
            schema = json.loads(schema_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON schema: {e}"

        # Basic JSON schema validation (supports type, required, properties)
        errors = self._validate_against_schema(data, schema, "")
        if errors:
            return False, "; ".join(errors)
        return True, ""

    def _validate_against_schema(self, data: Any, schema: Dict[str, Any], path: str) -> List[str]:
        """
        Recursively validate data against a JSON schema.

        Supports a subset of JSON Schema: type, required, properties, items, minItems.

        Args:
            data: The data to validate
            schema: The schema to validate against
            path: Current path in the data for error messages

        Returns:
            List of validation error messages
        """
        errors: List[str] = []
        path_prefix = f"{path}." if path else ""

        # Check type
        if "type" in schema:
            expected_type = schema["type"]
            type_map = {"string": str, "number": (int, float), "integer": int,
                        "boolean": bool, "array": list, "object": dict, "null": type(None)}
            if expected_type in type_map:
                expected = type_map[expected_type]
                if not isinstance(data, expected):
                    errors.append(f"{path or 'root'}: expected {expected_type}, got {type(data).__name__}")
                    return errors  # Don't check further if type is wrong

        # Check required properties (for objects)
        if "required" in schema and isinstance(data, dict):
            for req in schema["required"]:
                if req not in data:
                    errors.append(f"{path_prefix}{req}: required property missing")

        # Check properties (for objects)
        if "properties" in schema and isinstance(data, dict):
            for prop, prop_schema in schema["properties"].items():
                if prop in data:
                    errors.extend(self._validate_against_schema(data[prop], prop_schema, f"{path_prefix}{prop}"))

        # Check items (for arrays)
        if "items" in schema and isinstance(data, list):
            for i, item in enumerate(data):
                errors.extend(self._validate_against_schema(item, schema["items"], f"{path}[{i}]"))

        # Check minItems (for arrays)
        if "minItems" in schema and isinstance(data, list):
            if len(data) < schema["minItems"]:
                errors.append(f"{path or 'root'}: array has {len(data)} items, minimum is {schema['minItems']}")

        return errors

    def _validate_min_criteria(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate that each user story has at least the minimum number of acceptance criteria.

        Args:
            data: The PRD data to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is empty if valid.
        """
        if self._min_criteria is None:
            return True, ""

        stories = data.get("userStories", [])
        violations = []
        for story in stories:
            story_id = story.get("id", "unknown")
            criteria = story.get("acceptanceCriteria", [])
            if len(criteria) < self._min_criteria:
                violations.append(f"{story_id} has {len(criteria)} criteria (minimum: {self._min_criteria})")

        if violations:
            return False, "; ".join(violations)
        return True, ""

    def _apply_labels(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply custom labels to the PRD data.

        Labels are key=value pairs that get added to a 'labels' dict in the PRD.

        Args:
            data: The PRD data to annotate

        Returns:
            The PRD data with labels applied
        """
        if not self._labels:
            return data

        labels_dict: Dict[str, str] = {}
        for label in self._labels:
            if "=" in label:
                key, value = label.split("=", 1)
                labels_dict[key.strip()] = value.strip()
            else:
                # Labels without = are treated as tags with empty value
                labels_dict[label.strip()] = ""

        if labels_dict:
            data["labels"] = labels_dict
        return data

    def run_planner(self, user_intent: str) -> None:
        """
        Run the planner phase to create a Product Requirements Document.

        Generates a PRD with user stories and acceptance criteria,
        saved to .ralph/prd.json.

        Respects the following flags:
        - --schema: Validate PRD against a JSON schema file
        - --min-criteria: Ensure each story has at least N acceptance criteria
        - --label: Add custom labels to the PRD

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="planner"))
        self.hooks.emit(Event(EventType.PLANNER_START, phase="planner"))

        # Run pre-commands before phase execution
        if not self._run_pre_commands("planner"):
            Logger.info("⚠️ Planner aborted: pre-command failed.", "RED")
            self.hooks.emit(Event(EventType.PLANNER_FAILURE, phase="planner"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
            self._run_post_commands("planner", success=False)
            sys.exit(1)

        memory_map = self.memory.get_structure(
            include=self._include_patterns,
            exclude=self._exclude_patterns,
            limit=self._context_limit
        )

        prompt = TemplateManager.render(
            "planner.txt",
            user_intent=user_intent,
            memory_map=memory_map
        )

        for attempt in range(3):
            success, raw, _ = self.agent.run(prompt, "PLANNER")
            if not success: continue

            try:
                data = JsonUtils.parse(raw)
                if "userStories" not in data: raise ValueError("Missing userStories")

                # Validate against JSON schema if --schema is specified
                schema_valid, schema_error = self._validate_prd_schema(data)
                if not schema_valid:
                    Logger.info(f"⚠️ Schema validation failed (Attempt {attempt+1}): {schema_error}", "YELLOW")
                    continue

                # Validate minimum acceptance criteria if --min-criteria is specified
                criteria_valid, criteria_error = self._validate_min_criteria(data)
                if not criteria_valid:
                    Logger.info(f"⚠️ Criteria validation failed (Attempt {attempt+1}): {criteria_error}", "YELLOW")
                    continue

                # Apply labels if --label is specified
                data = self._apply_labels(data)

                self._prd.save(data)
                Logger.info(f"✅ PRD Created ({len(data['userStories'])} stories).", "GREEN")
                self.hooks.emit(Event(EventType.PRD_CREATED, phase="planner", prd_path=str(CONF.PRD_FILE)))
                self.hooks.emit(Event(EventType.PLANNER_SUCCESS, phase="planner"))
                self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
                self._run_post_commands("planner", success=True)
                return
            except Exception as e:
                Logger.info(f"⚠️ JSON Error (Attempt {attempt+1}): {e}", "YELLOW")

        Logger.info("❌ Planning Failed.", "RED")
        self.hooks.emit(Event(EventType.PLANNER_FAILURE, phase="planner"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
        self._run_post_commands("planner", success=False)
        sys.exit(1)

    def execute_loop(self) -> None:
        """
        Execute all pending tasks from the PRD.

        Iterates through user stories, executing each pending task
        with verification. Continues to next task on failure instead
        of terminating. Archives the PRD upon completion.

        Respects the following flags:
        - --test-cmd: Override the test command from memory
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --only: Execute only specified task IDs
        - --except: Skip specified task IDs
        - --resume: Resume execution from a specific task ID
        - --pre: Run pre-commands before phase execution
        - --post: Run post-commands after phase completion
        """
        prd = self._prd.load()
        # Use --test-cmd override if provided, otherwise extract from memory
        test_cmd = self._test_cmd_override if self._test_cmd_override else self.memory.extract_test_command()

        Logger.info(f"\n🚀 Starting Loop. Verify Command: '{test_cmd}'", "YELLOW")
        if self._skip_verify:
            Logger.info("   ⏭️  Verification will be skipped (--skip-verify)", "YELLOW")
        self.hooks.emit(Event(EventType.PHASE_START, phase="execute"))
        self.hooks.emit(Event(EventType.EXECUTE_START, phase="execute", verification_command=test_cmd))

        # Run pre-commands before phase execution
        if not self._run_pre_commands("execute"):
            Logger.info("⚠️ Execute aborted: pre-command failed.", "RED")
            self.hooks.emit(Event(EventType.EXECUTE_END, phase="execute"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="execute"))
            self._run_post_commands("execute", success=False)
            return

        failed_tasks: List[str] = []
        resume_found = self._resume_from is None  # If no --resume, start immediately

        for task in prd.get('userStories', []):
            task_id = task['id']

            # Handle --resume: skip tasks until we find the resume target
            if not resume_found:
                if task_id == self._resume_from:
                    resume_found = True
                    Logger.info(f"   ➡️  Resuming from task {task_id}", "CYAN")
                else:
                    Logger.debug(f"   ⏭️  Skipping {task_id} (before resume point)")
                    continue

            # Handle --only: execute only specified tasks
            if self._only_tasks and task_id not in self._only_tasks:
                Logger.debug(f"   ⏭️  Skipping {task_id} (not in --only list)")
                continue

            # Handle --except: skip specified tasks
            if self._except_tasks and task_id in self._except_tasks:
                Logger.info(f"   ⏭️  Skipping {task_id} (in --except list)", "YELLOW")
                continue

            if task.get('status') == 'completed':
                continue
            # Reset failed tasks to pending so they can be retried
            if task.get('status') == 'failed':
                task['status'] = 'pending'

            Logger.info(f"\n▶️  Task {task['id']}: {task['description']}", "CYAN")
            success = self._execute_task(prd, task, test_cmd)

            # Save state after each task attempt
            self._prd.save(prd)

            if not success:
                failed_tasks.append(task['id'])

        # Check if --resume target was not found
        if not resume_found:
            Logger.warning(f"Resume task '{self._resume_from}' not found in PRD. No tasks executed.")

        # Report summary
        phase_success = len(failed_tasks) == 0
        if failed_tasks:
            Logger.info(f"\n⚠️  {len(failed_tasks)} task(s) failed: {', '.join(failed_tasks)}", "YELLOW")
            Logger.info("Run 'ralph execute' again to retry failed tasks.", "YELLOW")
        else:
            Logger.info("\n🎉 All Tasks Complete.", "GREEN")

        self._archive_prd()
        self.hooks.emit(Event(EventType.EXECUTE_END, phase="execute"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="execute"))
        self._run_post_commands("execute", success=phase_success)

    def _sanitize_id(self, text: str) -> str:
        """Sanitize an ID string to contain only alphanumeric chars and hyphens/underscores."""
        return "".join(c for c in text if c.isalnum() or c in '-_')

    def _load_user_context(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> str:
        """Load and prepare user context from prompt.md with variable substitution."""
        # Use --prompt-file override if provided, otherwise default to prompt.md
        if self._prompt_file_override:
            prompt_md_path = Path(self._prompt_file_override)
            if not prompt_md_path.exists():
                Logger.error(f"Prompt file not found: {self._prompt_file_override}")
                sys.exit(1)
        else:
            prompt_md_path = CONF.BASE_DIR / "prompt.md"
            if not prompt_md_path.exists():
                return "No specific user preferences provided."

        raw_text = prompt_md_path.read_text(encoding='utf-8')
        # Only use prompt file if it has non-empty content
        if not raw_text.strip():
            if self._prompt_file_override:
                Logger.warning(f"Prompt file is empty: {self._prompt_file_override}, using default user context.")
            else:
                Logger.warning("prompt.md exists but is empty, using default user context.")
            return "No specific user preferences provided."

        replacements = {
            "{{PRD_ID}}": self._sanitize_id(prd['id']),
            "{{PRD_DESCRIPTION}}": prd['description'],
            "{{TASK_ID}}": self._sanitize_id(task['id']),
            "{{TASK_DESCRIPTION}}": task['description'],
            "{{TEST_CMD}}": test_cmd,
        }
        pattern = re.compile('|'.join(re.escape(k) for k in replacements.keys()))
        return pattern.sub(lambda m: replacements[m.group()], raw_text)

    def _format_acceptance_criteria(self, task: Dict[str, Any]) -> str:
        """Format acceptance criteria as a bulleted list for the developer prompt."""
        criteria = task.get('acceptanceCriteria', [])
        if not criteria:
            return "(No acceptance criteria specified)"
        return "\n".join(f"- {criterion}" for criterion in criteria)

    def _verify_task(self, task: Dict[str, Any], test_cmd: str) -> Tuple[bool, Optional[AgentError]]:
        """Run verification and return (success, error_if_failed)."""
        Logger.info("   🔒 Verifying Agent's Claim...", "YELLOW")
        self.hooks.emit(Event(
            EventType.VERIFICATION_START, phase="execute",
            task_id=task['id'], verification_command=test_cmd
        ))

        stdout, stderr, code = Shell.run(test_cmd)
        verify_log = f"CMD: {test_cmd}\nEXIT CODE: {code}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        Logger.file_log(verify_log, "VERIFICATION", f"WORKER-{task['id']}")

        if code == 0:
            Logger.info("   ✅ Verified.", "GREEN")
            self.hooks.emit(Event(
                EventType.VERIFICATION_SUCCESS, phase="execute",
                task_id=task['id'], verification_command=test_cmd, verification_exit_code=code
            ))
            return True, None

        Logger.info("   🛑 Agent Hallucinated Success.", "RED")
        error = AgentError(
            exception_type="VerificationError",
            message=f"Test command '{test_cmd}' failed with exit code {code}",
            stack_trace=f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            timestamp=datetime.datetime.now().isoformat(),
            agent_name=self.agent.get_name(),
            task_id=task['id'],
        )
        self.hooks.emit(Event(
            EventType.VERIFICATION_FAILURE, phase="execute",
            task_id=task['id'], verification_command=test_cmd,
            verification_exit_code=code, error=error
        ))
        return False, error

    def _execute_task(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> bool:
        """
        Execute a single task with retries.

        Respects the following flags:
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --timeout: Override agent timeout

        Returns:
            True if task completed successfully, False if max retries exhausted.
        """
        # Use --retries override if provided, otherwise use config default
        max_retries = self._retries_override if self._retries_override is not None else CONF.MAX_RETRIES

        self.hooks.emit(Event(
            EventType.TASK_START, phase="execute",
            task_id=task['id'], task_description=task['description'], max_retries=max_retries
        ))

        for retry in range(max_retries):
            prev_errors = CONF.PROGRESS_FILE.read_text(encoding='utf-8') if CONF.PROGRESS_FILE.exists() else ""
            prompt = TemplateManager.render(
                "developer.txt",
                task_id=task['id'], task_description=task['description'],
                acceptance_criteria=self._format_acceptance_criteria(task),
                memory_tree=self.memory.get_structure(
                    include=self._include_patterns,
                    exclude=self._exclude_patterns,
                    limit=self._context_limit
                ),
                user_context=self._load_user_context(prd, task, test_cmd),
                test_cmd=test_cmd, prev_errors=prev_errors if prev_errors else "(No previous errors)"
            )

            success, output, agent_error = self.agent.run(prompt, f"WORKER-{task['id']}")

            if not success:
                self._record_failure(retry, "CLI Crash", output, agent_error=agent_error, task_id=task['id'])
                self._emit_retry_event(task, retry)
                continue

            if "STATUS: SUCCESS" in output:
                # Handle --skip-verify: skip verification step if flag is set
                if self._skip_verify:
                    Logger.info("   ⏭️  Skipping verification (--skip-verify)", "YELLOW")
                    task['status'] = 'completed'
                    if CONF.PROGRESS_FILE.exists():
                        CONF.PROGRESS_FILE.unlink()
                    self.hooks.emit(Event(
                        EventType.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True

                verified, verify_error = self._verify_task(task, test_cmd)
                if verified:
                    task['status'] = 'completed'
                    if CONF.PROGRESS_FILE.exists():
                        CONF.PROGRESS_FILE.unlink()
                    self.hooks.emit(Event(
                        EventType.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True
                self._record_failure(retry, "Verification Failed", output[-1000:], agent_error=verify_error, task_id=task['id'])
            else:
                error = AgentError(
                    exception_type="AgentReportedFailure",
                    message="Agent did not report STATUS: SUCCESS",
                    stack_trace=f"Agent output (last 2000 chars):\n{output[-2000:]}",
                    timestamp=datetime.datetime.now().isoformat(),
                    agent_name=self.agent.get_name(),
                    task_id=task['id'],
                )
                self._record_failure(retry, "Agent Reported Failure", output[-1000:], agent_error=error, task_id=task['id'])

            self._emit_retry_event(task, retry, max_retries)

        Logger.info(f"🛑 Max retries for {task['id']}. Marking as failed and continuing.", "RED")
        task['status'] = 'failed'
        self.hooks.emit(Event(
            EventType.TASK_FAILURE, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=max_retries, max_retries=max_retries
        ))
        return False

    def _emit_retry_event(self, task: Dict[str, Any], retry: int, max_retries: Optional[int] = None) -> None:
        """Emit a task retry event."""
        if max_retries is None:
            max_retries = self._retries_override if self._retries_override is not None else CONF.MAX_RETRIES
        self.hooks.emit(Event(
            EventType.TASK_RETRY, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=retry + 1, max_retries=max_retries
        ))

    def _record_failure(self, retry: int, reason: str, detail: str, agent_error: Optional[AgentError] = None, task_id: Optional[str] = None) -> None:
        if agent_error:
            msg = (
                f"Attempt {retry+1} Failed: {reason}\n"
                f"--- Structured Error Context ---\n"
                f"{agent_error.format_log_entry()}\n"
                f"--- Agent Output (last 1000 chars) ---\n"
                f"{detail}"
            )
        else:
            msg = f"Attempt {retry+1} Failed: {reason}\n{detail}"
        CONF.PROGRESS_FILE.write_text(msg, encoding='utf-8')
        Logger.file_log(msg, "FAILURE_RECORD", f"RETRY-{retry+1}")
        Logger.info(f"   ⚠️ Retry {retry+1}/{CONF.MAX_RETRIES}: {reason}", "RED")
        self.hooks.emit(Event(
            EventType.ERROR,
            phase="execute",
            task_id=task_id or (agent_error.task_id if agent_error else None),
            error=agent_error,
            metadata={"reason": reason, "retry": retry + 1}
        ))

    def _archive_prd(self) -> None:
        if not self._prd.exists():
            return
        # Respect --no-archive flag (archive is default behavior)
        if not self._archive:
            Logger.debug("Skipping PRD archival (--no-archive)")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = CONF.ARCHIVE_DIR / f"prd_{ts}.json"
        shutil.move(str(CONF.PRD_FILE), str(dest))
        self._prd.invalidate_cache()
        Logger.info(f"📦 PRD Archived to {dest}", "MAGENTA")
        self.hooks.emit(Event(EventType.PRD_ARCHIVED, prd_path=str(dest)))

    def _export_memory(self, output_path: str) -> None:
        """
        Export memory contents to a file.

        Concatenates all memory files into a single output file for external use.

        Args:
            output_path: Path to write the exported memory content
        """
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        content_parts = []
        for path in sorted(CONF.MEMORY_DIR.rglob('*')):
            if path.is_file() and not path.name.startswith('.'):
                try:
                    rel_path = path.relative_to(CONF.MEMORY_DIR)
                    file_content = path.read_text(encoding='utf-8')
                    content_parts.append(f"# {rel_path}\n\n{file_content}")
                except Exception as e:
                    Logger.warning(f"Could not read memory file {path}: {e}")

        if content_parts:
            out_path.write_text("\n\n---\n\n".join(content_parts), encoding='utf-8')
            Logger.info(f"📋 Memory exported to {out_path}", "MAGENTA")
        else:
            Logger.warning("No memory files to export.")

    def _print_prd(self) -> None:
        """
        Print the PRD contents to stdout.

        Outputs the PRD as formatted JSON for inspection without execution.
        """
        if not self._prd.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        if self._json_output or self._ndjson_output:
            # For JSON/NDJSON mode, output as-is (already JSON)
            print(self._prd.read_raw())
        else:
            # Pretty print with indentation
            prd_data = self._prd.load()
            print(json.dumps(prd_data, indent=2))

    def _export_prd(self, output_path: str) -> None:
        """
        Export the PRD to a specified file.

        Args:
            output_path: Path to write the PRD content
        """
        if not self._prd.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self._prd.read_raw(), encoding='utf-8')
        Logger.info(f"📋 PRD exported to {out_path}", "MAGENTA")

    def _check_prd_status(self) -> int:
        """
        Check PRD status and return appropriate exit code.

        Returns:
            0 if all tasks completed, 1 if tasks pending/failed, 2 if no PRD exists.
        """
        if not self._prd.exists():
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("No PRD file found", "error", status="no_prd", exit_code=2))
            else:
                Logger.error("No PRD file found. Run planner first.")
            return 2

        prd = self._prd.load()
        tasks = prd.get('userStories', [])

        if not tasks:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("PRD has no tasks", "warn", status="empty", exit_code=1))
            else:
                Logger.warning("PRD has no tasks.")
            return 1

        completed = sum(1 for t in tasks if t.get('status') == 'completed')
        failed = sum(1 for t in tasks if t.get('status') == 'failed')
        pending = sum(1 for t in tasks if t.get('status') in ('pending', None))
        total = len(tasks)

        status_data = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
        }

        if completed == total:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("All tasks completed", "info", status="success", exit_code=0, **status_data))
            else:
                Logger.info(f"✅ All {total} task(s) completed.", "GREEN")
            return 0
        else:
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message("Tasks incomplete", "warn", status="incomplete", exit_code=1, **status_data))
            else:
                Logger.warning(f"Tasks incomplete: {completed}/{total} completed, {failed} failed, {pending} pending.")
            return 1

    def _validate_memory_on_startup(self) -> None:
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            return
        result = self.memory.validate_memory()
        if result['total'] == 0:
            return
        for key, label in [('corrupted', 'corrupted'), ('empty', 'empty')]:
            if result[key]:
                Logger.info(f"⚠️ Memory: {len(result[key])} {label} file(s): {', '.join(result[key])}", "YELLOW")
        if result['valid']:
            Logger.debug(f"✅ Memory OK ({result['total']} files)", "GREEN")

    def _prompt_user_for_phase(self, phase_name: str) -> bool:
        """Prompt user to run a phase, or fail in non-interactive mode."""
        if self._non_interactive:
            Logger.error(f"Cannot prompt for {phase_name} phase in non-interactive mode. Use --accept-all (-y) to auto-accept.")
            sys.exit(1)
        return input(f"{Logger.COLORS['YELLOW']}Run {phase_name} phase? (y/n): {Logger.COLORS['RESET']}").strip().lower() == 'y'

    def _get_intent(self, user_intent=None):
        """Get intent from flags or interactive prompt."""
        if user_intent:
            return user_intent
        # Check --intent flag
        if self._intent:
            return self._intent
        # Check --intent-file flag
        if self._intent_file:
            intent_path = Path(self._intent_file)
            if not intent_path.exists():
                Logger.error(f"Intent file not found: {self._intent_file}")
                sys.exit(1)
            content = intent_path.read_text(encoding='utf-8').strip()
            if not content:
                Logger.error(f"Intent file is empty: {self._intent_file}")
                sys.exit(1)
            return content
        # Non-interactive mode requires --intent or --intent-file
        if self._non_interactive:
            Logger.error("Intent required in non-interactive mode. Use --intent or --intent-file.")
            sys.exit(1)
        # Interactive prompt
        intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
        if not intent:
            sys.exit(0)
        return intent

    def _run_single_phase(self, phase: str) -> None:
        """Run a single specified phase with prerequisite checks."""
        Logger.info(f"📋 Phase: {phase} only", "YELLOW")

        if phase == "planner" and not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("❌ Memory missing. Run architect first.", "RED")
            sys.exit(1)
        if phase == "execute" and not self._prd.exists():
            Logger.info("❌ PRD missing. Run planner first.", "RED")
            sys.exit(1)

        if phase == "execute":
            self.execute_loop()
        else:
            user_intent = self._get_intent()
            if phase == "architect":
                self.run_architect(user_intent)
            else:
                self.run_planner(user_intent)

        Logger.info(f"✅ {phase.title()} complete.", "GREEN")

    def _run_all_phases(self, accept_all: bool) -> None:
        """Run all phases with optional user confirmation."""
        Logger.info("📋 Running all phases...", "YELLOW")
        user_intent = None

        # Architect phase
        if any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("📋 Memory exists, skipping architect.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Architect"):
            user_intent = self._get_intent()
            self.run_architect(user_intent)
        else:
            Logger.info("⏭️ Skipping architect.", "YELLOW")

        # Planner phase
        if self._prd.exists():
            Logger.info("📋 PRD exists, skipping planner.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Planner"):
            user_intent = self._get_intent(user_intent)
            self.run_planner(user_intent)
        else:
            Logger.info("⏭️ Skipping planner.", "YELLOW")

        # Execute phase
        if accept_all or self._prompt_user_for_phase("Execute"):
            self.execute_loop()
        else:
            Logger.info("⏭️ Skipping execute.", "YELLOW")

        Logger.info("✅ All phases complete.", "GREEN")

    def start(self, phase: str = "all", accept_all: bool = False) -> None:
        """
        Start the Ralph orchestrator.

        Args:
            phase: Which phase to run ("architect", "planner", "execute", or "all")
            accept_all: If True, skip user confirmation prompts

        Respects the following flags:
        - --print-prd: Print PRD contents and exit without executing
        - --prd-out: Export PRD to specified file and continue
        - --status-check: Check PRD status and exit with appropriate code
        """
        # Handle --status-check flag: check PRD status and exit
        if self._status_check:
            exit_code = self._check_prd_status()
            sys.exit(exit_code)

        # Handle --print-prd flag: print PRD and exit
        if self._print_prd_flag:
            self._print_prd()
            return

        # Handle --prd-out flag: export PRD to file
        if self._prd_out:
            self._export_prd(self._prd_out)

        Logger.info(f"🤖 Ralph {self.agent.get_name()} Agent active in: {CONF.BASE_DIR}", "GREEN")

        if phase in ("architect", "planner", "execute"):
            self._run_single_phase(phase)
        else:
            self._run_all_phases(accept_all)

def get_version() -> str:
    try:
        with open(Path(__file__).parent / "pyproject.toml", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', line)
                    if match:
                        return match.group(1)
    except (OSError, UnicodeDecodeError) as e:
        Logger.debug(f"Failed to read version from pyproject.toml: {type(e).__name__}: {e}")
    return "unknown"

def main() -> None:
    """Entry point for the ralph CLI."""
    agent = list_agents()[0]
    parser = argparse.ArgumentParser(description="Ralph - Autonomous Software Development Agent",
        epilog="Examples: ralph | ralph architect | ralph -y execute | ralph -vvv --no-emoji execute",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?", help="Phase to run")
    parser.add_argument("--version", action="version", version=f"Ralph {get_version()}")
    parser.add_argument("--accept-all", "-y", action="store_true", help="Skip prompts")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity (-v, -vv, -vvv)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress non-essential output")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument("--no-emoji", action="store_true", help="Replace emojis with text equivalents")
    parser.add_argument("--no-hooks", action="store_true", help="Disable hook execution")
    parser.add_argument("--hooks", nargs="+", metavar="NAME", help="Enable only specified hooks by name")
    parser.add_argument("--agent", choices=list_agents(), default=agent, help=f"Agent (default: {agent})")
    # Intent and input flags for non-interactive runs
    parser.add_argument("--intent", type=str, metavar="TEXT", help="Provide intent inline (what to build)")
    parser.add_argument("--intent-file", type=str, metavar="FILE", help="Load intent from a file")
    parser.add_argument("--prompt-file", type=str, metavar="FILE", help="Override prompt.md path for user context")
    # Architect control flags for context generation
    parser.add_argument("--tree-depth", type=int, default=2, metavar="N", help="File tree depth for architect (default: 2)")
    parser.add_argument("--tree-ignore", nargs="+", metavar="PATTERN", help="Patterns to ignore in file tree (default: node_modules, venv, .git, .ralph, __pycache__)")
    parser.add_argument("--memory-out", type=str, metavar="FILE", help="Export memory contents to file after architect phase")
    # Execution and verification flags for task control
    parser.add_argument("--test-cmd", type=str, metavar="CMD", help="Override test command for verification")
    parser.add_argument("--skip-verify", action="store_true", help="Skip verification step after task execution")
    parser.add_argument("--retries", type=int, metavar="N", help="Override max retries per task (default: 3)")
    parser.add_argument("--timeout", type=int, metavar="SECS", help="Override agent timeout in seconds (default: 600)")
    parser.add_argument("--only", nargs="+", metavar="TASK_ID", help="Execute only specified task IDs")
    parser.add_argument("--except", dest="except_tasks", nargs="+", metavar="TASK_ID", help="Skip specified task IDs")
    parser.add_argument("--resume", type=str, metavar="TASK_ID", help="Resume execution from a specific task ID")
    # Context and memory control flags for file filtering
    parser.add_argument("--include", nargs="+", metavar="PATTERN", help="Include only files matching these glob patterns in context")
    parser.add_argument("--exclude", nargs="+", metavar="PATTERN", help="Exclude files matching these glob patterns from context")
    parser.add_argument("--context-limit", type=int, metavar="N", help="Limit maximum number of context files considered")
    # Model and prompting flags for LLM customization
    parser.add_argument("--model", type=str, metavar="MODEL", help="Model identifier for LLM requests (e.g., claude-3-opus)")
    parser.add_argument("--temperature", type=float, metavar="TEMP", help="Sampling temperature (0.0-1.0) for response generation")
    parser.add_argument("--max-tokens", type=int, metavar="N", help="Maximum number of tokens in the LLM response")
    parser.add_argument("--seed", type=int, metavar="N", help="Random seed for reproducible outputs")
    # I/O, logging and output flags
    parser.add_argument("--log-file", type=str, metavar="FILE", help="Redirect log output to specified file")
    parser.add_argument("--log-level", type=str, choices=["debug", "info", "warn", "error"], metavar="LEVEL", help="Set log level (debug, info, warn, error)")
    # Output format flags (mutually exclusive)
    output_format_group = parser.add_mutually_exclusive_group()
    output_format_group.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")
    output_format_group.add_argument("--ndjson", dest="ndjson_output", action="store_true", help="Output in newline-delimited JSON format")
    # PRD output flags
    parser.add_argument("--print-prd", action="store_true", help="Print PRD contents and exit without executing")
    parser.add_argument("--prd-out", type=str, metavar="FILE", help="Export PRD to specified file")
    # Archive control flag
    parser.add_argument("--no-archive", action="store_false", dest="archive_enabled", default=True, help="Skip PRD archival after execution")
    # Headless operation flags for CI/CD pipelines
    parser.add_argument("--non-interactive", action="store_true", help="Disable all interactive prompts (fails if input required)")
    parser.add_argument("--ci", action="store_true", help="CI mode: enables --non-interactive --no-color --no-emoji --json")
    parser.add_argument("--status-check", action="store_true", help="Check PRD status and exit with code (0=complete, 1=incomplete, 2=no PRD)")
    # Extensibility and hook flags for custom commands and validators
    parser.add_argument("--pre", nargs="+", metavar="CMD", help="Shell command(s) to run before each phase (aborts on failure)")
    parser.add_argument("--post", nargs="+", metavar="CMD", help="Shell command(s) to run after each phase (receives RALPH_PHASE, RALPH_SUCCESS env vars)")
    parser.add_argument("--plugin", nargs="+", metavar="PATH", help="Load plugin(s) from Python file or directory path")
    # Safety and privacy flags for protecting sensitive data
    parser.add_argument("--redact", nargs="+", metavar="PATTERN", help="Regex patterns to redact from logs (e.g., API keys, passwords)")
    parser.add_argument("--redact-file", type=str, metavar="FILE", help="Load redaction patterns from file (one pattern per line)")
    parser.add_argument("--no-log-prompts", action="store_true", help="Do not log prompts to log file (protects sensitive input)")
    parser.add_argument("--no-log-responses", action="store_true", help="Do not log responses to log file (protects sensitive output)")
    # PRD and story control flags for validation and annotation
    parser.add_argument("--schema", type=str, metavar="FILE", help="Validate generated PRD against a JSON schema file")
    parser.add_argument("--min-criteria", type=int, metavar="N", help="Require at least N acceptance criteria per user story")
    parser.add_argument("--label", nargs="+", metavar="KEY=VAL", help="Add custom labels to PRD (format: key=value or just key)")
    args = parser.parse_args()

    # Handle --ci flag: apply CI defaults before other options
    # --ci implies: --non-interactive --no-color --no-emoji --json
    ci_mode = args.ci
    non_interactive = args.non_interactive or ci_mode

    # Configure logger settings
    Logger.set_verbosity(args.verbose)
    Logger.set_quiet(args.quiet)
    Logger.set_no_emoji(args.no_emoji or ci_mode)
    Logger.set_non_interactive(non_interactive)
    # Handle color: --no-color disables, --ci disables (default: colors enabled)
    if args.no_color or ci_mode:
        Logger.set_no_color(True)
    # Configure I/O and output format settings
    if args.log_file:
        Logger.set_log_file(args.log_file)
    if args.log_level:
        Logger.set_log_level(args.log_level)
    # --ci enables JSON output unless --ndjson is explicitly specified
    # NDJSON takes precedence over CI's default JSON output
    if args.ndjson_output:
        Logger.set_ndjson_output(True)
    elif args.json_output or ci_mode:
        Logger.set_json_output(True)
    # Configure safety and privacy flags
    if args.redact:
        Logger.set_redact_patterns(args.redact)
    if args.redact_file:
        Logger.add_redact_patterns_from_file(args.redact_file)
    if args.no_log_prompts:
        Logger.set_no_log_prompts(True)
    if args.no_log_responses:
        Logger.set_no_log_responses(True)

    # Determine hook configuration
    enable_hooks = not args.no_hooks
    enabled_hook_names = args.hooks if args.hooks else None

    # Validate mutually exclusive intent options
    if args.intent and args.intent_file:
        Logger.error("Cannot use both --intent and --intent-file together.")
        sys.exit(1)

    RalphOrchestrator(
        agent_name=args.agent,
        enable_hooks=enable_hooks,
        enabled_hook_names=enabled_hook_names,
        intent=args.intent,
        intent_file=args.intent_file,
        prompt_file=args.prompt_file,
        tree_depth=args.tree_depth,
        tree_ignore=args.tree_ignore,
        memory_out=args.memory_out,
        test_cmd=args.test_cmd,
        skip_verify=args.skip_verify,
        retries=args.retries,
        timeout=args.timeout,
        only=args.only,
        except_tasks=args.except_tasks,
        resume=args.resume,
        include=args.include,
        exclude=args.exclude,
        context_limit=args.context_limit,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        seed=args.seed,
        log_file=args.log_file,
        log_level=args.log_level,
        json_output=args.json_output,
        ndjson_output=args.ndjson_output,
        print_prd=args.print_prd,
        prd_out=args.prd_out,
        archive=args.archive_enabled,
        non_interactive=non_interactive,
        ci=ci_mode,
        status_check=args.status_check,
        pre=args.pre,
        post=args.post,
        plugin=args.plugin,
        schema=args.schema,
        min_criteria=args.min_criteria,
        label=args.label
    ).start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()
