# ~/my_plugins/monitoring.py
from pyralph import Event, EventType  # Optional: for type hints

EVENTS = ["PHASE_START", "PHASE_END", "TASK_SUCCESS", "TASK_FAILURE"]
PRIORITY = 50
TIMEOUT = 10.0

def on_event(event: Event) -> None:
    """Monitor Ralph lifecycle events."""
    if event.event_type.name == "TASK_SUCCESS":
        print(f"Task {event.task_id} completed")
    elif event.event_type.name == "TASK_FAILURE":
        print(f"Task {event.task_id} failed: {event.error}")
    elif event.event_type.name == "PHASE_START":
        print(f"Starting {event.phase} phase")