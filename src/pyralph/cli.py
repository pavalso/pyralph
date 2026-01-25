#!/usr/bin/env python3
"""CLI module for Ralph.

This module contains the CLI entry point (main) and version functions that handle
command-line argument parsing and application startup.
"""
import json
import sys
import argparse
from pathlib import Path

from .logger import Logger
from .agents import list_agents


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


def main() -> None:
    """Entry point for the ralph CLI."""
    agent = list_agents()[0]
    parser = argparse.ArgumentParser(description="Ralph - Autonomous Software Development Agent",
        epilog="Examples: ralph | ralph architect | ralph -y execute | ralph -vvv --no-emoji execute",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["architect", "planner", "execute", "all", "qa-status"], default="all", nargs="?", help="Phase to run")
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
    parser.add_argument("--qa-review", action="store_true", help="Enable QA agent to review implemented code for quality issues after each task. When combined with --qa-path, runs standalone QA review workflow")
    parser.add_argument("--no-qa-review", action="store_true", help="Disable QA review (overrides --enhance-all)")
    parser.add_argument("--qa-strict", action="store_true", help="Fail tasks when QA review finds critical issues (requires --qa-review)")
    parser.add_argument("--qa-path", type=str, metavar="PATH", help="Path to review for standalone QA workflow (requires --qa-review). If not specified with --qa-review, reviews the entire codebase")
    parser.add_argument("--qa-checklist", type=str, metavar="FILE", help="Path to custom QA checklist JSON file (default: .ralph/qa-checklist.json)")
    # Enhancement combination flag
    parser.add_argument("--enhance-all", action="store_true", help="Enable all enhancement features (--enhance-intent, --revise-prd, --qa-review). Individual --no-* flags can override specific features.")
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

    # Validate --qa-path requires --qa-review
    if args.qa_path and not args.qa_review:
        Logger.error("--qa-path requires --qa-review flag to run standalone QA workflow.")
        sys.exit(1)

    # Validate --qa-path is incompatible with phase execution
    if args.qa_path and args.phase != "all":
        Logger.error(f"--qa-path cannot be combined with phase '{args.phase}'. Standalone QA workflow runs independently of task phases.")
        sys.exit(1)

    # Validate --qa-checklist file exists and is valid JSON
    qa_checklist_path = None
    if args.qa_checklist:
        # Resolve relative paths from current working directory
        qa_checklist_path = Path(args.qa_checklist)
        if not qa_checklist_path.is_absolute():
            qa_checklist_path = Path.cwd() / qa_checklist_path
        qa_checklist_path = qa_checklist_path.resolve()

        if not qa_checklist_path.exists():
            Logger.error(f"QA checklist file not found: {qa_checklist_path}")
            sys.exit(1)

        # Validate JSON format
        try:
            content = qa_checklist_path.read_text(encoding='utf-8')
            json.loads(content)
        except json.JSONDecodeError as e:
            Logger.error(f"Invalid JSON in QA checklist file: {qa_checklist_path}")
            Logger.error(f"  Parse error: {e.msg} at line {e.lineno}, column {e.colno}")
            sys.exit(1)
        except OSError as e:
            Logger.error(f"Cannot read QA checklist file: {qa_checklist_path}")
            Logger.error(f"  Error: {e}")
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

    # Late import to allow tests to patch ralph.RalphOrchestrator
    import pyralph
    pyralph.RalphOrchestrator(
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
        qa_strict=args.qa_strict,
        qa_path=args.qa_path,
        qa_checklist=qa_checklist_path
    ).start(phase=args.phase, accept_all=args.accept_all)
