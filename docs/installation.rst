.. _installation:

Installation
============

This guide covers how to install pyralph and its requirements.

Requirements
------------

Before installing pyralph, ensure you have:

- **Python 3.8+** (supports Python 3.8-3.12)
- **Claude CLI** - The ``claude`` command must be available in PATH
- **Git** - Required for version control operations

Install from PyPI
-----------------

The simplest way to install pyralph:

.. code-block:: bash

   pip install pyralph

Install from Source
-------------------

For development or to get the latest changes:

.. code-block:: bash

   git clone https://github.com/pavalso/pyralph.git
   cd pyralph
   pip install -e .

Verifying Installation
----------------------

After installation, verify that Ralph is available:

.. code-block:: bash

   ralph --help

This should display the available commands and options.

Next Steps
----------

Once installed, proceed to the :doc:`quickstart` guide to begin using Ralph.
