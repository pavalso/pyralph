"""
Hook/Event system for Ralph lifecycle events.

This module provides a mechanism for external programs to subscribe to
Ralph's lifecycle events (phase start/end, task execution, verification, etc.).

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

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING
import importlib.util
import json
import subprocess
import threading

if TYPE_CHECKING:
    from agents.base import AgentError


# ==============================================================================
# EVENT TYPES
# ==============================================================================

class EventType(Enum):
    """All lifecycle events that can be hooked."""

    # Phase lifecycle (generic)
    PHASE_START = auto()
    PHASE_END = auto()

    # Architect phase
    ARCHITECT_START = auto()
    ARCHITECT_SUCCESS = auto()
    ARCHITECT_FAILURE = auto()

    # Planner phase
    PLANNER_START = auto()
    PLANNER_SUCCESS = auto()
    PLANNER_FAILURE = auto()

    # Execute phase
    EXECUTE_START = auto()
    EXECUTE_END = auto()

    # Task lifecycle
    TASK_START = auto()
    TASK_SUCCESS = auto()
    TASK_FAILURE = auto()
    TASK_RETRY = auto()

    # Verification
    VERIFICATION_START = auto()
    VERIFICATION_SUCCESS = auto()
    VERIFICATION_FAILURE = auto()

    # PRD events
    PRD_CREATED = auto()
    PRD_ARCHIVED = auto()

    # Error events
    ERROR = auto()

    # IssueWatcher events
    WATCHER_START = auto()
    WATCHER_STOP = auto()
    ISSUE_DETECTED = auto()
    ISSUE_STORED = auto()
    ISSUE_QUEUED = auto()
    ISSUE_PROCESSING_START = auto()
    ISSUE_PROCESSING_SUCCESS = auto()
    ISSUE_PROCESSING_FAILURE = auto()
    POLL_START = auto()
    POLL_SUCCESS = auto()
    POLL_ERROR = auto()


# ==============================================================================
# EVENT PAYLOAD
# ==============================================================================

@dataclass
class Event:
    """Immutable event payload passed to hooks."""

    event_type: EventType
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    phase: Optional[str] = None
    task_id: Optional[str] = None
    task_description: Optional[str] = None
    retry_count: Optional[int] = None
    max_retries: Optional[int] = None
    error: Optional[Any] = None  # AgentError when available
    verification_command: Optional[str] = None
    verification_exit_code: Optional[int] = None
    prd_path: Optional[str] = None
    # IssueWatcher-related fields
    issue_number: Optional[int] = None
    issue_title: Optional[str] = None
    issue_url: Optional[str] = None
    issues_count: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def _serialize_error(self) -> Optional[str]:
        """Serialize error field for JSON export."""
        if self.error is None:
            return None
        return self.error.format_log_entry() if hasattr(self.error, 'format_log_entry') else str(self.error)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to dictionary for JSON export."""
        return {
            "event_type": self.event_type.name,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "task_id": self.task_id,
            "task_description": self.task_description,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error": self._serialize_error(),
            "verification_command": self.verification_command,
            "verification_exit_code": self.verification_exit_code,
            "prd_path": self.prd_path,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "issue_url": self.issue_url,
            "issues_count": self.issues_count,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict())


# ==============================================================================
# HOOK BASE CLASS
# ==============================================================================

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


# ==============================================================================
# PYTHON MODULE HOOK
# ==============================================================================

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


# ==============================================================================
# EXECUTABLE HOOK
# ==============================================================================

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
        except Exception:
            pass  # Errors are handled by caller
        return None

    @staticmethod
    def _parse_event_type(data: Dict[str, Any], original: Event) -> EventType:
        """Parse event_type from data, falling back to original."""
        if 'event_type' not in data:
            return original.event_type
        name = data['event_type']
        return EventType[name.upper()] if hasattr(EventType, str(name).upper()) else original.event_type

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


# ==============================================================================
# FUNCTION HOOK (PROGRAMMATIC)
# ==============================================================================

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


# ==============================================================================
# HOOK MANAGER
# ==============================================================================

class HookManager:
    """Discovers, loads, and executes hooks."""

    CONFIG_FILENAME = "hooks.yaml"

    def __init__(self, hooks_dir: Path, logger: Any = None):
        """
        Initialize the hook manager.

        Args:
            hooks_dir: Path to the hooks directory (.ralph/hooks/)
            logger: Optional logger instance with warning() method
        """
        self.hooks_dir = hooks_dir
        self._logger = logger
        self._hooks: Dict[EventType, List[Hook]] = defaultdict(list)
        self._enabled = True
        self._loaded = False
        self._enabled_hooks: Optional[Set[str]] = None  # None means all hooks enabled

    def enable(self) -> None:
        """Enable hook execution."""
        self._enabled = True

    def disable(self) -> None:
        """Disable hook execution."""
        self._enabled = False

    def set_enabled_hooks(self, hook_names: Optional[List[str]]) -> None:
        """
        Set which hooks are enabled by name.

        Args:
            hook_names: List of hook names to enable, or None to enable all hooks.
        """
        if hook_names is None:
            self._enabled_hooks = None
        else:
            self._enabled_hooks = set(hook_names)

    @property
    def is_enabled(self) -> bool:
        """Check if hooks are enabled."""
        return self._enabled

    def is_hook_enabled(self, hook_name: str) -> bool:
        """
        Check if a specific hook is enabled.

        Args:
            hook_name: The name of the hook to check.

        Returns:
            True if the hook is enabled, False otherwise.
        """
        if not self._enabled:
            return False
        if self._enabled_hooks is None:
            return True
        return hook_name in self._enabled_hooks

    def register_hook(
        self,
        name: str,
        handler: Callable[[Event], Optional[Event]],
        events: List[str],
        priority: int = 100,
        timeout: float = 5.0,
        modifies_data: bool = False
    ) -> bool:
        """
        Register a hook programmatically.

        This allows plugins and external code to register hooks without
        creating files in the .ralph/hooks/ directory.

        Args:
            name: Unique identifier for this hook.
            handler: Callable that receives an Event and optionally returns a modified Event.
            events: List of event names to subscribe to (e.g., ["TASK_START", "TASK_SUCCESS"]).
            priority: Execution order (lower = earlier). Default: 100.
            timeout: Maximum execution time in seconds. Default: 5.0.
            modifies_data: If True, handler's return value is used as modified event.

        Returns:
            True if hook was registered successfully, False otherwise.

        Example:
            >>> def my_handler(event):
            ...     print(f"Task {event.task_id} started")
            >>> manager.register_hook(
            ...     name="my_plugin",
            ...     handler=my_handler,
            ...     events=["TASK_START", "TASK_SUCCESS"]
            ... )
        """
        # Parse event names to EventType
        event_types = PythonHook._parse_events(events)
        if not event_types:
            self._log_warning(f"Hook '{name}' has no valid events")
            return False

        # Check for duplicate hook name
        existing_hooks = self.get_all_hooks()
        for hook in existing_hooks:
            if hook.name == name:
                self._log_warning(f"Hook '{name}' is already registered")
                return False

        # Create and register the hook
        hook = FunctionHook(
            name=name,
            handler=handler,
            events=event_types,
            priority=priority,
            timeout=timeout,
            modifies_data=modifies_data
        )
        self._register_hook(hook)
        return True

    def unregister_hook(self, name: str) -> bool:
        """
        Unregister a hook by name.

        Removes a previously registered hook from all event subscriptions.

        Args:
            name: The name of the hook to unregister.

        Returns:
            True if a hook was unregistered, False if no hook with that name was found.

        Example:
            >>> manager.unregister_hook("my_plugin")
        """
        found = False
        for event_type in list(self._hooks.keys()):
            original_count = len(self._hooks[event_type])
            self._hooks[event_type] = [h for h in self._hooks[event_type] if h.name != name]
            if len(self._hooks[event_type]) < original_count:
                found = True
            # Clean up empty lists
            if not self._hooks[event_type]:
                del self._hooks[event_type]
        return found

    def clear_hooks(self) -> int:
        """
        Clear all registered hooks.

        This removes all hooks (both programmatically registered and discovered).
        Useful for resetting state in tests or when reloading plugins.

        Returns:
            Number of unique hooks that were cleared.

        Example:
            >>> count = manager.clear_hooks()
            >>> print(f"Cleared {count} hooks")
        """
        all_hooks = self.get_all_hooks()
        count = len(all_hooks)
        self._hooks.clear()
        self._loaded = False
        return count

    def discover(self) -> int:
        """
        Scan hooks directory and load all valid hooks.

        Hooks are loaded from three sources:
        1. Python module hooks (.py files in hooks directory)
        2. Executable hooks (executable files with optional .yaml config)
        3. Configuration file (hooks.yaml) for registering external scripts

        Returns:
            Number of hooks loaded.
        """
        if not self.hooks_dir.exists():
            self._loaded = True
            return 0

        count = 0

        # Load hooks from configuration file first
        count += self._load_hooks_from_config()

        # Load Python module hooks
        for py_file in self.hooks_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            if self._load_python_hook(py_file):
                count += 1

        # Load executable hooks (skip config file and companion yaml files)
        for exe_file in self.hooks_dir.iterdir():
            if exe_file.suffix in (".py", ".yaml", ".yml") or exe_file.name.startswith("_"):
                continue
            if exe_file.is_file() and self._is_executable(exe_file):
                if self._load_executable_hook(exe_file):
                    count += 1

        self._loaded = True
        return count

    def _load_hooks_from_config(self) -> int:
        """
        Load hooks from the configuration file (hooks.yaml).

        The configuration file allows registering external scripts as hooks
        without placing them in the hooks directory.

        Expected format:
            hooks:
              - name: my_hook
                path: /path/to/script.sh
                events:
                  - TASK_START
                  - TASK_SUCCESS
                priority: 100        # optional, default 100
                timeout: 5.0         # optional, default 5.0
                modifies_data: false # optional, default false

        Returns:
            Number of hooks loaded from config.
        """
        config_path = self.hooks_dir / self.CONFIG_FILENAME
        if not config_path.exists():
            return 0

        try:
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            if not config or not isinstance(config, dict):
                return 0

            hooks_config = config.get('hooks', [])
            if not isinstance(hooks_config, list):
                self._log_warning(f"Invalid hooks config: 'hooks' must be a list")
                return 0

            count = 0
            for hook_entry in hooks_config:
                if self._load_hook_from_config_entry(hook_entry):
                    count += 1

            return count

        except Exception as e:
            self._log_warning(f"Failed to load hooks config: {e}")
            return 0

    def _validate_config_entry(self, entry: Any) -> Optional[Tuple[str, Path, Set[EventType]]]:
        """
        Validate a hook config entry and extract required fields.

        Returns:
            Tuple of (name, path, events) if valid, None otherwise.
        """
        if not isinstance(entry, dict):
            self._log_warning("Invalid hook entry: must be a dictionary")
            return None

        name = entry.get('name')
        path_str = entry.get('path')

        if not name:
            self._log_warning("Hook entry missing 'name' field")
            return None
        if not path_str:
            self._log_warning(f"Hook '{name}' missing 'path' field")
            return None

        # Resolve path (support relative paths from hooks directory)
        path = Path(path_str) if Path(path_str).is_absolute() else self.hooks_dir / path_str

        if not path.exists() or not path.is_file():
            self._log_warning(f"Hook '{name}' path does not exist or is not a file: {path}")
            return None

        event_names = entry.get('events', [])
        if not isinstance(event_names, list) or not event_names:
            self._log_warning(f"Hook '{name}' has no valid events")
            return None

        events = PythonHook._parse_events(event_names)
        if not events:
            self._log_warning(f"Hook '{name}' has no valid events")
            return None

        return name, path, events

    def _load_hook_from_config_entry(self, entry: Dict[str, Any]) -> bool:
        """
        Load a single hook from a configuration entry.

        Returns:
            True if hook was loaded successfully, False otherwise.
        """
        validated = self._validate_config_entry(entry)
        if not validated:
            return False

        name, path, events = validated

        # Extract optional fields with type coercion
        priority = entry.get('priority', 100)
        timeout = entry.get('timeout', 5.0)
        modifies_data = entry.get('modifies_data', False)

        hook = ExecutableHook(
            path=path,
            events=events,
            priority=int(priority) if isinstance(priority, (int, float)) else 100,
            timeout=float(timeout) if isinstance(timeout, (int, float)) else 5.0,
            modifies_data=bool(modifies_data) if isinstance(modifies_data, bool) else False
        )
        hook.name = name
        self._register_hook(hook)
        return True

    def _load_python_hook(self, path: Path) -> bool:
        """Load a Python module hook."""
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Validate hook has required attributes
            if not hasattr(module, 'EVENTS'):
                self._log_warning(f"Hook '{path.name}' missing EVENTS attribute")
                return False

            if not hasattr(module, 'on_event'):
                self._log_warning(f"Hook '{path.name}' missing on_event function")
                return False

            hook = PythonHook(path, module)
            if not hook.events:
                self._log_warning(f"Hook '{path.name}' has no valid events")
                return False

            self._register_hook(hook)
            return True

        except Exception as e:
            self._log_warning(f"Failed to load hook '{path.name}': {e}")
            return False

    def _load_executable_hook(self, path: Path) -> bool:
        """Load an executable hook."""
        try:
            # Try to read hook config from companion .yaml file
            config_path = path.with_suffix('.yaml')
            events: Set[EventType] = set()
            priority = 100
            timeout = 5.0

            modifies_data = False
            if config_path.exists():
                import yaml
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                    event_names = config.get('events', [])
                    events = PythonHook._parse_events(event_names)
                    priority = config.get('priority', 100)
                    timeout = config.get('timeout', 5.0)
                    modifies_data = config.get('modifies_data', False)

            # If no config, subscribe to all events
            if not events:
                events = set(EventType)

            hook = ExecutableHook(path, events, priority, timeout, modifies_data)
            self._register_hook(hook)
            return True

        except Exception as e:
            self._log_warning(f"Failed to load executable hook '{path.name}': {e}")
            return False

    def _register_hook(self, hook: Hook) -> None:
        """Register a hook for its subscribed events."""
        for event_type in hook.events:
            self._hooks[event_type].append(hook)

    @staticmethod
    def _is_executable(path: Path) -> bool:
        """Check if a file is executable."""
        import os
        import stat

        # On Windows, check for common executable extensions
        if os.name == 'nt':
            return path.suffix.lower() in {'.exe', '.bat', '.cmd', '.ps1', '.sh'}

        # On Unix, check executable bit
        try:
            return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        except OSError:
            return False

    def emit(self, event: Event) -> Event:
        """
        Emit an event to all subscribed hooks.

        Hooks are executed in priority order (lower = earlier).
        Hook errors are caught and logged but never halt execution.
        Hooks with modifies_data=True can transform the event data, with
        modifications chained through subsequent hooks.

        Args:
            event: The event to emit.

        Returns:
            The (potentially modified) event after all hooks have executed.
        """
        if not self._enabled:
            return event

        if not self._loaded:
            self.discover()

        hooks = self._hooks.get(event.event_type, [])
        if not hooks:
            return event

        # Sort by priority (lower = earlier)
        hooks_sorted = sorted(hooks, key=lambda h: h.priority)

        # Filter by enabled hooks if selective enabling is active
        if self._enabled_hooks is not None:
            hooks_sorted = [h for h in hooks_sorted if h.name in self._enabled_hooks]

        current_event = event
        for hook in hooks_sorted:
            modified = self._safe_execute(hook, current_event)
            if modified is not None:
                current_event = modified

        return current_event

    def _safe_execute(self, hook: Hook, event: Event) -> Optional[Event]:
        """
        Execute a hook with timeout and error isolation.

        Returns:
            Modified Event if hook has modifies_data=True and returns a valid Event,
            None otherwise.
        """
        result: Dict[str, Any] = {'completed': False, 'error': None, 'modified_event': None}

        def run_hook():
            try:
                modified = hook.execute(event)
                result['completed'] = True
                if hook.modifies_data and isinstance(modified, Event):
                    result['modified_event'] = modified
            except Exception as e:
                result['error'] = e

        thread = threading.Thread(target=run_hook, daemon=True)
        thread.start()
        thread.join(timeout=hook.timeout)

        if thread.is_alive():
            self._log_warning(f"Hook '{hook.name}' timed out after {hook.timeout}s")
            return None
        elif result['error']:
            self._log_warning(f"Hook '{hook.name}' failed: {result['error']}")
            return None

        return result['modified_event']

    def _log_warning(self, message: str) -> None:
        """Log a warning message if logger is available."""
        if self._logger and hasattr(self._logger, 'warning'):
            self._logger.warning(message)

    def get_hooks_for_event(self, event_type: EventType) -> List[Hook]:
        """Get all hooks registered for an event type."""
        return list(self._hooks.get(event_type, []))

    def get_all_hooks(self) -> List[Hook]:
        """Get all registered hooks (deduplicated)."""
        seen: Dict[str, Hook] = {}
        for hook_list in self._hooks.values():
            for hook in hook_list:
                if hook.name not in seen:
                    seen[hook.name] = hook
        return list(seen.values())
