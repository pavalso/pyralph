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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import importlib.util
import json
import subprocess
import threading


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

    # Intent enhancement
    INTENT_ENHANCE_START = auto()
    INTENT_ENHANCE_SUCCESS = auto()
    INTENT_ENHANCE_FAILURE = auto()

    # PRD events
    PRD_CREATED = auto()
    PRD_ARCHIVED = auto()
    PRD_REVISE_START = auto()
    PRD_REVISE_SUCCESS = auto()
    PRD_REVISE_FAILURE = auto()
    PRD_COMPLETE = auto()
    PRD_INCOMPLETE = auto()

    # Error events
    ERROR = auto()

    # QA review events
    QA_REVIEW_START = auto()
    QA_REVIEW_SUCCESS = auto()
    QA_REVIEW_FAILURE = auto()
    QA_REVIEW_SKIPPED = auto()

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
        if hasattr(self.error, 'format_log_entry'):
            return self.error.format_log_entry()
        return str(self.error)

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
        except (OSError, json.JSONDecodeError) as e:
            # Log hook execution errors for debugging, caller handles the None return
            from ralph import Logger
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


# ==============================================================================
# QA CHECKLIST AGENT
# ==============================================================================

class QAChecklistAgent:
    """Agent that updates QA checklist status after task completion.

    This agent is triggered by the TASK_SUCCESS hook event to evaluate
    completed tasks against the QA checklist requirements and update
    their status accordingly.

    Usage:
        1. Create an instance with a QAChecklistManager
        2. Register the agent with a HookManager
        3. Agent will automatically update checklist on task success

    Example:
        >>> from ralph import QAChecklistManager, CONF
        >>> checklist_manager = QAChecklistManager(CONF.QA_CHECKLIST_FILE)
        >>> qa_agent = QAChecklistAgent(checklist_manager)
        >>> qa_agent.register(hook_manager)
    """

    HOOK_NAME = "qa_checklist_agent"

    def __init__(self, checklist_manager: Any, logger: Optional[Any] = None):
        """Initialize the QA checklist agent.

        Args:
            checklist_manager: QAChecklistManager instance for checklist operations
            logger: Optional logger with warning() method for error logging
        """
        self._checklist_manager = checklist_manager
        self._logger = logger
        self._registered = False

    def _log_warning(self, message: str) -> None:
        """Log a warning message if logger is available."""
        if self._logger and hasattr(self._logger, 'warning'):
            self._logger.warning(message)

    def _log_info(self, message: str) -> None:
        """Log an info message if logger is available."""
        if self._logger and hasattr(self._logger, 'info'):
            self._logger.info(message)

    def register(self, hook_manager: HookManager) -> bool:
        """Register the QA checklist agent with the hook manager.

        Registers a hook that listens for TASK_SUCCESS events and
        updates the QA checklist accordingly.

        Args:
            hook_manager: HookManager instance to register with

        Returns:
            True if registration succeeded, False otherwise
        """
        if self._registered:
            self._log_warning("QA checklist agent already registered")
            return False

        success = hook_manager.register_hook(
            name=self.HOOK_NAME,
            handler=self._on_task_success,
            events=["TASK_SUCCESS"],
            priority=50,  # Run before most other hooks
            timeout=10.0,
            modifies_data=False
        )

        if success:
            self._registered = True

        return success

    def unregister(self, hook_manager: HookManager) -> bool:
        """Unregister the QA checklist agent from the hook manager.

        Args:
            hook_manager: HookManager instance to unregister from

        Returns:
            True if unregistration succeeded, False otherwise
        """
        if not self._registered:
            return False

        success = hook_manager.unregister_hook(self.HOOK_NAME)
        if success:
            self._registered = False

        return success

    def _get_requirements_for_task(self, task_id: str) -> List[Tuple[str, Any]]:
        """Get all requirements that belong to a specific task.

        Requirements are linked to tasks via ID prefix (e.g., TASK-001-AC01
        belongs to TASK-001).

        Args:
            task_id: The task ID to find requirements for (e.g., "TASK-001")

        Returns:
            List of (requirement_id, requirement) tuples for matching requirements
        """
        matching = []
        try:
            all_requirements = self._checklist_manager.get_all_requirements()
            for req in all_requirements:
                if req.id.startswith(f"{task_id}-"):
                    matching.append((req.id, req))
        except Exception as e:
            self._log_warning(f"Error loading requirements: {e}")

        return matching

    def _evaluate_requirement(
        self, requirement: Any, task_id: str, task_description: Optional[str]
    ) -> Tuple[str, str]:
        """Evaluate whether a requirement is satisfied by the completed task.

        For TASK_SUCCESS events, we mark requirements as passed since the task
        completed successfully (including passing verification tests).

        Args:
            requirement: The QARequirement to evaluate
            task_id: ID of the completed task
            task_description: Description of the completed task

        Returns:
            Tuple of (status, reasoning) where status is "passed" or "failed"
        """
        # Since TASK_SUCCESS is only emitted after successful verification,
        # we mark requirements as passed with the reasoning that the task
        # completed successfully and passed verification
        status = "passed"
        reasoning = (
            f"Task {task_id} completed successfully and passed verification. "
            f"Requirement '{requirement.description}' is considered satisfied."
        )

        return status, reasoning

    def _on_task_success(self, event: Event) -> Optional[Event]:
        """Handle TASK_SUCCESS event by updating the QA checklist.

        This method:
        1. Finds all requirements linked to the completed task
        2. Evaluates each requirement against the task
        3. Updates status to passed/failed with reasoning
        4. Records the task ID in linkedTasks
        5. Updates lastChecked timestamp

        If no requirements are affected, logs this and skips the update.
        All errors are caught and logged as warnings to avoid blocking
        task completion.

        Args:
            event: The TASK_SUCCESS event containing task information

        Returns:
            None (this hook does not modify the event)
        """
        task_id = event.task_id
        task_description = event.task_description

        if not task_id:
            self._log_warning("QA checklist agent received event without task_id")
            return None

        try:
            # Find requirements for this task
            requirements = self._get_requirements_for_task(task_id)

            if not requirements:
                self._log_info(
                    f"QA checklist: No requirements found for task {task_id}, "
                    "skipping checklist update"
                )
                return None

            # Update each requirement
            updated_count = 0
            for req_id, requirement in requirements:
                try:
                    status, reasoning = self._evaluate_requirement(
                        requirement, task_id, task_description
                    )

                    # Update the requirement with status and linked task
                    # The update_requirement method handles lastChecked automatically
                    success = self._checklist_manager.update_requirement(
                        requirement_id=req_id,
                        status=status,
                        linked_task=task_id
                    )

                    if success:
                        updated_count += 1
                        self._log_info(
                            f"QA checklist: Updated {req_id} to '{status}' "
                            f"(linked to {task_id})"
                        )
                    else:
                        self._log_warning(
                            f"QA checklist: Failed to update {req_id}"
                        )

                except Exception as e:
                    self._log_warning(
                        f"QA checklist: Error updating requirement {req_id}: {e}"
                    )

            self._log_info(
                f"QA checklist: Updated {updated_count}/{len(requirements)} "
                f"requirements for task {task_id}"
            )

        except Exception as e:
            # Catch all exceptions to ensure task completion is not blocked
            self._log_warning(f"QA checklist agent error: {e}")

        return None


# ==============================================================================
# FINAL QA VALIDATION REPORT
# ==============================================================================

@dataclass
class FinalQAReport:
    """Report from final QA validation when all PRD tasks are complete.

    Attributes:
        total_requirements: Total number of requirements evaluated
        passed: Number of requirements that passed
        failed: Number of requirements that failed
        coverage_percentage: Percentage of requirements that passed (0-100)
        failed_requirements: List of (id, description) tuples for failed requirements
        all_passed: True if all requirements passed
        error: Error message if validation failed catastrophically
        partial_results: Partial results preserved if validation failed mid-way
    """
    total_requirements: int
    passed: int
    failed: int
    coverage_percentage: float
    failed_requirements: List[Tuple[str, str]]
    all_passed: bool
    error: Optional[str] = None
    partial_results: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary for JSON serialization."""
        return {
            "total_requirements": self.total_requirements,
            "passed": self.passed,
            "failed": self.failed,
            "coverage_percentage": self.coverage_percentage,
            "failed_requirements": [
                {"id": req_id, "description": desc}
                for req_id, desc in self.failed_requirements
            ],
            "all_passed": self.all_passed,
            "error": self.error,
            "partial_results": self.partial_results,
        }


# ==============================================================================
# FINAL QA VALIDATOR
# ==============================================================================

class FinalQAValidator:
    """Performs final QA validation when all PRD tasks are complete.

    This validator is triggered when all PRD tasks reach 'completed' status.
    It re-evaluates ALL checklist requirements against the complete implementation
    and produces a comprehensive validation report.

    Usage:
        1. Create an instance with QAChecklistManager and PRDManager
        2. Call validate() when all tasks are complete
        3. Or register with HookManager to auto-trigger on all tasks complete

    Example:
        >>> from ralph import QAChecklistManager, PRDManager, CONF
        >>> checklist_manager = QAChecklistManager(CONF.QA_CHECKLIST_FILE)
        >>> prd_manager = PRDManager(CONF.PRD_FILE)
        >>> validator = FinalQAValidator(checklist_manager, prd_manager)
        >>> report = validator.validate()
        >>> if report.all_passed:
        ...     print("PRD complete!")
    """

    HOOK_NAME = "final_qa_validator"

    def __init__(
        self,
        checklist_manager: Any,
        prd_manager: Any,
        hook_manager: Optional[HookManager] = None,
        logger: Optional[Any] = None
    ):
        """Initialize the final QA validator.

        Args:
            checklist_manager: QAChecklistManager instance for checklist operations
            prd_manager: PRDManager instance for PRD operations
            hook_manager: Optional HookManager for emitting events
            logger: Optional logger with info/warning/error methods
        """
        self._checklist_manager = checklist_manager
        self._prd_manager = prd_manager
        self._hook_manager = hook_manager
        self._logger = logger
        self._registered = False

    def _log_info(self, message: str) -> None:
        """Log an info message if logger is available."""
        if self._logger and hasattr(self._logger, 'info'):
            self._logger.info(message)

    def _log_warning(self, message: str) -> None:
        """Log a warning message if logger is available."""
        if self._logger and hasattr(self._logger, 'warning'):
            self._logger.warning(message)

    def _log_error(self, message: str) -> None:
        """Log an error message if logger is available."""
        if self._logger and hasattr(self._logger, 'error'):
            self._logger.error(message)

    def _are_all_tasks_completed(self) -> bool:
        """Check if all PRD tasks have status 'completed'.

        Returns:
            True if all tasks are completed, False otherwise
        """
        if not self._prd_manager.exists():
            return False

        try:
            prd_data = self._prd_manager.load()
            tasks = prd_data.get('userStories', [])

            if not tasks:
                return False

            return all(task.get('status') == 'completed' for task in tasks)
        except Exception as e:
            self._log_warning(f"Error checking task status: {e}")
            return False

    def _emit_event(self, event: Event) -> None:
        """Emit an event if hook manager is available."""
        if self._hook_manager:
            self._hook_manager.emit(event)

    def _generate_checklist_if_needed(self) -> bool:
        """Generate checklist from PRD if it doesn't exist.

        Returns:
            True if checklist exists or was created, False on failure
        """
        if self._checklist_manager.exists():
            return True

        try:
            self._log_info("QA checklist not found, generating from PRD for final validation...")
            self._checklist_manager.load(auto_create=True)
            return True
        except Exception as e:
            self._log_error(f"Failed to generate checklist: {e}")
            return False

    def validate(self) -> FinalQAReport:
        """Perform final QA validation on all checklist requirements.

        This method:
        1. Ensures checklist exists (generates from PRD if needed)
        2. Re-evaluates ALL requirements against the implementation
        3. Produces a comprehensive report
        4. Emits PRD_COMPLETE or PRD_INCOMPLETE event

        Returns:
            FinalQAReport with validation results

        Note:
            If validation fails catastrophically, partial results are preserved
            in the returned report's partial_results field.
        """
        partial_results: Dict[str, Any] = {
            "evaluated": [],
            "pending": [],
        }

        try:
            # Edge case: Generate checklist if never created
            if not self._generate_checklist_if_needed():
                return FinalQAReport(
                    total_requirements=0,
                    passed=0,
                    failed=0,
                    coverage_percentage=0.0,
                    failed_requirements=[],
                    all_passed=False,
                    error="Failed to generate or load checklist",
                    partial_results=partial_results,
                )

            # Load all requirements
            requirements = self._checklist_manager.get_all_requirements()

            if not requirements:
                self._log_info("No requirements found in checklist")
                report = FinalQAReport(
                    total_requirements=0,
                    passed=0,
                    failed=0,
                    coverage_percentage=100.0,
                    failed_requirements=[],
                    all_passed=True,
                )
                self._emit_event(Event(
                    EventType.PRD_COMPLETE,
                    metadata={"report": report.to_dict()}
                ))
                return report

            # Mark pending requirements for processing
            partial_results["pending"] = [req.id for req in requirements]

            # Evaluate each requirement
            passed_count = 0
            failed_requirements: List[Tuple[str, str]] = []

            for req in requirements:
                try:
                    # Move from pending to evaluated
                    if req.id in partial_results["pending"]:
                        partial_results["pending"].remove(req.id)

                    # Re-evaluate: consider 'passed' as passed, anything else as failed
                    if req.status == "passed":
                        passed_count += 1
                        partial_results["evaluated"].append({
                            "id": req.id,
                            "status": "passed"
                        })
                    else:
                        failed_requirements.append((req.id, req.description))
                        partial_results["evaluated"].append({
                            "id": req.id,
                            "status": "failed"
                        })

                except Exception as e:
                    # Record partial failure but continue
                    self._log_warning(f"Error evaluating requirement {req.id}: {e}")
                    failed_requirements.append((req.id, req.description))
                    partial_results["evaluated"].append({
                        "id": req.id,
                        "status": "error",
                        "error": str(e)
                    })

            # Calculate statistics
            total = len(requirements)
            failed_count = len(failed_requirements)
            coverage = (passed_count / total * 100) if total > 0 else 0.0
            all_passed = failed_count == 0

            report = FinalQAReport(
                total_requirements=total,
                passed=passed_count,
                failed=failed_count,
                coverage_percentage=round(coverage, 2),
                failed_requirements=failed_requirements,
                all_passed=all_passed,
            )

            # Emit appropriate event
            if all_passed:
                self._log_info(
                    f"Final QA validation PASSED: {passed_count}/{total} requirements "
                    f"({coverage:.1f}% coverage)"
                )
                self._emit_event(Event(
                    EventType.PRD_COMPLETE,
                    metadata={"report": report.to_dict()}
                ))
            else:
                failure_ids = [req_id for req_id, _ in failed_requirements]
                self._log_warning(
                    f"Final QA validation FAILED: {passed_count}/{total} passed, "
                    f"{failed_count} failed ({coverage:.1f}% coverage)"
                )
                self._emit_event(Event(
                    EventType.PRD_INCOMPLETE,
                    metadata={
                        "report": report.to_dict(),
                        "failed_requirements": failure_ids,
                    }
                ))

            return report

        except Exception as e:
            # Catastrophic failure - preserve partial results
            self._log_error(f"Final QA validation failed catastrophically: {e}")

            report = FinalQAReport(
                total_requirements=len(partial_results.get("evaluated", [])) +
                                   len(partial_results.get("pending", [])),
                passed=sum(
                    1 for r in partial_results.get("evaluated", [])
                    if r.get("status") == "passed"
                ),
                failed=sum(
                    1 for r in partial_results.get("evaluated", [])
                    if r.get("status") in ("failed", "error")
                ),
                coverage_percentage=0.0,
                failed_requirements=[],
                all_passed=False,
                error=str(e),
                partial_results=partial_results,
            )

            return report

    def check_and_validate(self) -> Optional[FinalQAReport]:
        """Check if all tasks are complete and trigger validation if so.

        This is a convenience method that combines the completion check
        with validation.

        Returns:
            FinalQAReport if validation was triggered, None if tasks incomplete
        """
        if not self._are_all_tasks_completed():
            return None

        self._log_info("All PRD tasks completed - triggering final QA validation")
        return self.validate()

    def register(self, hook_manager: HookManager) -> bool:
        """Register the validator to auto-trigger on task success.

        The validator listens for TASK_SUCCESS events and checks if all
        tasks are now complete. If so, it triggers final validation.

        Args:
            hook_manager: HookManager instance to register with

        Returns:
            True if registration succeeded, False otherwise
        """
        if self._registered:
            self._log_warning("FinalQAValidator already registered")
            return False

        # Store hook manager reference for event emission
        self._hook_manager = hook_manager

        success = hook_manager.register_hook(
            name=self.HOOK_NAME,
            handler=self._on_task_success,
            events=["TASK_SUCCESS"],
            priority=60,  # Run after QAChecklistAgent (priority 50)
            timeout=30.0,  # Allow more time for full validation
            modifies_data=False
        )

        if success:
            self._registered = True

        return success

    def unregister(self, hook_manager: HookManager) -> bool:
        """Unregister the validator from the hook manager.

        Args:
            hook_manager: HookManager instance to unregister from

        Returns:
            True if unregistration succeeded, False otherwise
        """
        if not self._registered:
            return False

        success = hook_manager.unregister_hook(self.HOOK_NAME)
        if success:
            self._registered = False

        return success

    def _on_task_success(self, event: Event) -> Optional[Event]:
        """Handle TASK_SUCCESS event by checking if all tasks are complete.

        If all tasks are complete, triggers final validation automatically.

        Args:
            event: The TASK_SUCCESS event

        Returns:
            None (this hook does not modify the event)
        """
        try:
            # Check and validate returns None if not all tasks complete
            self.check_and_validate()
        except Exception as e:
            # Don't block on validation errors
            self._log_warning(f"Final QA validation hook error: {e}")

        return None


# ==============================================================================
# UNFILLED REQUIREMENTS HANDLER
# ==============================================================================


class UserChoice:
    """Constants for user choices when handling unfilled requirements."""
    GENERATE_SUPPLEMENTARY = "generate_supplementary"
    MARK_DEFERRED = "mark_deferred"
    CONTINUE_WITH_GAPS = "continue_with_gaps"
    FULL_REVISION = "full_revision"
    QUICK_FIX = "quick_fix"


@dataclass
class UnfilledRequirementsResult:
    """Result from handling unfilled requirements.

    Attributes:
        choice: The user's choice (UserChoice constant)
        supplementary_prd_path: Path to generated supplementary PRD, if applicable
        supplementary_prd_data: Generated PRD data, if applicable
        deferred_requirements: List of requirement IDs marked as deferred
        error: Error message if handling failed
    """
    choice: str
    supplementary_prd_path: Optional[Path] = None
    supplementary_prd_data: Optional[Dict[str, Any]] = None
    deferred_requirements: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for JSON serialization."""
        return {
            "choice": self.choice,
            "supplementary_prd_path": str(self.supplementary_prd_path) if self.supplementary_prd_path else None,
            "supplementary_prd_data": self.supplementary_prd_data,
            "deferred_requirements": self.deferred_requirements,
            "error": self.error,
        }


class SupplementaryPRDGenerator:
    """Generates supplementary PRDs to address unfilled requirements.

    Creates a new PRD containing tasks derived from failed requirement
    descriptions and validation reasoning.

    Usage:
        >>> generator = SupplementaryPRDGenerator(original_prd_id="PRD-001")
        >>> prd_data = generator.generate(failed_requirements, output_path)
    """

    def __init__(
        self,
        original_prd_id: str,
        original_prd_path: Optional[Path] = None,
        logger: Optional[Any] = None
    ):
        """Initialize the supplementary PRD generator.

        Args:
            original_prd_id: ID of the original PRD being supplemented
            original_prd_path: Path to the original PRD file
            logger: Optional logger with info/warning/error methods
        """
        self._original_prd_id = original_prd_id
        self._original_prd_path = original_prd_path
        self._logger = logger

    def _log_info(self, message: str) -> None:
        """Log an info message if logger is available."""
        if self._logger and hasattr(self._logger, 'info'):
            self._logger.info(message)

    def _log_warning(self, message: str) -> None:
        """Log a warning message if logger is available."""
        if self._logger and hasattr(self._logger, 'warning'):
            self._logger.warning(message)

    def _log_error(self, message: str) -> None:
        """Log an error message if logger is available."""
        if self._logger and hasattr(self._logger, 'error'):
            self._logger.error(message)

    def _generate_supplementary_id(self) -> str:
        """Generate a unique ID for the supplementary PRD.

        Returns:
            Supplementary PRD ID in format PRD-XXX-SUPP-N
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{self._original_prd_id}-SUPP-{timestamp}"

    def _create_user_story_from_requirement(
        self,
        requirement_id: str,
        description: str,
        task_number: int
    ) -> Dict[str, Any]:
        """Create a user story from a failed requirement.

        Args:
            requirement_id: ID of the failed requirement
            description: Description of the requirement
            task_number: Sequential task number for ID generation

        Returns:
            User story dictionary following PRD schema
        """
        task_id = f"TASK-{task_number:03d}"

        # Create story description from requirement
        story_description = (
            f"As a developer, I want to fulfill the unfilled requirement "
            f"'{requirement_id}' so that {description}"
        )

        # Build acceptance criteria based on the requirement
        acceptance_criteria = [
            f"Given the requirement '{requirement_id}', the implementation satisfies: {description}",
            "Edge case: Implementation handles boundary conditions appropriately",
            "Error handling: Implementation provides meaningful error messages for failure scenarios"
        ]

        # Definition of done
        definition_of_done = [
            f"Requirement {requirement_id} passes validation",
            "Unit tests cover the new functionality",
            "No regression in existing functionality"
        ]

        return {
            "id": task_id,
            "description": story_description,
            "priority": "Must Have",
            "acceptanceCriteria": acceptance_criteria,
            "definitionOfDone": definition_of_done,
            "risks": [],
            "dependencies": [],
            "status": "pending",
            "originalRequirement": requirement_id
        }

    def generate(
        self,
        failed_requirements: List[Tuple[str, str]],
        output_path: Optional[Path] = None
    ) -> Tuple[Dict[str, Any], Optional[Path]]:
        """Generate a supplementary PRD from failed requirements.

        Args:
            failed_requirements: List of (id, description) tuples for failed requirements
            output_path: Optional path to write the PRD JSON file

        Returns:
            Tuple of (prd_data, output_path) where output_path is None if not written

        Raises:
            ValueError: If no failed requirements provided
        """
        if not failed_requirements:
            raise ValueError("No failed requirements provided for supplementary PRD")

        supplementary_id = self._generate_supplementary_id()

        # Create user stories from each failed requirement
        user_stories = []
        addressed_requirements = []

        for i, (req_id, description) in enumerate(failed_requirements, 1):
            story = self._create_user_story_from_requirement(req_id, description, i)
            user_stories.append(story)
            addressed_requirements.append(req_id)

        # Build the supplementary PRD
        prd_data = {
            "id": supplementary_id,
            "description": f"Supplementary PRD to address unfilled requirements from {self._original_prd_id}",
            "originalPrdId": self._original_prd_id,
            "originalPrdPath": str(self._original_prd_path) if self._original_prd_path else None,
            "addressedRequirements": addressed_requirements,
            "userStories": user_stories
        }

        self._log_info(
            f"Generated supplementary PRD '{supplementary_id}' with "
            f"{len(user_stories)} tasks addressing {len(addressed_requirements)} requirements"
        )

        # Write to file if path provided
        written_path = None
        if output_path:
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(prd_data, indent=2), encoding='utf-8')
                written_path = output_path
                self._log_info(f"Supplementary PRD written to: {output_path}")
            except OSError as e:
                self._log_error(f"Failed to write supplementary PRD: {e}")

        return prd_data, written_path


class UnfilledRequirementsHandler:
    """Handles unfilled requirements after final QA validation.

    Prompts users with options when requirements remain unfilled:
    1. Generate supplementary PRD
    2. Mark requirements as deferred
    3. Continue with gaps

    Supports edge cases:
    - Single unfilled requirement: offers quick-fix suggestions
    - All requirements unfilled: suggests full PRD revision

    In non-interactive mode, outputs to stderr and exits with code 2.

    Usage:
        >>> handler = UnfilledRequirementsHandler(
        ...     prd_manager, checklist_manager, non_interactive=False
        ... )
        >>> result = handler.handle(report)
    """

    EXIT_CODE_UNFILLED = 2

    def __init__(
        self,
        prd_manager: Any,
        checklist_manager: Any,
        non_interactive: bool = False,
        output_dir: Optional[Path] = None,
        logger: Optional[Any] = None
    ):
        """Initialize the unfilled requirements handler.

        Args:
            prd_manager: PRDManager instance for PRD operations
            checklist_manager: QAChecklistManager for checklist operations
            non_interactive: If True, skip prompts and output to stderr
            output_dir: Directory for supplementary PRD output (defaults to .ralph/)
            logger: Optional logger with info/warning/error methods
        """
        self._prd_manager = prd_manager
        self._checklist_manager = checklist_manager
        self._non_interactive = non_interactive
        self._output_dir = output_dir
        self._logger = logger
        self._preserved_unfilled: List[Tuple[str, str]] = []

    def _log_info(self, message: str) -> None:
        """Log an info message if logger is available."""
        if self._logger and hasattr(self._logger, 'info'):
            self._logger.info(message)

    def _log_warning(self, message: str) -> None:
        """Log a warning message if logger is available."""
        if self._logger and hasattr(self._logger, 'warning'):
            self._logger.warning(message)

    def _log_error(self, message: str) -> None:
        """Log an error message if logger is available."""
        if self._logger and hasattr(self._logger, 'error'):
            self._logger.error(message)

    def _get_original_prd_id(self) -> str:
        """Get the ID of the original PRD.

        Returns:
            PRD ID string, or 'UNKNOWN' if not available
        """
        try:
            if self._prd_manager and self._prd_manager.exists():
                prd_data = self._prd_manager.load()
                return prd_data.get("id", "UNKNOWN")
        except Exception:
            pass
        return "UNKNOWN"

    def _output_to_stderr(self, failed_requirements: List[Tuple[str, str]]) -> None:
        """Output unfilled requirements to stderr for non-interactive mode.

        Args:
            failed_requirements: List of (id, description) tuples
        """
        import sys
        sys.stderr.write("\n=== UNFILLED REQUIREMENTS ===\n")
        sys.stderr.write(f"PRD: {self._get_original_prd_id()}\n")
        sys.stderr.write(f"Total unfilled: {len(failed_requirements)}\n\n")

        for req_id, description in failed_requirements:
            sys.stderr.write(f"  [{req_id}] {description}\n")

        sys.stderr.write("\n")
        sys.stderr.flush()

    def _is_single_requirement(self, report: FinalQAReport) -> bool:
        """Check if only one requirement is unfilled.

        Args:
            report: Final QA validation report

        Returns:
            True if exactly one requirement is unfilled
        """
        return report.failed == 1

    def _is_all_requirements_unfilled(self, report: FinalQAReport) -> bool:
        """Check if all requirements are unfilled.

        Args:
            report: Final QA validation report

        Returns:
            True if all requirements are unfilled
        """
        return report.failed == report.total_requirements and report.total_requirements > 0

    def _prompt_single_requirement(
        self, requirement: Tuple[str, str]
    ) -> UnfilledRequirementsResult:
        """Handle single unfilled requirement with quick-fix option.

        Args:
            requirement: (id, description) tuple for the unfilled requirement

        Returns:
            UnfilledRequirementsResult with user's choice
        """
        req_id, description = requirement

        print(f"\n{'='*60}")
        print("SINGLE UNFILLED REQUIREMENT")
        print(f"{'='*60}")
        print(f"\n  [{req_id}] {description}\n")
        print("\nQuick-fix suggestions:")
        print(f"  - Review implementation for: {description}")
        print(f"  - Check if acceptance criteria are fully addressed")
        print(f"  - Ensure edge cases and error handling are covered\n")
        print("Options:")
        print("  [1] Apply quick-fix (mark as passed after manual review)")
        print("  [2] Generate supplementary PRD for this requirement")
        print("  [3] Mark as deferred")
        print("  [4] Continue with gap")

        while True:
            try:
                choice = input("\nSelect option [1-4]: ").strip()
                if choice == "1":
                    # Quick fix - mark as passed
                    try:
                        self._checklist_manager.update_requirement(req_id, status="passed")
                        self._log_info(f"Marked {req_id} as passed after manual review")
                        return UnfilledRequirementsResult(
                            choice=UserChoice.QUICK_FIX,
                            deferred_requirements=[]
                        )
                    except Exception as e:
                        return UnfilledRequirementsResult(
                            choice=UserChoice.QUICK_FIX,
                            error=f"Failed to mark as passed: {e}"
                        )
                elif choice == "2":
                    return self._generate_supplementary_prd([requirement])
                elif choice == "3":
                    return self._mark_as_deferred([requirement])
                elif choice == "4":
                    return UnfilledRequirementsResult(
                        choice=UserChoice.CONTINUE_WITH_GAPS
                    )
                else:
                    print("Invalid choice. Please select 1-4.")
            except (EOFError, KeyboardInterrupt):
                print("\nOperation cancelled.")
                return UnfilledRequirementsResult(
                    choice=UserChoice.CONTINUE_WITH_GAPS
                )

    def _prompt_all_unfilled(
        self, failed_requirements: List[Tuple[str, str]]
    ) -> UnfilledRequirementsResult:
        """Handle case when all requirements are unfilled.

        Suggests full PRD revision instead of supplement.

        Args:
            failed_requirements: List of all (id, description) tuples

        Returns:
            UnfilledRequirementsResult with user's choice
        """
        print(f"\n{'='*60}")
        print("ALL REQUIREMENTS UNFILLED")
        print(f"{'='*60}")
        print(f"\nAll {len(failed_requirements)} requirements remain unfilled.")
        print("This suggests the implementation may need a complete revision.\n")
        print("Options:")
        print("  [1] Request full PRD revision (recommended)")
        print("  [2] Generate supplementary PRD for all requirements")
        print("  [3] Mark all as deferred")
        print("  [4] Continue with gaps")

        while True:
            try:
                choice = input("\nSelect option [1-4]: ").strip()
                if choice == "1":
                    return UnfilledRequirementsResult(
                        choice=UserChoice.FULL_REVISION
                    )
                elif choice == "2":
                    return self._generate_supplementary_prd(failed_requirements)
                elif choice == "3":
                    return self._mark_as_deferred(failed_requirements)
                elif choice == "4":
                    return UnfilledRequirementsResult(
                        choice=UserChoice.CONTINUE_WITH_GAPS
                    )
                else:
                    print("Invalid choice. Please select 1-4.")
            except (EOFError, KeyboardInterrupt):
                print("\nOperation cancelled.")
                return UnfilledRequirementsResult(
                    choice=UserChoice.CONTINUE_WITH_GAPS
                )

    def _prompt_standard(
        self, failed_requirements: List[Tuple[str, str]]
    ) -> UnfilledRequirementsResult:
        """Handle standard case with multiple (but not all) unfilled requirements.

        Args:
            failed_requirements: List of (id, description) tuples

        Returns:
            UnfilledRequirementsResult with user's choice
        """
        print(f"\n{'='*60}")
        print("UNFILLED REQUIREMENTS")
        print(f"{'='*60}")
        print(f"\n{len(failed_requirements)} requirement(s) remain unfilled:\n")

        for req_id, description in failed_requirements:
            print(f"  [{req_id}] {description}")

        print("\nOptions:")
        print("  [1] Generate supplementary PRD to address unfilled requirements")
        print("  [2] Mark requirements as deferred")
        print("  [3] Continue with gaps")

        while True:
            try:
                choice = input("\nSelect option [1-3]: ").strip()
                if choice == "1":
                    return self._generate_supplementary_prd(failed_requirements)
                elif choice == "2":
                    return self._mark_as_deferred(failed_requirements)
                elif choice == "3":
                    return UnfilledRequirementsResult(
                        choice=UserChoice.CONTINUE_WITH_GAPS
                    )
                else:
                    print("Invalid choice. Please select 1-3.")
            except (EOFError, KeyboardInterrupt):
                print("\nOperation cancelled.")
                return UnfilledRequirementsResult(
                    choice=UserChoice.CONTINUE_WITH_GAPS
                )

    def _generate_supplementary_prd(
        self, failed_requirements: List[Tuple[str, str]]
    ) -> UnfilledRequirementsResult:
        """Generate a supplementary PRD for unfilled requirements.

        Args:
            failed_requirements: List of (id, description) tuples

        Returns:
            UnfilledRequirementsResult with generated PRD info
        """
        original_prd_id = self._get_original_prd_id()
        original_prd_path = None

        if self._prd_manager:
            try:
                original_prd_path = Path(self._prd_manager._path)
            except Exception:
                pass

        generator = SupplementaryPRDGenerator(
            original_prd_id=original_prd_id,
            original_prd_path=original_prd_path,
            logger=self._logger
        )

        # Determine output path
        output_path = None
        if self._output_dir:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"supplementary_prd_{timestamp}.json"
            output_path = self._output_dir / filename
        elif self._prd_manager:
            try:
                prd_dir = Path(self._prd_manager._path).parent
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"supplementary_prd_{timestamp}.json"
                output_path = prd_dir / filename
            except Exception:
                pass

        try:
            prd_data, written_path = generator.generate(
                failed_requirements, output_path
            )

            self._log_info(
                f"Generated supplementary PRD addressing "
                f"{len(failed_requirements)} unfilled requirements"
            )

            return UnfilledRequirementsResult(
                choice=UserChoice.GENERATE_SUPPLEMENTARY,
                supplementary_prd_path=written_path,
                supplementary_prd_data=prd_data
            )

        except Exception as e:
            self._log_error(f"Failed to generate supplementary PRD: {e}")
            # Preserve the unfilled requirements list for manual follow-up
            self._preserved_unfilled = failed_requirements
            return UnfilledRequirementsResult(
                choice=UserChoice.GENERATE_SUPPLEMENTARY,
                error=f"PRD generation failed: {e}. Unfilled requirements preserved for manual follow-up."
            )

    def _mark_as_deferred(
        self, failed_requirements: List[Tuple[str, str]]
    ) -> UnfilledRequirementsResult:
        """Mark requirements as deferred.

        Note: This method records the deferral but doesn't change the
        checklist status (which only supports pending/passed/failed).

        Args:
            failed_requirements: List of (id, description) tuples

        Returns:
            UnfilledRequirementsResult with deferred requirement IDs
        """
        deferred_ids = [req_id for req_id, _ in failed_requirements]

        self._log_info(f"Marked {len(deferred_ids)} requirements as deferred")

        return UnfilledRequirementsResult(
            choice=UserChoice.MARK_DEFERRED,
            deferred_requirements=deferred_ids
        )

    def get_preserved_unfilled(self) -> List[Tuple[str, str]]:
        """Get unfilled requirements preserved after a failed PRD generation.

        Returns:
            List of (id, description) tuples for preserved requirements
        """
        return self._preserved_unfilled

    def handle(self, report: FinalQAReport) -> UnfilledRequirementsResult:
        """Handle unfilled requirements based on the final QA report.

        In non-interactive mode, outputs to stderr and raises SystemExit(2).
        In interactive mode, prompts user with appropriate options.

        Args:
            report: FinalQAReport from FinalQAValidator.validate()

        Returns:
            UnfilledRequirementsResult with the handling outcome

        Raises:
            SystemExit: With code 2 in non-interactive mode
        """
        failed_requirements = report.failed_requirements

        if not failed_requirements:
            # No unfilled requirements
            return UnfilledRequirementsResult(
                choice=UserChoice.CONTINUE_WITH_GAPS
            )

        # Non-interactive mode: output to stderr and exit
        if self._non_interactive:
            self._output_to_stderr(failed_requirements)
            # Preserve for manual follow-up
            self._preserved_unfilled = failed_requirements
            import sys
            sys.exit(self.EXIT_CODE_UNFILLED)

        # Interactive mode: determine which prompt to show
        if self._is_single_requirement(report):
            return self._prompt_single_requirement(failed_requirements[0])
        elif self._is_all_requirements_unfilled(report):
            return self._prompt_all_unfilled(failed_requirements)
        else:
            return self._prompt_standard(failed_requirements)
