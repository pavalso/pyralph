---
type: wiki
title: Git Workflow Context
created: 2026-01-21
---

# Git Workflow

## Default Branch Detection

Ralph detects the repository's default branch during initialization using the following strategy:

1. **Primary**: Query `git symbolic-ref refs/remotes/origin/HEAD`
   - Extracts the branch name from the output (e.g., "main" or "master")
   - Works for cloned repositories with remote tracking

2. **Fallback**: Query `git rev-parse --abbrev-ref HEAD`
   - Falls back if remote detection fails
   - Returns the current branch name
   - Skips if in detached HEAD state

3. **Default**: Falls back to "main"
   - Used when all detection methods fail

## Feature Branch Creation (TASK-002)

**Implemented**: `GitUtils.create_feature_branch(base_branch: str, feature_branch_name: str) -> bool`

Each task execution now creates a feature branch before agent task execution:

1. **Checkout base branch** - ensures we're on the default branch
2. **Create and checkout feature branch** - new branch named `feature/{featureBranch}` from PRD
3. **Returns**: `True` if successful, `False` if either step fails

### Naming Convention
- Format: `feature/{featureBranch}` where `featureBranch` is from `.ralph/prd.json`
- Example: `feature/git-workflow-prd`

### Integration
- Called at start of each task (retries share same branch)
- Feature branch name stored in PRD JSON `featureBranch` field
- Default fallback: `feature/task-002` if featureBranch not set

## Storage Location

The detected default branch is:
- Stored in `.ralph/memory/git_workflow.md` during architect phase
- Available for reference throughout the workflow
- Used as the base branch for feature branch creation

## Supported Configurations

- **Remote repositories**: Auto-detects from `origin/HEAD`
- **Local repositories**: Defaults to current branch
- **Detached HEAD**: Falls back to "main"

## Task Branch Creation (TASK-003)

**Implemented**: `GitUtils.create_task_branch(prd_branch: str, task_id: str, description: str) -> Tuple[bool, str]`

Each task now creates its own branch from the PRD feature branch:

1. **Checkout PRD branch** - ensures we're on the PRD feature branch
2. **Create and checkout task branch** - new branch named `task/{TASK-ID}-{description-slug}` from PRD
3. **Returns**: Tuple of (success: bool, branch_name: str)

### Naming Convention
- Format: `task/{task-id}-{description-slug}` where description is converted to slug
- Slug generation: lowercase, replace spaces with hyphens, remove special characters
- Example: `task/task-003-implement-user-story-branches`

### Integration
- Called after feature branch creation for each task (retries share same branch)
- Creates branch from the PRD feature branch (not from default branch)
- Task branches are isolated workspaces for each user story within the feature

### Execution Flow
1. Create feature branch from default branch
2. Create task branch from feature branch
3. Agent executes on task branch
4. Tests verify on task branch
5. Commit created on task branch

## Task Branch Merge to PRD (TASK-004)

**Implemented**: `GitUtils.merge_task_to_prd(prd_branch: str, task_branch: str, task_id: str, description: str) -> bool`

After a task is successfully verified, the task branch is merged back to the PRD branch:

1. **Checkout PRD branch** - switches to the PRD feature branch
2. **Merge with --no-ff** - creates a merge commit using the `--no-ff` flag (no fast-forward)
3. **Commit message format** - `Merge {TASK-ID}: {description}`
4. **Returns**: True if successful, False otherwise

### Integration

- Called after task verification succeeds in `_execute_task()`
- Ensures task branch is merged before marking task as completed
- Preserves branch history with merge commits (not fast-forward)
- Allows rollback/revert of entire task if needed

### Merge Workflow

1. Task branch is created and worked on
2. Tests are run on task branch
3. If tests pass, merge branch to PRD
4. Task is marked completed
5. Task branch remains available for historical reference

## PRD Branch Merge to Default (TASK-005)

**Implemented**: `GitUtils.merge_prd_to_default(default_branch: str, prd_branch: str) -> bool`

After all user stories are successfully completed, the PRD feature branch is merged back to the default branch:

1. **Checkout default branch** - switches to the default branch (main, master, etc.)
2. **Merge with --no-ff** - creates a merge commit using the `--no-ff` flag (no fast-forward)
3. **Commit message format** - `Merge PRD: {featureBranch}`
4. **Returns**: True if successful, False otherwise

### Integration

- Called after all tasks complete in `execute_loop()`
- Ensures the completed feature work is integrated back to the main line of development
- Preserves branch history with a final merge commit
- Allows for feature rollback if needed

### Complete Workflow

1. **Default branch detected** (TASK-001 ✓)
2. **Feature branch created** from default branch (TASK-002 ✓)
3. **Task branches created** from PRD feature branch (TASK-003 ✓)
4. **Task branches merged** to PRD with merge commits (TASK-004 ✓)
5. **PRD branch merged** to default branch with merge commit (TASK-005 ✓)
6. **Orchestrator manages** the entire workflow (TASK-006)

## Implementation Status

All core git workflow features have been implemented and tested:

1. ✅ **TASK-001**: Default branch detection
2. ✅ **TASK-002**: Feature branch creation from default branch
3. ✅ **TASK-003**: Task branch creation from PRD feature branch
4. ✅ **TASK-004**: Merge task branch to PRD with merge commits
5. ✅ **TASK-005**: PRD branch merge to default branch
6. ✅ **TASK-006**: Orchestrator integration (completed)

## Integration Points

- **Architect Phase**: Detects and stores default branch
- **Feature Branch Creation** (TASK-002 ✓): Creates feature branch at task start
- **Task Branch Creation** (TASK-003 ✓): Creates task-specific branch from PRD branch
- **Task Merge to PRD** (TASK-004 ✓): Merges task branch with merge commit after verification
- **Workflow Management** (TASK-006): Reference for branch hierarchy

## Key Implementation Details

### Task Branch Merge (TASK-004)
- After task verification passes, task branch is automatically merged to PRD
- Merge uses `--no-ff` flag to preserve branch history
- Commit message format: `Merge {TASK-ID}: {description}`
- Task branch name is retained across retries for consistency
- Merge only occurs if `task_branch` variable is successfully set during branch creation

## Orchestrator Integration (TASK-006)

### Branch Lifecycle in RalphOrchestrator

The orchestrator manages the complete branch lifecycle through the `_execute_task()` and `execute_loop()` methods:

#### Task Execution Flow (`_execute_task()`)

1. **Branch Creation** (first retry only)
   - Detects default branch using `GitUtils.detect_default_branch()`
   - Creates feature branch from default branch: `GitUtils.create_feature_branch()`
   - Creates task branch from feature branch: `GitUtils.create_task_branch()`
   - Branch names are preserved across retries for idempotency

2. **Agent Execution**
   - Agent works on task branch (isolated workspace)
   - All changes are committed to task branch

3. **Verification & Merge**
   - Tests are run to verify agent's work
   - On success: task branch is merged to PRD with merge commit
   - Merge uses `--no-ff` flag to preserve history
   - Git commit created for task completion

#### Loop Completion (`execute_loop()`)

1. **All Tasks Processed**
   - Each task creates its own branch and is merged to PRD
   - PRD branch accumulates all completed task changes

2. **Final Integration**
   - After all tasks complete, PRD branch is merged to default branch
   - Uses `--no-ff` flag to preserve feature branch history
   - Merge commit message: `Merge PRD: {featureBranch}`

### Branch Hierarchy

```
main (or default)
    └── feature/{featureBranch}        # Created at task start
            └── task/{TASK-ID}-{slug}  # Created for each task
            └── task/{TASK-ID}-{slug}  # Created for each task
            └── task/{TASK-ID}-{slug}  # etc...

After completion, feature branch is merged back to main with final merge commit.
```

### Error Handling

- Feature branch creation failure: Task execution stops
- Task branch creation failure: Task execution stops
- Merge failures: Logged as warning but task marked complete (verification already passed)
- Retries share same branches for consistency

### Key Variables

- `task_branch`: Stored in `_execute_task()` across retries
- `featureBranch`: Stored in PRD JSON for persistence
- `default_branch`: Detected fresh for each major operation
- `prd_branch`: Derived from PRD JSON
