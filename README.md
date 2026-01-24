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
