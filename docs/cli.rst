.. _cli:

CLI Reference
=============

Ralph provides extensive command-line options organized into the following categories.

.. contents:: Table of Contents
   :local:
   :depth: 2

Quick Reference
---------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``-y``, ``--accept-all``
     - Skip all prompts and run automatically
   * - ``-v``, ``-vv``, ``-vvv``
     - Increase verbosity level
   * - ``-q``, ``--quiet``
     - Suppress non-essential output
   * - ``--ci``
     - CI mode (non-interactive, no color, JSON output)
   * - ``--intent "TEXT"``
     - Provide project intent inline
   * - ``--test-cmd "CMD"``
     - Override test command for verification
   * - ``--retries N``
     - Set max retries per task (default: 3)
   * - ``--only TASK_ID``
     - Execute only specified task(s)
   * - ``--resume TASK_ID``
     - Resume from a specific task
   * - ``--json``
     - Output in JSON format
   * - ``--enhance-intent``
     - Enhance intent before architect phase
   * - ``--revise-prd``
     - Revise PRD for quality improvements
   * - ``--enhance-all``
     - Enable all enhancement features

Output/Verbosity
----------------

Control how Ralph displays information during execution.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``-v``, ``--verbose``
     - Increase verbosity (use -v, -vv, or -vvv for more detail)
   * - ``-q``, ``--quiet``
     - Suppress non-essential output
   * - ``--no-color``
     - Disable colored output
   * - ``--no-emoji``
     - Replace emojis with text equivalents

**Example**: Run with maximum verbosity and no colors for log parsing:

.. code-block:: bash

   ralph -vvv --no-color execute

Intent/Input
------------

Specify what you want Ralph to build without interactive prompts.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--intent TEXT``
     - Provide intent inline (what to build)
   * - ``--intent-file FILE``
     - Load intent from a file
   * - ``--prompt-file FILE``
     - Override prompt.md path for user context

**Example**: Start a new project with intent from command line:

.. code-block:: bash

   ralph --intent "Build a REST API with user authentication" architect

Architect Control
-----------------

Configure the architect phase behavior.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--tree-depth N``
     - File tree depth for architect (default: 2)
   * - ``--tree-ignore PATTERN...``
     - Patterns to ignore in file tree

**Example**: Generate deeper file tree analysis while ignoring test directories:

.. code-block:: bash

   ralph --tree-depth 4 --tree-ignore "test*" "spec*" architect

Execution Control
-----------------

Fine-tune how Ralph executes tasks.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--test-cmd CMD``
     - Override test command for verification
   * - ``--skip-verify``
     - Skip verification step after task execution
   * - ``--retries N``
     - Override max retries per task (default: 3)
   * - ``--timeout SECS``
     - Override agent timeout in seconds (default: 600)
   * - ``--only TASK_ID...``
     - Execute only specified task IDs
   * - ``--except TASK_ID...``
     - Skip specified task IDs
   * - ``--resume TASK_ID``
     - Resume execution from a specific task ID

**Example**: Execute specific tasks with custom test command and extended timeout:

.. code-block:: bash

   ralph --only TASK-001 TASK-003 --test-cmd "npm test" --timeout 900 execute

Context
-------

Control which files Ralph considers and how context is managed.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--include PATTERN...``
     - Include only files matching these glob patterns in context
   * - ``--exclude PATTERN...``
     - Exclude files matching these glob patterns from context
   * - ``--context-limit N``
     - Limit maximum number of context files considered

**Example**: Focus Ralph on source files only, excluding generated code:

.. code-block:: bash

   ralph --include "src/**/*.py" --exclude "**/generated/**" execute

Model/LLM
---------

Configure the underlying language model behavior.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--model MODEL``
     - Model identifier for LLM requests (e.g., claude-3-opus)
   * - ``--temperature TEMP``
     - Sampling temperature (0.0-1.0) for response generation
   * - ``--max-tokens N``
     - Maximum number of tokens in the LLM response
   * - ``--seed N``
     - Random seed for reproducible outputs
   * - ``--agent AGENT``
     - Select agent backend (e.g., claude, copilot)

**Example**: Use a specific model with deterministic output:

.. code-block:: bash

   ralph --model claude-3-opus --temperature 0 --seed 42 execute

Logging/IO
----------

Configure logging behavior and output formats.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--log-file FILE``
     - Redirect log output to specified file
   * - ``--log-level LEVEL``
     - Set log level (debug, info, warn, error)
   * - ``--json``
     - Output in JSON format
   * - ``--ndjson``
     - Output in newline-delimited JSON format
   * - ``--print-prd``
     - Print PRD contents and exit without executing
   * - ``--prd-out FILE``
     - Export PRD to specified file
   * - ``--no-archive``
     - Skip PRD archival after execution

**Example**: Generate detailed logs for debugging:

.. code-block:: bash

   ralph --log-file debug.log --log-level debug --prd-out plan.json execute

.. _ci-integration:

Headless/CI
-----------

Options for running Ralph in continuous integration pipelines.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--non-interactive``
     - Disable all interactive prompts (fails if input required)
   * - ``--ci``
     - CI mode: enables --non-interactive --no-color --no-emoji --json
   * - ``--status-check``
     - Check PRD status and exit with code (0=complete, 1=incomplete, 2=no PRD)

**Example**: Run Ralph in a CI pipeline with JSON output:

.. code-block:: bash

   ralph --ci --intent-file requirements.txt all
   # Or check completion status in a script
   ralph --status-check && echo "All tasks complete"

CI Mode
^^^^^^^

The ``--ci`` flag enables a bundle of CI-friendly options:

.. code-block:: bash

   ralph --ci --intent-file requirements.txt all

This is equivalent to:

.. code-block:: bash

   ralph --non-interactive --no-color --no-emoji --json

Status Check Exit Codes
^^^^^^^^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Code
     - Meaning
   * - 0
     - All tasks complete
   * - 1
     - Tasks incomplete
   * - 2
     - No PRD found

JSON Output Format
^^^^^^^^^^^^^^^^^^

When using ``--json`` or ``--ndjson``, Ralph outputs structured data:

.. code-block:: json

   {
     "event": "TASK_SUCCESS",
     "task_id": "TASK-001",
     "timestamp": "2024-01-15T10:30:00Z",
     "phase": "execute",
     "verification_exit_code": 0
   }

Use ``--ndjson`` for streaming output where each event is a separate JSON line:

.. code-block:: bash

   ralph --ci --ndjson all | jq 'select(.event == "TASK_FAILURE")'

Extensibility
-------------

Extend Ralph with hooks and plugins.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--no-hooks``
     - Disable hook execution
   * - ``--hooks NAME...``
     - Enable only specified hooks by name
   * - ``--pre CMD...``
     - Shell command(s) to run before each phase
   * - ``--post CMD...``
     - Shell command(s) to run after each phase
   * - ``--plugin PATH...``
     - Load plugin(s) from Python file or directory path

**Example**: Run linting before each phase and notify on completion:

.. code-block:: bash

   ralph --pre "npm run lint" --post "curl -X POST https://hooks.example.com/notify" execute

For more details on hooks and plugins, see :ref:`hooks`.

Privacy
-------

Protect sensitive information in logs and outputs.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--redact PATTERN...``
     - Regex patterns to redact from logs (e.g., API keys)
   * - ``--redact-file FILE``
     - Load redaction patterns from file (one pattern per line)
   * - ``--no-log-prompts``
     - Do not log prompts to log file
   * - ``--no-log-responses``
     - Do not log responses to log file

**Example**: Redact sensitive data from logs:

.. code-block:: bash

   ralph --redact "sk-[a-zA-Z0-9]+" "password=\S+" --no-log-prompts execute

PRD Validation
--------------

Validate and customize the generated Product Requirements Document.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--schema FILE``
     - Validate generated PRD against a JSON schema file
   * - ``--min-criteria N``
     - Require at least N acceptance criteria per user story
   * - ``--label KEY=VAL...``
     - Add custom labels to PRD (format: key=value or just key)

**Example**: Enforce PRD quality standards:

.. code-block:: bash

   ralph --schema prd-schema.json --min-criteria 3 --label team=backend priority=high planner

.. _enhancement-features:

Enhancement Features
--------------------

Ralph provides AI-powered enhancement agents that improve the quality of your inputs and outputs throughout the development workflow.

Enhancement Flags Reference
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Flag
     - Description
   * - ``--enhance-intent``
     - Process intent through enhancement agent before architect phase
   * - ``--enhance-intent-strict``
     - Exit on enhancement failure instead of falling back to original intent
   * - ``--no-enhance-intent``
     - Disable intent enhancement (overrides ``--enhance-all``)
   * - ``--revise-prd``
     - Pass PRD through revision agent for quality improvements
   * - ``--no-revise-prd``
     - Disable PRD revision (overrides ``--enhance-all``)
   * - ``--enhance-all``
     - Enable all enhancement features at once

Intent Enhancement
^^^^^^^^^^^^^^^^^^

The ``--enhance-intent`` flag refines your initial project description to create a more precise, actionable, and well-structured description.

**What it does:**

- Clarifies ambiguities in your intent
- Adds specificity where the intent is too general
- Structures requirements into clear, logical components
- Surfaces implicit requirements that are essential but not explicitly stated
- Translates user-facing language into technical requirements

**Example**: Enhance intent before starting a project:

.. code-block:: bash

   ralph --enhance-intent --intent "Build a todo app" architect

**With strict mode**: Exit if enhancement fails:

.. code-block:: bash

   ralph --enhance-intent --enhance-intent-strict --intent "Build a todo app" architect

PRD Revision
^^^^^^^^^^^^

The ``--revise-prd`` flag reviews and improves the generated Product Requirements Document for clarity, completeness, and quality.

**What it does:**

- Ensures each user story has clear, unambiguous descriptions
- Verifies acceptance criteria are specific, measurable, and testable
- Identifies missing edge cases or error handling scenarios
- Ensures consistent terminology and formatting across all stories
- Verifies technical requirements are correctly specified
- Fixes any JSON formatting issues

**Example**: Revise PRD after generation:

.. code-block:: bash

   ralph --revise-prd planner

**Combined with schema validation**:

.. code-block:: bash

   ralph --revise-prd --schema prd-schema.json --min-criteria 3 planner

Using ``--enhance-all``
^^^^^^^^^^^^^^^^^^^^^^^

The ``--enhance-all`` flag enables both enhancement features at once:

.. code-block:: bash

   ralph --enhance-all all

**Selectively disable specific features** using ``--no-*`` flags:

.. code-block:: bash

   # Enable all enhancements except intent enhancement
   ralph --enhance-all --no-enhance-intent planner

Combining Enhancement Flags with Other Options
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enhancement flags work seamlessly with other Ralph options including headless operation modes.

**With CI mode**:

.. code-block:: bash

   ralph --ci --enhance-all --intent-file requirements.txt all

**With non-interactive mode**:

.. code-block:: bash

   ralph --non-interactive --enhance-intent --intent "Build an API" architect

**With quiet mode**:

.. code-block:: bash

   ralph --quiet --enhance-all execute

**Complete CI pipeline example**:

.. code-block:: bash

   ralph --ci --enhance-all --intent-file requirements.txt --test-cmd "npm test" all

Fallback Behavior
^^^^^^^^^^^^^^^^^

Enhancement agents gracefully handle failures:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Feature
     - Default Behavior
     - Strict Mode
   * - Intent Enhancement
     - Falls back to original intent
     - Exits with error (``--enhance-intent-strict``)
   * - PRD Revision
     - Falls back to original PRD
     - N/A

**When fallback occurs:**

- A warning is logged explaining the failure
- The original (unenhanced) content is used
- Execution continues normally

Error Messages and Resolutions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 40 30 30

   * - Error Message
     - Cause
     - Resolution
   * - ``Cannot enhance empty or whitespace-only intent.``
     - Empty intent provided with ``--enhance-intent``
     - Provide a non-empty intent via ``--intent`` or ``--intent-file``
   * - ``Intent enhancement failed: <error>``
     - Agent failed to process the intent
     - Check agent connectivity; intent will use fallback unless ``--enhance-intent-strict``
   * - ``Intent enhancement failed in strict mode. Exiting.``
     - Agent failed with ``--enhance-intent-strict`` enabled
     - Fix the underlying issue or remove ``--enhance-intent-strict``
   * - ``Enhancement agent returned empty response.``
     - Agent returned empty content
     - Check agent connectivity; will use fallback unless strict mode
   * - ``Could not parse enhanced intent from response.``
     - Agent response missing ``<ENHANCED_INTENT>`` tags
     - Will use fallback; check agent prompt compatibility
   * - ``Could not parse revised PRD from response.``
     - Agent response missing ``<REVISED_PRD>`` tags
     - Will use original PRD; check agent prompt compatibility
   * - ``Revised PRD failed schema validation: <error>``
     - Revised PRD doesn't match ``--schema`` file
     - Will use original PRD; review schema requirements
   * - ``Invalid JSON in revised PRD: <error>``
     - Agent returned malformed JSON
     - Will use original PRD; check agent output

Events Emitted by Enhancement Features
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enhancement features emit events that can be subscribed to via hooks:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Event Type
     - Trigger
   * - ``INTENT_ENHANCE_START``
     - Intent enhancement begins
   * - ``INTENT_ENHANCE_SUCCESS``
     - Intent successfully enhanced
   * - ``INTENT_ENHANCE_FAILURE``
     - Intent enhancement failed
   * - ``PRD_REVISE_START``
     - PRD revision begins
   * - ``PRD_REVISE_SUCCESS``
     - PRD successfully revised
   * - ``PRD_REVISE_FAILURE``
     - PRD revision failed

**Example hook for enhancement events**:

.. code-block:: python

   # .ralph/hooks/enhancement_monitor.py
   from pyralph import Event  # Optional: for type hints

   EVENTS = [
       "INTENT_ENHANCE_SUCCESS",
       "INTENT_ENHANCE_FAILURE",
       "PRD_REVISE_SUCCESS"
   ]

   def on_event(event: Event) -> None:
       if "FAILURE" in event.event_type.name:
           print(f"Enhancement failed: {event.event_type.name}")
       else:
           print(f"Enhancement completed: {event.event_type.name}")

.. _hooks:

Hook/Event System
-----------------

Ralph provides an extensible event-driven hook system that lets you subscribe to lifecycle events and execute custom code at key points during execution.

Available Event Types
^^^^^^^^^^^^^^^^^^^^^

Events are organized by lifecycle phase:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Category
     - Events
   * - **Phase**
     - ``PHASE_START``, ``PHASE_END``
   * - **Architect**
     - ``ARCHITECT_START``, ``ARCHITECT_SUCCESS``, ``ARCHITECT_FAILURE``
   * - **Planner**
     - ``PLANNER_START``, ``PLANNER_SUCCESS``, ``PLANNER_FAILURE``
   * - **Execute**
     - ``EXECUTE_START``, ``EXECUTE_END``
   * - **Task**
     - ``TASK_START``, ``TASK_SUCCESS``, ``TASK_FAILURE``, ``TASK_RETRY``
   * - **Verification**
     - ``VERIFICATION_START``, ``VERIFICATION_SUCCESS``, ``VERIFICATION_FAILURE``
   * - **PRD**
     - ``PRD_CREATED``, ``PRD_ARCHIVED``
   * - **Error**
     - ``ERROR``

Event Payload
^^^^^^^^^^^^^

All events carry the following data:

.. code-block:: python

   event_type: EventType           # The type of event
   timestamp: str                  # ISO format timestamp
   phase: Optional[str]            # Current phase: architect/planner/execute
   task_id: Optional[str]          # Task identifier
   task_description: Optional[str] # Task description
   retry_count: Optional[int]      # Current retry attempt
   max_retries: Optional[int]      # Maximum retries allowed
   error: Optional[Any]            # Error object if applicable
   verification_command: Optional[str]    # Test command
   verification_exit_code: Optional[int]  # Exit code from verification
   prd_path: Optional[str]         # Path to PRD file
   metadata: Dict[str, Any]        # Custom metadata

Creating Python Module Hooks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create a Python file in ``.ralph/hooks/``:

.. code-block:: python

   # .ralph/hooks/my_hook.py
   from pyralph import Event, EventType  # Optional: for type hints

   EVENTS = ["TASK_SUCCESS", "TASK_FAILURE"]  # Required: events to subscribe to
   PRIORITY = 50                               # Optional: lower = earlier (default: 100)
   TIMEOUT = 10.0                              # Optional: max seconds (default: 5.0)
   MODIFIES_DATA = False                       # Optional: can modify events (default: False)

   def on_event(event: Event) -> None:
       """Handle task completion events."""
       print(f"Task {event.task_id}: {event.event_type.name}")
       if event.error:
           print(f"  Error: {event.error}")

Hooks are auto-discovered from ``.ralph/hooks/`` on startup.

Creating Executable Hooks
^^^^^^^^^^^^^^^^^^^^^^^^^

Create a script with a companion YAML config:

.. code-block:: bash

   # .ralph/hooks/notify.sh
   #!/bin/bash
   EVENT_JSON=$(cat)  # Receive JSON event via stdin
   EVENT_TYPE=$(echo "$EVENT_JSON" | jq -r '.event_type')
   TASK_ID=$(echo "$EVENT_JSON" | jq -r '.task_id')

   echo "Task $TASK_ID: $EVENT_TYPE" >&2

.. code-block:: yaml

   # .ralph/hooks/notify.yaml
   events:
     - TASK_SUCCESS
     - TASK_FAILURE
   priority: 100
   timeout: 5.0

Hook Execution Behavior
^^^^^^^^^^^^^^^^^^^^^^^

- Hooks execute in priority order (lower values first)
- Each hook runs in an isolated thread with timeout protection
- Exceptions are caught and logged without halting execution
- Hooks with ``MODIFIES_DATA = True`` can transform event data

Plugin System
-------------

Plugins extend Ralph's functionality by registering hooks programmatically.

Loading Plugins
^^^^^^^^^^^^^^^

.. code-block:: bash

   # Load a single plugin file
   ralph --plugin /path/to/plugin.py

   # Load all plugins from a directory
   ralph --plugin /path/to/plugins/

Creating a Plugin
^^^^^^^^^^^^^^^^^

.. code-block:: python

   # ~/my_plugins/monitoring.py
   from pyralph import Event, EventType  # Optional: for type hints

   EVENTS = ["PHASE_START", "PHASE_END", "TASK_SUCCESS", "TASK_FAILURE"]
   PRIORITY = 50
   TIMEOUT = 10.0

   def on_event(event: Event) -> None:
       """Monitor Ralph lifecycle events."""
       if event.event_type.name == "TASK_SUCCESS":
           print(f"Task {event.task_id} completed")
       elif event.event_type.name == "TASK_FAILURE":
           print(f"Task {event.task_id} failed: {event.error}")
       elif event.event_type.name == "PHASE_START":
           print(f"Starting {event.phase} phase")

Combining with Hooks for CI Notifications
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # .ralph/hooks/ci_notify.py
   import os
   import requests
   from pyralph import Event  # Optional: for type hints

   EVENTS = ["TASK_FAILURE", "PLANNER_SUCCESS", "EXECUTE_END"]

   def on_event(event: Event) -> None:
       """Send notifications in CI environment."""
       webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
       if not webhook_url:
           return

       if event.event_type.name == "TASK_FAILURE":
           requests.post(webhook_url, json={
               "text": f"Task {event.task_id} failed: {event.error}"
           })
       elif event.event_type.name == "EXECUTE_END":
           requests.post(webhook_url, json={
               "text": "Ralph execution completed"
           })
