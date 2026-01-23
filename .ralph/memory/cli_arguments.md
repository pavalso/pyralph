---
type: wiki
title: Ralph CLI Arguments Implementation
created: 2026-01-21
---

# CLI Arguments Implementation

## Overview

The `main()` function in `ralph.py` (line 633-661) implements a complete ArgumentParser for the Ralph CLI with support for phase selection and automatic prompt skipping.

## ArgumentParser Configuration

### Description and Epilog

```python
parser = argparse.ArgumentParser(
    description="Ralph - Autonomous Software Development Agent",
    epilog="Examples:\n"
           "  ralph                          # Run all phases\n"
           "  ralph --phase architect        # Run architect phase only\n"
           "  ralph --phase planner          # Run planner phase only\n"
           "  ralph --phase execute          # Run execute phase only\n"
           "  ralph --accept-all             # Run all phases without prompts\n"
           "  ralph --phase execute --accept-all  # Execute with no prompts",
    formatter_class=argparse.RawDescriptionHelpFormatter
)
```

## Arguments

### --agent Argument

```python
parser.add_argument(
    "--agent",
    choices=list_agents(),
    default="claude",
    help="Select which agent to use (default: claude)"
)
```
- **Choices**: dynamically listed by `list_agents()`
- **Default**: claude
- **Description**: Select which agent implementation to use for the run


### --version Flag

```python
parser.add_argument(
    "--version",
    action="version",
    version=f"Ralph {get_version()}"
)
```

- **Action**: version (displays version and exits)
- **Output**: "Ralph <version>"
- **Description**: Display the current version of Ralph

The version is sourced from `pyproject.toml` using the `get_version()` helper function which parses the version field dynamically.

### phase Argument (positional or --phase)

```python
parser.add_argument(
    "--phase",
    choices=["architect", "planner", "execute", "all"],
    default="all",
    help="Select which phase to run (default: all)"
)
```

- **Choices**: architect, planner, execute, all
- **Default**: all
- **Description**: Select which Ralph phase to run

### --accept-all Flag

```python
parser.add_argument(
    "--accept-all",
    action="store_true",
    help="Skip user feedback prompts and proceed with all phases"
)
```

- **Action**: store_true (boolean flag, no value required)
- **Default**: False
- **Description**: Skip user confirmation prompts and proceed automatically

### --verbose Flag

```python
parser.add_argument(
    "--verbose",
    action="store_true",
    help="Enable debug-level logging and display Claude CLI prompts and responses"
)
```

- **Action**: store_true (boolean flag, no value required)
- **Default**: False
- **Description**: Enable debug-level output showing all Claude CLI prompts and responses
- **Behavior**: Sets `Logger.verbose = True` to display debug messages with colored output
- **Use cases**: Debugging agent behavior, understanding Claude prompt/response flow

### --no-color Flag

```python
parser.add_argument(
    "--no-color",
    action="store_true",
    help="Disable color output in CLI"
)
```

- **Action**: store_true (boolean flag, no value required)
- **Default**: False
- **Description**: Disable colored output in CLI messages
- **Behavior**: Sets `Logger.no_color = True` to suppress ANSI color codes in all output
- **Integration**: The Logger class uses this flag to disable all color codes in console output and logs.
- **Use cases**: Useful for environments where colored output is not supported (e.g., CI logs, plain terminals)
- **Tested**: CLI output is plain when --no-color is set; all 26 CLI argument tests pass including this flag.

## Argument Passing

```python
args = parser.parse_args()
RalphOrchestrator().start(phase=args.phase, accept_all=args.accept_all)
```

Parsed arguments are passed directly to `RalphOrchestrator.start()` method which handles phase-specific execution and prompt behavior.

## Integration with RalphOrchestrator

The `start()` method accepts both parameters:
- `phase`: Controls which phase(s) to execute
- `accept_all`: Skips user feedback prompts when True

User intent prompts still appear when needed (architect, planner phases) unless empty input is provided (which exits with code 0).

## Help Output

```
usage: ralph.py [-h] [--version] [--phase {architect,planner,execute,all}] [--accept-all] [--verbose]

Ralph - Autonomous Software Development Agent

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --phase {architect,planner,execute,all}
                        Select which phase to run (default: all)
  --accept-all          Skip user feedback prompts and proceed with all phases
  --verbose             Enable debug-level logging and display Claude CLI prompts and responses

Examples:
  ralph                          # Run all phases
  ralph --version                # Display version
  ralph --phase architect        # Run architect phase only
  ralph --phase planner          # Run planner phase only
  ralph --phase execute          # Run execute phase only
  ralph --accept-all             # Run all phases without prompts
  ralph --phase execute --accept-all  # Execute with no prompts
  ralph --verbose                # Run with debug-level logging enabled
  ralph --verbose --phase execute # Execute with verbose output for debugging
```

## Test Coverage

All 26 tests pass, including:
- Phase selection tests (architect, planner, execute, all)
- Accept-all flag behavior tests
- Verbose flag integration (Logger.set_verbose() called correctly)
- Prompt behavior validation
- Integration tests for argument passing
