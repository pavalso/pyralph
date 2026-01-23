# Ralph

Autonomous software development agent that iteratively builds projects by following a structured loop of exploration, planning, and action.

Based on [Ralph Wiggum as a "Software engineer"](https://ghuntley.com/ralph/).

## Features

- **File-based memory**: All context persisted to `.ralph/` for resumability
- **Verification gate**: Agent claims validated by running actual tests
- **Retry mechanism**: Failed tasks retry with error feedback
- **Knowledge injection**: Drop `.md` files in `memory/` to teach the agent

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
