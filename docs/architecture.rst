.. _architecture:

Architecture
============

This document describes Ralph's technical architecture. For detailed architectural decisions, see the ``ARCH.md`` file in the repository root.

.. contents:: Table of Contents
   :local:
   :depth: 2

Tech Stack
----------

- **Language**: Python 3.8+ (supports 3.8-3.12)
- **Testing**: pytest with unittest
- **Build**: setuptools (pyproject.toml)
- **Dependencies**: pyyaml>=6.0
- **Agents**: Claude CLI, GitHub Copilot CLI
- **External Tools**: git, gh (GitHub CLI), tree

Overview
--------

Ralph is an autonomous software development agent that iteratively builds projects through a three-phase loop:

1. **Architect** - Analyze structure
2. **Planner** - Create PRD with tasks
3. **Execute** - Implement and verify

State is persisted to ``.ralph/`` directory for resumability, and each task is verified by running tests before marking complete. The system supports extensibility through hooks, custom templates, and plugin modules.

Architectural Patterns
----------------------

- **Primary Pattern**: Three-Phase Autonomous Agent Loop (Architect -> Planner -> Execute)
- **Supporting Patterns**:
  - Repository (PRDManager)
  - Observer/Pub-Sub (HookManager)
  - Strategy (BaseAgent subclasses)
  - Factory (agent instantiation)
  - State Machine (task status transitions)

Key Components
--------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Component
     - Description
   * - ``src/pyralph/cli.py``
     - Command-line argument parsing with 50+ options
   * - ``src/pyralph/orchestrator.py``
     - Core execution logic coordinating all phases
   * - ``src/pyralph/hooks.py``
     - Event system with 30+ lifecycle events
   * - ``src/pyralph/agents/base.py``
     - Abstract BaseAgent class and AgentError dataclass
   * - ``src/pyralph/agents/claude.py``
     - ClaudeAgent implementation wrapping Claude CLI
   * - ``src/pyralph/agents/copilot.py``
     - GithubAgent implementation wrapping Copilot CLI
   * - ``src/pyralph/config.py``
     - Config dataclass and CONF singleton for paths/limits
   * - ``src/pyralph/logger.py``
     - Centralized Logger class with metaclass-based properties
   * - ``src/pyralph/shell.py``
     - Shell class for safe subprocess execution
   * - ``src/pyralph/prd.py``
     - PRDManager for PRD file operations with caching
   * - ``src/pyralph/templates.py``
     - TemplateManager for prompt generation

For detailed API documentation, see :doc:`api`.

SOLID Principles Assessment
---------------------------

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Principle
     - Status
     - Notes
   * - Single Responsibility
     - Good
     - Clear module boundaries; each class has focused purpose. Large modules (orchestrator.py, hooks.py) are documented as technical debt.
   * - Open/Closed
     - Good
     - BaseAgent allows extending with new agents without modifying existing code. Hook and template systems enable extensions.
   * - Liskov Substitution
     - Good
     - ClaudeAgent and GithubAgent implement BaseAgent interface identically; can substitute without client changes.
   * - Interface Segregation
     - Good
     - Minimal interfaces; BaseAgent exposes only essential abstract methods. Configuration uses focused dataclass.
   * - Dependency Inversion
     - Good
     - Logger and Config passed to agents via setters; modules depend on abstractions. No circular imports.

API Boundaries & Integration Points
-----------------------------------

External Integrations
^^^^^^^^^^^^^^^^^^^^^

- **Claude CLI** - LLM queries
- **GitHub Copilot CLI** - LLM queries
- **GitHub CLI** - Issue management
- **git** - Version control

Internal Interfaces
^^^^^^^^^^^^^^^^^^^

- **BaseAgent** - Agent abstraction
- **HookManager** - Event system
- **PRDManager** - PRD persistence
- **TemplateManager** - Prompt templates

Data Formats
^^^^^^^^^^^^

- **JSON** - PRD, hook payloads
- **Markdown** - Templates
- **YAML** - Frontmatter

Protocols
^^^^^^^^^

- CLI subprocess communication (stdin/stdout)
- File-based state persistence

Error Handling & Logging
------------------------

Error Strategy
^^^^^^^^^^^^^^

Structured ``AgentError`` dataclass captures:

- Exception type
- Message
- Stack trace
- Timestamp
- Agent name
- Task ID

Features:

- Retry with exponential backoff (configurable max retries)
- Failed tasks marked as ``failed`` but execution continues

Logging Approach
^^^^^^^^^^^^^^^^

Centralized ``Logger`` class with metaclass-based properties:

- Console output (with color/emoji)
- File output (ralph_log.txt)
- JSON/NDJSON output modes

Log Levels
^^^^^^^^^^

- ``debug`` (10)
- ``info`` (20)
- ``warn`` (30)
- ``error`` (40)

Verbosity flags: ``-v`` (verbose), ``-vv`` (very verbose), ``-vvv`` (debug)

Security Considerations
-----------------------

Authentication
^^^^^^^^^^^^^^

Delegates to CLI tools (claude, gh auth). No built-in auth mechanism.

Input Validation
^^^^^^^^^^^^^^^^

- Argparse for CLI args
- JSON schema validation for PRD (optional)
- UTF-8 encoding validation for files
- Path traversal prevention via pathlib

Secrets Management
^^^^^^^^^^^^^^^^^^

- Environment variables for CLI tool configuration
- Log redaction via ``--redact`` patterns
- No hard-coded secrets

Potential Concerns
^^^^^^^^^^^^^^^^^^

- ``shell=True`` execution risk (acceptable for local tool)
- Temp file exposure for Copilot prompts
- Log files may contain sensitive data without access control

Testing Conventions
-------------------

Layout
^^^^^^

All tests live under ``tests/`` with one file per feature/concern (e.g., ``test_orchestrator.py``, ``test_cli_arguments.py``). Place new cases in the file that matches the primary module under test; if none exists, create ``test_<feature>.py`` with a clear name.

Naming
^^^^^^

Use ``test_<area>.py`` filenames, ``Test*`` classes, and ``test_*`` methods following pytest style. Prefer descriptive method names over comments.

Shared Fixtures
^^^^^^^^^^^^^^^

Reuse pytest fixtures in ``tests/conftest.py``:

- ``temp_config`` - Temporary directory with CONF management
- ``temp_hooks`` - Temporary directory with hooks setup
- ``mock_orchestrator`` - Factory for mock orchestrators with CONF isolation
- ``mock_agent`` - Mock agent with standard interface
- ``logger_reset`` - Logger state management
- ``issue_watcher_env`` - IssueWatcher test environment

Utility classes like ``CaptureStdout`` are available in ``tests/helpers.py``.

For multi-feature or cross-cutting tests, rely on these fixtures and add new shared fixtures to ``tests/conftest.py`` rather than copying fixtures into multiple files.

Multi-Feature Tests
^^^^^^^^^^^^^^^^^^^

If a test spans multiple components, place it with the dominant behavior under test (usually the orchestrator or CLI). Reference shared helpers for setup and mocks.

Feedback and Updates
^^^^^^^^^^^^^^^^^^^^

If the testing doc is unclear or gaps are found, open a GitHub issue titled ``[Testing Docs] <summary>`` and assign it to the current PRD branch owner/maintainer.
