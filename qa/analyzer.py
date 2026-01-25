#!/usr/bin/env python3
"""QA findings analysis for Ralph.

This module contains the findings analyzer for QA review output:
- QAFindingsAnalyzer: Parses, categorizes, and groups QA findings
"""
from typing import Any, Dict, List

from qa.models import QAFinding, QAFindingType


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
