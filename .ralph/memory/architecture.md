---
type: wiki
title: Architecture
---

## Tech Stack

- **Language**: Python 3.8+
- **Build System**: setuptools (pyproject.toml)
- **Testing**: pytest, unittest
- **CLI**: argparse
- **External Agents**: Claude CLI, GitHub Copilot CLI

## Overview

Ralph is an autonomous software development agent that iteratively builds projects through a three-phase workflow:

1. **Architect** – Initializes project memory/wiki in `.ralph/memory/`
2. **Planner** – Creates a PRD (Product Requirements Document) with user stories
3. **Execute** – Implements tasks iteratively with verification loops

The system uses pluggable AI agents (Claude, Copilot) to execute prompts and verify outcomes against a test command.

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| `RalphOrchestrator` | `ralph.py:222` | Main controller orchestrating all phases |
| `Config` | `ralph.py:20` | Dataclass holding paths and limits |
| `Logger` | `ralph.py:44` | Console/file logging with color support |
| `Shell` | `ralph.py:100` | Subprocess wrapper for safe command execution |
| `MemoryManager` | `ralph.py:146` | Manages `.ralph/memory/` wiki files |
| `JsonUtils` | `ralph.py:130` | Robust JSON parsing for LLM outputs |
| `BaseAgent` | `agents/base.py:7` | Abstract interface for AI agents |
| `ClaudeAgent` | `agents/claude.py:10` | Claude CLI integration |
| `GithubAgent` | `agents/copilot.py:8` | GitHub Copilot CLI integration |

## Agent Abstraction Layer

The agent abstraction layer provides a pluggable architecture for integrating different AI backends. This design allows Ralph to work with multiple AI providers through a unified interface.

### Architecture Pattern

The layer follows the **Strategy Pattern** with a factory function for instantiation:

```
BaseAgent (ABC)          <- Abstract interface
    ├── ClaudeAgent      <- Claude CLI implementation
    └── GithubAgent      <- GitHub Copilot CLI implementation
```

### BaseAgent Interface (`agents/base.py:7`)

The abstract base class defines three required methods:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `run()` | `(prompt: str, tag: str) -> Tuple[bool, str]` | Execute prompt, return (success, output) |
| `check_dependencies()` | `() -> bool` | Verify CLI tool is installed |
| `get_name()` | `() -> str` | Return display name for logging |

### Concrete Implementations

**ClaudeAgent** (`agents/claude.py:10`):
- Wraps the `claude` CLI tool
- Passes prompts via stdin using `-p` flag
- Uses `--dangerously-skip-permissions` for autonomous execution

**GithubAgent** (`agents/copilot.py:8`):
- Wraps the `copilot` CLI tool
- Writes prompts to a temporary file (workaround for stdin limitations)
- Uses flags: `--allow-all-paths`, `--allow-all-tools`, `--no-ask-user`, `-s`

### Factory & Registry (`agents/__init__.py`)

```python
AVAILABLE_AGENTS = {
    "claude": ClaudeAgent,
    "copilot": GithubAgent,
}

get_agent(agent_name: str, **kwargs) -> BaseAgent
list_agents() -> List[str]
```

### Integration with Orchestrator (`ralph.py:222-237`)

The `RalphOrchestrator` uses dependency injection:

1. Calls `get_agent(agent_name)` to instantiate the selected agent
2. Injects logger and config via `set_logger()` and `set_config()`
3. Validates dependencies with `check_dependencies()`
4. Delegates all AI interactions through `agent.run(prompt, tag)`

### Adding New Agents

To add a new AI backend:

1. Create `agents/<name>.py` with a class extending `BaseAgent`
2. Implement `run()`, `check_dependencies()`, and `get_name()`
3. Register in `AVAILABLE_AGENTS` dict in `agents/__init__.py`
4. Export in `__all__` list

## Risks & Assumptions

- **Agent Availability**: Requires `claude` or `copilot` CLI to be installed and in PATH
- **Shell Execution**: Uses `shell=True` which carries injection risks if prompts contain malicious input
- **Timeout**: Default 600s timeout; long-running tasks may fail
- **Verification**: Relies on deterministic test command exit codes for success validation

## CLI Interface

Ralph uses `argparse` for its command-line interface, providing flexible control over the development workflow.

### Usage

```
ralph [phase] [options]
```

### Phases

| Phase | Description |
|-------|-------------|
| `all` | Run all phases in sequence (default) |
| `architect` | Initialize project memory/wiki only |
| `planner` | Create PRD with user stories only |
| `execute` | Execute pending tasks from PRD only |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--help` | `-h` | Show help message and exit |
| `--version` | | Display Ralph version |
| `--accept-all` | `-y` | Skip user confirmation prompts for all phases |
| `--verbose` | | Enable debug-level logging |
| `--no-color` | | Disable colored output |
| `--agent` | | Select AI agent (e.g., `claude`, `copilot`) |

### Examples

```bash
# Run all phases interactively
ralph

# Run only the architect phase
ralph architect

# Run only the planner phase
ralph planner

# Run only the execute phase
ralph execute

# Run all phases without confirmation prompts
ralph --accept-all
ralph -y

# Run execute with a specific agent
ralph execute --agent copilot

# Enable verbose logging
ralph --verbose

# Combine options
ralph execute --accept-all --verbose --agent claude
```

### Phase Dependencies

- **architect**: No dependencies; creates `.ralph/memory/` structure
- **planner**: Requires memory to exist (run `architect` first)
- **execute**: Requires PRD to exist (run `planner` first)

When running `all` phases, Ralph automatically skips phases whose artifacts already exist.

## Test Command

Test Command: `pytest`
