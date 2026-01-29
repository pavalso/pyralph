import json
from datetime import datetime as dt

from pyralph.hooks import Event, EventType


class TestEvent:
    EVENTS = [
        "PHASE_START", "PHASE_END", "ARCHITECT_START", "ARCHITECT_SUCCESS", "ARCHITECT_FAILURE",
        "PLANNER_START", "PLANNER_SUCCESS", "PLANNER_FAILURE", "EXECUTE_START", "EXECUTE_END",
        "TASK_START", "TASK_SUCCESS", "TASK_FAILURE", "TASK_RETRY",
        "VERIFICATION_START", "VERIFICATION_SUCCESS", "VERIFICATION_FAILURE",
        "PRD_CREATED", "PRD_ARCHIVED", "ERROR",
        "EXPLORATION_START", "EXPLORATION_SUCCESS", "EXPLORATION_FAILURE",
        "EXPLORATION_CONTEXT_LOADED", "EXPLORATION_CONTEXT_REUSED", "EXPLORATION_CONTEXT_CORRUPTED",
        "PRD_MD_START", "PRD_MD_CONTEXT_READY", "PRD_MD_COMPLETE", "PRD_MD_FAILED",
    ]

    def test_event_types_exist(self):
        for name in self.EVENTS:
            assert hasattr(EventType, name)
        assert len(EventType) == 49

    def test_event_creation_serialization(self):
        event = Event(EventType.TASK_SUCCESS, phase="execute", task_id="T-001", metadata={"k": "v"})
        assert event.phase == "execute"
        dt.fromisoformat(event.timestamp)
        result = event.to_dict()
        assert result["event_type"] == "TASK_SUCCESS"
        parsed = json.loads(event.to_json())
        assert parsed["event_type"] == "TASK_SUCCESS"

    def test_all_event_types_serialize(self):
        for et in EventType:
            parsed = json.loads(Event(et).to_json())
            assert parsed["event_type"] == et.name
