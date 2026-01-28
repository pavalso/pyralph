[![Documentation Status](https://readthedocs.org/projects/pyralph/badge/?version=latest)](https://pyralph.readthedocs.io/en/latest/?badge=latest)

# Ralph

**Ralph** is an autonomous software development agent that iteratively builds projects through a structured three-phase loop. It acts as a self-directing AI assistant that can understand project requirements, create detailed plans, and execute development tasks with built-in verification and error recovery.

Based on [Ralph Wiggum as a "Software engineer"](https://ghuntley.com/ralph/).

Yes. This is being developed using Ralph itself...

## Documentation

[**View Full Documentation**](https://pyralph.readthedocs.io/en/latest/) — Comprehensive guides, API reference, and examples.

## Three-Phase Workflow

Ralph operates through a continuous loop of three distinct phases:

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│   │   ARCHITECT  │───▶│    PLANNER   │───▶│   EXECUTE    │     │
│   │              │    │              │    │              │     │
│   │ • Explore    │    │ • Generate   │    │ • Run tasks  │     │
│   │   codebase   │    │   PRD with   │    │ • Verify via │     │
│   │ • Gather     │    │   user       │    │   tests      │     │
│   │   context    │    │   stories    │    │ • Retry on   │     │
│   │ • Build      │    │ • Define     │    │   failure    │     │
│   │   plan       │    │   acceptance │    │ • Commit on  │     │
│   │              │    │   criteria   │    │   success    │     │
│   └──────────────┘    └──────────────┘    └──────────────┘     │
│                                                    │           │
│                                                    ▼           │
│                                          ┌──────────────┐      │
│                                          │   Complete   │      │
│                                          │   or retry   │      │
│                                          └──────────────┘      │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

1. **Architect Phase**: Generates architecture documentation with project context
2. **Planner Phase**: Generates a Product Requirements Document (PRD) with user stories and acceptance criteria
3. **Execute Phase**: Iterates through tasks, running verification tests after each, and retrying on failure until completion

## Core Features

- **File-based state**: All context persisted to `.ralph/` directory for session resumability and crash recovery
- **Verification gate**: Agent claims validated by running actual tests (`pytest` by default) before accepting task completion
- **Retry mechanism**: Failed tasks automatically retry with error feedback injected into the next attempt
- **Knowledge injection**: Provide context directly via prompts or existing files.
- **Hook system**: Extensible event system for custom integrations—subscribe to lifecycle events (task start/success/failure, verification, phase transitions) via Python modules or executables
- **CI/CD support**: Headless mode with `--ci` flag, non-interactive execution, JSON/NDJSON output formats, and status checks for pipeline integration

## Installation

```bash
pip install pyralph
```

Or install from source:

```bash
git clone https://github.com/pavalso/pyralph.git
cd pyralph
pip install -e .
```

## Requirements

- Python 3.8+
- Claude CLI (`claude` command must be available in PATH)
- Git

## Usage

### Start the Agent

```bash
ralph
ralph --accept-all
ralph -y
ralph planner
ralph execute --accept-all
```

Starts or resumes the agent loop. If no project plan exists, prompts for a project description first.

You can use the `--accept-all` flag (or its shortcut `-y`) to skip all prompts and run every phase automatically.

### State Management

```bash
# Hard reset - clears all state
rm -rf .ralph/

# Re-plan - regenerate user stories
rm .ralph/prd.json
```

### Skip a Task

Edit `.ralph/prd.json` and change the task status to `"completed"`.

## CLI Reference

Ralph provides extensive command-line options organized into the following categories.

### Quick Reference

| Flag | Description |
|------|-------------|
| `-y`, `--accept-all` | Skip all prompts and run automatically |
| `-v`, `-vv`, `-vvv` | Increase verbosity level |
| `-q`, `--quiet` | Suppress non-essential output |
| `--ci` | CI mode (non-interactive, no color, JSON output) |
| `--intent "TEXT"` | Provide project intent inline |
| `--test-cmd "CMD"` | Override test command for verification |
| `--retries N` | Set max retries per task (default: 3) |
| `--only TASK_ID` | Execute only specified task(s) |
| `--resume TASK_ID` | Resume from a specific task |
| `--json` | Output in JSON format |
| `--enhance-intent` | Enhance intent before architect phase |
| `--revise-prd` | Revise PRD for quality improvements |
| `--enhance-all` | Enable all enhancement features |

### Output/Verbosity

Control how Ralph displays information during execution.

| Flag | Description |
|------|-------------|
| `-v`, `--verbose` | Increase verbosity (use -v, -vv, or -vvv for more detail) |
| `-q`, `--quiet` | Suppress non-essential output |
| `--no-color` | Disable colored output |
| `--no-emoji` | Replace emojis with text equivalents |

**Example**: Run with maximum verbosity and no colors for log parsing:
```bash
ralph -vvv --no-color execute
```

### Intent/Input

Specify what you want Ralph to build without interactive prompts.

| Flag | Description |
|------|-------------|
| `--intent TEXT` | Provide intent inline (what to build) |
| `--intent-file FILE` | Load intent from a file |
| `--prompt-file FILE` | Override prompt.md path for user context |

**Example**: Start a new project with intent from command line:
```bash
ralph --intent "Build a REST API with user authentication" architect
```

### Architect Control

Configure the architect phase behavior.

| Flag | Description |
|------|-------------|
| `--tree-depth N` | File tree depth for architect (default: 2) |
| `--tree-ignore PATTERN...` | Patterns to ignore in file tree |

**Example**: Generate deeper file tree analysis while ignoring test directories:
```bash
ralph --tree-depth 4 --tree-ignore "test*" "spec*" architect
```

### Execution Control

Fine-tune how Ralph executes tasks.

| Flag | Description |
|------|-------------|
| `--test-cmd CMD` | Override test command for verification |
| `--skip-verify` | Skip verification step after task execution |
| `--retries N` | Override max retries per task (default: 3) |
| `--timeout SECS` | Override agent timeout in seconds (default: 600) |
| `--only TASK_ID...` | Execute only specified task IDs |
| `--except TASK_ID...` | Skip specified task IDs |
| `--resume TASK_ID` | Resume execution from a specific task ID |

**Example**: Execute specific tasks with custom test command and extended timeout:
```bash
ralph --only TASK-001 TASK-003 --test-cmd "npm test" --timeout 900 execute
```

### Context

Control which files Ralph considers and how context is managed.

| Flag | Description |
|------|-------------|
| `--include PATTERN...` | Include only files matching these glob patterns in context |
| `--exclude PATTERN...` | Exclude files matching these glob patterns from context |
| `--context-limit N` | Limit maximum number of context files considered |

**Example**: Focus Ralph on source files only, excluding generated code:
```bash
ralph --include "src/**/*.py" --exclude "**/generated/**" execute
```

### Model/LLM

Configure the underlying language model behavior.

| Flag | Description |
|------|-------------|
| `--model MODEL` | Model identifier for LLM requests (e.g., claude-3-opus) |
| `--temperature TEMP` | Sampling temperature (0.0-1.0) for response generation |
| `--max-tokens N` | Maximum number of tokens in the LLM response |
| `--seed N` | Random seed for reproducible outputs |
| `--agent AGENT` | Select agent backend (e.g., claude, copilot) |

**Example**: Use a specific model with deterministic output:
```bash
ralph --model claude-3-opus --temperature 0 --seed 42 execute
```

### Logging/IO

Configure logging behavior and output formats.

| Flag | Description |
|------|-------------|
| `--log-file FILE` | Redirect log output to specified file |
| `--log-level LEVEL` | Set log level (debug, info, warn, error) |
| `--json` | Output in JSON format |
| `--ndjson` | Output in newline-delimited JSON format |
| `--print-prd` | Print PRD contents and exit without executing |
| `--prd-out FILE` | Export PRD to specified file |
| `--no-archive` | Skip PRD archival after execution |

**Example**: Generate detailed logs for debugging:
```bash
ralph --log-file debug.log --log-level debug --prd-out plan.json execute
```

### Headless/CI

Options for running Ralph in continuous integration pipelines.

| Flag | Description |
|------|-------------|
| `--non-interactive` | Disable all interactive prompts (fails if input required) |
| `--ci` | CI mode: enables --non-interactive --no-color --no-emoji --json |
| `--status-check` | Check PRD status and exit with code (0=complete, 1=incomplete, 2=no PRD) |

**Example**: Run Ralph in a CI pipeline with JSON output:
```bash
ralph --ci --intent-file requirements.txt all
# Or check completion status in a script
ralph --status-check && echo "All tasks complete"
```

### Extensibility

Extend Ralph with hooks and plugins.

| Flag | Description |
|------|-------------|
| `--no-hooks` | Disable hook execution |
| `--hooks NAME...` | Enable only specified hooks by name |
| `--pre CMD...` | Shell command(s) to run before each phase |
| `--post CMD...` | Shell command(s) to run after each phase |
| `--plugin PATH...` | Load plugin(s) from Python file or directory path |

**Example**: Run linting before each phase and notify on completion:
```bash
ralph --pre "npm run lint" --post "curl -X POST https://hooks.example.com/notify" execute
```

### Privacy

Protect sensitive information in logs and outputs.

| Flag | Description |
|------|-------------|
| `--redact PATTERN...` | Regex patterns to redact from logs (e.g., API keys) |
| `--redact-file FILE` | Load redaction patterns from file (one pattern per line) |
| `--no-log-prompts` | Do not log prompts to log file |
| `--no-log-responses` | Do not log responses to log file |

**Example**: Redact sensitive data from logs:
```bash
ralph --redact "sk-[a-zA-Z0-9]+" "password=\S+" --no-log-prompts execute
```

### PRD Validation

Validate and customize the generated Product Requirements Document.

| Flag | Description |
|------|-------------|
| `--schema FILE` | Validate generated PRD against a JSON schema file |
| `--min-criteria N` | Require at least N acceptance criteria per user story |
| `--label KEY=VAL...` | Add custom labels to PRD (format: key=value or just key) |

**Example**: Enforce PRD quality standards:
```bash
ralph --schema prd-schema.json --min-criteria 3 --label team=backend priority=high planner
```

### Enhancement Features

Ralph provides AI-powered enhancement agents that improve the quality of your inputs and outputs throughout the development workflow. These features can be enabled individually or all at once.

#### Enhancement Flags Reference

| Flag | Description |
|------|-------------|
| `--enhance-intent` | Process intent through enhancement agent before architect phase |
| `--enhance-intent-strict` | Exit on enhancement failure instead of falling back to original intent |
| `--no-enhance-intent` | Disable intent enhancement (overrides `--enhance-all`) |
| `--revise-prd` | Pass PRD through revision agent for quality improvements |
| `--no-revise-prd` | Disable PRD revision (overrides `--enhance-all`) |
| `--enhance-all` | Enable all enhancement features at once |

#### Intent Enhancement (`--enhance-intent`)

The intent enhancement agent refines your initial project description to create a more precise, actionable, and well-structured description. This helps ensure the architect phase receives clear requirements.

**What it does:**
- Clarifies ambiguities in your intent
- Adds specificity where the intent is too general
- Structures requirements into clear, logical components
- Surfaces implicit requirements that are essential but not explicitly stated
- Translates user-facing language into technical requirements

**Example**: Enhance intent before starting a project:
```bash
ralph --enhance-intent --intent "Build a todo app" architect
```

**With strict mode**: Exit if enhancement fails (instead of using original intent):
```bash
ralph --enhance-intent --enhance-intent-strict --intent "Build a todo app" architect
```

#### PRD Revision (`--revise-prd`)

The PRD revision agent reviews and improves the generated Product Requirements Document for clarity, completeness, and quality while preserving the original intent.

**What it does:**
- Ensures each user story has clear, unambiguous descriptions
- Verifies acceptance criteria are specific, measurable, and testable
- Identifies missing edge cases or error handling scenarios
- Ensures consistent terminology and formatting across all stories
- Verifies technical requirements are correctly specified
- Fixes any JSON formatting issues

**Example**: Revise PRD after generation:
```bash
ralph --revise-prd planner
```

**Combined with schema validation**:
```bash
ralph --revise-prd --schema prd-schema.json --min-criteria 3 planner
```

#### Using `--enhance-all`

The `--enhance-all` flag enables both enhancement features at once:
- Intent enhancement (`--enhance-intent`)
- PRD revision (`--revise-prd`)

**Example**: Enable all enhancements:
```bash
ralph --enhance-all all
```

**Selectively disable specific features** using `--no-*` flags:
```bash
# Enable all enhancements except intent enhancement
ralph --enhance-all --no-enhance-intent planner

# Enable only PRD revision (disable intent enhancement)
ralph --enhance-all --no-enhance-intent planner
```

#### Combining Enhancement Flags with Other Options

Enhancement flags work seamlessly with other Ralph options including headless operation modes.

**With CI mode**:
```bash
ralph --ci --enhance-all --intent-file requirements.txt all
```

**With non-interactive mode**:
```bash
ralph --non-interactive --enhance-intent --intent "Build an API" architect
```

**With quiet mode** (minimal output, but enhancements still run):
```bash
ralph --quiet --enhance-all execute
```

**Complete CI pipeline example**:
```bash
ralph --ci --enhance-all --intent-file requirements.txt --test-cmd "npm test" all
```

#### Fallback Behavior

Enhancement agents are designed to gracefully handle failures without blocking your workflow:

| Feature | Default Behavior | Strict Mode |
|---------|------------------|-------------|
| Intent Enhancement | Falls back to original intent | Exits with error (`--enhance-intent-strict`) |
| PRD Revision | Falls back to original PRD | N/A |

**When fallback occurs:**
- A warning is logged explaining the failure
- The original (unenhanced) content is used
- Execution continues normally

**Example fallback scenarios:**
- Enhancement agent timeout or network error
- Agent returns empty or unparseable response
- Revised PRD fails schema validation (falls back to original PRD)

#### Error Messages and Resolutions

| Error Message | Cause | Resolution |
|--------------|-------|------------|
| `Cannot enhance empty or whitespace-only intent.` | Empty intent provided with `--enhance-intent` | Provide a non-empty intent via `--intent` or `--intent-file` |
| `Intent enhancement failed: <error>` | Agent failed to process the intent | Check agent connectivity; intent will use fallback unless `--enhance-intent-strict` |
| `Intent enhancement failed in strict mode. Exiting.` | Agent failed with `--enhance-intent-strict` enabled | Fix the underlying issue or remove `--enhance-intent-strict` |
| `Enhancement agent returned empty response.` | Agent returned empty content | Check agent connectivity; will use fallback unless strict mode |
| `Could not parse enhanced intent from response.` | Agent response missing `<ENHANCED_INTENT>` tags | Will use fallback; check agent prompt compatibility |
| `Could not parse revised PRD from response.` | Agent response missing `<REVISED_PRD>` tags | Will use original PRD; check agent prompt compatibility |
| `Revised PRD failed schema validation: <error>` | Revised PRD doesn't match `--schema` file | Will use original PRD; review schema requirements |
| `Invalid JSON in revised PRD: <error>` | Agent returned malformed JSON | Will use original PRD; check agent output |

#### Events Emitted by Enhancement Features

Enhancement features emit events that can be subscribed to via hooks:

| Event Type | Trigger |
|-----------|---------|
| `INTENT_ENHANCE_START` | Intent enhancement begins |
| `INTENT_ENHANCE_SUCCESS` | Intent successfully enhanced |
| `INTENT_ENHANCE_FAILURE` | Intent enhancement failed |
| `PRD_REVISE_START` | PRD revision begins |
| `PRD_REVISE_SUCCESS` | PRD successfully revised |
| `PRD_REVISE_FAILURE` | PRD revision failed |

**Example hook for enhancement events**:
```python
# .ralph/hooks/enhancement_monitor.py
from pyralph import Event  # Optional: for type hints

EVENTS = [
    "INTENT_ENHANCE_SUCCESS",
    "INTENT_ENHANCE_FAILURE",
    "PRD_REVISE_SUCCESS"
]

def on_event(event: Event) -> None:
    if "FAILURE" in event.event_type.name:
        print(f"Enhancement failed: {event.event_type.name}")
    else:
        print(f"Enhancement completed: {event.event_type.name}")
```

## How It Works

1. **Architect phase**: Generates architecture documentation with project context
2. **Planner phase**: Generates PRD with user stories
3. **Execute loop**: Iterates tasks until completion or max retries

Each task is verified by running the test suite. On success, changes are committed to git.

## Project Structure

```
pyralph/
├── src/pyralph/              # Main package
│   ├── __init__.py           # Package initialization with re-exports
│   ├── ralph.py              # Thin entry point (backward-compatible)
│   ├── cli.py                # CLI argument parsing (~50 parameters)
│   ├── orchestrator.py       # Central coordinator (three-phase workflow)
│   ├── config.py             # Configuration dataclass and path constants
│   ├── logger.py             # Logging with verbosity, color, JSON support
│   ├── shell.py              # Shell command execution utilities
│   ├── prd.py                # PRD dataclasses (UserStory, PRD)
│   ├── templates.py          # Prompt template management
│   ├── hooks.py              # Event/hook system for extensibility
│   ├── fetch_ready_issues.py # GitHub issue fetcher utility
│   └── agents/               # Agent implementations
│       ├── __init__.py       # Agent factory and registration
│       ├── base.py           # Abstract BaseAgent interface
│       ├── claude.py         # Claude CLI agent implementation
│       └── copilot.py        # GitHub Copilot CLI agent implementation
├── prompt.md                 # Default user context prompt template
├── test_ralph.py             # Comprehensive test suite
├── pyproject.toml            # Build configuration
├── ARCH.md                   # Architecture decision record
└── .ralph/                   # Runtime state directory
    ├── archive/              # Completed PRD archives
    ├── hooks/                # Custom hook scripts
    ├── templates/            # Prompt templates
    ├── prd.json              # Current project plan
    ├── progress.txt          # Error state (if failing)
    └── ralph_log.txt         # Audit trail
```

## Architecture

Ralph follows a modular architecture with clear separation of concerns. For detailed technical specifications, see [ARCH.md](ARCH.md).

## Testing Conventions

- **Layout**: All tests live under `tests/` with one file per feature/concern (e.g., `test_orchestrator.py`, `test_cli_arguments.py`). Place new cases in the file that matches the primary module under test; if none exists, create `test_<feature>.py` with a clear name.
- **Naming**: Use `test_<area>.py` filenames, `Test*` classes, and `test_*` methods following pytest style. Prefer descriptive method names over comments.
- **Shared fixtures**: Reuse pytest fixtures in `tests/conftest.py` (e.g., `temp_config`, `temp_hooks`, `mock_orchestrator`, `mock_agent`, `logger_reset`, `issue_watcher_env`) instead of duplicating temp directory or config setup. For multi-feature or cross-cutting tests, rely on these fixtures and add new shared fixtures to `tests/conftest.py` rather than copying fixtures into multiple files. Utility classes like `CaptureStdout` are available in `tests/helpers.py`.
- **Multi-feature tests**: If a test spans multiple components, place it with the dominant behavior under test (usually the orchestrator or CLI). Reference shared helpers for setup and mocks; avoid redefining fixtures already available in `tests/helpers.py`.
- **Feedback and updates**: If the testing doc is unclear or gaps are found, open a GitHub issue titled `[Testing Docs] <summary>` and assign it to the current PRD branch owner/maintainer. Include the scenario and proposed update; the assignee owns incorporating feedback here.

### Core Components

| Component | Location | Description |
|-----------|----------|-------------|
| **RalphOrchestrator** | `src/pyralph/orchestrator.py` | Central coordinator managing the three-phase workflow (Architect → Planner → Execute). Handles phase transitions and component integration. |
| **CLI** | `src/pyralph/cli.py` | CLI argument parsing (~50 parameters) and command-line interface. |
| **Agent System** | `src/pyralph/agents/` | Pluggable agent backends for LLM interaction. Includes `BaseAgent` abstract class and implementations for Claude CLI and GitHub Copilot CLI. Agents handle prompt execution with configurable timeout, model selection, and error recovery. |
| **Hook System** | `src/pyralph/hooks.py` | Event-driven extensibility layer. Supports Python module hooks and executable hooks with priority ordering, timeout protection, and optional data modification. Subscribes to lifecycle events (phase, task, verification, PRD). |
| **Logger** | `src/pyralph/logger.py` | Static logging class with CLI-controlled verbosity levels, color output, JSON/NDJSON formats, sensitive data redaction, and both console and file output. |
| **Config** | `src/pyralph/config.py` | Dataclass holding all path constants and default limits (retry count, timeout). Ensures required directories exist on startup. |
| **PRD** | `src/pyralph/prd.py` | Dataclasses for PRD and UserStory structures. |
| **Templates** | `src/pyralph/templates.py` | Prompt template management and rendering. |
| **Shell** | `src/pyralph/shell.py` | Shell command execution utilities. |

### Agent Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     RalphOrchestrator                       │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                      BaseAgent                          ││
│  │  - timeout, model, temperature, seed, max_tokens        ││
│  │  - run(prompt) -> (exit_code, stdout, stderr)           ││
│  │  - check_dependencies() -> bool                         ││
│  └─────────────────────────────────────────────────────────┘│
│           ▲                           ▲                     │
│           │                           │                     │
│  ┌────────┴────────┐        ┌────────┴────────┐            │
│  │   ClaudeAgent   │        │   GithubAgent   │            │
│  │  (claude CLI)   │        │ (copilot CLI)   │            │
│  └─────────────────┘        └─────────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Architect Phase**: Scans codebase → Generates context documents → Builds project context
2. **Planner Phase**: Reads context + intent → Generates PRD with user stories → Validates acceptance criteria
3. **Execute Phase**: Iterates tasks → Runs agent → Verifies via tests → Commits on success or retries on failure

## Knowledge Injection

Provide necessary context within prompts or repository files.

## Debug

View recent log entries:

```bash
tail -n 50 .ralph/ralph_log.txt
```

## Advanced Features

### Hook/Event System

Ralph provides an extensible event-driven hook system that lets you subscribe to lifecycle events and execute custom code at key points during execution.

#### Available Event Types

Events are organized by lifecycle phase:

| Category | Events |
|----------|--------|
| **Phase** | `PHASE_START`, `PHASE_END` |
| **Architect** | `ARCHITECT_START`, `ARCHITECT_SUCCESS`, `ARCHITECT_FAILURE` |
| **Planner** | `PLANNER_START`, `PLANNER_SUCCESS`, `PLANNER_FAILURE` |
| **Execute** | `EXECUTE_START`, `EXECUTE_END` |
| **Task** | `TASK_START`, `TASK_SUCCESS`, `TASK_FAILURE`, `TASK_RETRY` |
| **Verification** | `VERIFICATION_START`, `VERIFICATION_SUCCESS`, `VERIFICATION_FAILURE` |
| **PRD** | `PRD_CREATED`, `PRD_ARCHIVED` |
| **Error** | `ERROR` |

#### Event Payload

All events carry the following data:

```python
event_type: EventType           # The type of event
timestamp: str                  # ISO format timestamp
phase: Optional[str]            # Current phase: architect/planner/execute
task_id: Optional[str]          # Task identifier
task_description: Optional[str] # Task description
retry_count: Optional[int]      # Current retry attempt
max_retries: Optional[int]      # Maximum retries allowed
error: Optional[Any]            # Error object if applicable
verification_command: Optional[str]    # Test command
verification_exit_code: Optional[int]  # Exit code from verification
prd_path: Optional[str]         # Path to PRD file
metadata: Dict[str, Any]        # Custom metadata
```

#### Creating Python Module Hooks

Create a Python file in `.ralph/hooks/`:

```python
# .ralph/hooks/my_hook.py
from pyralph import Event, EventType  # Optional: for type hints

EVENTS = ["TASK_SUCCESS", "TASK_FAILURE"]  # Required: events to subscribe to
PRIORITY = 50                               # Optional: lower = earlier (default: 100)
TIMEOUT = 10.0                              # Optional: max seconds (default: 5.0)
MODIFIES_DATA = False                       # Optional: can modify events (default: False)

def on_event(event: Event) -> None:
    """Handle task completion events."""
    print(f"Task {event.task_id}: {event.event_type.name}")
    if event.error:
        print(f"  Error: {event.error}")
```

Hooks are auto-discovered from `.ralph/hooks/` on startup.

#### Creating Executable Hooks

Create a script with a companion YAML config:

```bash
# .ralph/hooks/notify.sh
#!/bin/bash
EVENT_JSON=$(cat)  # Receive JSON event via stdin
EVENT_TYPE=$(echo "$EVENT_JSON" | jq -r '.event_type')
TASK_ID=$(echo "$EVENT_JSON" | jq -r '.task_id')

echo "Task $TASK_ID: $EVENT_TYPE" >&2
```

```yaml
# .ralph/hooks/notify.yaml
events:
  - TASK_SUCCESS
  - TASK_FAILURE
priority: 100
timeout: 5.0
```

#### Hook Execution Behavior

- Hooks execute in priority order (lower values first)
- Each hook runs in an isolated thread with timeout protection
- Exceptions are caught and logged without halting execution
- Hooks with `MODIFIES_DATA = True` can transform event data

### Plugin System

Plugins extend Ralph's functionality by registering hooks programmatically.

#### Loading Plugins

```bash
# Load a single plugin file
ralph --plugin /path/to/plugin.py

# Load all plugins from a directory
ralph --plugin /path/to/plugins/
```

#### Creating a Plugin

```python
# ~/my_plugins/monitoring.py
from pyralph import Event, EventType  # Optional: for type hints

EVENTS = ["PHASE_START", "PHASE_END", "TASK_SUCCESS", "TASK_FAILURE"]
PRIORITY = 50
TIMEOUT = 10.0

def on_event(event: Event) -> None:
    """Monitor Ralph lifecycle events."""
    if event.event_type.name == "TASK_SUCCESS":
        print(f"Task {event.task_id} completed")
    elif event.event_type.name == "TASK_FAILURE":
        print(f"Task {event.task_id} failed: {event.error}")
    elif event.event_type.name == "PHASE_START":
        print(f"Starting {event.phase} phase")
```

#### CLI Hook Options

| Flag | Description |
|------|-------------|
| `--no-hooks` | Disable all hook execution |
| `--hooks NAME...` | Enable only specified hooks by name |
| `--pre CMD...` | Shell command(s) to run before each phase |
| `--post CMD...` | Shell command(s) to run after each phase |
| `--plugin PATH...` | Load plugin(s) from file or directory |

### CI/CD Integration

Ralph supports headless operation for continuous integration pipelines.

#### CI Mode

The `--ci` flag enables a bundle of CI-friendly options:

```bash
ralph --ci --intent-file requirements.txt all
```

This is equivalent to:

```bash
ralph --non-interactive --no-color --no-emoji --json
```

#### Key CI/CD Flags

| Flag | Description |
|------|-------------|
| `--ci` | Enable CI mode (non-interactive, no color, JSON output) |
| `--non-interactive` | Disable all prompts (fails if input required) |
| `--json` | Output in JSON format for parsing |
| `--ndjson` | Output in newline-delimited JSON format |
| `--status-check` | Check PRD status and exit with code |

#### Status Check Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All tasks complete |
| 1 | Tasks incomplete |
| 2 | No PRD found |

#### JSON Output Format

When using `--json` or `--ndjson`, Ralph outputs structured data:

```json
{
  "event": "TASK_SUCCESS",
  "task_id": "TASK-001",
  "timestamp": "2024-01-15T10:30:00Z",
  "phase": "execute",
  "verification_exit_code": 0
}
```

Use `--ndjson` for streaming output where each event is a separate JSON line, making it easy to parse with tools like `jq`:

```bash
ralph --ci --ndjson all | jq 'select(.event == "TASK_FAILURE")'
```

#### Combining with Hooks for CI Notifications

```python
# .ralph/hooks/ci_notify.py
import os
import requests
from pyralph import Event  # Optional: for type hints

EVENTS = ["TASK_FAILURE", "PLANNER_SUCCESS", "EXECUTE_END"]

def on_event(event: Event) -> None:
    """Send notifications in CI environment."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return

    if event.event_type.name == "TASK_FAILURE":
        requests.post(webhook_url, json={
            "text": f"Task {event.task_id} failed: {event.error}"
        })
    elif event.event_type.name == "EXECUTE_END":
        requests.post(webhook_url, json={
            "text": "Ralph execution completed"
        })
```

## License

MIT
