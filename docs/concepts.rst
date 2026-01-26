.. _concepts:

Concepts
========

This section explains Ralph's core concepts and how it works.

Three-Phase Workflow
--------------------

Ralph operates through a continuous loop of three distinct phases:

.. code-block:: text

   +----------------------------------------------------------------+
   |                                                                |
   |   +-------------+    +-------------+    +-------------+        |
   |   |  ARCHITECT  |--->|   PLANNER   |--->|   EXECUTE   |        |
   |   |             |    |             |    |             |        |
   |   | - Explore   |    | - Generate  |    | - Run tasks |        |
   |   |   codebase  |    |   PRD with  |    | - Verify via|        |
   |   | - Gather    |    |   user      |    |   tests     |        |
   |   |   context   |    |   stories   |    | - Retry on  |        |
   |   | - Build     |    | - Define    |    |   failure   |        |
   |   |   plan      |    |   acceptance|    | - Commit on |        |
   |   |             |    |   criteria  |    |   success   |        |
   |   +-------------+    +-------------+    +-------------+        |
   |                                                    |           |
   |                                                    v           |
   |                                          +-------------+       |
   |                                          |  Complete   |       |
   |                                          |  or retry   |       |
   |                                          +-------------+       |
   |                                                                |
   +----------------------------------------------------------------+

1. **Architect Phase**: Generates architecture documentation with project context
2. **Planner Phase**: Generates a Product Requirements Document (PRD) with user stories and acceptance criteria
3. **Execute Phase**: Iterates through tasks, running verification tests after each, and retrying on failure until completion

Core Features
-------------

File-based State
^^^^^^^^^^^^^^^^

All context is persisted to the ``.ralph/`` directory for session resumability and crash recovery.

Verification Gate
^^^^^^^^^^^^^^^^^

Agent claims are validated by running actual tests (``pytest`` by default) before accepting task completion.

Retry Mechanism
^^^^^^^^^^^^^^^

Failed tasks automatically retry with error feedback injected into the next attempt.

Knowledge Injection
^^^^^^^^^^^^^^^^^^^

Provide context directly via prompts or existing files.

Hook System
^^^^^^^^^^^

Extensible event system for custom integrations. Subscribe to lifecycle events (task start/success/failure, verification, phase transitions) via Python modules or executables. See :ref:`hooks` for details.

CI/CD Support
^^^^^^^^^^^^^

Headless mode with ``--ci`` flag, non-interactive execution, JSON/NDJSON output formats, and status checks for pipeline integration. See :ref:`ci-integration` for details.

How It Works
------------

1. **Architect phase**: Generates architecture documentation with project context
2. **Planner phase**: Generates PRD with user stories
3. **Execute loop**: Iterates tasks until completion or max retries

Each task is verified by running the test suite. On success, changes are committed to git.

Data Flow
^^^^^^^^^

1. **Architect Phase**: Scans codebase -> Generates context documents -> Builds project context
2. **Planner Phase**: Reads context + intent -> Generates PRD with user stories -> Validates acceptance criteria
3. **Execute Phase**: Iterates tasks -> Runs agent -> Verifies via tests -> Commits on success or retries on failure

Knowledge Injection
-------------------

Provide necessary context within prompts or repository files. Ralph can read and incorporate this context during the architect and planner phases.

Project Structure
-----------------

.. code-block:: text

   pyralph/
   +-- src/pyralph/              # Main package
   |   +-- __init__.py           # Package initialization with re-exports
   |   +-- ralph.py              # Thin entry point (backward-compatible)
   |   +-- cli.py                # CLI argument parsing (~50 parameters)
   |   +-- orchestrator.py       # Central coordinator (three-phase workflow)
   |   +-- config.py             # Configuration dataclass and path constants
   |   +-- logger.py             # Logging with verbosity, color, JSON support
   |   +-- shell.py              # Shell command execution utilities
   |   +-- prd.py                # PRD dataclasses (UserStory, PRD)
   |   +-- templates.py          # Prompt template management
   |   +-- hooks.py              # Event/hook system for extensibility
   |   +-- fetch_ready_issues.py # GitHub issue fetcher utility
   |   +-- agents/               # Agent implementations
   |       +-- __init__.py       # Agent factory and registration
   |       +-- base.py           # Abstract BaseAgent interface
   |       +-- claude.py         # Claude CLI agent implementation
   |       +-- copilot.py        # GitHub Copilot CLI agent implementation
   +-- prompt.md                 # Default user context prompt template
   +-- test_ralph.py             # Comprehensive test suite
   +-- pyproject.toml            # Build configuration
   +-- ARCH.md                   # Architecture decision record
   +-- .ralph/                   # Runtime state directory
       +-- archive/              # Completed PRD archives
       +-- hooks/                # Custom hook scripts
       +-- templates/            # Prompt templates
       +-- prd.json              # Current project plan
       +-- progress.txt          # Error state (if failing)
       +-- ralph_log.txt         # Audit trail

Core Components
^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Component
     - Location
     - Description
   * - **RalphOrchestrator**
     - ``src/pyralph/orchestrator.py``
     - Central coordinator managing the three-phase workflow (Architect -> Planner -> Execute). Handles phase transitions and component integration.
   * - **CLI**
     - ``src/pyralph/cli.py``
     - CLI argument parsing (~50 parameters) and command-line interface.
   * - **Agent System**
     - ``src/pyralph/agents/``
     - Pluggable agent backends for LLM interaction. Includes ``BaseAgent`` abstract class and implementations for Claude CLI and GitHub Copilot CLI.
   * - **Hook System**
     - ``src/pyralph/hooks.py``
     - Event-driven extensibility layer. Supports Python module hooks and executable hooks with priority ordering, timeout protection, and optional data modification.
   * - **Logger**
     - ``src/pyralph/logger.py``
     - Static logging class with CLI-controlled verbosity levels, color output, JSON/NDJSON formats, sensitive data redaction.
   * - **Config**
     - ``src/pyralph/config.py``
     - Dataclass holding all path constants and default limits (retry count, timeout).
   * - **PRD**
     - ``src/pyralph/prd.py``
     - Dataclasses for PRD and UserStory structures.
   * - **Templates**
     - ``src/pyralph/templates.py``
     - Prompt template management and rendering.
   * - **Shell**
     - ``src/pyralph/shell.py``
     - Shell command execution utilities.

Agent Architecture
^^^^^^^^^^^^^^^^^^

.. code-block:: text

   +-------------------------------------------------------------+
   |                     RalphOrchestrator                       |
   |  +--------------------------------------------------------+ |
   |  |                      BaseAgent                         | |
   |  |  - timeout, model, temperature, seed, max_tokens       | |
   |  |  - run(prompt) -> (exit_code, stdout, stderr)          | |
   |  |  - check_dependencies() -> bool                        | |
   |  +--------------------------------------------------------+ |
   |           ^                           ^                     |
   |           |                           |                     |
   |  +--------+--------+        +--------+--------+            |
   |  |   ClaudeAgent   |        |   GithubAgent   |            |
   |  |  (claude CLI)   |        | (copilot CLI)   |            |
   |  +-----------------+        +-----------------+            |
   +-------------------------------------------------------------+
