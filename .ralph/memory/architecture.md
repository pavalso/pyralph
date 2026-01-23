---
type: wiki
title: Architecture
---

## Tech Stack

- **Language**: Python 3.8+
- **Build System**: setuptools (pyproject.toml)
- **Testing**: pytest
- **External Dependencies**: Claude CLI, Git

## Overview

Ralph is an autonomous software development agent that iteratively builds projects through a structured loop of exploration, planning, and action. It uses file-based memory (`.ralph/` directory) for state persistence and resumability.

## Key Components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `ralph.py` | Root | Main entry point and orchestration |
| `agents/base.py` | agents/ | Base agent abstraction |
| `agents/claude.py` | agents/ | Claude CLI integration |
| `agents/copilot.py` | agents/ | Copilot agent implementation |
| `test_ralph.py` | Root | Test suite |

**Execution Phases**:
1. **Architect** — Initializes memory with project context
2. **Planner** — Generates PRD with user stories
3. **Execute** — Iterates tasks until completion or max retries

## Risks & Assumptions

- **External CLI dependency**: Requires `claude` command in PATH
- **Git dependency**: Commits changes after verified tasks
- **No pinned dependencies**: `dependencies = []` in pyproject.toml — relies on system Python packages

## Test Command

Test Command: `pytest`
