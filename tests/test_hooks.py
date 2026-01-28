import threading
from unittest.mock import MagicMock

import pytest

from pyralph.hooks import (
    Event, EventType, HookManager, PythonHook, ExecutableHook, FunctionHook
)


class TestHookManager:
    def test_enable_disable(self, temp_hooks):
        assert temp_hooks.manager.is_enabled
        temp_hooks.manager.disable()
        assert not temp_hooks.manager.is_enabled
        temp_hooks.manager.enable()
        assert temp_hooks.manager.is_enabled

    def test_discover(self, temp_hooks):
        assert temp_hooks.manager.discover() == 0
        temp_hooks.create_hook_file("_private.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        assert temp_hooks.manager.discover() == 0
        temp_hooks.create_hook_file("valid.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        assert HookManager(temp_hooks.hooks_dir).discover() == 1

    @pytest.mark.parametrize("hook_name,content,description", [
        ("no_events.py", 'def on_event(e): pass', "hook with only handler, no EVENTS list"),
        ("no_handler.py", 'EVENTS = ["TASK_START"]', "hook with only EVENTS list, no handler"),
    ])
    def test_discover_rejects_invalid(self, tmp_path, hook_name, content, description):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_file = hooks_dir / hook_name
        hook_file.write_text(content, encoding='utf-8')
        manager = HookManager(hooks_dir, MagicMock())
        assert manager.discover() == 0, f"Expected 0 hooks discovered for {description}"

    def test_emit_priority_order(self, temp_hooks):
        import tests.test_hooks as test_module
        test_module.execution_order = []
        temp_hooks.create_hook_file("high.py", '''
EVENTS = ["TASK_START"]
PRIORITY = 200
def on_event(e):
    import tests.test_hooks as mod
    mod.execution_order.append("high")
''')
        temp_hooks.create_hook_file("low.py", '''
EVENTS = ["TASK_START"]
PRIORITY = 10
def on_event(e):
    import tests.test_hooks as mod
    mod.execution_order.append("low")
''')
        temp_hooks.manager.discover()
        temp_hooks.manager.emit(Event(EventType.TASK_START))
        assert test_module.execution_order == ["low", "high"]

    def test_set_enabled_hooks(self, temp_hooks):
        temp_hooks.manager.set_enabled_hooks(None)
        assert temp_hooks.manager.is_hook_enabled("any")
        temp_hooks.manager.set_enabled_hooks([])
        assert not temp_hooks.manager.is_hook_enabled("any")
        temp_hooks.manager.set_enabled_hooks(["a"])
        assert temp_hooks.manager.is_hook_enabled("a")
        assert not temp_hooks.manager.is_hook_enabled("b")


class TestHookModification:
    def test_modifying_hook(self, temp_hooks):
        temp_hooks.create_hook_file("mod.py", '''
from pyralph.hooks import Event
EVENTS = ["TASK_START"]
MODIFIES_DATA = True
def on_event(e):
    return Event(event_type=e.event_type, task_id="MOD-" + (e.task_id or ""))
''')
        temp_hooks.manager.discover()
        result = temp_hooks.manager.emit(Event(EventType.TASK_START, task_id="T-001"))
        assert result.task_id == "MOD-T-001"

    def test_non_modifying_ignored(self, temp_hooks):
        temp_hooks.create_hook_file("obs.py", '''
from pyralph.hooks import Event
EVENTS = ["TASK_START"]
def on_event(e):
    return Event(event_type=e.event_type, task_id="IGNORED")
''')
        temp_hooks.manager.discover()
        result = temp_hooks.manager.emit(Event(EventType.TASK_START, task_id="ORIG"))
        assert result.task_id == "ORIG"


class TestHookTypes:
    def test_python_hook_parse_events(self):
        assert PythonHook._parse_events(["TASK_START", "task_success"]) == {EventType.TASK_START, EventType.TASK_SUCCESS}
        assert PythonHook._parse_events(["INVALID"]) == set()

    def test_executable_hook(self, tmp_path):
        hook = ExecutableHook(tmp_path / "t.sh", {EventType.TASK_START}, priority=50, timeout=10.0, modifies_data=True)
        assert hook.name == "t.sh"
        assert hook.priority == 50
        assert hook.modifies_data

    def test_executable_hook_parse_modified(self):
        orig = Event(EventType.TASK_START, task_id="T-001", phase="execute")
        result = ExecutableHook._parse_modified_event('{"task_id": "MOD"}', orig)
        assert result.task_id == "MOD"
        assert ExecutableHook._parse_modified_event("bad", orig) is None

    def test_function_hook(self):
        called = []
        hook = FunctionHook("t", lambda e: called.append(e.task_id), {EventType.TASK_START})
        hook.execute(Event(EventType.TASK_START, task_id="T-001"))
        assert called == ["T-001"]

    def test_function_hook_modification(self):
        hook = FunctionHook("t", lambda e: Event(e.event_type, task_id="MOD"),
                           {EventType.TASK_START}, modifies_data=True)
        result = hook.execute(Event(EventType.TASK_START, task_id="ORIG"))
        assert result.task_id == "MOD"


class TestHookBehavior:
    def test_exception_isolation(self, temp_hooks):
        log = []
        temp_hooks.manager.register_hook("fail", lambda e: (_ for _ in ()).throw(RuntimeError()), ["TASK_START"], priority=10)
        temp_hooks.manager.register_hook("ok", lambda e: log.append("ok"), ["TASK_START"], priority=20)
        temp_hooks.manager.emit(Event(EventType.TASK_START))
        assert "ok" in log

    def test_concurrent_emit(self, temp_hooks):
        results = []
        lock = threading.Lock()
        temp_hooks.manager.register_hook("h", lambda e: (lock.acquire(), results.append(e.task_id), lock.release()), ["TASK_START"])
        threads = [threading.Thread(target=lambda i=i: temp_hooks.manager.emit(Event(EventType.TASK_START, task_id=f"T-{i}")))
                  for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
