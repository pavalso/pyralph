---
type: wiki
title: Architecture
---

# Architecture

## Tech Stack
- **Language**: Python 3.8+ (supports 3.8-3.12)
- **Testing**: pytest with unittest
- **Build**: setuptools (pyproject.toml)
- **Dependencies**: pyyaml>=6.0
- **Agents**: Claude CLI, GitHub Copilot CLI
- **External Tools**: git, gh (GitHub CLI), tree

## Overview
Ralph is an autonomous software development agent that iteratively builds projects through a three-phase loop: Architect (analyze structure) -> Planner (create PRD with tasks) -> Execute (implement and verify). State is persisted to `.ralph/` directory for resumability, and each task is verified by running tests before marking complete. The system supports extensibility through hooks, custom templates, and plugin modules.

## Architectural Patterns
- **Primary Pattern**: Three-Phase Autonomous Agent Loop (Architect -> Planner -> Execute)
- **Supporting Patterns**: Repository (PRDManager), Observer/Pub-Sub (HookManager), Strategy (BaseAgent subclasses), Factory (agent instantiation), State Machine (task status transitions)

## Key Components
| Component | Description |
|-----------|-------------|
| `src/pyralph/cli.py` | Command-line argument parsing with 50+ options |
| `src/pyralph/orchestrator.py` | Core execution logic coordinating all phases |
| `src/pyralph/hooks.py` | Event system with 30+ lifecycle events |
| `src/pyralph/agents/base.py` | Abstract BaseAgent class and AgentError dataclass |
| `src/pyralph/agents/claude.py` | ClaudeAgent implementation wrapping Claude CLI |
| `src/pyralph/agents/copilot.py` | GithubAgent implementation wrapping Copilot CLI |
| `src/pyralph/config.py` | Config dataclass and CONF singleton for paths/limits |
| `src/pyralph/logger.py` | Centralized Logger class with metaclass-based properties |
| `src/pyralph/shell.py` | Shell class for safe subprocess execution |
| `src/pyralph/prd.py` | PRDManager for PRD file operations with caching |
| `src/pyralph/memory.py` | MemoryManager for context file operations |
| `src/pyralph/templates.py` | PromptFormatter and TemplateManager for prompt generation |

## SOLID Principles Assessment
| Principle | Status | Notes |
|-----------|--------|-------|
| Single Responsibility | Good | Clear module boundaries; each class has focused purpose. Large modules (orchestrator.py, hooks.py) are documented as technical debt. |
| Open/Closed | Good | BaseAgent allows extending with new agents without modifying existing code. Hook and template systems enable extensions. |
| Liskov Substitution | Good | ClaudeAgent and GithubAgent implement BaseAgent interface identically; can substitute without client changes. |
| Interface Segregation | Good | Minimal interfaces; BaseAgent exposes only essential abstract methods. Configuration uses focused dataclass. |
| Dependency Inversion | Good | Logger and Config passed to agents via setters; modules depend on abstractions. No circular imports. |

## API Boundaries & Integration Points
- **External Integrations**: Claude CLI (LLM queries), GitHub Copilot CLI (LLM queries), GitHub CLI (issue management), git (version control)
- **Internal Interfaces**: BaseAgent (agent abstraction), HookManager (event system), PRDManager (PRD persistence), MemoryManager (context files)
- **Data Formats**: JSON (PRD, QA findings, hook payloads), Markdown (memory files, templates), YAML (frontmatter)
- **Protocols**: CLI subprocess communication (stdin/stdout), file-based state persistence

## Error Handling & Logging
- **Error Strategy**: Structured AgentError dataclass captures exception type, message, stack trace, timestamp, agent name, and task ID. Retry with exponential backoff (configurable max retries). Failed tasks marked as `failed` but execution continues.
- **Logging Approach**: Centralized Logger class with metaclass-based properties. Supports console (with color/emoji), file (ralph_log.txt), and JSON/NDJSON output modes.
- **Log Levels**: debug (10), info (20), warn (30), error (40). Verbosity flags: -v (verbose), -vv (very verbose), -vvv (debug).

## Security Considerations
- **Authentication**: Delegates to CLI tools (claude, gh auth). No built-in auth mechanism.
- **Input Validation**: Argparse for CLI args, JSON schema validation for PRD (optional), UTF-8 encoding validation for files, path traversal prevention via pathlib.
- **Secrets Management**: Environment variables for CLI tool configuration. Log redaction via --redact patterns. No hard-coded secrets.
- **Potential Concerns**: shell=True execution risk (acceptable for local tool), temp file exposure for Copilot prompts, log files may contain sensitive data without access control.

## Test Command
Test Command: `pytest`
