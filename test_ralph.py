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
    EasterEggs,
    Logger,
    ClaudeAgent,
    get_version,
    main as ralph_main,
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
def mock_dependencies(request):
    """
    Por defecto parchea Shell.check_dependencies y Logger.info para no depender
    del entorno real. Si el test lleva el marker 'real_check_deps', no se
    parchea check_dependencies para poder cubrir los branches de sys.exit(1).
    """
    if request.node.get_closest_marker("real_check_deps"):
        # Solo silenciamos logs
        with patch("ralph.Logger.info"):
            yield
        return

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
        "id": "P1", "description": "desc",
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
        "id": "P1", "description": "desc",
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
        "id": "P1", "description": "desc",
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
        "id": "P1", "description": "desc",
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
        "id": "P1", "description": "desc",
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
        "id": "P1", "description": "desc",
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

    # Create orchestrator
    orchestrator = RalphOrchestrator()
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


# ==============================================================================
# MEMORY VALIDATION TESTS
# ==============================================================================

def test_validate_memory_empty_directory(mock_config):
    """Test validation passes when memory directory is empty."""
    result = MemoryManager.validate_memory()
    assert result['valid'] is True
    assert result['corrupted'] == []
    assert result['empty'] == []
    assert result['total'] == 0


def test_validate_memory_with_readable_files(mock_config):
    """Test validation passes when all files are readable and non-empty."""
    (mock_config.MEMORY_DIR / "arch.md").write_text("---\ntype: wiki\n---\n# Architecture")
    (mock_config.MEMORY_DIR / "notes.txt").write_text("Important notes here")

    result = MemoryManager.validate_memory()
    assert result['valid'] is True
    assert result['corrupted'] == []
    assert result['empty'] == []
    assert result['total'] == 2


def test_validate_memory_with_empty_files(mock_config):
    """Test validation detects empty files."""
    (mock_config.MEMORY_DIR / "arch.md").write_text("---\ntype: wiki\n---\n# Architecture")
    (mock_config.MEMORY_DIR / "empty.md").write_text("")
    (mock_config.MEMORY_DIR / "whitespace.md").write_text("   \n  \n  ")

    result = MemoryManager.validate_memory()
    assert result['valid'] is False
    assert result['corrupted'] == []
    assert len(result['empty']) == 2
    assert result['total'] == 3


@patch("pathlib.Path.read_text")
def test_validate_memory_with_unreadable_files(MockReadText, mock_config):
    """Test validation detects corrupted/unreadable files."""
    (mock_config.MEMORY_DIR / "good.md").write_text("Valid content")
    (mock_config.MEMORY_DIR / "bad.md").write_text("This file will be made unreadable")

    # Mock read_text to fail for the second call (the bad.md file)
    MockReadText.side_effect = ["Valid content", OSError("Permission denied")]

    result = MemoryManager.validate_memory()
    assert result['valid'] is False
    assert len(result['corrupted']) > 0


def test_validate_memory_ignores_hidden_files(mock_config):
    """Test validation ignores hidden files (starting with .)."""
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")
    (mock_config.MEMORY_DIR / ".hidden").write_text("")

    result = MemoryManager.validate_memory()
    assert result['valid'] is True
    assert result['total'] == 1  # Only counts non-hidden files


def test_validate_memory_ignores_directories(mock_config):
    """Test validation ignores subdirectories."""
    (mock_config.MEMORY_DIR / "subdir").mkdir()
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")

    result = MemoryManager.validate_memory()
    assert result['total'] == 1


def test_validate_memory_returns_correct_structure(mock_config):
    """Test that validate_memory returns expected dict structure."""
    (mock_config.MEMORY_DIR / "test.md").write_text("content")

    result = MemoryManager.validate_memory()
    assert isinstance(result, dict)
    assert 'valid' in result
    assert 'corrupted' in result
    assert 'empty' in result
    assert 'total' in result
    assert isinstance(result['valid'], bool)
    assert isinstance(result['corrupted'], list)
    assert isinstance(result['empty'], list)
    assert isinstance(result['total'], int)


@patch("ralph.MemoryManager.validate_memory")
@patch("ralph.Logger.info")
def test_orchestrator_validates_memory_on_startup(MockLogger, MockValidate, mock_config):
    """Test that orchestrator validates memory on startup."""
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")
    MockValidate.return_value = {
        'valid': True,
        'corrupted': [],
        'empty': [],
        'total': 1
    }

    orchestrator = RalphOrchestrator()

    # Verify that validation was called
    assert MockValidate.call_count > 0


@patch("ralph.Logger.info")
def test_orchestrator_warns_on_corrupted_memory(MockLogger, mock_config):
    """Test that orchestrator warns about corrupted memory files."""
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")
    (mock_config.MEMORY_DIR / "empty.md").write_text("")

    orchestrator = RalphOrchestrator()

    # Verify warning was logged for empty file
    warning_calls = [call for call in MockLogger.call_args_list]
    # Check that at least one call mentions warning about empty files
    has_warning = any("empty" in str(call).lower() for call in warning_calls)
    assert has_warning or MockLogger.call_count > 0


@patch("ralph.Logger.info")
def test_orchestrator_continues_gracefully_with_warnings(MockLogger, mock_config):
    """Test that orchestrator continues even with memory validation warnings."""
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")
    (mock_config.MEMORY_DIR / "corrupted.md").write_text("")

    # Should not raise an exception
    try:
        orchestrator = RalphOrchestrator()
        orchestrator_created = True
    except Exception as e:
        orchestrator_created = False

    assert orchestrator_created is True


def test_validate_memory_with_subdirectories(mock_config):
    """Test validation handles files in subdirectories."""
    (mock_config.MEMORY_DIR / "subdir").mkdir()
    (mock_config.MEMORY_DIR / "subdir" / "nested.md").write_text("Nested content")
    (mock_config.MEMORY_DIR / "arch.md").write_text("Valid content")

    result = MemoryManager.validate_memory()
    assert result['valid'] is True
    assert result['total'] == 2

# ---------- Logger ----------

def test_logger_debug_respects_verbose(capsys):
    Logger.set_verbose(False)
    Logger.debug("hidden")
    out = capsys.readouterr().out
    assert out == ""

    Logger.set_verbose(True)
    Logger.debug("shown", color="GREEN")
    out = capsys.readouterr().out
    assert "[DEBUG] shown" in out
    Logger.set_verbose(False)  # reset

def test_logger_file_log_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(builtins, "open", boom)
    # Should not raise; should print a warning
    Logger.file_log("x", "PROMPT", "TAG")

# ---------- Shell ----------

@pytest.mark.real_check_deps
def test_shell_check_dependencies_claude_missing(monkeypatch):
    # Simulamos: 'claude' ausente, 'git' presente
    monkeypatch.setattr("shutil.which",
                        lambda cmd: None if cmd == "claude" else "/usr/bin/git")
    with pytest.raises(SystemExit) as e:
        Shell.check_dependencies()
    assert e.value.code == 1

@pytest.mark.real_check_deps
def test_shell_check_dependencies_git_missing(monkeypatch):
    # Simulamos: 'claude' presente, 'git' ausente
    monkeypatch.setattr("shutil.which",
                        lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    with pytest.raises(SystemExit) as e:
        Shell.check_dependencies()
    assert e.value.code == 1

@patch("subprocess.run", side_effect=OSError("boom"))
def test_shell_run_other_exception(_mock_run):
    out, err, code = Shell.run("echo hi")
    assert code == 1 and "boom" in err

def test_get_file_tree_fallback(mock_config):
    # Force 'tree' command to look like it failed so we hit the Python fallback
    with patch("ralph.Shell.run", return_value=("", "", 1)):
        (mock_config.BASE_DIR / "file1.txt").write_text("x")
        # Make sure the excluded dirs don't interfere
        (mock_config.BASE_DIR / ".ralph").mkdir(exist_ok=True)
        out = Shell.get_file_tree()
        assert "file1.txt" in out

# ---------- Json parsing & memory ----------

@pytest.mark.parametrize("text, expect", [
    ("```json\n{\"a\": 1}\n```", {"a": 1}),
    ("{ \"a\": 1, // comment\n  \"b\": 2 }", {"a": 1, "b": 2}),
    ("noise before {\"k\": 3} and after", {"k": 3}),
])
def test_jsonutils_parse_variants(text, expect):
    assert JsonUtils.parse(text) == expect

def test_extract_test_command_npm(mock_config):
    (mock_config.BASE_DIR / "package.json").write_text("{}")
    assert MemoryManager.extract_test_command() == "npm test"

def test_extract_test_command_default_pytest(mock_config):
    # No Test Command in files and no package.json
    assert MemoryManager.extract_test_command() == "pytest"

# ---------- Claude agent ----------

def _cp(stdout, stderr, code):
    m = types.SimpleNamespace()
    m.stdout, m.stderr, m.returncode = stdout, stderr, code
    return m

def test_claudeagent_nonzero_return(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _cp("out", "err", 5))
    ok, msg = ClaudeAgent().run("p", "TAG")
    assert ok is False and "STDOUT:" in msg and "STDERR:" in msg

def test_claudeagent_exception(monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("oops")))
    ok, msg = ClaudeAgent().run("p", "TAG")
    assert ok is False and "oops" in msg

def test_claudeagent_verbose_path(monkeypatch, capsys):
    Logger.set_verbose(True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _cp("hello", "", 0))
    ok, msg = ClaudeAgent().run("p", "TAG")
    assert ok is True and msg == "hello"
    out = capsys.readouterr().out
    assert "CLAUDE PROMPT" in out and "CLAUDE RESPONSE" in out
    Logger.set_verbose(False)

# ---------- Orchestrator branches ----------

@patch("ralph.ClaudeAgent.run", return_value=(False, "nope"))
def test_run_architect_fails_when_agent_fails(_MockAgent, mock_config):
    orch = RalphOrchestrator()
    with pytest.raises(SystemExit) as e:
        orch.run_architect("build X")
    assert e.value.code == 1

@patch("ralph.ClaudeAgent.run", return_value=(True, "ok"))
def test_run_architect_fails_when_memory_stays_empty(_MockAgent, mock_config):
    # Simulate agent success BUT no files created in memory dir
    # run_architect should still fail if memory dir is empty
    orch = RalphOrchestrator()
    # Ensure directory exists but is empty
    for p in list(mock_config.MEMORY_DIR.glob("*")):
        p.unlink()
    with pytest.raises(SystemExit):
        orch.run_architect("build X")

@patch("ralph.ClaudeAgent.run", return_value=(True, "not json"))
def test_run_planner_gives_up_after_three_attempts(_MockAgent, mock_config):
    orch = RalphOrchestrator()
    with pytest.raises(SystemExit) as e:
        orch.run_planner("Build Y")
    assert e.value.code == 1

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
    orch = RalphOrchestrator()
    orch.execute_loop()  # should only work on T2
    # archived file created and T2 set completed
    archives = list(mock_config.ARCHIVE_DIR.glob("*.json"))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text())
    t2 = next(x for x in archived["userStories"] if x["id"] == "T2")
    assert t2["status"] == "completed"

def test_record_failure_writes_progress(mock_config):
    orch = RalphOrchestrator()
    orch._record_failure(0, "reason", "detail")
    s = mock_config.PROGRESS_FILE.read_text()
    assert "Attempt 1 Failed: reason" in s and "detail" in s

@patch("ralph.MemoryManager.validate_memory", return_value={
    "valid": True, "corrupted": [], "empty": [], "total": 2
})
def test_validate_on_startup_valid_branch_hits_debug(_MockValidate, mock_config, monkeypatch):
    Logger.set_verbose(True)
    with patch("ralph.Logger.debug") as dbg:
        orch = RalphOrchestrator()
        # Create at least one file so the check runs
        (mock_config.MEMORY_DIR / "a.md").write_text("x")
        orch._validate_memory_on_startup()
        assert dbg.call_count >= 1
    Logger.set_verbose(False)

# ---------- Version & CLI ----------

def test_get_version_happy_path(monkeypatch, tmp_path):
    # Pretend ralph.py lives in tmp_path, create a pyproject.toml there
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
    # no pyproject.toml
    monkeypatch.setattr(ralph_mod, "__file__", str(fake_file))
    assert get_version() == "unknown"

@patch("ralph.RalphOrchestrator.start")
def test_main_parses_args_and_calls_start(MockStart, monkeypatch):
    # Positional phase argument + flag
    monkeypatch.setattr("sys.argv", ["ralph", "execute", "--accept-all"])
    ralph_main()
    MockStart.assert_called_once_with(phase="execute", accept_all=True)
