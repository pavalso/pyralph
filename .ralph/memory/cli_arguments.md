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

### --phase Argument

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
usage: ralph.py [-h] [--phase {architect,planner,execute,all}] [--accept-all]

Ralph - Autonomous Software Development Agent

options:
  -h, --help            show this help message and exit
  --phase {architect,planner,execute,all}
                        Select which phase to run (default: all)
  --accept-all          Skip user feedback prompts and proceed with all phases

Examples:
  ralph                          # Run all phases
  ralph --phase architect        # Run architect phase only
  ralph --phase planner          # Run planner phase only
  ralph --phase execute          # Run execute phase only
  ralph --accept-all             # Run all phases without prompts
  ralph --phase execute --accept-all  # Execute with no prompts
```

## Test Coverage

All 38 tests pass, including:
- Phase selection tests (architect, planner, execute, all)
- Accept-all flag behavior tests
- Prompt behavior validation
- Integration tests for argument passing
