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

## Integration Points

- **Architect Phase**: Detects and stores default branch
- **Feature Branch Creation** (TASK-002 ✓): Creates feature branch at task start
- **Workflow Management** (TASK-006): Reference for branch hierarchy
