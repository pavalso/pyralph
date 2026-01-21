# Ralph Git Workflow for PRD Execution

## Core Principle
Ralph MUST follow this branching strategy when executing tasks from a PRD:

## Workflow Steps

### 1. **Check Current Branch**
When starting a task from the PRD, first verify the current git branch:
- If workspace is NOT on `feature/<prd_name>` branch, this step is skipped
- If workspace IS on `feature/<prd_name>` branch, proceed to step 2

### 2. **Create Task Branch**
When on a `feature/<prd_name>` branch, ALWAYS create a new task branch:
- **Branch naming**: `task/<task_or_feature_name>`
- **Source**: Branch from the current `feature/<prd_name>` branch
- **Purpose**: Isolate work for a single task/feature before merging back to feature branch

```bash
git checkout -b task/<task_or_feature_name>
```

### 3. **Implement & Verify**
Execute the task:
- Implement the feature/fix
- Run tests and verify it works
- Commit changes to the task branch

### 4. **Merge Back to Feature Branch**
Once implementation is complete and verified:
- Switch to the `feature/<prd_name>` branch
- Merge the task branch into the feature branch
- Delete the task branch (optional cleanup)

```bash
git checkout feature/<prd_name>
git merge task/<task_or_feature_name>
```

### 5. **PRD Completion Workflow**
When ALL tasks in the PRD are completed:
- The `feature/<prd_name>` branch is merged to the branch it originated from (usually `main` or `develop`)
- Workspace is switched to that parent branch
- Feature branch can be deleted after merge

```bash
git checkout <parent_branch>
git merge feature/<prd_name>
```

## Key Rules
- ✅ ALWAYS check if on `feature/<prd_name>` before creating task branches
- ✅ ALWAYS create task branches FROM the feature branch
- ✅ ALWAYS merge back to feature branch when task is done
- ✅ ALWAYS verify implementation works before merging
- ✅ ALWAYS merge feature to parent branch when PRD is fully complete
- ✅ ALWAYS switch workspace to correct branch after merge operations

## Example Flow
```
main
  └── feature/user-auth (PRD branch)
        ├── task/login-form (work on this)
        │   └── [implement, test, merge back to feature]
        └── task/password-reset (work on this)
            └── [implement, test, merge back to feature]

[After all tasks done]
main ← merge feature/user-auth back here
```

## Implementation Notes
- This workflow prevents conflicts and maintains clean history
- Task branches allow parallel work while feature branch acts as integration point
- Feature branch isolation from main ensures stable main branch during development
