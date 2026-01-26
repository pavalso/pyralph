#!/usr/bin/env python3
"""Logger module for Ralph.

This module contains the Logger class and _LoggerMeta metaclass that provide
centralized logging infrastructure for the Ralph CLI tool.
"""
import datetime
import json
import re
from pathlib import Path
from typing import List, Optional

from .config import CONF


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
                # Support patterns passed with escaped backslashes (e.g., r'api_key=\\w+')
                normalized = pattern.encode().decode("unicode_escape")
                redacted = re.sub(normalized, '[REDACTED]', redacted)
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
    def _print_json(msg: str, level: str, **kwargs) -> None:
        formatted = Logger._format_json_message(msg, level, **kwargs)
        if Logger.ndjson_output:
            # Emit literal '\n' separators so consumers splitting on backslash-n work as expected.
            print(formatted, end="\\n")
        else:
            print(formatted)

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
                Logger._print_json(msg, "info")
            else:
                Logger._print_colored(msg, color)

    @staticmethod
    def debug(msg: str, color: str = "RESET") -> None:
        """Print debug message (requires verbosity >= 1 and log level <= debug)."""
        if Logger.verbosity >= 1 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                Logger._print_json(msg, "debug")
            else:
                Logger._print_colored(msg, color, prefix="[DEBUG] ")

    @staticmethod
    def trace(msg: str, color: str = "RESET") -> None:
        """Print trace message (requires verbosity >= 2 and log level <= debug)."""
        if Logger.verbosity >= 2 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                Logger._print_json(msg, "trace")
            else:
                Logger._print_colored(msg, color, prefix="[TRACE] ")

    @staticmethod
    def ultra(msg: str, color: str = "RESET") -> None:
        """Print ultra-verbose message (requires verbosity >= 3 and log level <= debug)."""
        if Logger.verbosity >= 3 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                Logger._print_json(msg, "ultra")
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
