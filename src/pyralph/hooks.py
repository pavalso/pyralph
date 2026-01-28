"""
Hook/Event system for Ralph lifecycle events.

This module provides a mechanism for external programs to subscribe to
Ralph's lifecycle events (phase start/end, task execution, verification, etc.).

This module re-exports all public symbols from the decomposed modules:
- events.py: EventType enum and Event dataclass
- hook_types.py: Hook base class, PythonHook, ExecutableHook, FunctionHook
- hook_manager.py: HookManager class

Usage:
    1. Create Python hooks in .ralph/hooks/*.py with:
       - EVENTS = ["TASK_SUCCESS", "TASK_FAILURE"]  # Required
       - PRIORITY = 100  # Optional, lower = earlier
       - def on_event(event): ...  # Required handler

    2. Or create executable hooks that receive JSON via stdin

Example hook (.ralph/hooks/notify.py):
    EVENTS = ["TASK_SUCCESS", "TASK_FAILURE"]

    def on_event(event):
        print(f"Task {event.task_id}: {event.event_type.name}")
"""

# Re-export all public symbols from decomposed modules.
# This maintains backward compatibility for existing imports from hooks.py.

_IMPORT_ERRORS = []

try:
    from .events import EventType, Event
except ImportError as e:
    _IMPORT_ERRORS.append(f"events: {e}")

try:
    from .hook_types import Hook, PythonHook, ExecutableHook, FunctionHook
except ImportError as e:
    _IMPORT_ERRORS.append(f"hook_types: {e}")

try:
    from .hook_manager import HookManager
except ImportError as e:
    _IMPORT_ERRORS.append(f"hook_manager: {e}")

# Raise clear error if any module failed to import
if _IMPORT_ERRORS:
    raise ImportError(
        f"Failed to import hook system modules: {'; '.join(_IMPORT_ERRORS)}. "
        "Ensure events.py, hook_types.py, and hook_manager.py exist in the pyralph package."
    )

# Public API - all symbols that should be importable from hooks.py
__all__ = [
    # From events.py
    "EventType",
    "Event",
    # From hook_types.py
    "Hook",
    "PythonHook",
    "ExecutableHook",
    "FunctionHook",
    # From hook_manager.py
    "HookManager",
]
