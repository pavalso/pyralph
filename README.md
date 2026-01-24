# Ralph

**Ralph** is an autonomous software development agent that iteratively builds projects through a structured three-phase loop. It acts as a self-directing AI assistant that can understand project requirements, create detailed plans, and execute development tasks with built-in verification and error recovery.

Based on [Ralph Wiggum as a "Software engineer"](https://ghuntley.com/ralph/).

Yes. This is being developed using Ralph itself...

## Three-Phase Workflow

Ralph operates through a continuous loop of three distinct phases:

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐         │
│   │   ARCHITECT  │───▶│    PLANNER   │───▶│   EXECUTE    │         │
│   │              │    │              │    │              │         │
│   │ • Explore    │    │ • Generate   │    │ • Run tasks  │         │
│   │   codebase   │    │   PRD with   │    │ • Verify via │         │
│   │ • Initialize │    │   user       │    │   tests      │         │
│   │   memory     │    │   stories    │    │ • Retry on   │         │
│   │ • Build      │    │ • Define     │    │   failure    │         │
│   │   context    │    │   acceptance │    │ • Commit on  │         │
│   │              │    │   criteria   │    │   success    │         │
│   └──────────────┘    └──────────────┘    └──────────────┘         │
│                                                    │                │
│                                                    ▼                │
│                                          ┌──────────────┐          │
│                                          │   Complete   │          │
│                                          │   or retry   │          │
│                                          └──────────────┘          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

1. **Architect Phase**: Initializes memory with project context by exploring the codebase and building a knowledge base
2. **Planner Phase**: Generates a Product Requirements Document (PRD) with user stories and acceptance criteria
3. **Execute Phase**: Iterates through tasks, running verification tests after each, and retrying on failure until completion

## Core Features

- **File-based memory**: All context persisted to `.ralph/` directory for session resumability and crash recovery
- **Verification gate**: Agent claims validated by running actual tests (`pytest` by default) before accepting task completion
- **Retry mechanism**: Failed tasks automatically retry with error feedback injected into the next attempt
- **Knowledge injection**: Drop `.md` files in `.ralph/memory/` to teach the agent project-specific context
- **Hook system**: Extensible event system for custom integrations—subscribe to lifecycle events (task start/success/failure, verification, phase transitions) via Python modules or executables
- **CI/CD support**: Headless mode with `--ci` flag, non-interactive execution, JSON/NDJSON output formats, and status checks for pipeline integration

## Installation

```bash
pip install ralph
```

Or install from source:

```bash
git clone https://github.com/your-repo/ralph.git
cd ralph
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

# Re-plan - keeps memory, regenerates user stories
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
| `--memory-out FILE` | Export memory contents to file after architect phase |

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

### Context/Memory

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

## How It Works

1. **Architect phase**: Initializes memory with project context
2. **Planner phase**: Generates PRD with user stories
3. **Execute loop**: Iterates tasks until completion or max retries

Each task is verified by running the test suite. On success, changes are committed to git.

## Project Structure

```
pyralph/
├── ralph.py              # Main entry point and orchestrator
├── hooks.py              # Event/hook system for extensibility
├── agents/               # Agent implementations
│   ├── __init__.py       # Agent factory and registration
│   ├── base.py           # Abstract BaseAgent interface
│   ├── claude.py         # Claude CLI agent implementation
│   └── copilot.py        # GitHub Copilot CLI agent implementation
├── prompt.md             # Default user context prompt template
├── fetch_ready_issues.py # GitHub issue fetcher utility
├── test_ralph.py         # Comprehensive test suite
├── pyproject.toml        # Build configuration
├── ARCH.md               # Architecture decision record
└── .ralph/               # Runtime state directory
    ├── memory/           # Knowledge base (wiki files)
    ├── archive/          # Completed PRD archives
    ├── hooks/            # Custom hook scripts
    ├── templates/        # Prompt templates
    ├── prd.json          # Current project plan
    ├── progress.txt      # Error state (if failing)
    └── ralph_log.txt     # Audit trail
```

## Architecture

Ralph follows a modular architecture with clear separation of concerns. For detailed technical specifications, see [ARCH.md](ARCH.md).

### Core Components

| Component | Location | Description |
|-----------|----------|-------------|
| **RalphOrchestrator** | `ralph.py` | Central coordinator managing the three-phase workflow (Architect → Planner → Execute). Handles CLI argument parsing (~50 parameters), phase transitions, and component integration. |
| **Agent System** | `agents/` | Pluggable agent backends for LLM interaction. Includes `BaseAgent` abstract class and implementations for Claude CLI and GitHub Copilot CLI. Agents handle prompt execution with configurable timeout, model selection, and error recovery. |
| **Hook System** | `hooks.py` | Event-driven extensibility layer. Supports Python module hooks and executable hooks with priority ordering, timeout protection, and optional data modification. Subscribes to lifecycle events (phase, task, verification, PRD). |
| **Logger** | `ralph.py` | Static logging class with CLI-controlled verbosity levels, color output, JSON/NDJSON formats, sensitive data redaction, and both console and file output. |
| **MemoryManager** | `ralph.py` | Manages the `.ralph/memory/` knowledge base. Handles memory validation, tag-based retrieval, file tree generation, and context injection for prompts. |
| **Config** | `ralph.py` | Dataclass holding all path constants and default limits (retry count, timeout). Ensures required directories exist on startup. |

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

1. **Architect Phase**: Scans codebase → Generates memory files → Builds project context
2. **Planner Phase**: Reads memory + intent → Generates PRD with user stories → Validates acceptance criteria
3. **Execute Phase**: Iterates tasks → Runs agent → Verifies via tests → Commits on success or retries on failure

## Knowledge Injection

To teach Ralph without repeating context in prompts:

1. Create a markdown file in `.ralph/memory/`
2. Ralph reads it on the next turn if relevant

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

EVENTS = ["TASK_SUCCESS", "TASK_FAILURE"]  # Required: events to subscribe to
PRIORITY = 50                               # Optional: lower = earlier (default: 100)
TIMEOUT = 10.0                              # Optional: max seconds (default: 5.0)
MODIFIES_DATA = False                       # Optional: can modify events (default: False)

def on_event(event):
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

EVENTS = ["PHASE_START", "PHASE_END", "TASK_SUCCESS", "TASK_FAILURE"]
PRIORITY = 50
TIMEOUT = 10.0

def on_event(event):
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

#### GitHub Actions Example

```yaml
name: Ralph CI
on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install ralph
          pip install -r requirements.txt

      - name: Run Ralph
        run: |
          ralph --ci --intent-file requirements.txt all
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Check completion status
        run: ralph --status-check
```

#### GitLab CI Example

```yaml
stages:
  - build

ralph-build:
  stage: build
  image: python:3.11
  script:
    - pip install ralph
    - ralph --ci --intent-file requirements.txt all
    - ralph --status-check
  variables:
    ANTHROPIC_API_KEY: $CI_ANTHROPIC_API_KEY
```

#### Jenkins Pipeline Example

```groovy
pipeline {
    agent any

    environment {
        ANTHROPIC_API_KEY = credentials('anthropic-api-key')
    }

    stages {
        stage('Build with Ralph') {
            steps {
                sh 'pip install ralph'
                sh 'ralph --ci --intent-file requirements.txt all'
            }
        }

        stage('Verify Completion') {
            steps {
                sh 'ralph --status-check'
            }
        }
    }
}
```

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

EVENTS = ["TASK_FAILURE", "PLANNER_SUCCESS", "EXECUTE_END"]

def on_event(event):
    """Send notifications in CI environment."""
    import os
    import requests

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
