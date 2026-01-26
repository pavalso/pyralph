Welcome to pyralph's documentation!
===================================

*"I'm helping!"* — Ralph Wiggum

**pyralph** is an autonomous software development agent that iteratively builds projects through a structured three-phase loop. It acts as a self-directing AI assistant that can understand project requirements, create detailed plans, and execute development tasks with built-in verification and error recovery.

Based on `Ralph Wiggum as a "Software engineer" <https://ghuntley.com/ralph/>`_.

.. tip::

   *"Me fail English? That's unpossible!"* — Just like Ralph, pyralph learns from its mistakes and keeps trying until it gets things right.

Getting Started
---------------

New to Ralph? Start here:

1. :doc:`installation` - Install pyralph and its dependencies
2. :doc:`quickstart` - Get up and running quickly
3. :doc:`concepts` - Understand how Ralph works

.. toctree::
   :maxdepth: 2
   :caption: User Guide:

   installation
   quickstart
   concepts
   cli

.. toctree::
   :maxdepth: 2
   :caption: Reference:

   api
   architecture

.. toctree::
   :maxdepth: 2
   :caption: Project Info:

   changelog

Key Features
------------

*"I bent my Wookiee."* — Ralph breaks things so you don't have to.

- **Three-Phase Workflow**: Architect -> Planner -> Execute loop for autonomous development
- **File-based State**: All context persisted to ``.ralph/`` directory for session resumability
- **Verification Gate**: Tasks validated by running actual tests before completion
- **Retry Mechanism**: Failed tasks automatically retry with error feedback
- **Hook System**: Extensible event system for custom integrations
- **CI/CD Support**: Headless mode with JSON output for pipeline integration

Quick Example
-------------

.. code-block:: bash

   # Install pyralph
   pip install pyralph

   # Start the agent
   ralph

   # Run with auto-accept mode
   ralph --accept-all

   # Run specific phase
   ralph architect
   ralph planner
   ralph execute

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
