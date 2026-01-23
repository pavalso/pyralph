# Architecture Decision Record

## Tech Stack

- **Language**: Python 3.8+
- **Testing**: pytest with unittest
- **Build**: setuptools (pyproject.toml)
- **Agents**: Claude CLI, GitHub Copilot CLI

## Overview

Ralph is an autonomous software development agent that iteratively builds projects through a structured loop: Architect -> Planner -> Execute. State is persisted to `.ralph/` for resumability. Each task is verified by running tests before marking complete.

## Key Components

| Component | Description |
|-----------|-------------|
| `ralph.py` | Main orchestrator containing `RalphOrchestrator`, `Config`, `Logger`, `Shell`, `JsonUtils`, and `MemoryManager` classes |
| `agents/base.py` | Abstract `BaseAgent` class and `AgentError` dataclass for structured error handling |
| `agents/claude.py` | `ClaudeAgent` implementation wrapping the Claude CLI |
| `agents/copilot.py` | `GithubAgent` implementation wrapping GitHub Copilot CLI |
| `test_ralph.py` | Comprehensive test suite covering all components |

## Execution Workflow

```
1. Architect Phase  -->  Initialize project memory and define architecture
2. Planner Phase    -->  Create PRD (Product Requirements Document) with tasks
3. Execute Phase    -->  Iterate through tasks, verify with tests, mark complete
```

## Error Recovery

- Task failures after max retries mark the task as `failed` (not terminating the process)
- Failed tasks are reset to `pending` on next execution run
- Other tasks continue executing even when one fails

## Task Status Values

| Status | Description |
|--------|-------------|
| `pending` | Task not yet attempted |
| `completed` | Task successfully verified |
| `failed` | Task exhausted max retries (can be retried on next run) |

## Risks & Assumptions

- Assumes `claude` or `copilot` CLI is available in PATH
- Shell commands execute with `shell=True` (potential security consideration)
- Test verification depends on deterministic test command exit codes
- Memory files must be UTF-8 encoded and non-empty

## Test Command

```
pytest
```

## Directory Structure

```
.ralph/
├── memory/          # Wiki files for state persistence
├── logs/            # Execution logs
└── prd.json         # Product Requirements Document
```
