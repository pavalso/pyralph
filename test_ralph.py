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
    prd = {"userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}]}
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    
    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")
    
    MockShell.return_value = ("Tests Passed", "", 0)

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
    
    prd = {"userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}]}
    mock_config.PRD_FILE.write_text(json.dumps(prd))
    
    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")
    
    # 1. Fail -> 2. Success -> 3. Git Commit
    MockShell.side_effect = [
        ("stdout", "Tests Failed", 1), 
        ("stdout", "Tests Passed", 0),
        ("stdout", "", 0)
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()
    
    # Check Archive
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1
    archived_data = json.loads(archives[0].read_text())
    assert archived_data["userStories"][0]["status"] == "completed"