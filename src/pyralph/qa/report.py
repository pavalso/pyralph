#!/usr/bin/env python3
"""QA Report writer module for Ralph.

This module provides the QAReportWriter class that generates timestamped
failure reports in .ralph/qa/reports/ when QA validation fails.
"""

import errno
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import CONF

if TYPE_CHECKING:
    from .executor import QAResult


class ReportWriteError(Exception):
    """Raised when report cannot be written due to permissions or other errors."""
    pass


class QAReportWriter:
    """Writes QA failure reports to the reports directory.

    This class handles:
    - Creating the reports directory if it doesn't exist
    - Generating unique timestamped filenames (millisecond precision)
    - Writing failure reports in a structured format
    - Handling permission errors with descriptive messages

    Usage:
        writer = QAReportWriter()
        report_path = writer.write_report(qa_result)
    """

    REPORT_FILENAME_FORMAT = "qa-report-{timestamp}.json"
    TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S-%f"

    def __init__(self, reports_dir: Path = None):
        """Initialize the report writer.

        Args:
            reports_dir: Path to the reports directory. Defaults to CONF.QA_REPORTS_DIR.
        """
        self._reports_dir = reports_dir or CONF.QA_REPORTS_DIR

    @property
    def reports_dir(self) -> Path:
        """Get the reports directory path."""
        return self._reports_dir

    def _ensure_directory(self) -> None:
        """Create the reports directory if it doesn't exist.

        Raises:
            PermissionError: If the directory cannot be created due to insufficient permissions.
        """
        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied: Cannot create QA reports directory '{self._reports_dir}'. "
                f"Please check write permissions on the parent directory."
            ) from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise PermissionError(
                    f"Permission denied: Cannot access QA reports directory '{self._reports_dir}'. "
                    f"Please check directory permissions."
                ) from e
            raise

    def _generate_unique_filename(self) -> str:
        """Generate a unique timestamped filename.

        Uses millisecond precision to ensure uniqueness. If a file with the same
        timestamp exists, appends a numeric suffix.

        Returns:
            Unique filename string.
        """
        timestamp = datetime.now().strftime(self.TIMESTAMP_FORMAT)
        base_filename = self.REPORT_FILENAME_FORMAT.format(timestamp=timestamp)

        # Check if file exists and add suffix if needed
        file_path = self._reports_dir / base_filename
        if not file_path.exists():
            return base_filename

        # Add numeric suffix for uniqueness
        suffix = 1
        while True:
            name_without_ext = base_filename.rsplit('.', 1)[0]
            suffixed_filename = f"{name_without_ext}-{suffix}.json"
            if not (self._reports_dir / suffixed_filename).exists():
                return suffixed_filename
            suffix += 1

    def _format_report(self, result: "QAResult") -> dict:
        """Format the QA result into a report structure.

        Args:
            result: The QAResult from validation.

        Returns:
            Dictionary containing the formatted report.
        """
        return {
            "generated_at": datetime.now().isoformat(),
            "success": result.success,
            "rules_checked": result.rules_checked,
            "violation_count": len(result.violations),
            "summary": result.summary,
            "violations": [v.to_dict() for v in result.violations],
        }

    def write_report(self, result: "QAResult") -> Path:
        """Write a failure report for the QA validation result.

        Only writes a report if there are violations (result.success is False).

        Args:
            result: The QAResult from validation.

        Returns:
            Path to the written report file.

        Raises:
            PermissionError: If the reports directory cannot be created or written to.
            ReportWriteError: If the report cannot be written for other reasons.
        """
        self._ensure_directory()

        filename = self._generate_unique_filename()
        file_path = self._reports_dir / filename

        report_content = self._format_report(result)

        try:
            file_path.write_text(
                json.dumps(report_content, indent=2),
                encoding="utf-8"
            )
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied: Cannot write QA report to '{file_path}'. "
                f"Please check write permissions for .ralph/qa/reports/ directory."
            ) from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise PermissionError(
                    f"Permission denied: Cannot write QA report to '{file_path}'. "
                    f"Error: {e}"
                ) from e
            raise ReportWriteError(
                f"Failed to write QA report to '{file_path}': {e}"
            ) from e

        return file_path

    def should_write_report(self, result: "QAResult") -> bool:
        """Determine if a report should be written for the given result.

        Reports are only generated for failed validations with violations.

        Args:
            result: The QAResult from validation.

        Returns:
            True if a report should be written, False otherwise.
        """
        return not result.success and len(result.violations) > 0
