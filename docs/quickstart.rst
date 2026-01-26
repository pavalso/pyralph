.. _quickstart:

Quick Start
===========

This guide helps you get started with Ralph quickly.

Starting the Agent
------------------

The simplest way to start Ralph:

.. code-block:: bash

   ralph

If no project plan exists, Ralph prompts for a project description first.

Auto-Accept Mode
^^^^^^^^^^^^^^^^

Skip all prompts and run every phase automatically:

.. code-block:: bash

   ralph --accept-all
   # or the shortcut
   ralph -y

Running Specific Phases
^^^^^^^^^^^^^^^^^^^^^^^

Run individual phases:

.. code-block:: bash

   ralph architect    # Generate architecture documentation
   ralph planner      # Generate PRD with user stories
   ralph execute      # Execute tasks with verification

Combine flags for specific workflows:

.. code-block:: bash

   ralph execute --accept-all

State Management
----------------

Ralph persists all state to the ``.ralph/`` directory. This enables session resumability and crash recovery.

Hard Reset
^^^^^^^^^^

Clear all state and start fresh:

.. code-block:: bash

   rm -rf .ralph/

Re-plan
^^^^^^^

Regenerate user stories while keeping other state:

.. code-block:: bash

   rm .ralph/prd.json

Skip a Task
^^^^^^^^^^^

To skip a task, edit ``.ralph/prd.json`` and change the task status to ``"completed"``.

Debugging
---------

View recent log entries to troubleshoot issues:

.. code-block:: bash

   tail -n 50 .ralph/ralph_log.txt

Next Steps
----------

- Learn about Ralph's :doc:`concepts` and three-phase workflow
- Explore the full :doc:`cli` options
- Understand the :doc:`architecture` for advanced usage
