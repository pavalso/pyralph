# CLAUDE.md - Git Operations Protocol

## Agent Directives
- **Non-Interactive Only:** specificy flags (e.g., `-m`, `--no-edit`) to prevent opening text editors.
- **Atomic Operations:** Verify branch status before and after every state change.
- **Error Handling:** On merge/rebase conflicts, ABORT immediately; do not attempt to resolve autonomously unless explicitly instructed.

## Workflow 1: Linearization (Squashing History)
*Goal: Combine commits from ID `X` to current HEAD into a single atomic commit.*

**Protocol:**
1. **Verify State:** Ensure working tree is clean (`git status --porcelain` returns empty).
2. **Soft Reset:** Move HEAD to the parent of X, preserving changes in index.
   `git reset --soft <ID_OF_X>^`
3. **Commit:** Create the single combined commit.
   `git commit -m "<Summary of all changes>" -m "<Detailed description>"`
4. **Force Push (If Remote Exists):**
   `git push --force-with-lease`

## Workflow 2: Stacked Dependency Chain
*Goal: Manage dependent branches (Feature -> Task 1 -> Task 2).*

### Branch Creation
1. **Root Branch:** `git checkout main && git pull`
2. **Feature Container:** `git checkout -b feature/<name>`
3. **Task 1 (Dependency):** `git checkout -b task/<name>-1 feature/<name>`
4. **Task 2 (Dependent):** `git checkout -b task/<name>-2 task/<name>-1`

### Propagation (Rebasing)
*Trigger: When Task 1 is modified, Task 2 must be updated.*

1. **Checkout Dependent:** `git checkout task/<name>-2`
2. **Rebase:** Attempt to replay Task 2 on top of new Task 1.
   `git rebase task/<name>-1`
3. **Exception Handling:**
   - If exit code 0: Success.
   - If exit code > 0 (Conflict):
     `git rebase --abort`
     *Action: Stop and report "Dependency Conflict" to user.*

## Safety Constraints
- **Restricted Branches:** NEVER force push to `main`, `master`, `prod`, or `develop`.
- **Verification:** Before applying changes, run `git branch --show-current` to confirm context.
- **Cleanliness:** Always run `git stash` before switching branches if the index is dirty, or `git stash pop` after returning.