#!/usr/bin/env python3
"""CLI module for Ralph QA command.

This module provides the CLI entry point for running QA validation
using an autonomous agent against defined QA rules.
"""

import argparse
import sys
from typing import Optional

from ..agents import list_agents
from ..logger import Logger
from .executor import (
    QAExecutor,
    AgentTimeoutError,
    AgentUnavailableError,
    MalformedResponseError,
)
from .report import QAReportWriter


def get_version() -> str:
    """Get the version string from package metadata.

    Returns:
        Version string from package metadata, or 'unknown' if not found.
    """
    try:
        from importlib.metadata import version
        return version("pyralph")
    except Exception:
        pass
    return "unknown"


def format_violation(violation, use_color: bool = True) -> str:
    """Format a single violation for display.

    Args:
        violation: QAViolation object.
        use_color: Whether to use ANSI colors.

    Returns:
        Formatted violation string.
    """
    severity_colors = {
        "critical": "RED",
        "major": "YELLOW",
        "minor": "CYAN",
        "info": "RESET",
    }

    severity = violation.severity.upper()
    location = ""
    if violation.file_path:
        location = f" in {violation.file_path}"
        if violation.line_number:
            location += f":{violation.line_number}"

    message = f"[{severity}] {violation.rule_name}{location}: {violation.message}"
    return message


def format_result_json(result) -> str:
    """Format the result as JSON.

    Args:
        result: QAResult object.

    Returns:
        JSON string.
    """
    import json
    return json.dumps(result.to_dict(), indent=2)


def main(args: Optional[list] = None) -> int:
    """Entry point for the ralph-qa CLI.

    Args:
        args: Optional command line arguments (for testing).

    Returns:
        Exit code (0 for success, 1 for violations found, 2 for errors).
    """
    available_agents = list_agents()
    default_agent = available_agents[0] if available_agents else "claude"

    parser = argparse.ArgumentParser(
        description="Ralph QA - Validate code changes against QA rules using an autonomous agent",
        epilog="Examples: ralph-qa --agent claude | ralph-qa --agent copilot --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Ralph QA {get_version()}",
    )
    parser.add_argument(
        "--agent",
        choices=available_agents,
        default=default_agent,
        required=True,
        help=f"Agent to use for QA validation (required). Available: {', '.join(available_agents)}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        metavar="SECS",
        help="Timeout in seconds for agent execution (default: 600)",
    )
    parser.add_argument(
        "--model",
        type=str,
        metavar="MODEL",
        help="Model identifier for LLM requests",
    )
    parser.add_argument(
        "--rules-dir",
        type=str,
        metavar="DIR",
        help="Path to QA rules directory (default: .ralph/qa/)",
    )

    # Output format options
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results in JSON format",
    )
    output_group.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress all output except errors",
    )

    # Logger configuration
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv, -vvv)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "--no-emoji",
        action="store_true",
        help="Disable emoji output",
    )

    parsed_args = parser.parse_args(args)

    # Configure logger
    Logger.set_verbosity(parsed_args.verbose)
    Logger.set_quiet(parsed_args.quiet)
    Logger.set_no_emoji(parsed_args.no_emoji)
    if parsed_args.no_color:
        Logger.set_no_color(True)
    if parsed_args.json_output:
        Logger.set_json_output(True)

    try:
        executor = QAExecutor(
            agent_name=parsed_args.agent,
            timeout=parsed_args.timeout,
            model=parsed_args.model,
            rules_dir=parsed_args.rules_dir,
        )

        result = executor.run()

        # Output results
        if parsed_args.json_output:
            print(format_result_json(result))
        elif not parsed_args.quiet:
            if result.rules_checked == 0:
                Logger.info("No QA rules found in .ralph/qa/ directory. Nothing to validate.")
            elif result.success:
                Logger.success(f"QA validation passed. {result.rules_checked} rule(s) checked.")
                if result.summary:
                    Logger.info(result.summary)
            else:
                Logger.error(f"QA validation failed. Found {len(result.violations)} violation(s).")
                print("")
                for violation in result.violations:
                    print(format_violation(violation, use_color=not parsed_args.no_color))
                print("")
                if result.summary:
                    Logger.info(result.summary)

        # Generate failure report if there are violations
        if not result.success and len(result.violations) > 0:
            report_writer = QAReportWriter()
            report_path = report_writer.write_report(result)
            if not parsed_args.quiet:
                Logger.info(f"Failure report written to: {report_path}")

        # Return appropriate exit code
        if result.rules_checked == 0:
            return 0  # No rules = success (as per acceptance criteria)
        return 0 if result.success else 1

    except AgentTimeoutError as e:
        if parsed_args.json_output:
            import json
            print(json.dumps({
                "error": "timeout",
                "message": str(e),
            }))
        else:
            Logger.error(f"Timeout Error: {e}")
        return 2

    except AgentUnavailableError as e:
        if parsed_args.json_output:
            import json
            print(json.dumps({
                "error": "agent_unavailable",
                "message": str(e),
            }))
        else:
            Logger.error(f"Agent Error: {e}")
        return 2

    except MalformedResponseError as e:
        if parsed_args.json_output:
            import json
            print(json.dumps({
                "error": "malformed_response",
                "message": str(e),
            }))
        else:
            Logger.error(f"Response Format Error: {e}")
        return 2

    except PermissionError as e:
        if parsed_args.json_output:
            import json
            print(json.dumps({
                "error": "permission_denied",
                "message": str(e),
            }))
        else:
            Logger.error(f"Permission Error: {e}")
        return 2

    except KeyboardInterrupt:
        if not parsed_args.quiet:
            Logger.warning("Interrupted by user")
        return 130

    except Exception as e:
        if parsed_args.json_output:
            import json
            print(json.dumps({
                "error": "unexpected_error",
                "message": str(e),
            }))
        else:
            Logger.error(f"Unexpected error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
