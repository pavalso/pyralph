# Ralph Recommended Git Workflow

## Overview
This workflow ensures efficient, consistent, and conflict-free collaboration for all contributors. It is designed for PRD-driven development, but can be adapted for general feature work.

## Branching Strategy
- **Feature branches**: For each PRD or major feature, create a branch named `feature/<prd_name>` from `main` (or `develop` if used).
- **Task branches**: For each task or sub-feature, create a branch named `task/<task_or_feature_name>` from the relevant `feature/<prd_name>` branch.

## Step-by-Step Workflow

1. **Start from the correct base**
   - Ensure you are on the `feature/<prd_name>` branch before starting a new task.

2. **Create a task branch**
   - Branch from the feature branch:
     ```bash
     git checkout feature/<prd_name>
     git pull
     git checkout -b task/<task_or_feature_name>
     ```

3. **Implement and commit**
   - Make your changes, commit frequently with clear messages.
   - Run tests locally to verify your work.

4. **Merge task branch back to feature branch**
   - When the task is complete and tested:
     ```bash
     git checkout feature/<prd_name>
     git pull
     git merge task/<task_or_feature_name>
     git branch -d task/<task_or_feature_name>  # optional cleanup
     ```

5. **Complete the PRD/feature**
   - When all tasks are merged into the feature branch, merge the feature branch into `main` (or `develop`):
     ```bash
     git checkout main
     git pull
     git merge feature/<prd_name>
     git branch -d feature/<prd_name>  # optional cleanup
     ```

## Key Rules
- ALWAYS branch from the correct base (feature or main).
- ALWAYS use task branches for individual work.
- ALWAYS verify with tests before merging.
- NEVER commit directly to `main` or `feature/<prd_name>` without review.
- Keep branches up to date with their base before merging.

## Example
```
main
  └── feature/user-auth
        ├── task/login-form
        └── task/password-reset
```

## Benefits
- Isolates work for easier review and conflict resolution.
- Enables parallel development on multiple tasks.
- Maintains a clean, understandable git history.

## Notes
- Adapt branch names to your team's conventions if needed.
- For hotfixes, branch from `main` and merge back after testing.
