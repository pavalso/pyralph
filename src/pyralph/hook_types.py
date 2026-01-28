"""Hook type definitions for the hook system.

This module defines the Hook abstract base class and its concrete implementations:
- PythonHook: Hooks loaded from Python modules
- ExecutableHook: Hooks that run external executables
- FunctionHook: Hooks backed by Python callables for programmatic registration
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import importlib.util
import json
import subprocess

from .events import Event, EventType


class Hook(ABC):
    """Abstract base class for all hook types."""

    def __init__(
        self,
        name: str,
        events: Set[EventType],
        priority: int = 100,
        timeout: float = 5.0,
        modifies_data: bool = False
    ):
        self.name = name
        self.events = events
        self.priority = priority
        self.timeout = timeout
        self.modifies_data = modifies_data

    @abstractmethod
    def execute(self, event: Event) -> Optional[Event]:
        """
        Execute the hook with the given event.

        Args:
            event: The event to process.

        Returns:
            If modifies_data is True, returns a modified Event or None to keep original.
            If modifies_data is False, return value is ignored.
        """
        pass


class PythonHook(Hook):
    """Hook loaded from a Python module."""

    def __init__(self, path: Path, module: Any):
        events = self._parse_events(getattr(module, 'EVENTS', []))
        priority = getattr(module, 'PRIORITY', 100)
        timeout = getattr(module, 'TIMEOUT', 5.0)
        modifies_data = getattr(module, 'MODIFIES_DATA', False)
        super().__init__(path.stem, events, priority, timeout, modifies_data)
        self._module = module
        self._handler: Optional[Callable[[Event], Optional[Event]]] = getattr(module, 'on_event', None)

    @staticmethod
    def _parse_events(event_names: List[str]) -> Set[EventType]:
        """Parse event names to EventType enum values."""
        result = set()
        for name in event_names:
            if hasattr(EventType, name.upper()):
                result.add(EventType[name.upper()])
        return result

    def execute(self, event: Event) -> Optional[Event]:
        """
        Execute the Python hook handler.

        Returns:
            Modified Event if modifies_data is True and handler returns an Event,
            None otherwise.
        """
        if self._handler:
            result = self._handler(event)
            if self.modifies_data and isinstance(result, Event):
                return result
        return None


class ExecutableHook(Hook):
    """Hook that runs an external executable."""

    def __init__(
        self,
        path: Path,
        events: Set[EventType],
        priority: int = 100,
        timeout: float = 5.0,
        modifies_data: bool = False
    ):
        super().__init__(path.name, events, priority, timeout, modifies_data)
        self._path = path

    def execute(self, event: Event) -> Optional[Event]:
        """
        Execute the external hook, passing event JSON via stdin.

        If modifies_data is True, the hook's stdout is parsed as JSON to
        create a modified Event.

        Returns:
            Modified Event if modifies_data is True and valid JSON is returned,
            None otherwise.
        """
        try:
            result = subprocess.run(
                [str(self._path)],
                input=event.to_json(),
                timeout=self.timeout,
                capture_output=True,
                text=True,
                check=False
            )
            if self.modifies_data and result.returncode == 0 and result.stdout:
                return self._parse_modified_event(result.stdout, event)
        except subprocess.TimeoutExpired:
            pass  # Timeout is handled by caller
        except (OSError, json.JSONDecodeError) as e:
            # Log hook execution errors for debugging, caller handles the None return
            from .logger import Logger
            Logger.debug(f"Hook execution failed for {self._path}: {type(e).__name__}: {e}")
        return None

    @staticmethod
    def _parse_event_type(data: Dict[str, Any], original: Event) -> EventType:
        """Parse event_type from data, falling back to original."""
        if 'event_type' not in data:
            return original.event_type
        name = data['event_type']
        if hasattr(EventType, str(name).upper()):
            return EventType[name.upper()]
        return original.event_type

    @staticmethod
    def _parse_modified_event(json_str: str, original: Event) -> Optional[Event]:
        """Parse JSON output from executable hook into an Event."""
        try:
            data = json.loads(json_str)
            if not isinstance(data, dict):
                return None

            return Event(
                event_type=ExecutableHook._parse_event_type(data, original),
                timestamp=data.get('timestamp', original.timestamp),
                phase=data.get('phase', original.phase),
                task_id=data.get('task_id', original.task_id),
                task_description=data.get('task_description', original.task_description),
                retry_count=data.get('retry_count', original.retry_count),
                max_retries=data.get('max_retries', original.max_retries),
                error=data.get('error', original.error),
                verification_command=data.get('verification_command', original.verification_command),
                verification_exit_code=data.get('verification_exit_code', original.verification_exit_code),
                prd_path=data.get('prd_path', original.prd_path),
                issue_number=data.get('issue_number', original.issue_number),
                issue_title=data.get('issue_title', original.issue_title),
                issue_url=data.get('issue_url', original.issue_url),
                issues_count=data.get('issues_count', original.issues_count),
                metadata=data.get('metadata', original.metadata),
            )
        except (json.JSONDecodeError, TypeError):
            return None


class FunctionHook(Hook):
    """Hook backed by a Python callable for programmatic registration."""

    def __init__(
        self,
        name: str,
        handler: Callable[[Event], Optional[Event]],
        events: Set[EventType],
        priority: int = 100,
        timeout: float = 5.0,
        modifies_data: bool = False
    ):
        """
        Create a hook from a Python callable.

        Args:
            name: Unique identifier for this hook.
            handler: Callable that receives an Event and optionally returns a modified Event.
            events: Set of EventType values this hook subscribes to.
            priority: Execution order (lower = earlier). Default: 100.
            timeout: Maximum execution time in seconds. Default: 5.0.
            modifies_data: If True, handler's return value is used as modified event.
        """
        super().__init__(name, events, priority, timeout, modifies_data)
        self._handler = handler

    def execute(self, event: Event) -> Optional[Event]:
        """
        Execute the callable handler.

        Returns:
            Modified Event if modifies_data is True and handler returns an Event,
            None otherwise.
        """
        result = self._handler(event)
        if self.modifies_data and isinstance(result, Event):
            return result
        return None
