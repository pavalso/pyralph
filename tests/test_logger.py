import json
from io import StringIO

from pyralph.logger import Logger

from .helpers import LoggerTestCase


class TestLogger(LoggerTestCase):
    def test_toggles(self):
        for setter, attr in [(Logger.set_no_color, 'no_color'), (Logger.set_verbose, 'verbose')]:
            setter(True)
            assert getattr(Logger, attr)
            setter(False)
            assert not getattr(Logger, attr)

    def test_colors_keys(self):
        assert set(Logger.COLORS.keys()) == {"RESET", "GREEN", "RED", "CYAN", "YELLOW", "MAGENTA"}

    def test_info_debug_output(self):
        cases = [
            (False, True, 'info', "Plain", None, ["Plain"], ["\033["]),
            (False, False, 'info', "Colored", "GREEN", ["\033[92m", "Colored"], []),
            (False, False, 'debug', "Debug", None, [], ["Debug"]),
            (True, True, 'debug', "Debug", None, ["[DEBUG] Debug"], ["\033["]),
        ]
        for verbose, no_color, method, msg, color, exp_in, exp_not in cases:
            self.held_output = StringIO()
            self.original_stdout = self.held_output
            self.held_output = StringIO()
            import sys
            sys.stdout = self.held_output
            Logger.set_verbose(verbose)
            Logger.set_no_color(no_color)
            getattr(Logger, method)(msg, color) if color else getattr(Logger, method)(msg)
            output = self.held_output.getvalue()
            for e in exp_in:
                assert e in output
            for e in exp_not:
                assert e not in output
            sys.stdout = self.original_stdout

    def test_file_log(self):
        cases = [("PROMPT", "TAG", "➡️"), ("RESPONSE", "TAG", "⬅️"), ("ERROR", "TAG", "❌"), ("INFO", None, "ℹ️")]
        for log_type, tag, icon in cases:
            if self.log_file.exists():
                self.log_file.unlink()
            Logger.file_log("Content", log_type, tag) if tag else Logger.file_log("Content", log_type)
            assert icon in self.log_file.read_text(encoding="utf-8")

    def test_verbosity_levels(self):
        cases = [(0, 'debug', False), (1, 'debug', True), (0, 'trace', False), (2, 'trace', True),
                 (0, 'ultra', False), (3, 'ultra', True)]
        for verbosity, method, should_output in cases:
            self.held_output = StringIO()
            import sys
            sys.stdout = self.held_output
            Logger.set_verbosity(verbosity)
            Logger.set_no_color(True)
            getattr(Logger, method)("test")
            output = self.held_output.getvalue()
            assert ("test" in output) == should_output
            sys.stdout = self.original_stdout

    def test_verbosity_sync_and_clamp(self):
        Logger.set_verbosity(0)
        assert not Logger.verbose
        Logger.set_verbosity(1)
        assert Logger.verbose
        Logger.set_verbosity(-5)
        assert Logger.verbosity == 0
        Logger.set_verbosity(10)
        assert Logger.verbosity == 3

    def test_verbosity_property_sync_direct_assignment(self):
        Logger.verbosity = 0
        assert not Logger.verbose
        assert Logger.verbosity == 0

        Logger.verbosity = 2
        assert Logger.verbose
        assert Logger.verbosity == 2

        Logger.verbose = False
        assert not Logger.verbose
        assert Logger.verbosity == 0

        Logger.verbose = True
        assert Logger.verbose
        assert Logger.verbosity == 1

        Logger.verbosity = -10
        assert Logger.verbosity == 0
        assert not Logger.verbose

        Logger.verbosity = 100
        assert Logger.verbosity == 3
        assert Logger.verbose

    def test_quiet_mode(self):
        Logger.set_no_color(True)
        Logger.set_quiet(True)
        import sys
        sys.stdout = self.held_output
        Logger.info("suppressed")
        assert self.held_output.getvalue() == ""
        Logger.warning("warning")
        Logger.error("error")
        output = self.held_output.getvalue()
        assert "warning" in output
        assert "error" in output

    def test_no_emoji(self):
        Logger.set_no_color(True)
        Logger.set_no_emoji(True)
        import sys
        sys.stdout = self.held_output
        Logger.info("🤖 Robot ✅")
        output = self.held_output.getvalue()
        assert "[BOT]" in output
        assert "🤖" not in output

    def test_strip_emoji_all(self):
        emojis = ["🤖", "🕵️", "🧠", "🚀", "✅", "❌", "⚠️", "▶️", "🔒", "🛑", "⏭️", "📋", "📦", "🎉", "➡️", "⬅️", "ℹ️", "❓"]
        for emoji in emojis:
            result = Logger._strip_emoji(f"Test {emoji} msg")
            assert emoji not in result


class TestLoggerRedaction(LoggerTestCase):
    def test_set_redact_patterns(self):
        Logger.set_redact_patterns(['p1', 'p2'])
        assert Logger.redact_patterns == ['p1', 'p2']
        Logger.set_redact_patterns(['new'])
        assert Logger.redact_patterns == ['new']

    def test_redact_content(self):
        cases = [
            ([], "api_key=secret", "api_key=secret"),
            ([r'api_key=\\w+'], "The api_key=secret here", "The [REDACTED] here"),
            ([r'api_key=\\w+', r'pwd=\\w+'], "api_key=x pwd=y", "[REDACTED] [REDACTED]"),
            ([r'secret\\d+'], "secret1 secret2 secret3", "[REDACTED] [REDACTED] [REDACTED]"),
        ]
        for patterns, content, expected in cases:
            Logger.redact_patterns = patterns
            assert Logger._redact_content(content) == expected

    def test_redact_from_file(self):
        import tempfile
        from pathlib import Path
        redact_file = Path(self.temp_dir) / "redact.txt"
        redact_file.write_text("pattern1\n\n# comment\npattern2\n", encoding='utf-8')
        Logger.add_redact_patterns_from_file(str(redact_file))
        assert Logger.redact_patterns == ['pattern1', 'pattern2']

    def test_no_log_prompts_responses(self):
        for flag, log_type, other_type in [
            ('no_log_prompts', 'PROMPT', 'RESPONSE'),
            ('no_log_responses', 'RESPONSE', 'PROMPT'),
        ]:
            if self.log_file.exists():
                self.log_file.unlink()
            setattr(Logger, flag, True)
            Logger.file_log("skip", log_type, "T")
            assert not self.log_file.exists()
            Logger.file_log("keep", other_type, "T")
            assert "keep" in self.log_file.read_text(encoding='utf-8')
            setattr(Logger, flag, False)

    def test_file_log_redaction(self):
        Logger.set_redact_patterns([r'secret_key=\\w+'])
        Logger.file_log("secret_key=abc123", "INFO", "T")
        content = self.log_file.read_text(encoding="utf-8")
        assert "[REDACTED]" in content
        assert "abc123" not in content


class TestLoggerJsonOutput(LoggerTestCase):
    def test_json_output_mode(self):
        Logger.json_output = True
        import sys
        sys.stdout = self.held_output
        Logger.info("test message")
        output = self.held_output.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["message"] == "test message"
        assert parsed["level"] == "info"

    def test_ndjson_output_mode(self):
        Logger.ndjson_output = True
        import sys
        sys.stdout = self.held_output
        Logger.info("msg1")
        Logger.info("msg2")
        lines = [l for l in self.held_output.getvalue().strip().split('\\n') if l]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)

    def test_log_level_filtering(self):
        cases = [(20, 'info', True), (30, 'info', False), (30, 'warning', True), (40, 'warning', False)]
        for level, method, should_output in cases:
            Logger.log_level = level
            Logger.set_no_color(True)
            Logger.set_verbosity(0)
            self.held_output = StringIO()
            import sys
            sys.stdout = self.held_output
            getattr(Logger, method)("test")
            assert ("test" in self.held_output.getvalue()) == should_output
            sys.stdout = self.original_stdout
