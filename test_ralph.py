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
    with patch("ralph.Shell.check_dependencies"), patch("ralph.Logger.info"):
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

@patch("ralph.Shell.run")
def test_create_task_branch_success(MockShellRun):
    """Test successful task branch creation from PRD branch."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "", 0),  # git checkout -b task_branch success
    ]
    success, branch_name = GitUtils.create_task_branch("feature/git-workflow-prd", "TASK-003", "Implement user story branches")
    assert success is True
    assert branch_name == "task/task-003-implement-user-story-branches"

@patch("ralph.Shell.run")
def test_create_task_branch_slug_generation(MockShellRun):
    """Test slug generation with various special characters."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "", 0),  # git checkout -b task_branch success
    ]
    success, branch_name = GitUtils.create_task_branch("feature/git-workflow-prd", "TASK-001", "Implement: Default Branch (Detection)")
    assert success is True
    # Verify slug handles special characters
    assert branch_name == "task/task-001-implement-default-branch-detection"

@patch("ralph.Shell.run")
def test_create_task_branch_checkout_prd_fails(MockShellRun):
    """Test task branch creation when checkout to PRD branch fails."""
    MockShellRun.return_value = ("", "error", 1)
    success, branch_name = GitUtils.create_task_branch("feature/git-workflow-prd", "TASK-003", "Implement user story branches")
    assert success is False
    assert branch_name == "task/task-003-implement-user-story-branches"

@patch("ralph.Shell.run")
def test_create_task_branch_create_fails(MockShellRun):
    """Test task branch creation when creating new branch fails."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "error", 1),  # git checkout -b task_branch fails
    ]
    success, branch_name = GitUtils.create_task_branch("feature/git-workflow-prd", "TASK-003", "Implement user story branches")
    assert success is False
    assert branch_name == "task/task-003-implement-user-story-branches"

@patch("ralph.Shell.run")
def test_create_task_branch_naming_convention(MockShellRun):
    """Test that task branches follow naming convention: task/{TASK-ID}-{description-slug}"""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "", 0),  # git checkout -b task_branch success
    ]

    # Test with different task IDs and descriptions
    test_cases = [
        ("TASK-001", "Default branch detection", "task/task-001-default-branch-detection"),
        ("TASK-002", "Feature branch creation", "task/task-002-feature-branch-creation"),
        ("TASK-003", "User story branches", "task/task-003-user-story-branches"),
    ]

    for task_id, description, expected_branch in test_cases:
        MockShellRun.side_effect = [
            ("", "", 0),  # git checkout prd_branch success
            ("", "", 0),  # git checkout -b task_branch success
        ]
        success, branch_name = GitUtils.create_task_branch("feature/git-workflow-prd", task_id, description)
        assert success is True
        assert branch_name == expected_branch

@patch("ralph.Shell.run")
def test_merge_task_to_prd_success(MockShellRun):
    """Test successful merge of task branch to PRD branch with --no-ff."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "", 0),  # git merge --no-ff success
    ]
    result = GitUtils.merge_task_to_prd("feature/git-workflow-prd", "task/task-004-test", "TASK-004", "Test merge commit")
    assert result is True

@patch("ralph.Shell.run")
def test_merge_task_to_prd_checkout_fails(MockShellRun):
    """Test merge when checkout to PRD branch fails."""
    MockShellRun.return_value = ("", "error", 1)
    result = GitUtils.merge_task_to_prd("feature/git-workflow-prd", "task/task-004-test", "TASK-004", "Test merge commit")
    assert result is False

@patch("ralph.Shell.run")
def test_merge_task_to_prd_merge_fails(MockShellRun):
    """Test merge when merge command fails."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "conflict", 1),  # git merge fails
    ]
    result = GitUtils.merge_task_to_prd("feature/git-workflow-prd", "task/task-004-test", "TASK-004", "Test merge commit")
    assert result is False

@patch("ralph.Shell.run")
def test_merge_task_to_prd_commit_message_format(MockShellRun):
    """Test that merge commit message follows correct format: 'Merge {TASK-ID}: {description}'"""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout prd_branch success
        ("", "", 0),  # git merge --no-ff success
    ]
    GitUtils.merge_task_to_prd("feature/git-workflow-prd", "task/task-004-implement-merge", "TASK-004", "Implement merge-commit strategy for user story branches to PRD branch")

    # Verify the merge command contains the correct message format
    merge_call = MockShellRun.call_args_list[1][0][0]
    assert "git merge --no-ff" in merge_call
    assert 'Merge TASK-004: Implement merge-commit strategy for user story branches to PRD branch' in merge_call

@patch("ralph.Shell.run")
def test_merge_prd_to_default_success(MockShellRun):
    """Test successful merge of PRD branch to default branch with --no-ff."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout default_branch success
        ("", "", 0),  # git merge --no-ff success
    ]
    result = GitUtils.merge_prd_to_default("main", "feature/git-workflow-prd")
    assert result is True

@patch("ralph.Shell.run")
def test_merge_prd_to_default_checkout_fails(MockShellRun):
    """Test merge when checkout to default branch fails."""
    MockShellRun.return_value = ("", "error", 1)
    result = GitUtils.merge_prd_to_default("main", "feature/git-workflow-prd")
    assert result is False

@patch("ralph.Shell.run")
def test_merge_prd_to_default_merge_fails(MockShellRun):
    """Test merge when merge command fails."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout default_branch success
        ("", "conflict", 1),  # git merge fails
    ]
    result = GitUtils.merge_prd_to_default("main", "feature/git-workflow-prd")
    assert result is False

@patch("ralph.Shell.run")
def test_merge_prd_to_default_commit_message_format(MockShellRun):
    """Test that PRD merge commit message follows correct format: 'Merge PRD: {featureBranch}'"""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout default_branch success
        ("", "", 0),  # git merge --no-ff success
    ]
    GitUtils.merge_prd_to_default("main", "feature/git-workflow-prd")

    # Verify the merge command contains the correct message format
    merge_call = MockShellRun.call_args_list[1][0][0]
    assert "git merge --no-ff" in merge_call
    assert 'Merge PRD: feature/git-workflow-prd' in merge_call

@patch("ralph.Shell.run")
def test_merge_prd_to_default_uses_no_ff_flag(MockShellRun):
    """Test that merge uses --no-ff flag to preserve feature branch history."""
    MockShellRun.side_effect = [
        ("", "", 0),  # git checkout default_branch success
        ("", "", 0),  # git merge --no-ff success
    ]
    GitUtils.merge_prd_to_default("master", "feature/my-feature")

    # Verify the merge command uses --no-ff flag
    merge_call = MockShellRun.call_args_list[1][0][0]
    assert "--no-ff" in merge_call

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
    """Test full flow: Agent writes code -> Tests Pass -> Merge -> Commit -> Archive."""

    # Setup PRD
    prd = {
        "featureBranch": "feature/task-002",
        "userStories": [{"id": "T1", "description": "desc", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence: detect_branch, checkout base, checkout -b feature, checkout feature (for task branch), checkout -b task branch, test, checkout for merge, merge, commit, detect for PRD merge, checkout main, merge PRD to default
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/task-002
        ("", "", 0),  # git checkout feature/task-002 (for task branch)
        ("", "", 0),  # git checkout -b task/t1-desc
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/task-002 (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch (for PRD merge)
        ("", "", 0),  # git checkout main (for PRD merge)
        ("", "", 0)  # git merge --no-ff feature/task-002 (PRD merge)
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

    # Sequence: detect, checkout base, -b feature, checkout feature (for task), -b task, test (fail), test (pass), checkout for merge, merge, commit, detect for PRD, checkout main, merge PRD
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/task-002
        ("", "", 0),  # git checkout feature/task-002 (for task branch)
        ("", "", 0),  # git checkout -b task/t1-desc
        ("stdout", "Tests Failed", 1),  # test fails
        ("stdout", "Tests Passed", 0),  # test passes on retry
        ("", "", 0),  # git checkout feature/task-002 (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch (for PRD merge)
        ("", "", 0),  # git checkout main (for PRD merge)
        ("", "", 0)  # git merge --no-ff feature/task-002 (PRD merge)
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

    # Sequence: detect, checkout main, -b feature, checkout feature (for task), -b task, pytest, checkout for merge, merge, commit, detect for PRD, checkout main, merge PRD
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/task-002
        ("", "", 0),  # git checkout feature/task-002 (for task branch)
        ("", "", 0),  # git checkout -b task/t1-desc
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/task-002 (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch (for PRD merge)
        ("", "", 0),  # git checkout main (for PRD merge)
        ("", "", 0)  # git merge --no-ff feature/task-002 (PRD merge)
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # Verify the sequence included branch creation calls
    shell_calls = [call[0][0] for call in MockShell.call_args_list]
    assert any("git checkout main" in call for call in shell_calls)
    assert any("git checkout -b feature/task-002" in call for call in shell_calls)

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_creates_task_branch_from_prd(MockAgentClass, MockShell, mock_config):
    """Test that task branch is created from PRD branch during task execution."""

    prd = {
        "featureBranch": "feature/git-workflow-prd",
        "userStories": [{"id": "TASK-003", "description": "Implement user story branches", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence:
    # 1. detect_default_branch
    # 2. git checkout main (for feature branch)
    # 3. git checkout -b feature/git-workflow-prd (create feature branch)
    # 4. git checkout feature/git-workflow-prd (for task branch - checkout PRD)
    # 5. git checkout -b task/task-003-implement-user-story-branches (create task branch)
    # 6. pytest
    # 7. git checkout feature/git-workflow-prd (for merge)
    # 8. git merge --no-ff
    # 9. git commit
    # 10. detect_default_branch (for PRD merge)
    # 11. git checkout main (for PRD merge)
    # 12. git merge --no-ff feature/git-workflow-prd (PRD merge)
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/git-workflow-prd
        ("", "", 0),  # git checkout feature/git-workflow-prd (for task branch)
        ("", "", 0),  # git checkout -b task/task-003-implement-user-story-branches
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/git-workflow-prd (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch (for PRD merge)
        ("", "", 0),  # git checkout main (for PRD merge)
        ("", "", 0)  # git merge --no-ff feature/git-workflow-prd (PRD merge)
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # Verify the sequence included task branch creation calls
    shell_calls = [call[0][0] for call in MockShell.call_args_list]
    assert any("git checkout feature/git-workflow-prd" in call for call in shell_calls)
    assert any("git checkout -b task/task-003-implement-user-story-branches" in call for call in shell_calls)

    # Verify task branch is created from PRD branch (not from main/default)
    # The PRD branch should be checked out before creating the task branch
    prd_checkout_idx = None
    task_branch_creation_idx = None
    for i, call in enumerate(shell_calls):
        if "git checkout -b task/task-003-implement-user-story-branches" in call:
            task_branch_creation_idx = i
        if "git checkout feature/git-workflow-prd" in call and "checkout -b" not in call and prd_checkout_idx is None:
            prd_checkout_idx = i

    # PRD branch should be checked out before task branch creation
    assert task_branch_creation_idx is not None
    assert prd_checkout_idx is not None
    assert prd_checkout_idx < task_branch_creation_idx

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_task_merges_task_to_prd(MockAgentClass, MockShell, mock_config):
    """Test that task branch is merged to PRD branch after successful verification."""

    prd = {
        "featureBranch": "feature/git-workflow-prd",
        "userStories": [{"id": "TASK-004", "description": "Implement merge-commit strategy", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence:
    # 1. detect_default_branch
    # 2. git checkout main (for feature branch)
    # 3. git checkout -b feature/git-workflow-prd (create feature branch)
    # 4. git checkout feature/git-workflow-prd (for task branch)
    # 5. git checkout -b task/task-004-implement-merge-commit-strategy (create task branch)
    # 6. pytest (verification)
    # 7. git checkout feature/git-workflow-prd (for merge)
    # 8. git merge --no-ff task/task-004-... (merge to PRD)
    # 9. git commit
    # 10. detect_default_branch (for PRD merge)
    # 11. git checkout main (for PRD merge)
    # 12. git merge --no-ff feature/git-workflow-prd (PRD merge)
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/git-workflow-prd
        ("", "", 0),  # git checkout feature/git-workflow-prd (for task branch)
        ("", "", 0),  # git checkout -b task/task-004-implement-merge-commit-strategy
        ("Tests Passed", "", 0),  # pytest (verification)
        ("", "", 0),  # git checkout feature/git-workflow-prd (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch (for PRD merge)
        ("", "", 0),  # git checkout main (for PRD merge)
        ("", "", 0)  # git merge --no-ff feature/git-workflow-prd (PRD merge)
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # Verify merge commands were called
    shell_calls = [call[0][0] for call in MockShell.call_args_list]
    merge_commands = [call for call in shell_calls if "git merge --no-ff" in call]
    assert len(merge_commands) > 0

    # Verify merge command format
    merge_cmd = merge_commands[0]
    assert "git merge --no-ff" in merge_cmd
    assert "Merge TASK-004: Implement merge-commit strategy" in merge_cmd

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_execute_loop_merges_prd_to_default_after_all_tasks(MockAgentClass, MockShell, mock_config):
    """Test that PRD branch is merged to default branch after all tasks complete."""

    prd = {
        "featureBranch": "feature/git-workflow-prd",
        "userStories": [
            {"id": "TASK-001", "description": "Task one", "acceptanceCriteria": [], "status": "pending"},
            {"id": "TASK-002", "description": "Task two", "acceptanceCriteria": [], "status": "pending"}
        ]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Sequence for TASK-001:
    # 1. detect_default_branch
    # 2. git checkout main (for feature branch)
    # 3. git checkout -b feature/git-workflow-prd (create feature branch)
    # 4. git checkout feature/git-workflow-prd (for task branch)
    # 5. git checkout -b task/task-001-task-one (create task branch)
    # 6. pytest (verification)
    # 7. git checkout feature/git-workflow-prd (for merge task to PRD)
    # 8. git merge --no-ff task/task-001-... (merge task to PRD)
    # 9. git commit
    #
    # Sequence for TASK-002 (feature branch already exists):
    # 10. detect_default_branch (redetect for next task)
    # 11. git checkout feature/git-workflow-prd (feature already exists, just checkout)
    # 12. git checkout -b task/task-002-task-two (create new task branch)
    # 13. pytest (verification)
    # 14. git checkout feature/git-workflow-prd (for merge)
    # 15. git merge --no-ff task/task-002-... (merge to PRD)
    # 16. git commit
    #
    # After all tasks complete:
    # 17. detect_default_branch (for PRD merge)
    # 18. git checkout main (for PRD merge)
    # 19. git merge --no-ff feature/git-workflow-prd (merge PRD to default)

    MockShell.side_effect = [
        # TASK-001
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main (for feature branch)
        ("", "", 0),  # git checkout -b feature/git-workflow-prd (create feature branch)
        ("", "", 0),  # git checkout feature/git-workflow-prd (for task)
        ("", "", 0),  # git checkout -b task/task-001-task-one
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/git-workflow-prd (for merge)
        ("", "", 0),  # git merge --no-ff task/task-001-task-one
        ("", "", 0),  # git commit
        # TASK-002
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main (for feature branch - already exists, but we checkout base anyway)
        ("", "", 0),  # git checkout -b feature/git-workflow-prd (already exists, this will fail but we continue)
        ("", "", 0),  # git checkout feature/git-workflow-prd (for task branch)
        ("", "", 0),  # git checkout -b task/task-002-task-two
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/git-workflow-prd (for merge)
        ("", "", 0),  # git merge --no-ff task/task-002-task-two
        ("", "", 0),  # git commit
        # PRD merge to default
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git merge --no-ff feature/git-workflow-prd
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.execute_loop()

    # Verify all merge commands were called
    shell_calls = [call[0][0] for call in MockShell.call_args_list]
    prd_merge_commands = [call for call in shell_calls if "Merge PRD:" in call]
    assert len(prd_merge_commands) > 0

    # Verify the PRD merge command format
    prd_merge_cmd = prd_merge_commands[0]
    assert "git merge --no-ff" in prd_merge_cmd
    assert "Merge PRD: feature/git-workflow-prd" in prd_merge_cmd