#!/usr/bin/env python3
import json
import subprocess
import sys
import re
import shutil
import datetime
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from enum import IntEnum
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
# QA FINDINGS ANALYSIS
# ==============================================================================


class QAFindingType(IntEnum):
    """Finding severity/type categories ordered by severity (highest first)."""
    ERROR = 0      # Critical issues (maps to 'critical_issues' in JSON)
    WARNING = 1    # Warnings that should be addressed
    SUGGESTION = 2 # Nice-to-have improvements
    INFO = 3       # Informational notes


@dataclass
class QAFinding:
    """Represents a single QA finding with location and severity information."""
    finding_type: QAFindingType
    category: str
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    recommendation: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    has_missing_data: bool = False
    missing_data_note: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], finding_type: QAFindingType) -> 'QAFinding':
        """Create a QAFinding from a dictionary, handling malformed/incomplete data.

        Args:
            data: Dictionary containing finding data from QA JSON output
            finding_type: The severity/type of this finding

        Returns:
            QAFinding instance with available data and notes about missing fields
        """
        missing_fields = []
        has_missing = False

        # Extract category with fallback
        category = data.get('category')
        if not category or not isinstance(category, str):
            category = 'unknown'
            missing_fields.append('category')
            has_missing = True

        # Extract description with fallback
        description = data.get('description')
        if not description or not isinstance(description, str):
            description = 'No description provided'
            missing_fields.append('description')
            has_missing = True

        # Parse location field (format: "file:line" or "file" or general text)
        file_path = None
        line_number = None
        location = data.get('location', '')

        if location and isinstance(location, str):
            # Try to parse "file:line" format
            if ':' in location:
                parts = location.rsplit(':', 1)
                potential_file = parts[0].strip()
                potential_line = parts[1].strip()

                # Check if the second part looks like a line number
                if potential_line.isdigit():
                    file_path = potential_file
                    line_number = int(potential_line)
                else:
                    # Could be file path with colon (e.g., C:\path) or general description
                    file_path = location.strip()
            else:
                # Just a file path or general description
                file_path = location.strip()
        else:
            missing_fields.append('location')
            has_missing = True

        # Extract recommendation
        recommendation = data.get('recommendation')
        if recommendation and not isinstance(recommendation, str):
            recommendation = None

        # Build missing data note
        missing_note = None
        if missing_fields:
            missing_note = f"Missing fields: {', '.join(missing_fields)}"

        return cls(
            finding_type=finding_type,
            category=category,
            description=description,
            file_path=file_path,
            line_number=line_number,
            recommendation=recommendation,
            raw_data=data,
            has_missing_data=has_missing,
            missing_data_note=missing_note
        )

    def format_location(self) -> str:
        """Format the location string for display.

        Returns:
            Formatted location string (e.g., "src/file.py:42" or "src/file.py" or "")
        """
        if self.file_path and self.line_number:
            return f"{self.file_path}:{self.line_number}"
        elif self.file_path:
            return self.file_path
        return ""


class QAFindingsAnalyzer:
    """Analyzes and categorizes QA findings from review output.

    Provides functionality to:
    - Parse and categorize findings by type (error, warning, suggestion, info)
    - Group findings by file path
    - Sort findings by severity
    - Handle pagination for large outputs
    - Handle malformed or incomplete finding data gracefully
    """

    # Default number of findings to display before summarizing
    DEFAULT_PAGE_SIZE = 10
    # Threshold for considering output as "large" (spanning many files)
    LARGE_OUTPUT_THRESHOLD = 50

    def __init__(self, findings_dict: Dict[str, Any], page_size: int = DEFAULT_PAGE_SIZE):
        """Initialize the analyzer with parsed QA findings.

        Args:
            findings_dict: Parsed QA findings dictionary from agent response
            page_size: Maximum number of findings to display per category before summarizing
        """
        self._raw_findings = findings_dict
        self._page_size = page_size
        self._findings: List[QAFinding] = []
        self._findings_by_file: Dict[str, List[QAFinding]] = {}
        self._summary = findings_dict.get('summary', 'UNKNOWN')
        self._passed_checks = findings_dict.get('passed_checks', [])
        self._parse_findings()

    def _parse_findings(self) -> None:
        """Parse all findings from the raw dictionary."""
        type_mapping = {
            'critical_issues': QAFindingType.ERROR,
            'warnings': QAFindingType.WARNING,
            'suggestions': QAFindingType.SUGGESTION,
            'info': QAFindingType.INFO
        }

        for key, finding_type in type_mapping.items():
            items = self._raw_findings.get(key, [])
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    # Handle non-dict items by creating a minimal finding
                    finding = QAFinding(
                        finding_type=finding_type,
                        category='unknown',
                        description=str(item) if item else 'Invalid finding data',
                        has_missing_data=True,
                        missing_data_note='Finding was not a valid dictionary'
                    )
                else:
                    finding = QAFinding.from_dict(item, finding_type)

                self._findings.append(finding)
                self._add_to_file_group(finding)

    def _add_to_file_group(self, finding: QAFinding) -> None:
        """Add a finding to the appropriate file group."""
        file_key = finding.file_path or '(no file)'
        if file_key not in self._findings_by_file:
            self._findings_by_file[file_key] = []
        self._findings_by_file[file_key].append(finding)

    @property
    def summary(self) -> str:
        """Get the overall summary status."""
        return self._summary

    @property
    def passed_checks(self) -> List[str]:
        """Get the list of passed checks."""
        return self._passed_checks

    @property
    def all_findings(self) -> List[QAFinding]:
        """Get all findings sorted by severity (errors first)."""
        return sorted(self._findings, key=lambda f: f.finding_type)

    @property
    def is_large_output(self) -> bool:
        """Check if output spans many files (above threshold)."""
        return len(self._findings_by_file) > self.LARGE_OUTPUT_THRESHOLD

    @property
    def total_files(self) -> int:
        """Get total number of files with findings."""
        return len(self._findings_by_file)

    def get_findings_by_type(self, finding_type: QAFindingType) -> List[QAFinding]:
        """Get all findings of a specific type.

        Args:
            finding_type: The type of findings to retrieve

        Returns:
            List of findings matching the specified type
        """
        return [f for f in self._findings if f.finding_type == finding_type]

    def get_findings_grouped_by_file(self) -> Dict[str, List[QAFinding]]:
        """Get findings grouped by file, with each group sorted by severity.

        Returns:
            Dictionary mapping file paths to lists of findings, sorted by severity
        """
        result = {}
        # Sort files: files with errors first, then by path
        sorted_files = sorted(
            self._findings_by_file.keys(),
            key=lambda f: (
                min((finding.finding_type for finding in self._findings_by_file[f]), default=QAFindingType.INFO),
                f
            )
        )

        for file_key in sorted_files:
            # Sort findings within each file by severity
            result[file_key] = sorted(
                self._findings_by_file[file_key],
                key=lambda f: f.finding_type
            )

        return result

    def get_counts(self) -> Dict[str, int]:
        """Get counts of findings by type.

        Returns:
            Dictionary with counts for each finding type
        """
        return {
            'errors': len(self.get_findings_by_type(QAFindingType.ERROR)),
            'warnings': len(self.get_findings_by_type(QAFindingType.WARNING)),
            'suggestions': len(self.get_findings_by_type(QAFindingType.SUGGESTION)),
            'info': len(self.get_findings_by_type(QAFindingType.INFO)),
            'total': len(self._findings),
            'files': len(self._findings_by_file)
        }

    def get_summary_report(self) -> Dict[str, Any]:
        """Get a summary report suitable for pagination.

        Returns:
            Dictionary with summary statistics and truncated findings lists
        """
        counts = self.get_counts()
        grouped = self.get_findings_grouped_by_file()

        # Build paginated/summarized output
        summary_files = {}
        files_shown = 0
        total_files = len(grouped)

        for file_path, findings in grouped.items():
            if files_shown >= self._page_size and self.is_large_output:
                break
            summary_files[file_path] = [
                {
                    'type': f.finding_type.name.lower(),
                    'category': f.category,
                    'description': f.description,
                    'line_number': f.line_number,
                    'recommendation': f.recommendation,
                    'has_missing_data': f.has_missing_data,
                    'missing_data_note': f.missing_data_note
                }
                for f in findings[:self._page_size]
            ]
            if len(findings) > self._page_size:
                remaining = len(findings) - self._page_size
                summary_files[file_path].append({
                    'type': 'truncated',
                    'description': f'... and {remaining} more finding(s) in this file'
                })
            files_shown += 1

        remaining_files = total_files - files_shown
        return {
            'summary': self._summary,
            'counts': counts,
            'files': summary_files,
            'remaining_files': remaining_files if remaining_files > 0 else 0,
            'is_paginated': self.is_large_output,
            'passed_checks': self._passed_checks
        }

    def format_for_display(self, show_full: bool = False, verbosity: int = 0) -> str:
        """Format findings for display output.

        Args:
            show_full: If True, show all findings regardless of count
            verbosity: Verbosity level (0=normal, 1=verbose, 2+=debug)

        Returns:
            Formatted string for display
        """
        lines = []
        counts = self.get_counts()
        grouped = self.get_findings_grouped_by_file()

        # Summary line
        lines.append(f"QA Summary: {self._summary}")
        lines.append(f"Files: {counts['files']} | Errors: {counts['errors']} | "
                     f"Warnings: {counts['warnings']} | Suggestions: {counts['suggestions']}")

        if not self._findings:
            lines.append("No issues found.")
            return '\n'.join(lines)

        # Determine display limits based on verbosity and show_full
        if show_full:
            file_limit = len(grouped)
            finding_limit = float('inf')
        elif self.is_large_output:
            file_limit = self._page_size
            finding_limit = 5
        else:
            file_limit = len(grouped)
            finding_limit = 10 if verbosity >= 1 else 5

        lines.append("")
        files_shown = 0

        for file_path, findings in grouped.items():
            if files_shown >= file_limit:
                remaining = len(grouped) - files_shown
                lines.append(f"\n... and {remaining} more file(s) with findings")
                lines.append("Use --verbose or JSON output to see full details")
                break

            lines.append(f"\n{file_path}:")
            findings_shown = 0

            for finding in findings:
                if findings_shown >= finding_limit:
                    remaining = len(findings) - findings_shown
                    lines.append(f"  ... and {remaining} more finding(s)")
                    break

                type_prefix = {
                    QAFindingType.ERROR: "  [ERROR]",
                    QAFindingType.WARNING: "  [WARN]",
                    QAFindingType.SUGGESTION: "  [SUGG]",
                    QAFindingType.INFO: "  [INFO]"
                }.get(finding.finding_type, "  [?]")

                location = ""
                if finding.line_number:
                    location = f":{finding.line_number}"

                lines.append(f"{type_prefix}{location} [{finding.category}] {finding.description}")

                if finding.recommendation and verbosity >= 1:
                    lines.append(f"    -> {finding.recommendation}")

                if finding.has_missing_data and finding.missing_data_note:
                    lines.append(f"    (Note: {finding.missing_data_note})")

                findings_shown += 1

            files_shown += 1

        # Show passed checks at verbose level
        if verbosity >= 1 and self._passed_checks:
            lines.append(f"\nPassed checks: {', '.join(self._passed_checks)}")

        return '\n'.join(lines)


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
Analyze the project structure and initialize comprehensive architecture documentation following software engineering best practices.

# CONTEXT

## User Intent
<USER_INTENT>
{{user_intent}}
</USER_INTENT>

## Project File Tree
<FILE_TREE>
{{file_tree}}
</FILE_TREE>

# ANALYSIS REQUIREMENTS

## 1. SOLID Principles Analysis
When analyzing code structure, evaluate adherence to SOLID principles:
- **Single Responsibility**: Each module/class should have one reason to change
- **Open/Closed**: Code should be open for extension, closed for modification
- **Liskov Substitution**: Subtypes must be substitutable for their base types
- **Interface Segregation**: Prefer specific interfaces over general-purpose ones
- **Dependency Inversion**: Depend on abstractions, not concrete implementations

Document any violations or areas for improvement in the architecture documentation.

## 2. Architectural Patterns Identification
Identify and document which architectural patterns are present in the codebase:
- **MVC/MVP/MVVM**: Model-View-Controller and variants
- **Layered Architecture**: Presentation, business logic, data access layers
- **Microservices**: Independent, deployable services
- **Event-Driven**: Pub/sub, message queues, event sourcing
- **Repository Pattern**: Data access abstraction
- **Clean Architecture**: Dependency rules, use cases, entities
- **Hexagonal/Ports & Adapters**: Core domain isolated from external concerns

Note which patterns are used and how consistently they are applied.

## 3. Security Considerations
Analyze and document security aspects of the architecture:
- **Authentication/Authorization**: How identity and permissions are handled
- **Input Validation**: Where and how user input is validated
- **Data Protection**: Encryption at rest and in transit
- **Secrets Management**: How API keys, passwords, and tokens are stored
- **OWASP Top 10**: Potential vulnerabilities (injection, XSS, CSRF, etc.)
- **Dependency Security**: Third-party package vulnerability risks

Flag any security concerns or gaps that require attention.

## 4. Error Handling and Logging Patterns
Document the error handling and logging strategies used:
- **Error Handling Strategy**: Try/catch patterns, error boundaries, fallbacks
- **Error Propagation**: How errors bubble up through layers
- **Logging Framework**: What logging library/approach is used
- **Log Levels**: How different severity levels are applied
- **Error Reporting**: Integration with monitoring/alerting systems
- **Graceful Degradation**: How the system handles partial failures

## 5. API Boundaries and Integration Points
Identify and document all API boundaries and integration points:
- **External APIs**: Third-party services the codebase integrates with
- **Internal APIs**: Module boundaries and internal service interfaces
- **Data Formats**: JSON, XML, Protocol Buffers, etc.
- **Communication Protocols**: REST, GraphQL, gRPC, WebSocket, etc.
- **Database Interfaces**: ORM usage, raw queries, connection management
- **File System Interfaces**: File I/O patterns and locations
- **Environment Dependencies**: Config files, environment variables, secrets

# STRUCTURED REASONING PROTOCOL

Before executing any action, you MUST complete this reasoning framework:

## Step 1: Pre-Execution Analysis
Answer these questions explicitly in your thinking:
1. **Scope Assessment**: What is the boundary of my analysis? What am I including/excluding and why?
2. **Approach Justification**: What method will I use to analyze this codebase? Why is this approach suitable?
3. **Expected Outcomes**: What deliverables do I expect to produce? What format and content?
4. **Risk Identification**: What could go wrong? What assumptions am I making?

## Step 2: Validation Checkpoints
At each major step, verify before proceeding:
- [ ] **File Tree Verification**: Have I examined the file tree structure completely?
- [ ] **Technology Detection Confidence**: Am I confident about the technologies I've identified? If unsure, what additional evidence would I need?
- [ ] **Pattern Recognition Accuracy**: Are the architectural patterns I've identified actually present, or am I inferring from limited evidence?
- [ ] **Completeness Check**: Have I addressed all required sections (SOLID, patterns, security, error handling, API boundaries)?

## Step 3: Evidence-Based Conclusions
For each conclusion in your analysis:
- Cite specific file paths or patterns that support your conclusion
- Distinguish between observed facts and inferences
- Rate your confidence level: HIGH (direct evidence), MEDIUM (strong inference), LOW (limited evidence)

# FALLBACK STRATEGIES

## When Primary Analysis Approaches Fail

### Scenario 1: Minimal File Tree Information
**Primary approach fails when**: File tree is sparse or lacks typical project structure indicators
**Fallback strategy**:
1. Focus on file extensions to infer language(s)
2. Look for configuration files (package.json, pyproject.toml, Cargo.toml, etc.)
3. Identify entry points by common naming (main.*, index.*, app.*)
4. Document uncertainty explicitly in output

### Scenario 2: Unfamiliar Technology Stack
**Primary approach fails when**: Technologies present are outside common patterns
**Fallback strategy**:
1. Identify configuration files and their formats
2. Document what CAN be determined with confidence
3. Explicitly list technologies that could not be identified
4. Recommend manual verification for uncertain elements

### Scenario 3: Inconsistent or Legacy Architecture
**Primary approach fails when**: Codebase shows mixed patterns or no clear architecture
**Fallback strategy**:
1. Document the observed inconsistencies rather than forcing a pattern
2. Note areas where architecture is unclear
3. Identify dominant patterns even if not universally applied
4. Flag areas that may need architectural review

### Scenario 4: Missing Test Information
**Primary approach fails when**: No clear test framework or test files visible
**Fallback strategy**:
1. Check for common test directories (test/, tests/, spec/, __tests__/)
2. Look for test configuration files (pytest.ini, jest.config.js, etc.)
3. If still unclear, document "Test Command: [Unable to determine - manual verification required]"
4. Note the absence of visible testing infrastructure as a finding

# LIMITATIONS AND UNCERTAINTY ACKNOWLEDGMENT

You MUST explicitly acknowledge limitations and uncertainty in your output:

## Required Disclosures
1. **Analysis Scope Limitations**: What aspects of the codebase could NOT be analyzed from the file tree alone?
2. **Confidence Levels**: For each major finding, indicate whether it is:
   - CONFIRMED: Directly visible in file tree or explicitly stated
   - INFERRED: Reasonable conclusion based on available evidence
   - UNCERTAIN: Limited evidence, requires verification
3. **Missing Information**: What information would improve the analysis if available?
4. **Assumptions Made**: List any assumptions made during analysis

## Output Format for Uncertainty
When documenting uncertain findings, use this format:
- "Based on [evidence], [conclusion] (Confidence: HIGH/MEDIUM/LOW)"
- "Unable to determine [aspect] due to [reason]. Recommendation: [next step]"
- "Assumption: [assumption made]. If incorrect, [impact on analysis]"

# CONSTRAINTS
- You MUST create exactly two files: .ralph/memory/architecture.md and ARCH.md
- You MUST use the exact YAML frontmatter format specified below
- You MUST include ALL required sections in the exact order specified
- You MUST detect the actual test command from the project (pytest, npm test, etc.)
- You MUST NOT invent or assume technologies not evident in the file tree
- You MUST apply SOLID principles analysis when evaluating code structure
- You MUST identify architectural patterns present in the codebase
- You MUST document security considerations and potential vulnerabilities
- You MUST document error handling strategies and logging patterns
- You MUST identify API boundaries and integration points
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

## Architectural Patterns
[Identify patterns used: MVC, Layered, Microservices, Event-Driven, etc.]
- **Primary Pattern**: [main architectural pattern]
- **Supporting Patterns**: [additional patterns used]

## Key Components
| Component | Description |
|-----------|-------------|
| [path/file] | [brief description] |
[list 3-6 key components]

## SOLID Principles Assessment
| Principle | Status | Notes |
|-----------|--------|-------|
| Single Responsibility | [Good/Needs Work] | [brief observation] |
| Open/Closed | [Good/Needs Work] | [brief observation] |
| Liskov Substitution | [Good/N/A] | [brief observation] |
| Interface Segregation | [Good/Needs Work] | [brief observation] |
| Dependency Inversion | [Good/Needs Work] | [brief observation] |

## API Boundaries & Integration Points
- **External Integrations**: [list external APIs/services]
- **Internal Interfaces**: [key module boundaries]
- **Data Formats**: [JSON, XML, etc.]
- **Protocols**: [REST, GraphQL, gRPC, etc.]

## Error Handling & Logging
- **Error Strategy**: [how errors are handled]
- **Logging Approach**: [logging framework and patterns]
- **Log Levels**: [how levels are used]

## Security Considerations
- **Authentication**: [method used or N/A]
- **Input Validation**: [where/how validated]
- **Secrets Management**: [how secrets are handled]
- **Potential Concerns**: [any security gaps identified]

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
Create a comprehensive Product Requirements Document (PRD) in JSON format that follows industry-standard product management best practices.

# CONTEXT

## User Intent
<USER_INTENT>
{{user_intent}}
</USER_INTENT>

## Available Memory Files
<MEMORY_MAP>
{{memory_map}}
</MEMORY_MAP>

# PRODUCT MANAGEMENT BEST PRACTICES

## INVEST Criteria for User Stories
Every user story MUST satisfy the INVEST criteria:
- **Independent**: Stories should be self-contained with no inherent dependencies on other stories where possible
- **Negotiable**: Stories are not contracts; details can be negotiated during implementation
- **Valuable**: Each story must deliver clear value to the user or stakeholder
- **Estimable**: Stories must be clear enough that effort can be reasonably estimated
- **Small**: Stories should be completable within a single iteration/sprint
- **Testable**: Stories must have clear conditions that can verify completion

## Risk Assessment
For each story, consider and document:
- Technical risks (complexity, unfamiliar technology, integration challenges)
- Dependency risks (external systems, third-party APIs, other teams)
- Scope risks (unclear requirements, potential scope creep)

## Prioritization (MoSCoW Method)
When multiple stories exist, assign priority using MoSCoW:
- **Must Have**: Critical for the release; without these, the product is not viable
- **Should Have**: Important but not critical; workarounds exist if omitted
- **Could Have**: Desirable but not necessary; include if time/resources permit
- **Won't Have**: Explicitly out of scope for this release but may be considered later

## Edge Cases and Error Scenarios
Acceptance criteria MUST include:
- Happy path scenarios (normal expected behavior)
- Edge cases (boundary conditions, empty states, maximum limits)
- Error scenarios (invalid input, network failures, permission denied, etc.)
- Recovery behavior (what happens after an error, rollback, retry logic)

## Definition of Done
Each story MUST have a clear "definitionOfDone" that includes criteria beyond acceptance criteria:
- Code is reviewed and meets coding standards
- Unit/integration tests are written and passing
- Documentation is updated if applicable
- No regressions introduced
- Feature is deployable

# STRUCTURED REASONING PROTOCOL

Before generating the PRD, you MUST complete this reasoning framework explicitly:

## Step 1: Pre-Execution Analysis
Answer these questions explicitly in your thinking before writing any output:
1. **Intent Clarification**: What exactly is the user asking for? Restate the request in your own words.
2. **Scope Boundaries**: What is in scope vs out of scope? Be explicit about boundaries.
3. **Stakeholder Identification**: Who are the users/roles affected by this feature?
4. **Success Definition**: How will we know when this is successfully implemented?
5. **Assumption Inventory**: What assumptions am I making about the codebase, technology, or requirements?

## Step 2: Validation Checkpoints
At each major step, verify before proceeding:
- [ ] **Intent Verification**: Does my interpretation match what the user actually requested? If ambiguous, have I noted the ambiguity?
- [ ] **INVEST Compliance**: Does each user story I'm creating satisfy ALL six INVEST criteria?
- [ ] **Completeness Check**: Have I considered happy path, edge cases, AND error scenarios for each story?
- [ ] **Dependency Accuracy**: Are the dependencies I've identified actually necessary, or am I over-constraining?
- [ ] **Testability Verification**: Can each acceptance criterion be objectively verified? If not, refine it.

## Step 3: Chain-of-Thought Reasoning
Work through these steps systematically:

1. **Scope Analysis**: What is the user trying to build or achieve?
2. **Feature Decomposition**: What distinct features or tasks are needed?
3. **INVEST Validation**: Does each story satisfy all INVEST criteria? If not, refine it.
4. **User Story Mapping**: For each feature, who is the user and what value do they get?
5. **Acceptance Criteria**: What specific, testable conditions define success? Include edge cases and error scenarios.
6. **Definition of Done**: What additional quality gates must be met beyond acceptance criteria?
7. **Risk Assessment**: What are the technical, dependency, and scope risks for each story?
8. **Dependencies**: Are there any ordering constraints between tasks? Document them explicitly.
9. **Prioritization**: If multiple stories exist, apply MoSCoW to determine implementation order.

## Step 4: Self-Review Before Output
Before generating the final JSON, verify:
- [ ] Each user story follows the "As a <role>, I want <feature> so that <benefit>" format
- [ ] Acceptance criteria are specific enough to be testable by a developer
- [ ] Risks identified are actionable with concrete mitigations
- [ ] No circular dependencies exist between tasks
- [ ] Priority assignments reflect actual business value

# FALLBACK STRATEGIES

## When Primary Approaches Fail

### Scenario 1: Ambiguous User Intent
**Primary approach fails when**: The user request is vague, underspecified, or could be interpreted multiple ways
**Fallback strategy**:
1. Document the ambiguity explicitly in the PRD description
2. Choose the most reasonable interpretation and state it as an assumption
3. Add a risk item: "Scope Risk: Requirement interpreted as [X]; if intended as [Y], stories may need revision"
4. Consider splitting into multiple smaller stories that can be validated incrementally

### Scenario 2: Overly Large Scope
**Primary approach fails when**: The request implies work that cannot fit in a single story
**Fallback strategy**:
1. Break down into multiple independent stories following INVEST
2. Create explicit dependencies where truly necessary
3. Prioritize using MoSCoW to identify the minimal viable subset
4. Document what is being deferred as "Won't Have" for this iteration

### Scenario 3: Unclear Technical Feasibility
**Primary approach fails when**: Cannot determine if the feature is technically feasible from available context
**Fallback strategy**:
1. Add a "spike" or investigation story as a prerequisite: "As a developer, I want to investigate [X] so that we can determine feasibility"
2. Document technical risks with "Unknown" severity
3. Include in Definition of Done: "Technical feasibility confirmed before implementation begins"
4. Note that estimates may change significantly pending investigation

### Scenario 4: Missing Context from Memory Files
**Primary approach fails when**: Memory files don't provide enough architectural context
**Fallback strategy**:
1. Generate stories that are context-agnostic where possible
2. Add "Assumption" comments noting what was assumed about the codebase
3. Include risk: "Dependency Risk: Stories assume [architecture pattern]; verify before implementation"
4. Recommend architect phase be run first if critical context is missing

### Scenario 5: Conflicting Requirements
**Primary approach fails when**: Different parts of the request seem to contradict each other
**Fallback strategy**:
1. Document the conflict explicitly
2. Prioritize based on which interpretation provides more value
3. Create separate stories for conflicting interpretations if both are valuable
4. Add risk: "Scope Risk: Requirements conflict detected; stakeholder clarification recommended"

# LIMITATIONS AND UNCERTAINTY ACKNOWLEDGMENT

You MUST explicitly acknowledge limitations and uncertainty in your reasoning and output:

## Required Disclosures
For each user story, internally assess and document where applicable:

1. **Requirement Confidence**: How confident are you that this story captures the user's intent?
   - HIGH: Requirement is explicit and unambiguous
   - MEDIUM: Reasonable interpretation of somewhat vague input
   - LOW: Significant assumptions made; recommend validation

2. **Completeness Confidence**: How confident are you that acceptance criteria are complete?
   - HIGH: All scenarios (happy path, edge cases, errors) are covered
   - MEDIUM: Main scenarios covered; some edge cases may be missing
   - LOW: Only primary scenario defined; significant gaps possible

3. **Dependency Confidence**: How confident are you in the task ordering?
   - HIGH: Dependencies are technically required
   - MEDIUM: Dependencies are recommended but could be parallelized with risk
   - LOW: Dependencies are assumed; actual ordering may differ

## Expressing Uncertainty in Output
When uncertainty exists, reflect it in the PRD:
- Add specific risks for uncertain areas
- Use acceptance criteria like "Verify that [assumption] is correct before proceeding"
- Include in Definition of Done: "Confirm [uncertain element] with stakeholder"
- For LOW confidence stories, add risk: "Scope Risk: Story based on assumed requirements; validation recommended"

## What NOT to Assume
Do not assume without explicit evidence:
- Specific technology choices (unless visible in memory files)
- User preferences for implementation approach
- Performance or scale requirements
- Security requirements beyond standard best practices
- Integration points not mentioned in the request

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
      "priority": "Must Have|Should Have|Could Have|Won't Have",
      "acceptanceCriteria": [
        "Given <context>, when <action>, then <expected result>",
        "The feature must <specific requirement>",
        "Edge case: When <boundary condition>, then <expected behavior>",
        "Error handling: When <error case>, then <expected recovery behavior>"
      ],
      "definitionOfDone": [
        "Code reviewed and approved",
        "Unit tests written and passing",
        "Integration tests passing",
        "Documentation updated",
        "No regressions in existing functionality"
      ],
      "risks": [
        {
          "type": "technical|dependency|scope",
          "description": "Description of the risk",
          "mitigation": "How to mitigate or address the risk"
        }
      ],
      "dependencies": ["TASK-XXX"],
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
| userStories[].priority | string | Yes | MoSCoW priority: "Must Have", "Should Have", "Could Have", or "Won't Have" |
| userStories[].acceptanceCriteria | array | Yes | Testable conditions including edge cases and error scenarios (minimum 3 per story) |
| userStories[].definitionOfDone | array | Yes | Quality criteria beyond acceptance criteria (minimum 3 per story) |
| userStories[].risks | array | Yes | Identified risks with type, description, and mitigation (can be empty array if no risks) |
| userStories[].dependencies | array | Yes | List of dependent task IDs (can be empty array if no dependencies) |
| userStories[].status | string | Yes | Must be "pending" for new stories |

# CONSTRAINTS
- Output ONLY valid JSON - no markdown fences, no explanatory text before or after
- Each user story MUST follow the "As a <role>, I want <feature> so that <benefit>" format
- Each user story MUST satisfy all INVEST criteria (Independent, Negotiable, Valuable, Estimable, Small, Testable)
- Each acceptance criterion MUST be specific and testable
- Acceptance criteria MUST include at least one edge case and one error scenario
- Each story MUST have a definitionOfDone array with quality gates
- Each story MUST have a priority using MoSCoW method
- Each story MUST have risks array (empty if no risks identified) and dependencies array (empty if no dependencies)
- Task IDs MUST be sequential (TASK-001, TASK-002, etc.)
- Status MUST always be "pending" for new stories
- Minimum 3 acceptance criteria per user story (including edge case and error handling)
- Do NOT include any text outside the JSON object

# RESPONSE FORMAT
Output the raw JSON object directly. Example:
{"id":"PRD-001","description":"...","userStories":[{"id":"TASK-001","description":"...","priority":"Must Have","acceptanceCriteria":[...],"definitionOfDone":[...],"risks":[],"dependencies":[],"status":"pending"}]}
""",
        "developer.txt": """# ROLE
Developer

# OBJECTIVE
Implement the assigned task following the acceptance criteria and verification requirements while adhering to software engineering best practices.

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

# SOFTWARE ENGINEERING BEST PRACTICES

## Code Quality Principles
Apply these fundamental principles to all code changes:

### DRY (Don't Repeat Yourself)
- Extract repeated code into reusable functions, methods, or classes
- Centralize configuration and constants; avoid magic numbers/strings scattered throughout code
- If you find yourself copying and pasting code, refactor it into a shared abstraction
- Use inheritance, composition, or utility modules to eliminate duplication

### KISS (Keep It Simple, Stupid)
- Prefer straightforward solutions over clever or complex ones
- Write code that is easy to read, understand, and maintain
- Avoid premature optimization; make it work correctly first
- Use standard library functions and well-known patterns when available
- Break complex logic into smaller, well-named functions

### YAGNI (You Aren't Gonna Need It)
- Only implement features explicitly required by the acceptance criteria
- Do NOT add speculative functionality, configuration options, or extension points
- Avoid over-engineering; build for current requirements, not hypothetical futures
- Remove unused code, imports, and dead branches rather than commenting them out

## Security Best Practices (OWASP Top 10 Prevention)
Actively prevent these common security vulnerabilities in all code changes:

### Injection Prevention (SQL, Command, LDAP, XPath)
- ALWAYS use parameterized queries or prepared statements for database operations
- NEVER concatenate user input directly into SQL, shell commands, or system calls
- Use ORM methods that automatically escape parameters
- Validate and sanitize all user input before processing

### Cross-Site Scripting (XSS) Prevention
- Escape or encode all user-supplied data before rendering in HTML, JavaScript, or CSS
- Use templating engines with auto-escaping enabled by default
- Implement Content Security Policy (CSP) headers where applicable
- Validate input on the server side, even if client-side validation exists

### Authentication & Session Management
- Never store passwords in plain text; use strong hashing algorithms (bcrypt, Argon2)
- Implement proper session timeout and invalidation
- Use secure, HTTP-only, SameSite cookies for session tokens
- Protect against brute force with rate limiting and account lockout

### Sensitive Data Exposure
- Never log sensitive data (passwords, tokens, PII, credit card numbers)
- Use environment variables or secure vaults for secrets; never hardcode them
- Encrypt sensitive data at rest and in transit (TLS/HTTPS)
- Implement proper access controls for sensitive endpoints

### Security Misconfiguration
- Disable debug mode and verbose error messages in production code
- Remove default credentials and unnecessary features
- Keep dependencies updated to patch known vulnerabilities
- Follow the principle of least privilege for file and API permissions

### Additional Security Considerations
- Validate file uploads: check type, size, and sanitize filenames
- Implement CSRF protection for state-changing operations
- Use secure deserialization practices; avoid deserializing untrusted data
- Log security-relevant events for audit trails without exposing sensitive data

## Error Handling Patterns
Implement robust error handling following these guidelines:

### Exception Hierarchy
- Use specific exception types rather than generic Exception catches
- Create custom exception classes for domain-specific errors when appropriate
- Preserve the exception chain: use `raise NewException(...) from original_exception`
- Catch exceptions at the appropriate level; don't catch too early or too broadly

### Error Handling Strategy
```
Try to follow this pattern:
1. Catch specific exceptions you can handle meaningfully
2. Log the error with appropriate context (see Logging section)
3. Either recover gracefully or re-raise with additional context
4. Let unhandled exceptions propagate to a top-level handler
```

### Logging Best Practices
- Use appropriate log levels: DEBUG for diagnostics, INFO for normal operations, WARNING for recoverable issues, ERROR for failures, CRITICAL for fatal errors
- Include relevant context in log messages: operation being performed, relevant IDs, input parameters (excluding sensitive data)
- Use structured logging where available (key-value pairs) for easier parsing
- Avoid logging sensitive information (passwords, tokens, PII)
- Log at entry and exit points of significant operations for traceability

### Graceful Degradation
- Provide meaningful error messages to users without exposing internal details
- Implement fallbacks for non-critical features when dependencies fail
- Ensure partial failures don't corrupt data; use transactions where appropriate
- Clean up resources (files, connections) in finally blocks or use context managers

## Project Conventions Adherence
Before writing any code, analyze the existing codebase to detect and follow conventions:

### Code Style Detection
- Examine existing files to identify naming conventions (snake_case, camelCase, PascalCase)
- Follow the established indentation style (spaces vs tabs, indentation width)
- Match the existing quote style for strings (single vs double quotes)
- Maintain consistent line length limits as seen in the codebase

### Architectural Patterns
- Identify and follow existing patterns (MVC, layered architecture, repository pattern, etc.)
- Place new code in the appropriate layer/module based on existing structure
- Use existing utility functions and helpers rather than creating duplicates
- Follow established dependency injection or configuration patterns

### Import Organization
- Follow the existing import ordering convention (stdlib, third-party, local)
- Match the grouping and sorting style of imports in existing files
- Use relative vs absolute imports consistently with the codebase

### Testing Conventions
- Follow existing test file naming and organization patterns
- Use the same assertion style and test framework patterns
- Match the level of test coverage and mocking strategies used

## Self-Documenting Code
Write code that explains itself through clarity, not comments:

### Meaningful Naming
- Use descriptive, intention-revealing names for variables, functions, and classes
- Names should answer: What does this represent? What does this do?
- Avoid abbreviations unless they are universally understood (e.g., `id`, `url`)
- Use verb phrases for functions (`calculate_total`, `validate_input`, `send_notification`)
- Use noun phrases for variables and classes (`user_count`, `OrderProcessor`)

### Function Design
- Functions should do one thing and do it well (Single Responsibility)
- Keep functions short; if it exceeds 20-30 lines, consider refactoring
- Limit parameters to 3-4; use objects/dictionaries for complex inputs
- Return early to avoid deep nesting; handle edge cases first
- Avoid boolean flag parameters that change function behavior

### Code Structure
- Group related code together; separate concerns into distinct functions/classes
- Use whitespace and blank lines to create logical sections
- Order methods/functions logically: public before private, called before calling
- Extract complex conditionals into well-named boolean variables or functions

### When Comments Are Appropriate
- Explain WHY, not WHAT (the code shows what, comments explain intent)
- Document public APIs, especially non-obvious parameters and return values
- Mark TODOs with context: `# TODO(issue-123): Refactor when API v2 is available`
- Explain workarounds for bugs or unusual requirements with references

## Performance Considerations
Consider performance implications for these operations:

### Loop Optimization
- Avoid nested loops where possible; consider alternative data structures
- Move invariant calculations outside loops
- Use generators for large sequences to reduce memory usage
- Consider early termination with `break` when the result is found
- Profile before optimizing; don't prematurely optimize

### I/O Operations
- Batch database queries; avoid N+1 query patterns
- Use connection pooling for database and HTTP connections
- Buffer file reads/writes for large files; use streaming for very large data
- Consider async/await for I/O-bound operations to improve throughput
- Cache expensive I/O results when data doesn't change frequently

### Data Structure Selection
- Choose appropriate data structures for the access patterns:
  - Lists: ordered sequences, frequent iteration
  - Sets: membership testing, uniqueness
  - Dictionaries: key-value lookup, fast access by key
  - Deques: efficient append/pop from both ends
- Consider memory vs speed tradeoffs for large datasets
- Use appropriate collection methods (e.g., `dict.get()` vs `dict[]` with try/except)

### Memory Management
- Release references to large objects when no longer needed
- Use context managers for resources (files, connections, locks)
- Be cautious with mutable default arguments (use `None` and initialize inside)
- Consider `__slots__` for classes with many instances and fixed attributes

# STRUCTURED REASONING PROTOCOL

Before writing any code, you MUST complete this reasoning framework:

## Step 1: Pre-Execution Analysis
Answer these questions explicitly in your thinking before taking action:
1. **Requirement Understanding**: What exactly does the acceptance criteria require? Restate each criterion in your own words.
2. **Scope Boundaries**: What changes are in scope? What is explicitly OUT of scope?
3. **Existing Code Analysis**: What existing code will I modify? What patterns and conventions does it use?
4. **Approach Selection**: What implementation approach will I take? Why is this the best approach?
5. **Risk Assessment**: What could go wrong? What are the potential side effects of my changes?
6. **Assumption Inventory**: What assumptions am I making? Are they valid?

## Step 2: Validation Checkpoints
At each major step, verify before proceeding:

### Before Reading Code
- [ ] Have I identified all files that may be relevant to this task?
- [ ] Do I understand the file structure and module organization?

### Before Writing Code
- [ ] Have I read and understood all code I'm about to modify?
- [ ] Do I understand the existing patterns, naming conventions, and style?
- [ ] Have I identified potential impacts on other parts of the codebase?
- [ ] Is my planned change the minimal change required to meet the acceptance criteria?

### Before Each Edit
- [ ] Does this change directly address an acceptance criterion?
- [ ] Am I following the existing code style and conventions?
- [ ] Have I considered error handling for this change?
- [ ] Have I considered security implications of this change?
- [ ] Am I introducing any code duplication that should be refactored?

### After Implementation
- [ ] Have I addressed ALL acceptance criteria, not just some?
- [ ] Have I avoided adding features beyond what was specified?
- [ ] Is the code I wrote self-documenting with meaningful names?
- [ ] Have I cleaned up any debugging code or comments?

## Step 3: Evidence-Based Decision Making
For each implementation decision:
- Cite specific code patterns from the existing codebase that inform your approach
- Reference specific acceptance criteria that justify each change
- Document trade-offs considered and rationale for chosen approach

# FALLBACK STRATEGIES

When primary approaches fail, apply these recovery strategies:

## Scenario 1: Cannot Find Relevant Code
**Primary approach fails when**: Search doesn't locate the code that needs modification
**Fallback strategy**:
1. Broaden search terms; try synonyms and related concepts
2. Examine import statements to trace module dependencies
3. Look for configuration files that might reference the relevant code
4. Search for tests that exercise the functionality to find the implementation
5. If still not found, document the search attempts and what was tried

## Scenario 2: Existing Code Uses Unfamiliar Patterns
**Primary approach fails when**: The codebase uses patterns or frameworks you don't immediately recognize
**Fallback strategy**:
1. Look for similar code elsewhere in the codebase as examples
2. Examine test files to understand expected behavior
3. Trace the code execution path to understand data flow
4. Match the existing pattern even if it seems suboptimal; consistency is priority
5. Document any assumptions about the pattern's purpose

## Scenario 3: Tests Fail After Changes
**Primary approach fails when**: Verification command returns failures
**Fallback strategy**:
1. Read the FULL error message, not just the summary
2. Identify whether the failure is in new code or regression in existing code
3. If regression: revert to understand what broke, then fix incrementally
4. If new code failure: verify your understanding of the acceptance criteria
5. Check for missing imports, typos, or incorrect function signatures
6. If same error occurs twice, try a fundamentally different approach

## Scenario 4: Conflicting Requirements
**Primary approach fails when**: Acceptance criteria seem to contradict each other or existing behavior
**Fallback strategy**:
1. Re-read criteria carefully; apparent conflicts may be misunderstandings
2. Check if the conflict is between criteria vs existing tests (prioritize criteria)
3. Document the conflict explicitly in your reasoning
4. Implement the most conservative interpretation that satisfies both where possible
5. If truly unresolvable, document and report as blocker

## Scenario 5: Changes Have Unintended Side Effects
**Primary approach fails when**: Fixing one thing breaks another
**Fallback strategy**:
1. Identify the coupling between components that caused the side effect
2. Consider a more targeted fix that doesn't affect the coupled component
3. If coupling is intentional, update both components consistently
4. If coupling seems accidental, consider if refactoring is in scope
5. Verify ALL tests pass after each incremental change

## Scenario 6: Cannot Meet Performance Requirements
**Primary approach fails when**: Implementation is correct but too slow or uses too much memory
**Fallback strategy**:
1. Profile to identify the actual bottleneck (don't guess)
2. Check if there's an existing utility or library that handles this more efficiently
3. Consider algorithmic improvements (better data structures, caching)
4. If optimization requires significant refactoring, document trade-offs
5. Verify optimizations don't break correctness (run tests)

# LIMITATIONS AND UNCERTAINTY ACKNOWLEDGMENT

You MUST explicitly acknowledge limitations and uncertainty:

## Required Self-Assessment
Before finalizing any implementation, assess:

1. **Understanding Confidence**: How well do I understand the code I'm modifying?
   - HIGH: I understand the purpose, behavior, and integration of this code
   - MEDIUM: I understand what the code does but not all the context
   - LOW: I'm making changes based on limited understanding

2. **Solution Confidence**: How confident am I that my solution is correct?
   - HIGH: Solution directly addresses criteria with clear evidence of correctness
   - MEDIUM: Solution should work but has untested edge cases
   - LOW: Solution is my best guess; verification is critical

3. **Impact Confidence**: How well do I understand the impact of my changes?
   - HIGH: I understand all components affected by this change
   - MEDIUM: I know the immediate impact but may have missed indirect effects
   - LOW: Changes may have effects I haven't anticipated

## What to Do with Low Confidence
When confidence is LOW in any area:
- Proceed with extra caution; make smaller, more incremental changes
- Add extra validation in your testing
- Document your uncertainty in reasoning
- Consider if you need to read more code before proceeding

## Expressing Uncertainty in Implementation
When uncertainty affects your implementation:
- Add comments explaining non-obvious decisions with rationale
- Include defensive error handling for uncertain edge cases
- Write tests that verify your assumptions about behavior
- Document in your output what aspects you're uncertain about

## Hard Limitations to Acknowledge
Be explicit when you encounter these situations:
- "I cannot determine [X] from the available context"
- "This change may affect [Y] but I cannot verify without [Z]"
- "I'm assuming [A] because [B]; if incorrect, [consequence]"
- "The acceptance criteria don't specify [X]; I'm interpreting it as [Y]"

## What NOT to Do When Uncertain
- Do NOT guess at implementation details without reading the code first
- Do NOT make changes to code you haven't read
- Do NOT skip verification because you're confident the code is correct
- Do NOT ignore test failures as "probably unrelated"
- Do NOT add speculative features to "handle" uncertainty

# EXECUTION WORKFLOW

## Phase 1: Planning
1. Analyze the task requirements and acceptance criteria
2. Review existing code to understand conventions and patterns
3. Identify files that need to be created or modified
4. Consider edge cases, error scenarios, and potential security implications
5. Plan the implementation order

## Phase 2: Implementation
1. Make changes incrementally, following the best practices above
2. Match existing code patterns and conventions detected from the codebase
3. Keep changes minimal and focused on the task
4. Do NOT add features beyond what is specified (YAGNI)
5. Write self-documenting code with meaningful names
6. Implement proper error handling with appropriate logging
7. Consider security implications of each change
8. Consider performance implications for loops, I/O, and data structures

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
- You MUST NOT modify unrelated files
- You MUST follow the code quality principles (DRY, KISS, YAGNI)
- You MUST consider and prevent OWASP Top 10 security vulnerabilities
- You MUST implement proper error handling with appropriate logging
- You MUST follow existing project conventions detected from the codebase
- You MUST write self-documenting code with meaningful names
- You MUST consider performance implications for loops, I/O, and data structures""",
        "enhance_intent.txt": """# ROLE
Intent Enhancement Specialist

# OBJECTIVE
Refine and clarify the user's initial intent to create a more precise, actionable, and well-structured description of what they want to build.

# ORIGINAL USER INTENT
<ORIGINAL_INTENT>
{{original_intent}}
</ORIGINAL_INTENT>

# ENHANCEMENT GUIDELINES

Your task is to enhance the user's intent by:

1. **Clarifying Ambiguities**: Identify and resolve any vague or ambiguous parts of the intent
2. **Adding Specificity**: Add concrete details where the intent is too general
3. **Structuring Requirements**: Organize the intent into clear, logical components
4. **Identifying Implicit Needs**: Surface any implicit requirements that are essential but not explicitly stated
5. **Technical Translation**: Translate user-facing language into technical requirements where appropriate

# CONSTRAINTS

- PRESERVE the user's core intent - do not change what they fundamentally want to build
- DO NOT add features or requirements the user did not mention or imply
- KEEP the enhanced intent concise and focused
- AVOID over-engineering or adding unnecessary complexity
- MAINTAIN the user's tone and terminology where possible

# OUTPUT FORMAT

Provide your response in the following format:

<ENHANCED_INTENT>
[Your enhanced, refined version of the user's intent goes here. This should be a clear, well-structured description that can be directly passed to the architect phase.]
</ENHANCED_INTENT>

IMPORTANT: Output ONLY the enhanced intent within the tags. Do not include explanations, reasoning, or any other text outside the tags.""",
        "revise_prd.txt": """# ROLE
PRD Quality Reviewer and Reviser

# OBJECTIVE
Review and improve the provided Product Requirements Document (PRD) for clarity, completeness, and quality while preserving the original intent.

# ORIGINAL PRD
<ORIGINAL_PRD>
{{original_prd}}
</ORIGINAL_PRD>

# REVIEW GUIDELINES

Your task is to review and improve the PRD by:

1. **Clarity Enhancement**: Ensure each user story has clear, unambiguous descriptions
2. **Acceptance Criteria Quality**: Verify acceptance criteria are specific, measurable, and testable
3. **Completeness Check**: Identify any missing edge cases or error handling scenarios
4. **Consistency**: Ensure consistent terminology and formatting across all stories
5. **Technical Accuracy**: Verify technical requirements are correctly specified
6. **JSON Structure**: Ensure the PRD is valid JSON with correct structure

# CONSTRAINTS

- PRESERVE the original intent and scope of each user story
- DO NOT add new user stories or major features not implied in the original
- DO NOT remove any user stories from the original PRD
- KEEP the same task IDs and overall structure
- MAINTAIN all existing fields and their purposes
- FIX any JSON formatting issues if present

# REVISION CATEGORIES

When reviewing, consider these improvement categories:
- Grammar and spelling corrections
- Clarification of vague requirements
- Addition of missing edge cases to acceptance criteria
- Improvement of testability for acceptance criteria
- Consistency in terminology and formatting

# OUTPUT FORMAT

Provide your response in the following format:

<REVISED_PRD>
{
  "userStories": [
    // Your revised user stories array with the same structure as input
  ]
}
</REVISED_PRD>

<REVISION_SUMMARY>
[Brief summary of changes made. If no changes were needed, state "No revisions needed - PRD already meets quality standards."]
</REVISION_SUMMARY>

IMPORTANT:
- Output ONLY valid JSON within the <REVISED_PRD> tags
- The JSON must have the same structure as the input PRD
- If the original PRD has invalid JSON, attempt to fix the formatting issues
- If the PRD is already optimal, output it unchanged with a note in the summary""",

        "qa_review.txt": """# ROLE
QA Code Review Agent

# OBJECTIVE
Review implemented code changes for quality issues including missing error handling, security vulnerabilities, code style violations, missing tests, and documentation gaps.

# CONTEXT

## Task Information
<TASK_ID>{{task_id}}</TASK_ID>
<TASK_DESCRIPTION>{{task_description}}</TASK_DESCRIPTION>

## Acceptance Criteria
<ACCEPTANCE_CRITERIA>
{{acceptance_criteria}}
</ACCEPTANCE_CRITERIA>

## Code Changes
<CODE_CHANGES>
{{code_changes}}
</CODE_CHANGES>

## Project Memory
<MEMORY>
{{memory_map}}
</MEMORY>

# QA REVIEW CHECKLIST

Review the implemented code for the following quality issues:

## 1. Error Handling
- Are all potential exceptions properly caught and handled?
- Are error messages informative and user-friendly?
- Are resources properly cleaned up in error scenarios?
- Is there appropriate use of try/except/finally or context managers?

## 2. Security Vulnerabilities (OWASP Top 10)
- Input validation: Is all user input validated before processing?
- Injection prevention: Are parameterized queries/prepared statements used?
- XSS prevention: Is output properly escaped/encoded?
- Authentication/Authorization: Are access controls properly implemented?
- Sensitive data exposure: Are secrets, credentials, or PII protected?
- Security misconfiguration: Are debug modes disabled, defaults changed?

## 3. Code Style and Quality
- Does the code follow the project's existing conventions?
- Are variable and function names descriptive and consistent?
- Is the code DRY (Don't Repeat Yourself)?
- Is the code readable and maintainable?
- Are there any code smells or anti-patterns?

## 4. Testing Coverage
- Are there tests for the new functionality?
- Are edge cases covered by tests?
- Are error scenarios tested?
- Do tests follow the project's testing conventions?

## 5. Documentation
- Are complex functions documented?
- Are public APIs documented?
- Are any TODOs or FIXMEs addressed?
- Is README or other user documentation updated if needed?

# OUTPUT FORMAT

Provide your review in the following format:

<QA_FINDINGS>
{
  "summary": "[Overall assessment: PASS, WARN, or FAIL]",
  "critical_issues": [
    {
      "category": "[error_handling|security|style|testing|documentation]",
      "severity": "critical",
      "description": "[description of the issue]",
      "location": "[file:line or general location]",
      "recommendation": "[how to fix]"
    }
  ],
  "warnings": [
    {
      "category": "[error_handling|security|style|testing|documentation]",
      "severity": "warning",
      "description": "[description of the issue]",
      "location": "[file:line or general location]",
      "recommendation": "[how to fix]"
    }
  ],
  "suggestions": [
    {
      "category": "[error_handling|security|style|testing|documentation]",
      "severity": "suggestion",
      "description": "[description of the improvement]",
      "location": "[file:line or general location]",
      "recommendation": "[suggested improvement]"
    }
  ],
  "passed_checks": [
    "[List of checks that passed without issues]"
  ]
}
</QA_FINDINGS>

# SEVERITY LEVELS

- **critical**: Issues that must be fixed before merging (security vulnerabilities, data loss risks, breaking bugs)
- **warning**: Issues that should be addressed (error handling gaps, style violations, missing tests)
- **suggestion**: Nice-to-have improvements (documentation, minor refactoring)

# CONSTRAINTS

- ONLY review code quality, DO NOT modify the code
- DO NOT duplicate task creation logic - focus solely on review
- Be specific about issue locations and how to fix them
- If no issues found, output summary as "PASS" with empty issue arrays
- Output ONLY valid JSON within the <QA_FINDINGS> tags"""
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

    @staticmethod
    def delete(template_name: str) -> None:
        """Delete a template file from the templates directory.

        Args:
            template_name: Name of the template to delete (with or without extension)

        Raises:
            ValueError: If template_name is empty or invalid
            FileNotFoundError: If templates directory doesn't exist or template not found
            PermissionError: If permission denied when deleting
        """
        if not template_name or not template_name.strip():
            raise ValueError("Invalid template name: template name cannot be empty")

        template_name = template_name.strip()

        if not CONF.TEMPLATES_DIR.exists():
            raise FileNotFoundError("Templates directory not found.")

        # Try exact match first
        path = CONF.TEMPLATES_DIR / template_name
        if path.exists() and path.is_file():
            path.unlink()
            return

        # Try with .txt extension if no extension provided
        if not Path(template_name).suffix:
            path_with_ext = CONF.TEMPLATES_DIR / f"{template_name}.txt"
            if path_with_ext.exists() and path_with_ext.is_file():
                path_with_ext.unlink()
                return

        raise FileNotFoundError(f"Template {template_name} not found.")


# ==============================================================================
# ORCHESTRATOR
# ==============================================================================

class RalphOrchestrator:
    def __init__(self, agent_name: str = "claude", enable_hooks: bool = True, enabled_hook_names: Optional[List[str]] = None,
                 intent: Optional[str] = None, intent_file: Optional[str] = None, prompt_file: Optional[str] = None,
                 enhance_intent: bool = False, enhance_intent_strict: bool = False,
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
                 label: Optional[List[str]] = None, revise_prd: bool = False,
                 qa_review: bool = False, qa_strict: bool = False) -> None:
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
        self._enhance_intent = enhance_intent
        self._enhance_intent_strict = enhance_intent_strict
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
        self._revise_prd = revise_prd
        # Store QA review flags
        self._qa_review = qa_review
        self._qa_strict = qa_strict
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

    def _revise_prd_impl(self, original_prd: Dict[str, Any]) -> Dict[str, Any]:
        """Revise PRD through the revision agent for quality improvements.

        Args:
            original_prd: The original PRD data to revise

        Returns:
            Revised PRD data, or original PRD on failure

        Notes:
            - If revision fails or times out, falls back to original PRD with warning
            - If revised PRD fails schema validation, falls back to original PRD
            - Logs when no revision was needed (PRD already optimal)
        """
        Logger.info("\n🔧 Revising PRD...", "CYAN")
        self.hooks.emit(Event(EventType.PRD_REVISE_START, phase="planner"))

        # Convert PRD to JSON string for the prompt
        original_prd_json = json.dumps(original_prd, indent=2)

        prompt = TemplateManager.render(
            "revise_prd.txt",
            original_prd=original_prd_json
        )

        success, stdout, error = self.agent.run(prompt, "REVISE_PRD")

        if not success:
            error_msg = error.message if error else "Unknown error"
            Logger.warning(f"PRD revision failed: {error_msg}")
            Logger.warning("Falling back to original PRD.")
            self.hooks.emit(Event(EventType.PRD_REVISE_FAILURE, phase="planner",
                                  metadata={"reason": "agent_failure", "error": error_msg}))
            return original_prd

        # Parse the revised PRD from response
        revised_prd, revision_summary = self._parse_revised_prd(stdout, original_prd)

        if revised_prd is None:
            Logger.warning("Could not parse revised PRD from response.")
            Logger.warning("Falling back to original PRD.")
            self.hooks.emit(Event(EventType.PRD_REVISE_FAILURE, phase="planner",
                                  metadata={"reason": "parse_failure"}))
            return original_prd

        # Validate revised PRD against schema if specified
        if self._schema_path:
            schema_valid, schema_error = self._validate_prd_schema(revised_prd)
            if not schema_valid:
                Logger.warning(f"Revised PRD failed schema validation: {schema_error}")
                Logger.warning("Falling back to original PRD.")
                self.hooks.emit(Event(EventType.PRD_REVISE_FAILURE, phase="planner",
                                      metadata={"reason": "schema_validation_failed", "error": schema_error}))
                return original_prd

        # Check if no revision was needed
        if revision_summary and "no revision" in revision_summary.lower():
            Logger.info("✅ PRD already optimal, no revision needed.", "GREEN")
        else:
            Logger.debug(f"Revision summary: {revision_summary}", "CYAN")
            Logger.info("✅ PRD revised.", "GREEN")

        self.hooks.emit(Event(EventType.PRD_REVISE_SUCCESS, phase="planner",
                              metadata={"summary": revision_summary or ""}))
        return revised_prd

    def _parse_revised_prd(self, response: str, fallback: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Parse the revised PRD from the agent response.

        Args:
            response: The raw response from the revision agent
            fallback: The fallback PRD data if parsing fails

        Returns:
            Tuple of (revised_prd_data, revision_summary). revised_prd_data is None if parsing fails.
        """
        import re

        # Extract content between <REVISED_PRD> tags
        prd_pattern = r'<REVISED_PRD>\s*(.*?)\s*</REVISED_PRD>'
        prd_match = re.search(prd_pattern, response, re.DOTALL)

        # Extract revision summary
        summary_pattern = r'<REVISION_SUMMARY>\s*(.*?)\s*</REVISION_SUMMARY>'
        summary_match = re.search(summary_pattern, response, re.DOTALL)
        revision_summary = summary_match.group(1).strip() if summary_match else None

        if not prd_match:
            Logger.warning("Could not find <REVISED_PRD> tags in response.")
            return None, revision_summary

        prd_text = prd_match.group(1).strip()

        try:
            revised_prd = JsonUtils.parse(prd_text)
            # Validate basic structure
            if "userStories" not in revised_prd:
                Logger.warning("Revised PRD missing 'userStories' key.")
                return None, revision_summary
            return revised_prd, revision_summary
        except json.JSONDecodeError as e:
            Logger.warning(f"Invalid JSON in revised PRD: {e}")
            return None, revision_summary

    def run_planner(self, user_intent: str) -> None:
        """
        Run the planner phase to create a Product Requirements Document.

        Generates a PRD with user stories and acceptance criteria,
        saved to .ralph/prd.json.

        Respects the following flags:
        - --schema: Validate PRD against a JSON schema file
        - --min-criteria: Ensure each story has at least N acceptance criteria
        - --label: Add custom labels to the PRD
        - --revise-prd: Pass PRD through revision agent before saving

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

                # Revise PRD if --revise-prd is specified
                if self._revise_prd:
                    data = self._revise_prd_impl(data)
                    # Re-validate against schema after revision
                    if self._schema_path:
                        schema_valid, schema_error = self._validate_prd_schema(data)
                        if not schema_valid:
                            Logger.warning(f"Revised PRD failed schema validation: {schema_error}")
                            # This shouldn't happen as _revise_prd_impl already validates,
                            # but we check again for safety
                            continue

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
                    # Run QA review if enabled (after verification/skip-verify)
                    qa_passed, _ = self._run_qa_review(task)
                    if not qa_passed:
                        self._record_failure(retry, "QA Review Failed (Critical Issues)", "", task_id=task['id'])
                        self._emit_retry_event(task, retry, max_retries)
                        continue
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
                    # Run QA review if enabled (after verification success)
                    qa_passed, _ = self._run_qa_review(task)
                    if not qa_passed:
                        self._record_failure(retry, "QA Review Failed (Critical Issues)", "", task_id=task['id'])
                        self._emit_retry_event(task, retry, max_retries)
                        continue
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

    def _get_code_changes(self) -> str:
        """Get recent code changes using git diff.

        Returns:
            String containing diff output, or message if no changes or git unavailable.
        """
        try:
            stdout, stderr, code = Shell.run("git diff HEAD~1 --stat", timeout=30)
            if code != 0:
                stdout, stderr, code = Shell.run("git diff --cached --stat", timeout=30)
            if code == 0 and stdout.strip():
                diff_stdout, _, diff_code = Shell.run("git diff HEAD~1", timeout=60)
                if diff_code == 0 and diff_stdout.strip():
                    if len(diff_stdout) > 50000:
                        return diff_stdout[:50000] + "\n... (truncated, diff too large)"
                    return diff_stdout
                return "(No detailed diff available)"
            return "(No code changes detected)"
        except Exception as e:
            Logger.debug(f"Failed to get code changes: {e}")
            return "(Unable to detect code changes)"

    def _run_qa_review(self, task: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Run QA review on the implemented code.

        Args:
            task: The task that was just completed

        Returns:
            Tuple of (passed: bool, findings: Optional[Dict]).
            passed is True if no critical issues found (or QA review disabled/failed).
            findings contains the parsed QA findings if available.
        """
        if not self._qa_review:
            return True, None

        Logger.info("   🔍 Running QA review...", "CYAN")
        self.hooks.emit(Event(
            EventType.QA_REVIEW_START, phase="execute",
            task_id=task['id'], task_description=task['description']
        ))

        code_changes = self._get_code_changes()
        if code_changes in ("(No code changes detected)", "(Unable to detect code changes)"):
            Logger.info("   ⏭️  Skipping QA review: no code changes detected", "YELLOW")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SKIPPED, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"reason": "no_changes"}
            ))
            return True, None

        prompt = TemplateManager.render(
            "qa_review.txt",
            task_id=task['id'],
            task_description=task['description'],
            acceptance_criteria=self._format_acceptance_criteria(task),
            code_changes=code_changes,
            memory_map=self.memory.get_structure(
                include=self._include_patterns,
                exclude=self._exclude_patterns,
                limit=self._context_limit
            )
        )

        try:
            success, output, agent_error = self.agent.run(prompt, f"QA-{task['id']}")
        except Exception as e:
            Logger.warning(f"QA review agent failed: {e}")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"reason": "agent_exception", "error": str(e)}
            ))
            return True, None

        if not success:
            Logger.warning("QA review agent failed to respond. Continuing without QA review.")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="execute",
                task_id=task['id'], task_description=task['description'],
                error=agent_error,
                metadata={"reason": "agent_failure"}
            ))
            return True, None

        findings = self._parse_qa_findings(output)
        if findings is None:
            Logger.warning("Could not parse QA findings. Continuing without QA review.")
            self.hooks.emit(Event(
                EventType.QA_REVIEW_FAILURE, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"reason": "parse_failure"}
            ))
            return True, None

        self._report_qa_findings(task, findings)

        has_critical = len(findings.get("critical_issues", [])) > 0
        has_warnings = len(findings.get("warnings", [])) > 0
        has_any_findings = has_critical or has_warnings

        if has_critical:
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SUCCESS, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"summary": findings.get("summary", "FAIL"), "critical_count": len(findings.get("critical_issues", []))}
            ))
            if self._qa_strict:
                Logger.info("   ❌ QA review found critical issues (--qa-strict mode)", "RED")
                return False, findings
            else:
                Logger.info("   ⚠️  QA review found critical issues (non-strict mode, continuing)", "YELLOW")
        elif has_warnings:
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SUCCESS, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"summary": findings.get("summary", "WARN"), "warning_count": len(findings.get("warnings", []))}
            ))
            Logger.info("   ⚠️  QA review found warnings.", "YELLOW")
        else:
            self.hooks.emit(Event(
                EventType.QA_REVIEW_SUCCESS, phase="execute",
                task_id=task['id'], task_description=task['description'],
                metadata={"summary": findings.get("summary", "PASS")}
            ))
            Logger.info("   ✅ QA review passed with no issues.", "GREEN")

        # Prompt for PRD generation if findings exist and not in strict mode (which already failed)
        if has_any_findings and not self._qa_strict:
            if self._prompt_for_prd_generation(findings):
                self._generate_prd_from_qa_findings(task, findings)
            else:
                # User declined - display summary of findings and exit gracefully
                total_issues = len(findings.get("critical_issues", [])) + len(findings.get("warnings", []))
                Logger.info(f"   📋 QA review complete with {total_issues} finding(s). Continuing without PRD generation.", "CYAN")

        return True, findings

    def _parse_qa_findings(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse QA findings from agent response.

        Args:
            response: Raw response from the QA agent

        Returns:
            Parsed findings dictionary or None if parsing fails
        """
        import re
        pattern = r'<QA_FINDINGS>\s*(.*?)\s*</QA_FINDINGS>'
        match = re.search(pattern, response, re.DOTALL)

        if not match:
            Logger.debug("Could not find <QA_FINDINGS> tags in response.")
            return None

        findings_text = match.group(1).strip()

        try:
            findings = JsonUtils.parse(findings_text)
            if not isinstance(findings, dict):
                return None
            return findings
        except json.JSONDecodeError as e:
            Logger.debug(f"Failed to parse QA findings JSON: {e}")
            return None

    def _report_qa_findings(self, task: Dict[str, Any], findings: Dict[str, Any]) -> None:
        """Report QA findings in the appropriate format.

        Uses QAFindingsAnalyzer to categorize, group, and format findings.
        Findings are grouped by file and sorted by severity (errors first).

        Args:
            task: The task being reviewed
            findings: Parsed QA findings dictionary
        """
        # Use the analyzer to process and categorize findings
        analyzer = QAFindingsAnalyzer(findings)
        counts = analyzer.get_counts()
        grouped = analyzer.get_findings_grouped_by_file()

        if Logger.json_output or Logger.ndjson_output:
            # JSON output includes full structured analysis
            qa_report = {
                "timestamp": datetime.datetime.now().isoformat(),
                "level": "info",
                "message": "QA Review Complete",
                "task_id": task['id'],
                "qa_summary": analyzer.summary,
                "counts": counts,
                "findings_by_file": {
                    file_path: [
                        {
                            "type": f.finding_type.name.lower(),
                            "category": f.category,
                            "description": f.description,
                            "line_number": f.line_number,
                            "recommendation": f.recommendation,
                            "has_missing_data": f.has_missing_data,
                            "missing_data_note": f.missing_data_note
                        }
                        for f in file_findings
                    ]
                    for file_path, file_findings in grouped.items()
                },
                "passed_checks": analyzer.passed_checks,
                "is_paginated": analyzer.is_large_output
            }
            print(json.dumps(qa_report))
        else:
            # Text output with severity-based formatting
            Logger.info(f"   📋 QA Summary: {analyzer.summary}", "CYAN")
            Logger.info(f"   📊 Files: {counts['files']} | Errors: {counts['errors']} | "
                       f"Warnings: {counts['warnings']} | Suggestions: {counts['suggestions']}", "CYAN")

            if not analyzer.all_findings:
                Logger.info("   ✅ No issues found.", "GREEN")
            else:
                # Display findings grouped by file, sorted by severity
                errors = analyzer.get_findings_by_type(QAFindingType.ERROR)
                warnings_list = analyzer.get_findings_by_type(QAFindingType.WARNING)
                suggestions = analyzer.get_findings_by_type(QAFindingType.SUGGESTION)

                # Show errors first (always displayed)
                if errors:
                    Logger.info(f"   ❌ Critical Issues ({len(errors)}):", "RED")
                    self._display_findings_by_file(errors, "RED", max_per_file=5, max_files=10)

                # Show warnings
                if warnings_list:
                    Logger.info(f"   ⚠️  Warnings ({len(warnings_list)}):", "YELLOW")
                    self._display_findings_by_file(warnings_list, "YELLOW", max_per_file=3, max_files=5)

                # Show suggestions only at verbose level
                if suggestions and Logger.verbosity >= 1:
                    Logger.debug(f"   💡 Suggestions ({len(suggestions)}):")
                    self._display_findings_by_file(suggestions, None, max_per_file=3, max_files=3, use_debug=True)

                # Large output summary
                if analyzer.is_large_output:
                    Logger.info(f"   📄 Output truncated ({counts['files']} files total). "
                               "Use --json for full details.", "CYAN")

        Logger.file_log(json.dumps(findings, indent=2), "QA_REVIEW", f"QA-{task['id']}")

    def _prompt_for_prd_generation(self, findings: Dict[str, Any]) -> bool:
        """Prompt user to generate a PRD to address QA findings.

        Args:
            findings: The QA findings dictionary

        Returns:
            True if user wants to generate PRD, False otherwise.
            In non-interactive mode, returns False.
        """
        if self._non_interactive:
            Logger.info("   ℹ️  Skipping PRD generation prompt (non-interactive mode)", "CYAN")
            return False

        total_issues = (
            len(findings.get("critical_issues", [])) +
            len(findings.get("warnings", []))
        )

        while True:
            response = input(
                f"{Logger.COLORS['YELLOW']}Generate a PRD to address {total_issues} QA finding(s)? (y/n): {Logger.COLORS['RESET']}"
            ).strip().lower()

            if response in ('y', 'yes'):
                return True
            elif response in ('n', 'no'):
                return False
            else:
                Logger.info("   ⚠️  Invalid input. Please enter 'y' or 'n'.", "YELLOW")

    def _generate_prd_from_qa_findings(self, task: Dict[str, Any], findings: Dict[str, Any]) -> None:
        """Generate a PRD to address QA findings.

        Creates a structured PRD document from QA findings. Each finding or group
        of related findings becomes a user story with acceptance criteria.

        Handles edge cases:
        - Single finding: Creates a single-story PRD
        - 50+ findings: Groups findings by category into logical stories

        Args:
            task: The task that was reviewed
            findings: The QA findings dictionary

        Raises:
            Logs error and notifies user if PRD generation fails
        """
        Logger.info("\n🔧 Generating PRD from QA findings...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="prd_from_qa"))

        try:
            prd_data = self._create_prd_from_findings(task, findings)
        except (KeyError, TypeError, ValueError) as e:
            error_msg = f"Invalid finding data: {type(e).__name__}: {e}"
            Logger.error(f"PRD generation failed: {error_msg}")
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa",
                                  metadata={"success": False, "error": error_msg}))
            return

        # Validate against JSON schema if --schema is specified
        if self._schema_path:
            schema_valid, schema_error = self._validate_prd_schema(prd_data)
            if not schema_valid:
                Logger.error(f"Generated PRD failed schema validation: {schema_error}")
                self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa",
                                      metadata={"success": False, "error": f"schema_validation: {schema_error}"}))
                return

        # Validate minimum acceptance criteria if --min-criteria is specified
        criteria_valid, criteria_error = self._validate_min_criteria(prd_data)
        if not criteria_valid:
            Logger.warning(f"Generated PRD criteria validation warning: {criteria_error}")

        # Apply labels if --label is specified
        prd_data = self._apply_labels(prd_data)

        # Archive existing PRD if present
        self._archive_prd()

        # Save the generated PRD to file
        story_count = len(prd_data.get('userStories', []))
        output_path = self._save_generated_prd(prd_data)
        if output_path:
            Logger.info(f"✅ PRD Generated from QA findings ({story_count} stories).", "GREEN")
            self.hooks.emit(Event(EventType.PRD_CREATED, phase="prd_from_qa", prd_path=str(output_path)))
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa", metadata={"success": True}))
        else:
            # Save was aborted (user declined overwrite), but PRD was printed to stdout
            self.hooks.emit(Event(EventType.PHASE_END, phase="prd_from_qa", metadata={"success": False, "reason": "save_aborted"}))

    def _create_prd_from_findings(self, task: Dict[str, Any], findings: Dict[str, Any]) -> Dict[str, Any]:
        """Create a structured PRD from QA findings.

        Args:
            task: The task that was reviewed
            findings: The QA findings dictionary with critical_issues, warnings, suggestions

        Returns:
            A PRD dictionary following the standard schema

        Raises:
            ValueError: If findings data is invalid
        """
        # Threshold for grouping findings by category instead of individual stories
        GROUPING_THRESHOLD = 50

        # Collect all findings with their types
        all_findings: List[Dict[str, Any]] = []

        for issue in findings.get("critical_issues", []):
            if not isinstance(issue, dict):
                continue
            all_findings.append({
                "severity": "critical",
                "category": issue.get("category", "unknown"),
                "description": issue.get("description", "No description"),
                "location": issue.get("location", ""),
                "recommendation": issue.get("recommendation", "")
            })

        for warning in findings.get("warnings", []):
            if not isinstance(warning, dict):
                continue
            all_findings.append({
                "severity": "warning",
                "category": warning.get("category", "unknown"),
                "description": warning.get("description", "No description"),
                "location": warning.get("location", ""),
                "recommendation": warning.get("recommendation", "")
            })

        for suggestion in findings.get("suggestions", []):
            if not isinstance(suggestion, dict):
                continue
            all_findings.append({
                "severity": "suggestion",
                "category": suggestion.get("category", "unknown"),
                "description": suggestion.get("description", "No description"),
                "location": suggestion.get("location", ""),
                "recommendation": suggestion.get("recommendation", "")
            })

        if not all_findings:
            raise ValueError("No valid findings to generate PRD from")

        # Generate PRD ID based on original task
        original_task_id = task.get('id', 'UNKNOWN')
        prd_id = f"PRD-QA-{original_task_id}"

        # Determine if we need to group findings (50+ threshold)
        if len(all_findings) >= GROUPING_THRESHOLD:
            user_stories = self._create_grouped_stories(all_findings, original_task_id)
        else:
            user_stories = self._create_individual_stories(all_findings, original_task_id)

        return {
            "id": prd_id,
            "description": f"Address QA findings from review of {original_task_id}: {task.get('description', 'N/A')}",
            "userStories": user_stories
        }

    def _create_individual_stories(self, findings: List[Dict[str, Any]], original_task_id: str) -> List[Dict[str, Any]]:
        """Create individual user stories for each finding or small groups.

        For fewer than 50 findings, each distinct finding becomes its own story.

        Args:
            findings: List of finding dictionaries
            original_task_id: The original task ID for context

        Returns:
            List of user story dictionaries
        """
        user_stories = []

        for i, finding in enumerate(findings, 1):
            task_id = f"TASK-{i:03d}"
            severity = finding["severity"]
            category = finding["category"]
            description = finding["description"]
            location = finding.get("location", "")
            recommendation = finding.get("recommendation", "")

            # Create user story description in proper format
            story_desc = (
                f"As a developer, I want to fix the {severity} {category} issue "
                f"so that the codebase meets quality standards."
            )

            # Build acceptance criteria
            acceptance_criteria = [
                f"Given the {category} issue '{description}', when the fix is applied, then the issue is resolved",
            ]

            if location:
                acceptance_criteria.append(
                    f"Given the issue location at {location}, when reviewing the fix, then the specific location is addressed"
                )

            if recommendation:
                acceptance_criteria.append(
                    f"Given the recommendation '{recommendation}', when implementing, then the suggested approach is followed"
                )

            acceptance_criteria.append(
                f"Given the fix is complete, when running QA review, then no {severity} issues of this type are reported"
            )

            user_stories.append({
                "id": task_id,
                "description": story_desc,
                "priority": "Must Have" if severity == "critical" else ("Should Have" if severity == "warning" else "Could Have"),
                "acceptanceCriteria": acceptance_criteria,
                "definitionOfDone": [
                    "Code reviewed and approved",
                    "Fix verified through testing",
                    "No regressions introduced"
                ],
                "risks": [
                    {
                        "type": "technical",
                        "description": f"Fix may have unintended side effects",
                        "mitigation": "Thorough testing and code review"
                    }
                ],
                "dependencies": [f"Original task: {original_task_id}"],
                "status": "pending"
            })

        return user_stories

    def _create_grouped_stories(self, findings: List[Dict[str, Any]], original_task_id: str) -> List[Dict[str, Any]]:
        """Create grouped user stories when findings exceed 50.

        Groups findings by category to create manageable stories.

        Args:
            findings: List of finding dictionaries
            original_task_id: The original task ID for context

        Returns:
            List of user story dictionaries
        """
        # Group findings by category
        by_category: Dict[str, List[Dict[str, Any]]] = {}
        for finding in findings:
            category = finding.get("category", "other")
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(finding)

        user_stories = []
        task_num = 1

        # Sort categories by severity priority (critical findings first)
        def category_priority(cat: str) -> int:
            cat_findings = by_category[cat]
            has_critical = any(f["severity"] == "critical" for f in cat_findings)
            has_warning = any(f["severity"] == "warning" for f in cat_findings)
            if has_critical:
                return 0
            if has_warning:
                return 1
            return 2

        sorted_categories = sorted(by_category.keys(), key=category_priority)

        for category in sorted_categories:
            cat_findings = by_category[category]
            task_id = f"TASK-{task_num:03d}"
            task_num += 1

            # Determine overall severity for this category
            has_critical = any(f["severity"] == "critical" for f in cat_findings)
            has_warning = any(f["severity"] == "warning" for f in cat_findings)

            severity_text = "critical" if has_critical else ("warning" if has_warning else "suggestion")
            priority = "Must Have" if has_critical else ("Should Have" if has_warning else "Could Have")

            # Create story description
            story_desc = (
                f"As a developer, I want to address all {category} issues ({len(cat_findings)} findings) "
                f"so that the codebase quality in this area is improved."
            )

            # Build acceptance criteria from findings
            acceptance_criteria = [
                f"Given {len(cat_findings)} {category} issues identified, when fixes are applied, then all issues are resolved"
            ]

            # Add specific criteria for top findings (up to 5)
            for j, finding in enumerate(cat_findings[:5], 1):
                desc = finding["description"]
                if len(desc) > 100:
                    desc = desc[:97] + "..."
                acceptance_criteria.append(
                    f"Given issue {j}: '{desc}', when fixed, then the specific problem is resolved"
                )

            if len(cat_findings) > 5:
                acceptance_criteria.append(
                    f"Given {len(cat_findings) - 5} additional {category} issues, when fixed, then all remaining issues are resolved"
                )

            acceptance_criteria.append(
                f"Given all {category} fixes are complete, when running QA review, then no {severity_text} {category} issues are reported"
            )

            user_stories.append({
                "id": task_id,
                "description": story_desc,
                "priority": priority,
                "acceptanceCriteria": acceptance_criteria,
                "definitionOfDone": [
                    "Code reviewed and approved",
                    "All fixes verified through testing",
                    f"No {category} issues remain",
                    "No regressions introduced"
                ],
                "risks": [
                    {
                        "type": "scope",
                        "description": f"Large number of {category} issues ({len(cat_findings)}) may require significant effort",
                        "mitigation": "Prioritize critical issues first, batch related fixes"
                    }
                ],
                "dependencies": [f"Original task: {original_task_id}"],
                "status": "pending"
            })

        return user_stories

    def _display_findings_by_file(self, findings: List['QAFinding'], color: Optional[str],
                                   max_per_file: int = 5, max_files: int = 10,
                                   use_debug: bool = False) -> None:
        """Display findings grouped by file.

        Args:
            findings: List of QAFinding objects to display
            color: Color to use for output (None for default)
            max_per_file: Maximum findings to show per file before truncating
            max_files: Maximum files to show before summarizing
            use_debug: If True, use Logger.debug instead of Logger.info
        """
        # Group findings by file
        by_file: Dict[str, List[QAFinding]] = {}
        for f in findings:
            key = f.file_path or "(no file)"
            if key not in by_file:
                by_file[key] = []
            by_file[key].append(f)

        # Sort files: files with more findings first
        sorted_files = sorted(by_file.keys(), key=lambda k: -len(by_file[k]))

        log_fn = Logger.debug if use_debug else Logger.info
        files_shown = 0

        for file_path in sorted_files:
            if files_shown >= max_files:
                remaining_files = len(sorted_files) - files_shown
                remaining_findings = sum(len(by_file[f]) for f in sorted_files[files_shown:])
                log_fn(f"      ... and {remaining_findings} more finding(s) in {remaining_files} file(s)", color)
                break

            file_findings = by_file[file_path]
            log_fn(f"      {file_path}:", color)

            for i, finding in enumerate(file_findings):
                if i >= max_per_file:
                    remaining = len(file_findings) - i
                    log_fn(f"        ... and {remaining} more in this file", color)
                    break

                line_info = f":{finding.line_number}" if finding.line_number else ""
                log_fn(f"        - [{finding.category}]{line_info} {finding.description}", color)

                if finding.has_missing_data and finding.missing_data_note and Logger.verbosity >= 2:
                    log_fn(f"          (Note: {finding.missing_data_note})", color)

            files_shown += 1

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

    def _save_generated_prd(self, prd_data: Dict[str, Any]) -> Optional[Path]:
        """
        Save generated PRD to a file with overwrite protection.

        Saves the PRD to the location specified by --prd-out flag, or to the default
        .ralph/prd.json location. Handles:
        - Overwrite confirmation in interactive mode
        - Error in non-interactive mode when file exists
        - Auto-creation of output directory
        - Fallback to stdout on write errors

        Args:
            prd_data: The PRD dictionary to save

        Returns:
            Path to the saved file if successful, None if save was aborted
        """
        # Determine output path: use --prd-out if specified, otherwise default
        if self._prd_out:
            out_path = Path(self._prd_out)
        else:
            out_path = CONF.PRD_FILE

        prd_json = json.dumps(prd_data, indent=2)

        # Check if file exists and handle overwrite
        if out_path.exists():
            if self._non_interactive:
                Logger.error(f"Output file already exists: {out_path}")
                Logger.error("Use a different path or remove the existing file.")
                Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
                print(prd_json)
                return None
            else:
                # Interactive mode: prompt for overwrite confirmation
                if not self._prompt_overwrite_confirmation(out_path):
                    Logger.info("PRD save cancelled. PRD content printed to stdout:", "YELLOW")
                    print(prd_json)
                    return None

        # Ensure output directory exists
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            Logger.error(f"Failed to create output directory {out_path.parent}: {e}")
            Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
            print(prd_json)
            return None

        # Write the file with error handling
        try:
            out_path.write_text(prd_json, encoding='utf-8')
            Logger.info(f"📋 PRD saved to {out_path}", "MAGENTA")
            # Update PRD manager cache if saved to default location
            if out_path == CONF.PRD_FILE:
                self._prd.invalidate_cache()
            return out_path
        except PermissionError as e:
            Logger.error(f"Permission denied writing to {out_path}: {e}")
            Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
            print(prd_json)
            return None
        except OSError as e:
            Logger.error(f"Failed to write PRD to {out_path}: {e}")
            Logger.info("PRD content printed to stdout as fallback:", "YELLOW")
            print(prd_json)
            return None

    def _prompt_overwrite_confirmation(self, file_path: Path) -> bool:
        """
        Prompt user to confirm overwriting an existing file.

        Args:
            file_path: Path to the file that would be overwritten

        Returns:
            True if user confirms overwrite, False otherwise
        """
        while True:
            response = input(
                f"{Logger.COLORS['YELLOW']}File {file_path} already exists. Overwrite? (y/n): {Logger.COLORS['RESET']}"
            ).strip().lower()

            if response in ('y', 'yes'):
                return True
            elif response in ('n', 'no'):
                return False
            else:
                Logger.info("   ⚠️  Invalid input. Please enter 'y' or 'n'.", "YELLOW")

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

    def _enhance_intent_impl(self, original_intent: str) -> str:
        """Enhance user intent through the enhancement agent.

        Args:
            original_intent: The original user intent to enhance

        Returns:
            Enhanced intent string, or original intent on failure (unless strict mode)

        Raises:
            SystemExit: If strict mode is enabled and enhancement fails
        """
        # Validate input - empty or whitespace-only intent
        if not original_intent or not original_intent.strip():
            Logger.error("Cannot enhance empty or whitespace-only intent.")
            sys.exit(1)

        Logger.info("\n🔧 Enhancing intent...", "CYAN")
        self.hooks.emit(Event(EventType.INTENT_ENHANCE_START, phase="enhance_intent"))

        prompt = TemplateManager.render(
            "enhance_intent.txt",
            original_intent=original_intent
        )

        success, stdout, error = self.agent.run(prompt, "ENHANCE_INTENT")

        if not success:
            error_msg = error.message if error else "Unknown error"
            Logger.warning(f"Intent enhancement failed: {error_msg}")
            self.hooks.emit(Event(EventType.INTENT_ENHANCE_FAILURE, phase="enhance_intent"))

            if self._enhance_intent_strict:
                Logger.error("Intent enhancement failed in strict mode. Exiting.")
                sys.exit(1)

            Logger.warning("Falling back to original intent.")
            return original_intent

        # Extract enhanced intent from response
        enhanced_intent = self._parse_enhanced_intent(stdout, original_intent)

        # Validate enhanced intent is not empty
        if not enhanced_intent or not enhanced_intent.strip():
            Logger.warning("Enhancement agent returned empty response.")
            self.hooks.emit(Event(EventType.INTENT_ENHANCE_FAILURE, phase="enhance_intent"))

            if self._enhance_intent_strict:
                Logger.error("Intent enhancement returned invalid response in strict mode. Exiting.")
                sys.exit(1)

            Logger.warning("Falling back to original intent.")
            return original_intent

        # Log both original and enhanced intent when verbose
        Logger.debug(f"Original intent: {original_intent}", "CYAN")
        Logger.debug(f"Enhanced intent: {enhanced_intent}", "GREEN")

        Logger.info("✅ Intent enhanced.", "GREEN")
        self.hooks.emit(Event(EventType.INTENT_ENHANCE_SUCCESS, phase="enhance_intent"))

        return enhanced_intent

    def _parse_enhanced_intent(self, response: str, fallback: str) -> str:
        """Parse the enhanced intent from the agent response.

        Args:
            response: The raw response from the enhancement agent
            fallback: The fallback value if parsing fails

        Returns:
            The parsed enhanced intent or fallback value
        """
        # Try to extract content between <ENHANCED_INTENT> tags
        import re
        pattern = r'<ENHANCED_INTENT>\s*(.*?)\s*</ENHANCED_INTENT>'
        match = re.search(pattern, response, re.DOTALL)

        if match:
            return match.group(1).strip()

        # If no tags found, log warning and return fallback
        Logger.warning("Could not parse enhanced intent from response. Falling back to original.")
        return fallback

    def _get_and_enhance_intent(self, user_intent=None) -> str:
        """Get intent and optionally enhance it based on --enhance-intent flag.

        Args:
            user_intent: Optional pre-provided intent

        Returns:
            The (possibly enhanced) intent string
        """
        intent = self._get_intent(user_intent)

        if self._enhance_intent:
            intent = self._enhance_intent_impl(intent)

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
            user_intent = self._get_and_enhance_intent()
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
            user_intent = self._get_and_enhance_intent()
            self.run_architect(user_intent)
        else:
            Logger.info("⏭️ Skipping architect.", "YELLOW")

        # Planner phase
        if self._prd.exists():
            Logger.info("📋 PRD exists, skipping planner.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Planner"):
            user_intent = self._get_and_enhance_intent(user_intent)
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
    parser.add_argument("--enhance-intent", action="store_true", help="Process intent through enhancement agent before architect phase")
    parser.add_argument("--no-enhance-intent", action="store_true", help="Disable intent enhancement (overrides --enhance-all)")
    parser.add_argument("--enhance-intent-strict", action="store_true", help="Exit on enhancement failure instead of falling back to original intent")
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
    parser.add_argument("--revise-prd", action="store_true", help="Pass PRD through revision agent for quality improvements before planner phase")
    parser.add_argument("--no-revise-prd", action="store_true", help="Disable PRD revision (overrides --enhance-all)")
    # QA review flags for automated code quality review
    parser.add_argument("--qa-review", action="store_true", help="Enable QA agent to review implemented code for quality issues after each task")
    parser.add_argument("--no-qa-review", action="store_true", help="Disable QA review (overrides --enhance-all)")
    parser.add_argument("--qa-strict", action="store_true", help="Fail tasks when QA review finds critical issues (requires --qa-review)")
    # Enhancement combination flag
    parser.add_argument("--enhance-all", action="store_true", help="Enable all enhancement features (--enhance-intent, --revise-prd, --qa-review). Individual --no-* flags can override specific features.")
    # Template management flags
    parser.add_argument("--template-delete", type=str, metavar="NAME", help="Delete a template by name from .ralph/templates/ directory")
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

    # Handle --template-delete flag: delete template and exit
    if args.template_delete is not None:
        try:
            TemplateManager.delete(args.template_delete)
            Logger.info(f"Template {args.template_delete} deleted successfully.")
            sys.exit(0)
        except ValueError as e:
            Logger.error(str(e))
            sys.exit(1)
        except FileNotFoundError as e:
            Logger.error(str(e))
            sys.exit(1)
        except PermissionError as e:
            Logger.error(f"Permission denied: {e}")
            sys.exit(1)

    # Handle --enhance-all flag: apply enhancement defaults with explicit overrides
    # --enhance-all enables: --enhance-intent, --revise-prd, --qa-review
    # Individual --no-* flags can override specific features
    enhance_all = args.enhance_all

    # Calculate effective enhancement flag values
    # Explicit positive flags or --enhance-all enable the feature
    # Explicit negative flags disable the feature (override --enhance-all)
    enhance_intent = args.enhance_intent or (enhance_all and not args.no_enhance_intent)
    revise_prd = args.revise_prd or (enhance_all and not args.no_revise_prd)
    qa_review = args.qa_review or (enhance_all and not args.no_qa_review)

    # Log which enhancement features are actually enabled when --enhance-all is used
    if enhance_all:
        enabled_features = []
        disabled_features = []

        if enhance_intent:
            enabled_features.append("intent enhancement")
        else:
            disabled_features.append("intent enhancement")

        if revise_prd:
            enabled_features.append("PRD revision")
        else:
            disabled_features.append("PRD revision")

        if qa_review:
            enabled_features.append("QA review")
        else:
            disabled_features.append("QA review")

        if enabled_features:
            Logger.info(f"Enhancement features enabled: {', '.join(enabled_features)}")
        if disabled_features:
            Logger.info(f"Enhancement features disabled by explicit flags: {', '.join(disabled_features)}")

    RalphOrchestrator(
        agent_name=args.agent,
        enable_hooks=enable_hooks,
        enabled_hook_names=enabled_hook_names,
        intent=args.intent,
        intent_file=args.intent_file,
        prompt_file=args.prompt_file,
        enhance_intent=enhance_intent,
        enhance_intent_strict=args.enhance_intent_strict,
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
        label=args.label,
        revise_prd=revise_prd,
        qa_review=qa_review,
        qa_strict=args.qa_strict
    ).start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()
