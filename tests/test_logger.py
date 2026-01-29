import json
import sys
import warnings

import pytest

from pyralph.logger import Logger

from .helpers import CaptureStdout


class TestLogger:
    """Tests for Logger basic functionality."""

    def test_toggles(self, logger_reset):
        # Test non-deprecated setter
        Logger.set_no_color(True)
        assert Logger.no_color
        Logger.set_no_color(False)
        assert not Logger.no_color

        # Test deprecated set_verbose (still works but warns)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Logger.set_verbose(True)
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        assert Logger.verbose

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Logger.set_verbose(False)
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        assert not Logger.verbose

    def test_colors_keys(self, logger_reset):
        assert set(Logger.COLORS.keys()) == {"RESET", "GREEN", "RED", "CYAN", "YELLOW", "MAGENTA"}

    def test_file_log(self, logger_test_env):
        log_file = logger_test_env['log_file']
        cases = [("PROMPT", "TAG", "➡️"), ("RESPONSE", "TAG", "⬅️"), ("ERROR", "TAG", "❌"), ("INFO", None, "ℹ️")]
        for log_type, tag, icon in cases:
            if log_file.exists():
                log_file.unlink()
            Logger.file_log("Content", log_type, tag) if tag else Logger.file_log("Content", log_type)
            assert icon in log_file.read_text(encoding="utf-8")

    def test_verbosity_sync_and_clamp(self, logger_reset):
        Logger.set_verbosity(0)
        assert not Logger.verbose
        Logger.set_verbosity(1)
        assert Logger.verbose
        Logger.set_verbosity(-5)
        assert Logger.verbosity == 0
        Logger.set_verbosity(10)
        assert Logger.verbosity == 3

    def test_verbosity_property_sync_direct_assignment(self, logger_reset):
        Logger.verbosity = 0
        assert not Logger.verbose
        assert Logger.verbosity == 0

        Logger.verbosity = 2
        assert Logger.verbose
        assert Logger.verbosity == 2

        # Setting verbose directly is deprecated but still works
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Logger.verbose = False
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()
        assert not Logger.verbose
        assert Logger.verbosity == 0

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Logger.verbose = True
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        assert Logger.verbose
        assert Logger.verbosity == 1

        Logger.verbosity = -10
        assert Logger.verbosity == 0
        assert not Logger.verbose

        Logger.verbosity = 100
        assert Logger.verbosity == 3
        assert Logger.verbose

    def test_quiet_mode(self, logger_reset):
        Logger.set_no_color(True)
        Logger.set_quiet(True)
        with CaptureStdout() as captured:
            Logger.info("suppressed")
            assert captured.getvalue() == ""
            Logger.warning("warning")
            Logger.error("error")
            output = captured.getvalue()
        assert "warning" in output
        assert "error" in output

    def test_no_emoji(self, logger_reset):
        Logger.set_no_color(True)
        Logger.set_no_emoji(True)
        with CaptureStdout() as captured:
            Logger.info("🤖 Robot ✅")
            output = captured.getvalue()
        assert "[BOT]" in output
        assert "🤖" not in output

    def test_strip_emoji_all(self, logger_reset):
        emojis = ["🤖", "🕵️", "🧠", "🚀", "✅", "❌", "⚠️", "▶️", "🔒", "🛑", "⏭️", "📋", "📦", "🎉", "➡️", "⬅️", "ℹ️", "❓"]
        for emoji in emojis:
            result = Logger._strip_emoji(f"Test {emoji} msg")
            assert emoji not in result


class TestLoggerRedaction:
    """Tests for Logger redaction functionality."""

    def test_set_redact_patterns(self, logger_reset):
        Logger.set_redact_patterns(['p1', 'p2'])
        assert Logger.redact_patterns == ['p1', 'p2']
        Logger.set_redact_patterns(['new'])
        assert Logger.redact_patterns == ['new']

    def test_redact_content(self, logger_reset):
        cases = [
            ([], "api_key=secret", "api_key=secret"),
            ([r'api_key=\\w+'], "The api_key=secret here", "The [REDACTED] here"),
            ([r'api_key=\\w+', r'pwd=\\w+'], "api_key=x pwd=y", "[REDACTED] [REDACTED]"),
            ([r'secret\\d+'], "secret1 secret2 secret3", "[REDACTED] [REDACTED] [REDACTED]"),
        ]
        for patterns, content, expected in cases:
            Logger.redact_patterns = patterns
            assert Logger._redact_content(content) == expected

    def test_redact_from_file(self, logger_test_env):
        temp_dir = logger_test_env['temp_dir']
        redact_file = temp_dir / "redact.txt"
        redact_file.write_text("pattern1\n\n# comment\npattern2\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        assert Logger.redact_patterns == ['pattern1', 'pattern2']

    def test_no_log_prompts_responses(self, logger_test_env):
        log_file = logger_test_env['log_file']
        for flag, log_type, other_type in [
            ('no_log_prompts', 'PROMPT', 'RESPONSE'),
            ('no_log_responses', 'RESPONSE', 'PROMPT'),
        ]:
            if log_file.exists():
                log_file.unlink()
            setattr(Logger, flag, True)
            Logger.file_log("skip", log_type, "T")
            assert not log_file.exists()
            Logger.file_log("keep", other_type, "T")
            assert "keep" in log_file.read_text(encoding='utf-8')
            setattr(Logger, flag, False)

    def test_file_log_redaction(self, logger_test_env):
        log_file = logger_test_env['log_file']
        Logger.set_redact_patterns([r'secret_key=\\w+'])
        Logger.file_log("secret_key=abc123", "INFO", "T")
        content = log_file.read_text(encoding="utf-8")
        assert "[REDACTED]" in content
        assert "abc123" not in content


class TestLoggerJsonOutput:
    """Tests for Logger JSON output functionality."""

    def test_json_output_mode(self, logger_reset):
        Logger.json_output = True
        with CaptureStdout() as captured:
            Logger.info("test message")
            output = captured.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["message"] == "test message"
        assert parsed["level"] == "info"

    def test_ndjson_output_mode(self, logger_reset):
        Logger.ndjson_output = True
        with CaptureStdout() as captured:
            Logger.info("msg1")
            Logger.info("msg2")
            lines = [line for line in captured.getvalue().strip().split('\n') if line]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)


class TestCaptureStdoutEdgeCases:
    """Test edge cases for CaptureStdout context manager."""

    def test_empty_capture_returns_empty_string(self):
        """Empty stdout capture returns empty string without error."""
        with CaptureStdout() as captured:
            pass  # No output
        assert captured.getvalue() == ""
        assert captured.output == ""

    def test_nested_capture_raises_error(self):
        """Nested capture_stdout calls raise clear RuntimeError."""
        with CaptureStdout():
            with pytest.raises(RuntimeError) as exc_info:
                with CaptureStdout():
                    pass
            assert "Nested stdout capture is not supported" in str(exc_info.value)

    def test_stdout_restored_on_exception(self):
        """Stdout is properly restored even when assertions fail."""
        original = sys.stdout

        try:
            with CaptureStdout():
                raise ValueError("test exception")
        except ValueError:
            pass

        assert sys.stdout is original

    def test_output_property_alias(self):
        """The output property is an alias for getvalue()."""
        with CaptureStdout() as captured:
            print("test", end="")
        assert captured.output == "test"
        assert captured.output == captured.getvalue()


# Parametrized test for info/debug output combinations
INFO_DEBUG_OUTPUT_CASES = [
    pytest.param(False, True, 'info', "Plain", None, ["Plain"], ["\033["], id="info_plain_no_color"),
    pytest.param(False, False, 'info', "Colored", "GREEN", ["\033[92m", "Colored"], [], id="info_colored"),
    pytest.param(False, False, 'debug', "Debug", None, [], ["Debug"], id="debug_hidden_not_verbose"),
    pytest.param(True, True, 'debug', "Debug", None, ["[DEBUG] Debug"], ["\033["], id="debug_verbose_no_color"),
]


@pytest.mark.parametrize("verbose,no_color,method,msg,color,exp_in,exp_not", INFO_DEBUG_OUTPUT_CASES)
def test_info_debug_output(logger_reset, verbose, no_color, method, msg, color, exp_in, exp_not):
    """Test info and debug output with various verbosity and color settings.

    Tests 4 combinations:
    - info message with no_color=True: plain text without ANSI codes
    - info message with color: includes ANSI escape sequences
    - debug message with verbose=False: suppressed (not shown)
    - debug message with verbose=True and no_color=True: shows [DEBUG] prefix
    """
    # Reset Logger state for clean test environment
    Logger.json_output = False
    Logger.ndjson_output = False
    Logger.quiet = False

    with CaptureStdout() as captured:
        Logger.set_verbosity(1 if verbose else 0)
        Logger.set_no_color(no_color)
        if not hasattr(Logger, method):
            raise AttributeError(f"Logger has no method '{method}'")
        if color:
            getattr(Logger, method)(msg, color)
        else:
            getattr(Logger, method)(msg)
        output = captured.getvalue()
    for e in exp_in:
        assert e in output, f"Expected '{e}' in output: {output!r}"
    for e in exp_not:
        assert e not in output, f"Expected '{e}' not in output: {output!r}"


# Parametrized test for verbosity level behavior
# Format: (verbosity_level, method, should_output)
VERBOSITY_LEVEL_CASES = [
    # Edge case: minimum verbosity (0) - no debug output
    pytest.param(0, 'debug', False, id="verbosity_0_debug_suppressed"),
    pytest.param(0, 'trace', False, id="verbosity_0_trace_suppressed"),
    pytest.param(0, 'ultra', False, id="verbosity_0_ultra_suppressed"),
    # Verbosity 1: debug enabled, trace/ultra suppressed
    pytest.param(1, 'debug', True, id="verbosity_1_debug_shown"),
    pytest.param(1, 'trace', False, id="verbosity_1_trace_suppressed"),
    pytest.param(1, 'ultra', False, id="verbosity_1_ultra_suppressed"),
    # Verbosity 2: debug and trace enabled, ultra suppressed
    pytest.param(2, 'debug', True, id="verbosity_2_debug_shown"),
    pytest.param(2, 'trace', True, id="verbosity_2_trace_shown"),
    pytest.param(2, 'ultra', False, id="verbosity_2_ultra_suppressed"),
    # Edge case: maximum verbosity (3) - all output enabled
    pytest.param(3, 'debug', True, id="verbosity_3_debug_shown"),
    pytest.param(3, 'trace', True, id="verbosity_3_trace_shown"),
    pytest.param(3, 'ultra', True, id="verbosity_3_ultra_shown"),
]


@pytest.mark.parametrize("verbosity,method,should_output", VERBOSITY_LEVEL_CASES)
def test_verbosity_levels(logger_reset, verbosity, method, should_output):
    """Test that each verbosity level enables the correct log methods.

    Verbosity levels control which log methods produce output:
    - 0 (normal): debug, trace, ultra all suppressed
    - 1 (verbose): debug shown, trace/ultra suppressed
    - 2 (very verbose): debug/trace shown, ultra suppressed
    - 3 (debug): all shown (debug, trace, ultra)
    """
    Logger.json_output = False
    Logger.ndjson_output = False
    Logger.quiet = False

    with CaptureStdout() as captured:
        Logger.set_verbosity(verbosity)
        Logger.set_no_color(True)
        getattr(Logger, method)("test")
        output = captured.getvalue()
    assert ("test" in output) == should_output, (
        f"verbosity={verbosity}, method={method}: "
        f"expected {'output' if should_output else 'no output'}, got {output!r}"
    )


# Parametrized test for invalid verbosity level handling
INVALID_VERBOSITY_CASES = [
    pytest.param(-1, 0, id="negative_clamped_to_min"),
    pytest.param(-100, 0, id="large_negative_clamped_to_min"),
    pytest.param(4, 3, id="above_max_clamped_to_max"),
    pytest.param(100, 3, id="large_positive_clamped_to_max"),
]


@pytest.mark.parametrize("invalid_level,expected_level", INVALID_VERBOSITY_CASES)
def test_verbosity_invalid_levels(logger_reset, invalid_level, expected_level):
    """Test that invalid verbosity levels are clamped to valid range [0, 3].

    Invalid levels are handled gracefully:
    - Negative values are clamped to 0 (minimum)
    - Values above 3 are clamped to 3 (maximum)
    """
    Logger.set_verbosity(invalid_level)
    assert Logger.verbosity == expected_level, (
        f"Expected verbosity {expected_level} after setting {invalid_level}, "
        f"got {Logger.verbosity}"
    )


# Parametrized test for log level filtering behavior
# Log levels: debug=10, info=20, warn=30, error=40
# Format: (log_level, method, should_output)
LOG_LEVEL_FILTERING_CASES = [
    # Original test cases: basic filtering behavior
    pytest.param(20, 'info', True, id="info_level_shows_info"),
    pytest.param(30, 'info', False, id="warn_level_filters_info"),
    pytest.param(30, 'warning', True, id="warn_level_shows_warning"),
    pytest.param(40, 'warning', False, id="error_level_filters_warning"),
    # Edge case: DEBUG level (10) shows all messages
    pytest.param(10, 'info', True, id="debug_level_shows_info"),
    pytest.param(10, 'warning', True, id="debug_level_shows_warning"),
    pytest.param(10, 'error', True, id="debug_level_shows_error"),
    # Edge case: ERROR level (40) filters all lower-priority messages
    pytest.param(40, 'info', False, id="error_level_filters_info"),
    pytest.param(40, 'error', True, id="error_level_shows_error"),
]


@pytest.mark.parametrize("log_level,method,should_output", LOG_LEVEL_FILTERING_CASES)
def test_log_level_filtering(logger_reset, log_level, method, should_output):
    """Test that log level filtering correctly shows/hides messages.

    Log levels control which messages are output:
    - debug (10): All messages shown
    - info (20): info, warning, error shown
    - warn (30): warning, error shown
    - error (40): Only error shown

    Edge cases:
    - DEBUG level (10) shows all messages (info, warning, error)
    - ERROR level (40) filters all lower-priority messages

    Note: Invalid log level values are not validated by Logger.log_level.
    Setting Logger.log_level to an arbitrary integer is allowed; values
    below 10 show all messages, values above 40 hide all messages.
    Use Logger.set_log_level() with valid string keys for safe level setting.
    """
    Logger.json_output = False
    Logger.ndjson_output = False
    Logger.quiet = False
    Logger.log_level = log_level
    Logger.set_no_color(True)
    Logger.set_verbosity(0)

    with CaptureStdout() as captured:
        getattr(Logger, method)("test")
        output = captured.getvalue()

    assert ("test" in output) == should_output, (
        f"log_level={log_level}, method={method}: "
        f"expected {'output' if should_output else 'no output'}, got {output!r}"
    )
