import json
import pytest
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure your main script is named 'ralph.py'
from ralph import (
    MemoryManager,
    JsonUtils,
    Shell,
    GitUtils,
    RalphOrchestrator
)

# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def mock_config(tmp_path):
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
def mock_dependencies():
    with patch("ralph.Shell.check_dependencies"):
        yield

# ==============================================================================
# TESTS
# ==============================================================================

def test_json_parse_clean():
    text = '{"key": "value"}'
    assert JsonUtils.parse(text) == {"key": "value"}

def test_shell_run_timeout():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=1)
        stdout, stderr, code = Shell.run("sleep 10")
        assert "Timed Out" in stderr
        assert code == 1

def test_memory_structure_populated(mock_config):
    (mock_config.MEMORY_DIR / "arch.md").write_text("content")
    structure = MemoryManager.get_structure()
    assert "arch.md" in structure

def test_extract_test_command_from_file(mock_config):
    (mock_config.MEMORY_DIR / "meta.md").write_text("Test Command: `yarn test`")
    cmd = MemoryManager.extract_test_command()
    assert cmd == "yarn test"

# ==============================================================================
# GIT UTILITIES TESTS
# ==============================================================================

@patch("ralph.Shell.run")
def test_detect_default_branch_from_remote(MockShellRun):
    """Test detecting default branch from remote HEAD."""
    MockShellRun.return_value = ("ref: refs/remotes/origin/main\n", "", 0)
    branch = GitUtils.detect_default_branch()
    assert branch == "main"

@patch("ralph.Shell.run")
def test_detect_default_branch_master(MockShellRun):
    """Test detecting 'master' as default branch."""
    MockShellRun.return_value = ("ref: refs/remotes/origin/master\n", "", 0)
    branch = GitUtils.detect_default_branch()
    assert branch == "master"

@patch("ralph.Shell.run")
def test_detect_default_branch_fallback_to_current(MockShellRun):
    """Test fallback to current branch when remote HEAD fails."""
    MockShellRun.side_effect = [
        ("", "", 1),  # symbolic-ref fails
        ("develop\n", "", 0),  # current branch is develop
    ]
    branch = GitUtils.detect_default_branch()
    assert branch == "develop"

@patch("ralph.Shell.run")
def test_detect_default_branch_fallback_default(MockShellRun):
    """Test default fallback when both methods fail."""
    MockShellRun.return_value = ("", "", 1)
    branch = GitUtils.detect_default_branch()
    assert branch == "main"

@patch("ralph.Shell.run")
def test_detect_default_branch_detached_head(MockShellRun):
    """Test fallback when in detached HEAD state."""
    MockShellRun.side_effect = [
        ("", "", 1),  # symbolic-ref fails
        ("HEAD\n", "", 0),  # in detached HEAD state
    ]
    branch = GitUtils.detect_default_branch()
    assert branch == "main"

@patch("ralph.Shell.run")
def test_create_feature_branch_success(MockShellRun):
    """Test successful feature branch creation."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout base_branch success
        ("", "", 0),  # git checkout -b feature_branch success
    ]
    result = GitUtils.create_feature_branch("main", "feature/task-002")
    assert result is True

@patch("ralph.Shell.run")
def test_create_feature_branch_checkout_base_fails(MockShellRun):
    """Test feature branch creation when checkout base branch fails."""
    MockShellRun.return_value = ("", "error", 1)
    result = GitUtils.create_feature_branch("main", "feature/task-002")
    assert result is False

@patch("ralph.Shell.run")
def test_create_feature_branch_create_fails(MockShellRun):
    """Test feature branch creation when creating new branch fails."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout base_branch success
        ("", "error", 1),  # git checkout -b feature_branch fails
    ]
    result = GitUtils.create_feature_branch("main", "feature/task-002")
    assert result is False

# ==============================================================================
# ORCHESTRATOR TESTS
# ==============================================================================

@patch("ralph.ClaudeAgent")
def test_planner_retry_on_failure(MockAgentClass, mock_config):
    """Test that planner retries if agent returns garbage."""
    mock_agent_instance = MockAgentClass.return_value
    
    # 1. Fail (Garbage) -> 2. Success (JSON)
    mock_agent_instance.run.side_effect = [
        (True, "I am not sure what to do."), 
        (True, json.dumps({"featureBranch": "main", "userStories": [{"id": "1"}]}))
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.run_planner("Build something")
    
    assert mock_config.PRD_FILE.exists()
    assert mock_agent_instance.run.call_count == 2

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_verification_success(MockAgentClass, MockShell, mock_config):
    """Test full flow: Agent writes code -> Tests Pass -> Commit -> Archive."""

    # Setup PRD
    prd = {
        "featureBranch": "feature/task-002",
        "userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence: detect_branch, checkout base, checkout -b feature, test, commit
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/task-002
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0)  # git commit
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # FIX: Check ARCHIVE directory (PRD_FILE is gone/moved)
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1

    archived_data = json.loads(archives[0].read_text())
    assert archived_data["userStories"][0]["status"] == "completed"

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_verification_fail(MockAgentClass, MockShell, mock_config):
    """Test flow: Agent claims success -> Tests Fail -> Loop Retries."""

    prd = {
        "featureBranch": "feature/task-002",
        "userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence: detect_branch, checkout base, checkout -b feature, test (fail), test (pass), commit
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/task-002
        ("stdout", "Tests Failed", 1),  # test fails
        ("stdout", "Tests Passed", 0),  # test passes on retry
        ("stdout", "", 0)  # git commit
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # Check Archive
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1
    archived_data = json.loads(archives[0].read_text())
    assert archived_data["userStories"][0]["status"] == "completed"

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_creates_feature_branch(MockAgentClass, MockShell, mock_config):
    """Test that feature branch is created before task execution."""

    prd = {
        "featureBranch": "feature/task-002",
        "userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence: git symbolic-ref (detect branch), git checkout main, git checkout -b feature/task-002, pytest, git commit
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/task-002
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0)  # git commit
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # Verify the sequence included branch creation calls
    shell_calls = [call[0][0] for call in MockShell.call_args_list]
    assert any("git checkout main" in call for call in shell_calls)
    assert any("git checkout -b feature/task-002" in call for call in shell_calls)