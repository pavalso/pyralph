---
type: wiki
title: Ralph Architecture
created: 2026-01-20
---

# Ralph - Autonomous Software Development Agent

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.x |
| Testing | pytest |
| AI Backend | Claude CLI (`claude -p --dangerously-skip-permissions`) |
| VCS | Git |

## Test Command

```
pytest
```

## Project Structure

```
testralph/
├── ralph.py        # Main orchestrator and agent logic
├── test_ralph.py   # Unit tests (pytest)
├── pyproject.toml  # Package metadata and build config
├── MANIFEST.in     # Distribution file inclusions/exclusions
├── README.md       # PyPI long description
├── CLAUDE.md       # Instructions for Claude Code
└── .ralph/         # Agent state directory
    ├── memory/     # Knowledge base (wiki files)
    ├── archive/    # Completed PRD archives
    ├── prd.json    # Current project plan
    ├── progress.txt # Error state (if failing)
    └── ralph_log.txt # Audit trail
```

## Packaging

The project uses `pyproject.toml` for packaging with setuptools:

- **Build backend**: setuptools + wheel
- **Entry point**: `ralph` command maps to `ralph:main`
- **Python requirement**: >=3.8
- **Explicit module**: `py-modules = ["ralph"]` (excludes test files from build)

### Build Commands

```bash
# Install build tool (one-time)
pip install build

# Build wheel and sdist
python -m build

# Install from wheel
pip install dist/ralph-0.1.0-py3-none-any.whl

# Install in development mode
pip install -e .
```

### Distribution Files

After running `python -m build`:
- `dist/ralph-0.1.0-py3-none-any.whl` - Wheel distribution
- `dist/ralph-0.1.0.tar.gz` - Source distribution

### MANIFEST.in

Controls what files are included in source distributions:

- **Included**: `CLAUDE.md`, `README.md`, `pyproject.toml`
- **Excluded**: `.ralph/` (agent state), test files, `__pycache__/`, `.pytest_cache/`

## Core Components

### Config (dataclass)
- Defines all paths relative to `Path.cwd()`
- Settings: `MAX_RETRIES=3`, `TIMEOUT_SECONDS=600`

### Logger
- Console output with ANSI colors
- Persistent file logging to `ralph_log.txt`

### Shell
- Subprocess wrapper with timeout handling
- Dependency checks for `claude` and `git` CLIs

### JsonUtils
- Robust JSON extraction from LLM output
- Handles markdown code blocks and comments

### MemoryManager
- Lists files in `.ralph/memory/`
- Extracts test command from wiki files

### ClaudeAgent
- Invokes Claude CLI with prompts
- Returns `(success: bool, output: str)`

### RalphOrchestrator
- **start()**: Entry point; prompts user if memory/PRD missing
- **Architect phase**: Initializes memory with project context
- **Planner phase**: Generates PRD with user stories
- **Execute loop**: Iterates tasks until completion or max retries

## Agent Loop Flow

```
start() -> run_architect() -> run_planner() -> execute_loop()
                                                    |
                                          for each pending task:
                                                    |
                                          _execute_task() [max 3 retries]
                                                    |
                                          verify with test command
                                                    |
                                          git commit on success
```

## Key Design Decisions

1. **File-based state**: All context persisted to `.ralph/` for resumability
2. **Verification gate**: Agent claims are validated by running actual tests
3. **Retry mechanism**: Failed tasks retry with error feedback in prompt
4. **Memory injection**: Drop `.md` files in `memory/` to teach the agent

## Test Suite

7 unit tests in `test_ralph.py` covering:
- `JsonUtils.parse()` - JSON extraction
- `Shell.run()` - Timeout handling
- `MemoryManager` - File structure and test command extraction
- `RalphOrchestrator` - Planner retry, task verification success/failure flows
