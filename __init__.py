#!/usr/bin/env python3
"""Ralph - Autonomous Software Development Agent.

This package provides the Ralph CLI tool for autonomous software development
through a three-phase loop: Architect -> Planner -> Execute.

Public API:
    Core:
        RalphOrchestrator - Main orchestrator class for running Ralph workflows
        main - CLI entry point function
        get_version - Get the version string from pyproject.toml

    Configuration:
        Config - Configuration dataclass
        CONF - Global configuration singleton

    Logging:
        Logger - Static logger class with CLI-controlled flags

    Utilities:
        Shell - Safe wrapper for subprocess calls
        PRDManager - PRD file operations with caching
        JsonUtils - Robust JSON parsing of LLM outputs
        MemoryManager - Memory file operations with filtering
        PromptFormatter - Utility for consistent prompt formatting
        TemplateManager - Template loading and management

    Hooks:
        HookManager - Hook discovery, loading, and execution
        Event - Immutable event payload
        EventType - Lifecycle event types enum
        Hook - Abstract base class for hooks

    Agents:
        BaseAgent - Abstract base class for agents
        AgentError - Agent execution error
        get_agent - Factory function to get agent instances
        list_agents - List available agent names

Example:
    >>> from ralph import RalphOrchestrator, Logger
    >>> Logger.set_verbosity(1)
    >>> orchestrator = RalphOrchestrator()
    >>> orchestrator.start(phase="all")
"""

# Import order is carefully structured to avoid circular imports.
# Base modules with no internal dependencies come first.
#
# This module supports two import modes:
# 1. Direct execution (current directory in sys.path) - absolute imports
# 2. Package installation - relative imports with fallback to absolute
#
# The try/except pattern allows both modes to work seamlessly.

try:
    # Try absolute imports first (for direct execution mode)
    from config import Config, CONF
    from logger import Logger
    from shell import Shell
    from prd import PRDManager, JsonUtils
    from memory import MemoryManager
    from templates import PromptFormatter, TemplateManager
    from hooks import (
        HookManager,
        Event,
        EventType,
        Hook,
        PythonHook,
        ExecutableHook,
        FunctionHook,
        QAChecklistAgent,
        FinalQAReport,
        FinalQAValidator,
        UnfilledRequirementsHandler,
        SupplementaryPRDGenerator,
        UserChoice,
        UnfilledRequirementsResult,
    )
    from agents import (
        BaseAgent,
        AgentError,
        get_agent,
        list_agents,
        AVAILABLE_AGENTS,
    )
    from cli import main, get_version
    from orchestrator import RalphOrchestrator
except ImportError:
    # Fall back to relative imports (for package installation mode)
    from .config import Config, CONF
    from .logger import Logger
    from .shell import Shell
    from .prd import PRDManager, JsonUtils
    from .memory import MemoryManager
    from .templates import PromptFormatter, TemplateManager
    from .hooks import (
        HookManager,
        Event,
        EventType,
        Hook,
        PythonHook,
        ExecutableHook,
        FunctionHook,
        QAChecklistAgent,
        FinalQAReport,
        FinalQAValidator,
        UnfilledRequirementsHandler,
        SupplementaryPRDGenerator,
        UserChoice,
        UnfilledRequirementsResult,
    )
    from .agents import (
        BaseAgent,
        AgentError,
        get_agent,
        list_agents,
        AVAILABLE_AGENTS,
    )
    from .cli import main, get_version
    from .orchestrator import RalphOrchestrator

# Version for package metadata
__version__ = get_version()

# Define public API for star imports
__all__ = [
    # Core
    "RalphOrchestrator",
    "main",
    "get_version",
    # Configuration
    "Config",
    "CONF",
    # Logging
    "Logger",
    # Utilities
    "Shell",
    "PRDManager",
    "JsonUtils",
    "MemoryManager",
    "PromptFormatter",
    "TemplateManager",
    # Hooks
    "HookManager",
    "Event",
    "EventType",
    "Hook",
    "PythonHook",
    "ExecutableHook",
    "FunctionHook",
    "QAChecklistAgent",
    "FinalQAReport",
    "FinalQAValidator",
    "UnfilledRequirementsHandler",
    "SupplementaryPRDGenerator",
    "UserChoice",
    "UnfilledRequirementsResult",
    # Agents
    "BaseAgent",
    "AgentError",
    "get_agent",
    "list_agents",
    "AVAILABLE_AGENTS",
    # Version
    "__version__",
]
