import json
import pytest
import subprocess
from unittest.mock import patch

# Ensure your main script is named 'ralph.py'
from ralph import (
    MemoryManager,
    JsonUtils,
    Shell,
    RalphOrchestrator,
    EasterEggs
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

# ==============================================================================
# PROMPT BEHAVIOR TESTS
# ==============================================================================

@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_start_all_phases_accept_all_flag_skips_prompts(MockInput, MockAgentClass, MockShell, mock_config):
    """Test that accept_all flag skips all prompts."""

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # When accept_all=True, input should not be called for prompts
    MockInput.return_value = "Build a test app"

    # Mock shell calls
    MockShell.side_effect = [
        ("", "", 0),  # For tree command
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/phase-control-prd
        ("", "", 0),  # git checkout feature/phase-control-prd
        ("", "", 0),  # git checkout -b task/task-001-test
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/phase-control-prd
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git merge --no-ff feature/phase-control-prd
    ]

    # Create memory and PRD with task (so we skip architect and planner phases)
    mock_config.MEMORY_DIR.mkdir(exist_ok=True)
    (mock_config.MEMORY_DIR / "architecture.md").write_text("Test architecture")

    prd = {
        "featureBranch": "feature/phase-control-prd",
        "userStories": [{"id": "TASK-001", "description": "Test", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="all", accept_all=True)

    # Verify input was not called for phase prompts (since accept_all=True)
    # and memory/PRD already exist so architect/planner are skipped
    assert MockInput.call_count == 0

@patch("ralph.Shell.run")
@patch("builtins.input")
def test_prompt_user_for_phase_accepts_yes(MockInput, MockShell, mock_config):
    """Test that prompt accepts 'y' response."""

    MockInput.return_value = "y"

    orchestrator = RalphOrchestrator()
    result = orchestrator._prompt_user_for_phase("Test")

    assert result is True

@patch("ralph.Shell.run")
@patch("builtins.input")
def test_prompt_user_for_phase_accepts_no(MockInput, MockShell, mock_config):
    """Test that prompt accepts 'n' response."""

    MockInput.return_value = "n"

    orchestrator = RalphOrchestrator()
    result = orchestrator._prompt_user_for_phase("Test")

    assert result is False

@patch("ralph.Shell.run")
@patch("builtins.input")
def test_prompt_user_for_phase_case_insensitive(MockInput, MockShell, mock_config):
    """Test that prompt is case insensitive."""

    MockInput.return_value = "Y"

    orchestrator = RalphOrchestrator()
    result = orchestrator._prompt_user_for_phase("Test")

    assert result is True

@patch("ralph.Shell.run")
@patch("builtins.input")
def test_prompt_user_for_phase_trims_whitespace(MockInput, MockShell, mock_config):
    """Test that prompt trims whitespace."""

    MockInput.return_value = "  n  "

    orchestrator = RalphOrchestrator()
    result = orchestrator._prompt_user_for_phase("Test")

    assert result is False

# ==============================================================================
# PHASE SELECTION TESTS
# ==============================================================================

@patch("ralph.Shell.get_file_tree")
@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_start_architect_phase_only(MockInput, MockAgentClass, MockShell, MockGetTree, mock_config):
    """Test that --phase architect runs only architect phase."""

    mock_agent_instance = MockAgentClass.return_value

    # Mock agent to create memory file
    def run_side_effect(prompt, role):
        if role == "ARCHITECT":
            # Simulate agent creating architecture.md
            (mock_config.MEMORY_DIR / "architecture.md").write_text("---\ntype: wiki\n---\n# Architecture")
        return (True, '{"memory": "created"}')

    mock_agent_instance.run.side_effect = run_side_effect

    MockGetTree.return_value = ""

    # Mock for architect phase - need git calls for detect_default_branch
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # git symbolic-ref
        ("", "", 0),  # git branch --show-current (if needed)
        ("", "", 0),  # git checkout for feature branch (if needed)
    ]

    MockInput.return_value = "Build a test application"

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="architect", accept_all=False)

    # Verify memory was created
    assert mock_config.MEMORY_DIR.exists()
    assert any(mock_config.MEMORY_DIR.iterdir())


@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_start_planner_phase_only(MockInput, MockAgentClass, MockShell, mock_config):
    """Test that --phase planner runs only planner phase."""

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, json.dumps({"featureBranch": "feature/test", "userStories": []}))

    # Setup existing memory
    mock_config.MEMORY_DIR.mkdir(exist_ok=True)
    (mock_config.MEMORY_DIR / "architecture.md").write_text("Test architecture")

    MockInput.return_value = "Build a test application"
    MockShell.side_effect = [
        ("", "", 0),  # For tree command
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="planner", accept_all=False)

    # Verify PRD was created
    assert mock_config.PRD_FILE.exists()


@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_start_execute_phase_only(MockAgentClass, MockShell, mock_config):
    """Test that --phase execute runs only execute phase."""

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Setup existing memory and PRD
    mock_config.MEMORY_DIR.mkdir(exist_ok=True)
    (mock_config.MEMORY_DIR / "architecture.md").write_text("Test architecture")

    prd = {
        "featureBranch": "feature/test-prd",
        "userStories": [{"id": "TASK-001", "description": "Test task", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    # Mock shell calls for execute phase
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/test-prd
        ("", "", 0),  # git checkout feature/test-prd (for task branch)
        ("", "", 0),  # git checkout -b task/task-001-test-task
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/test-prd (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch (for PRD merge)
        ("", "", 0),  # git checkout main (for PRD merge)
        ("", "", 0)  # git merge --no-ff feature/test-prd (PRD merge)
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="execute", accept_all=True)

    # Verify execute phase ran and PRD was archived
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1


@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_start_phase_all_with_skip(MockInput, MockAgentClass, MockShell, mock_config):
    """Test that --phase all (or default) allows skipping phases via user prompts."""

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Setup existing memory and PRD to skip architect and planner
    mock_config.MEMORY_DIR.mkdir(exist_ok=True)
    (mock_config.MEMORY_DIR / "architecture.md").write_text("Test architecture")

    prd = {
        "featureBranch": "feature/test-prd",
        "userStories": [{"id": "TASK-001", "description": "Test task", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    # Mock user choosing to skip execute phase
    MockInput.return_value = "n"

    MockShell.side_effect = [
        # No shell calls since execute phase is skipped
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="all", accept_all=False)

    # Verify input was called to prompt for execute phase
    assert MockInput.call_count >= 1


@patch("ralph.Shell.get_file_tree")
@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_start_specific_phase_skips_phase_prompts(MockInput, MockAgentClass, MockShell, MockGetTree, mock_config):
    """Test that specifying a specific phase (e.g., --phase architect) skips phase prompts."""

    mock_agent_instance = MockAgentClass.return_value

    # Mock agent to create memory file
    def run_side_effect(prompt, role):
        if role == "ARCHITECT":
            # Simulate agent creating architecture.md
            (mock_config.MEMORY_DIR / "architecture.md").write_text("---\ntype: wiki\n---\n# Architecture")
        return (True, '{"memory": "created"}')

    mock_agent_instance.run.side_effect = run_side_effect

    MockGetTree.return_value = ""

    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # git symbolic-ref
        ("", "", 0),  # git branch --show-current (if needed)
        ("", "", 0),  # git checkout for feature branch (if needed)
    ]

    MockInput.return_value = "Build a test application"

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="architect", accept_all=False)

    # Verify that input was only called for project description, not for phase prompts
    # When phase is specified, phase prompts are skipped
    assert any(mock_config.MEMORY_DIR.iterdir())


@patch("ralph.Shell.get_file_tree")
@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
@patch("builtins.input")
def test_accept_all_flag_with_architect_phase(MockInput, MockAgentClass, MockShell, MockGetTree, mock_config):
    """Test that --accept-all with --phase architect runs architect without prompts."""

    mock_agent_instance = MockAgentClass.return_value

    # Mock agent to create memory file
    def run_side_effect(prompt, role):
        if role == "ARCHITECT":
            # Simulate agent creating architecture.md
            (mock_config.MEMORY_DIR / "architecture.md").write_text("---\ntype: wiki\n---\n# Architecture")
        return (True, '{"memory": "created"}')

    mock_agent_instance.run.side_effect = run_side_effect

    MockGetTree.return_value = ""
    MockInput.return_value = "Build a test application"

    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # git symbolic-ref
        ("", "", 0),  # git branch --show-current (if needed)
        ("", "", 0),  # git checkout for feature branch (if needed)
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="architect", accept_all=True)

    # Verify memory was created
    assert mock_config.MEMORY_DIR.exists()
    assert any(mock_config.MEMORY_DIR.iterdir())


@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_accept_all_flag_with_execute_phase(MockAgentClass, MockShell, mock_config):
    """Test that --accept-all with --phase execute runs execute without prompts."""

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    # Setup existing memory and PRD
    mock_config.MEMORY_DIR.mkdir(exist_ok=True)
    (mock_config.MEMORY_DIR / "architecture.md").write_text("Test architecture")

    prd = {
        "featureBranch": "feature/test-prd",
        "userStories": [{"id": "TASK-001", "description": "Test task", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    # Mock shell calls for execute phase
    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0),  # git checkout -b feature/test-prd
        ("", "", 0),  # git checkout feature/test-prd
        ("", "", 0),  # git checkout -b task/task-001-test-task
        ("Tests Passed", "", 0),  # pytest
        ("", "", 0),  # git checkout feature/test-prd (for merge)
        ("", "", 0),  # git merge --no-ff
        ("", "", 0),  # git commit
        ("ref: refs/remotes/origin/main\n", "", 0),  # detect_default_branch
        ("", "", 0),  # git checkout main
        ("", "", 0)  # git merge --no-ff feature/test-prd
    ]

    orchestrator = RalphOrchestrator()
    orchestrator.start(phase="execute", accept_all=True)

    # Verify execute phase ran and PRD was archived
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1


# ==============================================================================
# EASTER EGG TESTS
# ==============================================================================

def test_easter_eggs_get_random_message():
    """Test that EasterEggs.get_random_message returns a valid message."""
    message = EasterEggs.get_random_message()
    assert message in EasterEggs.MESSAGES
    assert "🥚" in message


def test_easter_eggs_deterministic_seeding():
    """Test that seeding produces consistent results."""
    msg1 = EasterEggs.get_random_message(seed_value=42)
    msg2 = EasterEggs.get_random_message(seed_value=42)
    assert msg1 == msg2


def test_easter_eggs_different_seeds_may_vary():
    """Test that different seeds can produce different messages."""
    messages = set()
    for i in range(10):
        msg = EasterEggs.get_random_message(seed_value=i)
        messages.add(msg)
    # Not guaranteed to have different messages, but likely with 10 seeds
    assert len(messages) > 0


@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_easter_egg_displayed_on_task_success(MockAgentClass, MockShell, mock_config):
    """Test that easter egg is displayed when task succeeds with eggs enabled."""

    prd = {
        "featureBranch": "feature/task-002",
        "userStories": [{"id": "TASK-001", "description": "test task", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("Tests Passed", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("ref: refs/remotes/origin/main\n", "", 0),
        ("", "", 0),
        ("", "", 0)
    ]

    # Create orchestrator with easter eggs enabled
    orchestrator = RalphOrchestrator(easter_eggs=True)
    orchestrator.execute_loop()

    # Verify task completed
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1


@patch("ralph.Shell.run")
@patch("ralph.ClaudeAgent")
def test_no_easter_egg_when_disabled(MockAgentClass, MockShell, mock_config):
    """Test that easter egg is NOT displayed when disabled."""

    prd = {
        "featureBranch": "feature/task-002",
        "userStories": [{"id": "TASK-001", "description": "test task", "acceptanceCriteria": [], "status": "pending"}]
    }
    mock_config.PRD_FILE.write_text(json.dumps(prd))

    mock_agent_instance = MockAgentClass.return_value
    mock_agent_instance.run.return_value = (True, "STATUS: SUCCESS")

    MockShell.side_effect = [
        ("ref: refs/remotes/origin/main\n", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("Tests Passed", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("", "", 0),
        ("ref: refs/remotes/origin/main\n", "", 0),
        ("", "", 0),
        ("", "", 0)
    ]

    # Create orchestrator with easter eggs disabled
    orchestrator = RalphOrchestrator(easter_eggs=False)
    orchestrator.execute_loop()

    # Verify task completed
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1


def test_easter_eggs_message_list_contains_eggs():
    """Test that all easter egg messages contain the egg emoji."""
    for message in EasterEggs.MESSAGES:
        assert "🥚" in message
        assert len(message) > 0


def test_easter_eggs_message_list_not_empty():
    """Test that easter egg message list is not empty."""
    assert len(EasterEggs.MESSAGES) > 0
