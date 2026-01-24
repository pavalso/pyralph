# Ralph

**Ralph** is an autonomous software development agent that iteratively builds projects through a structured three-phase loop. It acts as a self-directing AI assistant that can understand project requirements, create detailed plans, and execute development tasks with built-in verification and error recovery.

Based on [Ralph Wiggum as a "Software engineer"](https://ghuntley.com/ralph/).

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
your-project/
└── .ralph/
    ├── memory/        # Knowledge base (wiki files)
    ├── archive/       # Completed PRD archives
    ├── prd.json       # Current project plan
    ├── progress.txt   # Error state (if failing)
    └── ralph_log.txt  # Audit trail
```

## Knowledge Injection

To teach Ralph without repeating context in prompts:

1. Create a markdown file in `.ralph/memory/`
2. Ralph reads it on the next turn if relevant

## Debug

View recent log entries:

```bash
tail -n 50 .ralph/ralph_log.txt
```

## License

MIT
