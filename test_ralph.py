import json
import pytest
import subprocess
import builtins
import types
from unittest.mock import patch

from ralph import (
    MemoryManager,
    JsonUtils,
    Shell,
    RalphOrchestrator,
    Logger,
    ClaudeAgent,
    get_version,
    main as ralph_main,
)

# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_config(tmp_path):
    # Patch global CONF so the code writes into tmp_path
    with patch("ralph.CONF") as mock_conf:
        mock_conf.BASE_DIR = tmp_path
        mock_conf.ROOT_DIR = tmp_path / ".ralph"
        mock_conf.MEMORY_DIR = mock_conf.ROOT_DIR / "memory"
        mock_conf.ARCHIVE_DIR = mock_conf.ROOT_DIR / "archive"
        mock_conf.PRD_FILE = mock_conf.ROOT_DIR / "prd.json"
        mock_conf.PROGRESS_FILE = mock_conf.ROOT_DIR / "progress.txt"
        mock_conf.LOG_FILE = mock_conf.ROOT_DIR / "ralph_log.txt"
        mock_conf.MAX_RETRIES = 2
        mock_conf.TIMEOUT_SECONDS = 1
        mock_conf.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        mock_conf.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        yield mock_conf

@pytest.fixture(autouse=True)
def mock_dependencies(request):
    """
    By default patch Shell.check_dependencies and Logger.info to avoid real env.
    If test is marked 'real_check_deps', don't patch check_dependencies (to hit
    sys.exit branches).
    """
    if request.node.get_closest_marker("real_check_deps"):
        with patch("ralph.Logger.info"):
            yield
        return
    with patch("ralph.Shell.check_dependencies"), patch("ralph.Logger.info"):
        yield

# ============================================================================
# JSON parsing
# ============================================================================

@pytest.mark.parametrize("text, expect", [
    ("```json\n{\"a\": 1}\n```", {"a": 1}),
    ("{ \"a\": 1, // comment\n \"b\": 2 }", {"a": 1, "b": 2}),
    ("noise before {\"k\": 3} and after", {"k": 3}),
])
def test_jsonutils_parse_variants(text, expect):
    assert JsonUtils.parse(text) == expect

# ============================================================================
# Shell
# ============================================================================

def test_shell_run_timeout():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
        stdout, stderr, code = Shell.run("sleep 10")
        assert "Timed Out" in stderr
        assert code == 1

@patch("subprocess.run", side_effect=OSError("boom"))
def test_shell_run_other_exception(_mock_run):
    out, err, code = Shell.run("echo hi")
    assert code == 1 and "boom" in err

def test_get_file_tree_fallback(mock_config):
    # Force 'tree' command fallback
    with patch("ralph.Shell.run", return_value=("", "", 1)):
        (mock_config.BASE_DIR / "file1.txt").write_text("x")
        (mock_config.BASE_DIR / ".ralph").mkdir(exist_ok=True)
        out = Shell.get_file_tree()
        assert "file1.txt" in out

@pytest.mark.real_check_deps
def test_shell_check_dependencies_claude_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None if cmd == "claude" else "/usr/bin/git")
    with pytest.raises(SystemExit) as e:
        Shell.check_dependencies()
    assert e.value.code == 1

@pytest.mark.real_check_deps
def test_shell_check_dependencies_git_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    with pytest.raises(SystemExit) as e:
        Shell.check_dependencies()
    assert e.value.code == 1

# ============================================================================
# MemoryManager
# ============================================================================

def test_validate_memory_when_dir_missing_returns_default(mock_config):
    # Remove directory to hit early return
    if mock_config.MEMORY_DIR.exists():
        for p in mock_config.MEMORY_DIR.glob("*"):
            if p.is_dir():
                import shutil; shutil.rmtree(p)
            else:
                p.unlink()
        mock_config.MEMORY_DIR.rmdir()
    result = MemoryManager.validate_memory()
    assert result["valid"] is True and result["total"] == 0

def test_validate_memory_with_readable_files(mock_config):
    (mock_config.MEMORY_DIR / "arch.md").write_text("---\ntype: wiki\n---\n# Architecture")
    (mock_config.MEMORY_DIR / "notes.txt").write_text("Important notes here")
    result = MemoryManager.validate_memory()
    assert result["valid"] is True
    assert result["corrupted"] == []
    assert result["empty"] == []
    assert result["total"] == 2

def test_validate_memory_with_empty_files(mock_config):
    (mock_config.MEMORY_DIR / "arch.md").write_text("---\ntype: wiki\n---\n# Architecture")
    (mock_config.MEMORY_DIR / "empty.md").write_text("")
    (mock_config.MEMORY_DIR / "whitespace.md").write_text(" \n \n ")
    result = MemoryManager.validate_memory()
    assert result["valid"] is False
    assert result["corrupted"] == []
    assert len(result["empty"]) == 2
    assert result["total"] == 3

@patch("pathlib.Path.read_text")
def test_validate_memory_with_unreadable_files(MockReadText, mock_config):
    (mock_config.MEMORY_DIR / "good.md").write_text("Valid content")
    (mock_config.MEMORY_DIR / "bad.md").write_text("This file will be unreadable")
    MockReadText.side_effect = ["Valid content", OSError("Permission denied")]
    result = MemoryManager.validate_memory()
    assert result["valid"] is False
    assert len(result["corrupted"]) > 0

def test_validate_memory_ignores_hidden_files(mock_config):
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")
    (mock_config.MEMORY_DIR / ".hidden").write_text("")
    result = MemoryManager.validate_memory()
    assert result["valid"] is True
    assert result["total"] == 1

def test_validate_memory_ignores_directories(mock_config):
    (mock_config.MEMORY_DIR / "subdir").mkdir()
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")
    result = MemoryManager.validate_memory()
    assert result["total"] == 1

def test_memory_get_structure_handles_relative_to_error(mock_config, tmp_path):
    (mock_config.MEMORY_DIR / "f.md").write_text("x")
    other_root = tmp_path / "other"; other_root.mkdir()
    # Force relative_to to fail
    mock_config.BASE_DIR = other_root
    s = MemoryManager.get_structure()
    assert isinstance(s, str)

# Command extraction

def test_extract_test_command_from_file(mock_config):
    (mock_config.MEMORY_DIR / "meta.md").write_text("Test Command: `yarn test`")
    cmd = MemoryManager.extract_test_command()
    assert cmd == "yarn test"

def test_extract_test_command_npm(mock_config):
    (mock_config.BASE_DIR / "package.json").write_text("{}")
    assert MemoryManager.extract_test_command() == "npm test"

def test_extract_test_command_default_pytest(mock_config):
    assert MemoryManager.extract_test_command() == "pytest"

def test_extract_test_command_skips_unreadable_file(monkeypatch, mock_config):
    from pathlib import Path as _Path
    good = mock_config.MEMORY_DIR / "good.md"
    bad = mock_config.MEMORY_DIR / "bad.md"
    good.write_text("Test Command: `ok`")
    bad.write_text("do not read")
    orig = _Path.read_text
    def fake_read(self, *a, **k):
        if self.name == "bad.md":
            raise OSError("boom")
        return orig(self, *a, **k)
    monkeypatch.setattr(_Path, "read_text", fake_read)
    assert MemoryManager.extract_test_command() == "ok"

# ============================================================================
# Logger
# ============================================================================

def test_logger_debug_respects_verbose(capsys):
    Logger.set_verbose(False)
    Logger.debug("hidden")
    out = capsys.readouterr().out
    assert out == ""
    Logger.set_verbose(True)
    Logger.debug("shown", color="GREEN")
    out = capsys.readouterr().out
    assert "[DEBUG] shown" in out
    Logger.set_verbose(False)

def test_logger_file_log_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(builtins, "open", boom)
    # Should not raise; prints a warning to stdout
    Logger.file_log("x", "PROMPT", "TAG")

# ============================================================================
# ClaudeAgent
# ============================================================================

# helper to simulate CompletedProcess-like object

def _cp(stdout, stderr, code):
    m = types.SimpleNamespace()
    m.stdout, m.stderr, m.returncode = stdout, stderr, code
    return m

def test_claudeagent_verbose_path(monkeypatch, capsys):
    Logger.set_verbose(True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _cp("hello", "", 0))
    ok, msg = ClaudeAgent().run("p", "TAG")
    assert ok is True and msg == "hello"
    out = capsys.readouterr().out
    assert "CLAUDE PROMPT" in out and "CLAUDE RESPONSE" in out
    Logger.set_verbose(False)

def test_claudeagent_error_verbose_debug(monkeypatch, capsys):
    Logger.set_verbose(True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _cp("OUT", "ERR", 2))
    ok, _ = ClaudeAgent().run("p", "TAG")
    assert ok is False
    out = capsys.readouterr().out
    assert "CLAUDE ERROR" in out
    Logger.set_verbose(False)

def test_claudeagent_exception_verbose(monkeypatch, capsys):
    Logger.set_verbose(True)
    def raiser(*a, **k):
        raise RuntimeError("oops")
    monkeypatch.setattr("subprocess.run", raiser)
    ok, msg = ClaudeAgent().run("p", "TAG")
    assert ok is False and "oops" in msg
    out = capsys.readouterr().out
    assert "CLAUDE EXCEPTION" in out
    Logger.set_verbose(False)

# ============================================================================
# Orchestrator & execution loop
# ============================================================================

@patch("ralph.ClaudeAgent")
def test_planner_retry_on_failure(MockAgentClass, mock_config):
    mock_agent = MockAgentClass.return_value
    mock_agent.run.side_effect = [
        (True, "I am not sure what to do."),
        (True, json.dumps({"featureBranch": "main", "userStories": [{"id": "1"}]})),
    ]
    orch = RalphOrchestrator()
    orch.run_planner("Build something")
    assert mock_config.PRD_FILE.exists()
    assert mock_agent.run.call_count == 2

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_verification_success(MockAgentClass, MockShell, mock_config):
    prd = {
        "id": "P1", "description": "desc",
        "userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}],
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    MockAgentClass.return_value.run.return_value = (True, "STATUS: SUCCESS")
    # Only the test command needs to return success
    MockShell.return_value = ("Tests Passed", "", 0)
    RalphOrchestrator().execute_loop()
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1
    archived_data = json.loads(archives[0].read_text())
    assert archived_data["userStories"][0]["status"] == "completed"

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_verification_fail_then_pass(MockAgentClass, MockShell, mock_config):
    prd = {
        "id": "P1", "description": "desc",
        "userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}],
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    MockAgentClass.return_value.run.return_value = (True, "STATUS: SUCCESS")
    MockShell.side_effect = [
        ("stdout", "Tests Failed", 1),
        ("stdout", "Tests Passed", 0),
    ]
    RalphOrchestrator().execute_loop()
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1
    archived_data = json.loads(archives[0].read_text())
    assert archived_data["userStories"][0]["status"] == "completed"

@patch("ralph.ClaudeAgent.run", return_value=(True, "STATUS: SUCCESS"))
@patch("ralph.Shell.run", return_value=("ok", "", 0))
def test_execute_loop_skips_completed_then_executes(_MockShell, _MockAgent, mock_config):
    prd = {
        "id": "P1",
        "description": "desc",
        "userStories": [
            {"id": "DONE", "description": "d", "acceptanceCriteria": [], "status": "completed"},
            {"id": "T2", "description": "todo", "acceptanceCriteria": [], "status": "pending"},
        ],
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    RalphOrchestrator().execute_loop()
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text())
    t2 = next(x for x in archived["userStories"] if x["id"] == "T2")
    assert t2["status"] == "completed"

@patch("ralph.Shell.run", return_value=("ok", "", 0))
@patch("ralph.MemoryManager.extract_test_command", return_value="pytest")
@patch("ralph.ClaudeAgent.run", side_effect=[(False, "no"), (True, "STATUS: SUCCESS")])
def test_execute_task_reads_prompt_md_and_replaces(MockAgent, _cmd, _shell, mock_config):
    (mock_config.BASE_DIR / "prompt.md").write_text(
        "PRD={{PRD_ID}} DESC={{PRD_DESCRIPTION}} TID={{TASK_ID}} TD={{TASK_DESCRIPTION}} CMD={{TEST_CMD}}"
    )
    prd = {
        "id": "P1", "description": "desc",
        "userStories": [{"id": "T1", "description": "do", "acceptanceCriteria": [], "status": "pending"}],
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    RalphOrchestrator().execute_loop()

@patch("ralph.ClaudeAgent.run", return_value=(False, "boom"))
def test_execute_task_cli_crash_records_and_retries(_MockAgent, mock_config):
    prd = {"id": "P1", "description": "d", "userStories": [{"id": "T1", "description": "do", "acceptanceCriteria": [], "status": "pending"}]}
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    with pytest.raises(SystemExit):
        RalphOrchestrator().execute_loop()
    s = mock_config.PROGRESS_FILE.read_text()
    assert "CLI Crash" in s

@patch("ralph.ClaudeAgent.run", return_value=(True, "STATUS: SUCCESS"))
@patch("ralph.Shell.run", return_value=("stdout", "Failed", 1))
def test_execute_task_hallucinated_success_until_exit(_shell, _agent, mock_config):
    prd = {"id": "P1", "description": "d", "userStories": [{"id": "T1", "description": "do", "acceptanceCriteria": [], "status": "pending"}]}
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    with pytest.raises(SystemExit):
        RalphOrchestrator().execute_loop()

@patch("ralph.ClaudeAgent.run", return_value=(True, "nope"))
def test_execute_task_agent_reported_failure_branch(_agent, mock_config):
    prd = {"id": "P1", "description": "d", "userStories": [{"id": "T1", "description": "do", "acceptanceCriteria": [], "status": "pending"}]}
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    with pytest.raises(SystemExit):
        RalphOrchestrator().execute_loop()
    s = mock_config.PROGRESS_FILE.read_text()
    assert "Agent Reported Failure" in s

# Architect / planner failure branches

@patch("ralph.ClaudeAgent.run", return_value=(False, "nope"))
def test_run_architect_fails_when_agent_fails(_MockAgent, mock_config):
    orch = RalphOrchestrator()
    with pytest.raises(SystemExit) as e:
        orch.run_architect("build X")
    assert e.value.code == 1

@patch("ralph.ClaudeAgent.run", return_value=(True, "ok"))
def test_run_architect_fails_when_memory_stays_empty(_MockAgent, mock_config):
    orch = RalphOrchestrator()
    # ensure dir exists but empty
    for p in list(mock_config.MEMORY_DIR.glob("*")):
        if p.is_file():
            p.unlink()
    with pytest.raises(SystemExit):
        orch.run_architect("build X")

@patch("ralph.ClaudeAgent.run", return_value=(True, "not json"))
def test_run_planner_gives_up_after_three_attempts(_MockAgent, mock_config):
    orch = RalphOrchestrator()
    with pytest.raises(SystemExit) as e:
        orch.run_planner("Build Y")
    assert e.value.code == 1

# record_failure utility

def test_record_failure_writes_progress(mock_config):
    orch = RalphOrchestrator()
    orch._record_failure(0, "reason", "detail")
    s = mock_config.PROGRESS_FILE.read_text()
    assert "Attempt 1 Failed: reason" in s and "detail" in s

# Validate on startup branches

@patch("ralph.MemoryManager.validate_memory", return_value={"valid": True, "corrupted": [], "empty": [], "total": 2})
def test_validate_on_startup_valid_branch_hits_debug(_MockValidate, mock_config):
    Logger.set_verbose(True)
    with patch("ralph.Logger.debug") as dbg:
        orch = RalphOrchestrator()
        (mock_config.MEMORY_DIR / "a.md").write_text("x")
        orch._validate_memory_on_startup()
        assert dbg.call_count >= 1
    Logger.set_verbose(False)

@patch("ralph.MemoryManager.validate_memory", return_value={"valid": True, "corrupted": [], "empty": [], "total": 0})
def test_validate_memory_on_startup_total_zero_returns_early(_MockValidate, mock_config):
    (mock_config.MEMORY_DIR / "x.md").write_text("x")
    RalphOrchestrator()._validate_memory_on_startup()

@patch("ralph.MemoryManager.validate_memory", return_value={"valid": False, "corrupted": ["a.md", "b.md"], "empty": [], "total": 2})
@patch("ralph.Logger.info")
def test_validate_memory_on_startup_logs_corrupted(MockLog, _MockValidate, mock_config):
    (mock_config.MEMORY_DIR / "x.md").write_text("x")
    RalphOrchestrator()._validate_memory_on_startup()
    calls = " ".join(str(c) for c in MockLog.call_args_list)
    assert "corrupted" in calls and "a.md" in calls and "b.md" in calls

# ============================================================================
# Prompt helper (parametrized consolidation)
# ============================================================================

@pytest.mark.parametrize("raw, expected", [
    ("y", True), ("n", False), ("Y", True), (" n ", False),
])
@patch("ralph.Shell.run")
def test_prompt_user_for_phase_variants(_MockShell, mock_config, raw, expected):
    with patch("builtins.input", return_value=raw):
        assert RalphOrchestrator()._prompt_user_for_phase("Test") is expected

# ============================================================================
# Start phase flows (consolidated)
# ============================================================================

@patch("ralph.Shell.get_file_tree", return_value="")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_start_architect_phase_only(MockInput, MockAgentClass, _get_tree, mock_config):
    mock_agent = MockAgentClass.return_value
    def run_side_effect(prompt, role):
        if role == "ARCHITECT":
            (mock_config.MEMORY_DIR / "architecture.md").write_text("---\ntype: wiki\n---\n# Arch")
            return (True, '{}')
        return (True, '{}')
    mock_agent.run.side_effect = run_side_effect
    MockInput.return_value = "Build a test application"
    RalphOrchestrator().start(phase="architect", accept_all=False)
    assert any(mock_config.MEMORY_DIR.iterdir())

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_start_execute_phase_only_accept_all(MockAgentClass, MockShell, mock_config):
    MockAgentClass.return_value.run.return_value = (True, "STATUS: SUCCESS")
    (mock_config.MEMORY_DIR / "architecture.md").write_text("ok")
    prd = {"id": "P1", "description": "desc", "userStories": [{"id": "TASK-001", "description": "Test task", "acceptanceCriteria": [], "status": "pending"}]}
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    MockShell.return_value = ("Tests Passed", "", 0)
    RalphOrchestrator().start(phase="execute", accept_all=True)
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1

# Guard rails

def test_start_planner_without_memory_exits(mock_config):
    for p in list(mock_config.MEMORY_DIR.iterdir()):
        if p.is_file(): p.unlink()
    with pytest.raises(SystemExit) as e:
        RalphOrchestrator().start(phase="planner", accept_all=False)
    assert e.value.code == 1

def test_start_execute_without_prd_exits(mock_config):
    if mock_config.PRD_FILE.exists():
        mock_config.PRD_FILE.unlink()
    with pytest.raises(SystemExit) as e:
        RalphOrchestrator().start(phase="execute", accept_all=True)
    assert e.value.code == 1

def test_start_with_unknown_phase_exits(mock_config):
    with pytest.raises(SystemExit) as e:
        RalphOrchestrator().start(phase="unknown", accept_all=True)
    assert e.value.code == 1

# ============================================================================
# Version & CLI
# ============================================================================

def test_get_version_happy_path(monkeypatch, tmp_path):
    import ralph as ralph_mod
    fake_file = tmp_path / "ralph.py"
    fake_file.write_text("# here")
    (tmp_path / "pyproject.toml").write_text('version = "1.2.3"')
    monkeypatch.setattr(ralph_mod, "__file__", str(fake_file))
    assert get_version() == "1.2.3"

def test_get_version_unknown(monkeypatch, tmp_path):
    import ralph as ralph_mod
    fake_file = tmp_path / "ralph.py"
    fake_file.write_text("# here")
    monkeypatch.setattr(ralph_mod, "__file__", str(fake_file))
    assert get_version() == "unknown"

@patch("ralph.RalphOrchestrator.start")
def test_main_parses_args_and_calls_start(MockStart, monkeypatch):
    monkeypatch.setattr("sys.argv", ["ralph", "execute", "--accept-all"])
    ralph_main()
    MockStart.assert_called_once_with(phase="execute", accept_all=True)