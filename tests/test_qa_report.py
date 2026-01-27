"""Tests for QA report writer module."""

import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from pyralph.config import CONF
from pyralph.qa.report import QAReportWriter, ReportWriteError
from pyralph.qa.executor import QAResult, QAViolation


class QAReportWriterTestCase(unittest.TestCase):
    """Base test case for QAReportWriter tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.reports_dir = self.temp_path / ".ralph" / "qa" / "reports"
        self._original_qa_reports_dir = CONF.QA_REPORTS_DIR
        CONF.QA_REPORTS_DIR = self.reports_dir

    def tearDown(self):
        CONF.QA_REPORTS_DIR = self._original_qa_reports_dir
        # Restore permissions before cleanup
        self._restore_permissions(self.temp_path)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _restore_permissions(self, path: Path) -> None:
        """Recursively restore write permissions for cleanup."""
        for root, dirs, files in os.walk(path):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), stat.S_IRWXU)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), stat.S_IRWXU)
                except OSError:
                    pass

    def create_failed_result(self, violations_count: int = 1) -> QAResult:
        """Create a failed QAResult with violations."""
        violations = [
            QAViolation(
                rule_id=f"rule-{i}",
                rule_name=f"Rule {i}",
                severity="major",
                message=f"Violation {i}",
                file_path=f"file{i}.py",
                line_number=i * 10,
            )
            for i in range(1, violations_count + 1)
        ]
        return QAResult(
            success=False,
            rules_checked=violations_count,
            violations=violations,
            summary=f"{violations_count} violation(s) found",
        )

    def create_passed_result(self) -> QAResult:
        """Create a successful QAResult with no violations."""
        return QAResult(
            success=True,
            rules_checked=3,
            violations=[],
            summary="All rules passed",
        )


class TestQAReportWriterBasics(QAReportWriterTestCase):
    """Tests for basic QAReportWriter functionality."""

    def test_creates_reports_directory(self):
        """Test that reports directory is created automatically."""
        assert not self.reports_dir.exists()
        writer = QAReportWriter()
        result = self.create_failed_result()
        writer.write_report(result)
        assert self.reports_dir.exists()

    def test_writes_report_file(self):
        """Test that a report file is created."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        report_path = writer.write_report(result)
        assert report_path.exists()
        assert report_path.suffix == ".json"

    def test_report_contains_expected_fields(self):
        """Test that report contains all expected fields."""
        writer = QAReportWriter()
        result = self.create_failed_result(violations_count=2)
        report_path = writer.write_report(result)

        content = json.loads(report_path.read_text(encoding="utf-8"))
        assert "generated_at" in content
        assert content["success"] is False
        assert content["rules_checked"] == 2
        assert content["violation_count"] == 2
        assert "summary" in content
        assert len(content["violations"]) == 2

    def test_report_violations_serialized(self):
        """Test that violations are properly serialized."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        report_path = writer.write_report(result)

        content = json.loads(report_path.read_text(encoding="utf-8"))
        violation = content["violations"][0]
        assert violation["rule_id"] == "rule-1"
        assert violation["rule_name"] == "Rule 1"
        assert violation["severity"] == "major"
        assert violation["message"] == "Violation 1"
        assert violation["file_path"] == "file1.py"
        assert violation["line_number"] == 10


class TestQAReportTimestamp(QAReportWriterTestCase):
    """Tests for timestamped report filenames."""

    def test_filename_contains_timestamp(self):
        """Test that report filename contains a timestamp."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        report_path = writer.write_report(result)

        filename = report_path.name
        assert filename.startswith("qa-report-")
        assert filename.endswith(".json")
        # Should contain date pattern like 20240101-
        assert "-" in filename

    def test_unique_filename_with_suffix(self):
        """Test that duplicate timestamps get numeric suffix."""
        writer = QAReportWriter()
        result = self.create_failed_result()

        # Ensure reports directory exists
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # Create first file with a specific timestamp
        timestamp = "20240101-120000-000000"
        first_file = self.reports_dir / f"qa-report-{timestamp}.json"
        first_file.write_text("{}", encoding="utf-8")

        # Mock datetime.now() to return the same timestamp
        mock_now = MagicMock()
        mock_now.strftime.return_value = timestamp
        mock_now.isoformat.return_value = "2024-01-01T12:00:00"

        with patch("pyralph.qa.report.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now

            writer2 = QAReportWriter()
            result2 = self.create_failed_result()
            report_path2 = writer2.write_report(result2)

            # Second file should have suffix -1
            assert "-1" in report_path2.name

    def test_millisecond_precision(self):
        """Test that timestamps have millisecond precision for uniqueness."""
        writer = QAReportWriter()
        result = self.create_failed_result()

        # Generate multiple reports quickly
        paths = []
        for _ in range(3):
            paths.append(writer.write_report(result))
            time.sleep(0.001)  # Small delay to ensure different microseconds

        # All paths should be unique
        assert len(set(paths)) == 3


class TestQAReportDirectoryCreation(QAReportWriterTestCase):
    """Tests for automatic directory creation."""

    def test_creates_nested_directories(self):
        """Test that nested .ralph/qa/reports/ directory is created."""
        # Set a deeply nested path
        nested_dir = self.temp_path / "a" / "b" / "c" / "reports"
        writer = QAReportWriter(reports_dir=nested_dir)
        result = self.create_failed_result()

        report_path = writer.write_report(result)
        assert nested_dir.exists()
        assert report_path.exists()


class TestQAReportPermissionErrors(QAReportWriterTestCase):
    """Tests for permission error handling."""

    @unittest.skipIf(os.name == 'nt', "Permission tests behave differently on Windows")
    def test_permission_denied_on_directory_creation(self):
        """Test that permission error is raised when directory can't be created."""
        # Create parent directory with no write permissions
        parent_dir = self.temp_path / "no_write"
        parent_dir.mkdir()
        os.chmod(parent_dir, stat.S_IRUSR | stat.S_IXUSR)

        writer = QAReportWriter(reports_dir=parent_dir / "reports")
        result = self.create_failed_result()

        with self.assertRaises(PermissionError) as context:
            writer.write_report(result)

        assert "Permission denied" in str(context.exception)
        assert "Cannot create" in str(context.exception)

    @unittest.skipIf(os.name == 'nt', "Permission tests behave differently on Windows")
    def test_permission_denied_on_file_write(self):
        """Test that permission error is raised when file can't be written."""
        # Create reports directory with no write permissions
        self.reports_dir.mkdir(parents=True)
        os.chmod(self.reports_dir, stat.S_IRUSR | stat.S_IXUSR)

        writer = QAReportWriter()
        result = self.create_failed_result()

        with self.assertRaises(PermissionError) as context:
            writer.write_report(result)

        assert "Permission denied" in str(context.exception)

    def test_permission_error_message_describes_issue(self):
        """Test that permission error messages describe the issue."""
        writer = QAReportWriter()

        # Mock mkdir to raise PermissionError
        with patch.object(Path, "mkdir") as mock_mkdir:
            mock_mkdir.side_effect = PermissionError("Access denied")
            result = self.create_failed_result()

            with self.assertRaises(PermissionError) as context:
                writer.write_report(result)

            assert "Permission denied" in str(context.exception)
            assert "write permissions" in str(context.exception).lower() or "access" in str(context.exception).lower()


class TestQAReportShouldWrite(QAReportWriterTestCase):
    """Tests for should_write_report logic."""

    def test_should_write_for_failed_result(self):
        """Test that report should be written for failed results."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        assert writer.should_write_report(result) is True

    def test_should_not_write_for_passed_result(self):
        """Test that report should not be written for passed results."""
        writer = QAReportWriter()
        result = self.create_passed_result()
        assert writer.should_write_report(result) is False

    def test_should_not_write_for_failed_but_no_violations(self):
        """Test that report should not be written when no violations."""
        writer = QAReportWriter()
        result = QAResult(
            success=False,
            rules_checked=1,
            violations=[],  # No violations
            summary="Failed but no violations",
        )
        assert writer.should_write_report(result) is False


class TestQAReportCustomDirectory(QAReportWriterTestCase):
    """Tests for using custom reports directory."""

    def test_custom_reports_dir(self):
        """Test that custom reports directory is used."""
        custom_dir = self.temp_path / "custom_reports"
        writer = QAReportWriter(reports_dir=custom_dir)
        result = self.create_failed_result()

        report_path = writer.write_report(result)
        assert report_path.parent == custom_dir
        assert custom_dir.exists()

    def test_reports_dir_property(self):
        """Test that reports_dir property returns the configured directory."""
        custom_dir = self.temp_path / "my_reports"
        writer = QAReportWriter(reports_dir=custom_dir)
        assert writer.reports_dir == custom_dir

    def test_default_reports_dir_from_config(self):
        """Test that default reports directory comes from CONF."""
        writer = QAReportWriter()
        assert writer.reports_dir == CONF.QA_REPORTS_DIR


class TestQAReportFormat(QAReportWriterTestCase):
    """Tests for report format and content."""

    def test_report_is_valid_json(self):
        """Test that report is valid JSON."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        report_path = writer.write_report(result)

        # Should not raise
        content = json.loads(report_path.read_text(encoding="utf-8"))
        assert isinstance(content, dict)

    def test_report_generated_at_is_iso_format(self):
        """Test that generated_at timestamp is ISO format."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        report_path = writer.write_report(result)

        content = json.loads(report_path.read_text(encoding="utf-8"))
        # Should parse without error
        datetime.fromisoformat(content["generated_at"])

    def test_report_is_human_readable(self):
        """Test that report is formatted with indentation."""
        writer = QAReportWriter()
        result = self.create_failed_result()
        report_path = writer.write_report(result)

        text = report_path.read_text(encoding="utf-8")
        # JSON should be indented (multi-line)
        assert "\n" in text
        assert "  " in text  # 2-space indentation


if __name__ == "__main__":
    unittest.main()
