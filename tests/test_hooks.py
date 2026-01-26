import threading
from unittest.mock import MagicMock

from pyralph.hooks import (
    Event, EventType, HookManager, PythonHook, ExecutableHook, FunctionHook
)

from .helpers import TempHooksTestCase


class TestHookManager(TempHooksTestCase):
    def test_enable_disable(self):
        assert self.manager.is_enabled
        self.manager.disable()
        assert not self.manager.is_enabled
        self.manager.enable()
        assert self.manager.is_enabled

    def test_discover(self):
        assert self.manager.discover() == 0
        self.create_hook_file("_private.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        assert self.manager.discover() == 0
        self.create_hook_file("valid.py", 'EVENTS = ["TASK_START"]\ndef on_event(e): pass')
        assert HookManager(self.hooks_dir).discover() == 1

    def test_discover_rejects_invalid(self):
        for name, content in [("no_ev.py", 'def on_event(e): pass'), ("no_h.py", 'EVENTS = ["TASK_START"]')]:
            self.tearDown()
            self.setUp()
            self.create_hook_file(name, content)
            assert HookManager(self.hooks_dir, MagicMock()).discover() == 0

    def test_emit_priority_order(self):
        import tests.test_hooks as test_module
        test_module.execution_order = []
        self.create_hook_file("high.py", '''
EVENTS = ["TASK_START"]
PRIORITY = 200
def on_event(e):
    import tests.test_hooks as mod
    mod.execution_order.append("high")
''')
        self.create_hook_file("low.py", '''
EVENTS = ["TASK_START"]
PRIORITY = 10
def on_event(e):
    import tests.test_hooks as mod
    mod.execution_order.append("low")
''')
        self.manager.discover()
        self.manager.emit(Event(EventType.TASK_START))
        assert test_module.execution_order == ["low", "high"]

    def test_set_enabled_hooks(self):
        self.manager.set_enabled_hooks(None)
        assert self.manager.is_hook_enabled("any")
        self.manager.set_enabled_hooks([])
        assert not self.manager.is_hook_enabled("any")
        self.manager.set_enabled_hooks(["a"])
        assert self.manager.is_hook_enabled("a")
        assert not self.manager.is_hook_enabled("b")


class TestHookModification(TempHooksTestCase):
    def test_modifying_hook(self):
        self.create_hook_file("mod.py", '''
from pyralph.hooks import Event
EVENTS = ["TASK_START"]
MODIFIES_DATA = True
def on_event(e):
    return Event(event_type=e.event_type, task_id="MOD-" + (e.task_id or ""))
''')
        self.manager.discover()
        result = self.manager.emit(Event(EventType.TASK_START, task_id="T-001"))
        assert result.task_id == "MOD-T-001"

    def test_non_modifying_ignored(self):
        self.create_hook_file("obs.py", '''
from pyralph.hooks import Event
EVENTS = ["TASK_START"]
def on_event(e):
    return Event(event_type=e.event_type, task_id="IGNORED")
''')
        self.manager.discover()
        result = self.manager.emit(Event(EventType.TASK_START, task_id="ORIG"))
        assert result.task_id == "ORIG"


class TestHookTypes(TempHooksTestCase):
    def test_python_hook_parse_events(self):
        assert PythonHook._parse_events(["TASK_START", "task_success"]) == {EventType.TASK_START, EventType.TASK_SUCCESS}
        assert PythonHook._parse_events(["INVALID"]) == set()

    def test_executable_hook(self):
        hook = ExecutableHook(self.temp_path/"t.sh", {EventType.TASK_START}, priority=50, timeout=10.0, modifies_data=True)
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


class TestHookBehavior(TempHooksTestCase):
    def test_exception_isolation(self):
        log = []
        self.manager.register_hook("fail", lambda e: (_ for _ in ()).throw(RuntimeError()), ["TASK_START"], priority=10)
        self.manager.register_hook("ok", lambda e: log.append("ok"), ["TASK_START"], priority=20)
        self.manager.emit(Event(EventType.TASK_START))
        assert "ok" in log

    def test_concurrent_emit(self):
        results = []
        lock = threading.Lock()
        self.manager.register_hook("h", lambda e: (lock.acquire(), results.append(e.task_id), lock.release()), ["TASK_START"])
        threads = [threading.Thread(target=lambda i=i: self.manager.emit(Event(EventType.TASK_START, task_id=f"T-{i}")))
                  for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
