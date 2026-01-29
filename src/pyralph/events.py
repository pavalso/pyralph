"""Event types and payload for the hook system.

This module defines the EventType enum representing all lifecycle events
that can be hooked, and the Event dataclass representing the immutable
payload passed to hooks.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional
import json


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

    # PRD Markdown events
    PRD_MD_START = auto()
    PRD_MD_SUCCESS = auto()
    PRD_MD_FAILURE = auto()

    # Exploration context events
    EXPLORATION_START = auto()
    EXPLORATION_SUCCESS = auto()
    EXPLORATION_FAILURE = auto()
    EXPLORATION_CONTEXT_LOADED = auto()
    EXPLORATION_CONTEXT_REUSED = auto()
    EXPLORATION_CONTEXT_CORRUPTED = auto()

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
    prd_md_path: Optional[str] = None
    exploration_context_path: Optional[str] = None
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
            "prd_md_path": self.prd_md_path,
            "exploration_context_path": self.exploration_context_path,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "issue_url": self.issue_url,
            "issues_count": self.issues_count,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict())
