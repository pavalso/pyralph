# Gitflow Lifecycle

Complete workflow from task start to merge completion.

## Key Principle

**One branch per PRD, merged only after all tasks are complete.**

A PRD uses a single branch (`PRD/<prd-short-description>`) for all its tasks. Each task is committed directly to this branch. The branch is only merged back to master after every task in the PRD has been completed and verified.

## Phases

| Phase | Description |
|-------|-------------|
| 1. PRD Branch Setup | Create a single PRD branch from master for all PRD tasks |
| 2. Commit Workflow | Commit all tasks directly to the PRD branch (no feature branches) |
| 3. Test Verification | Ensure all tests pass after all tasks are complete |
| 4. Merge to Master | Merge PRD branch to master only after ALL tasks are finished (MANDATORY) |
| 5. Conflict Resolution | Resolve merge conflicts (if needed) |

## Branch Hierarchy

```
master
  └── PRD/<prd-short-description>
        ├── TASK-001 commit
        ├── TASK-002 commit
        ├── TASK-003 commit
        └── ... (all tasks committed here, then merged to master)
```

---

## Phase 1: PRD Branch Setup

When starting work on a PRD, create a single dedicated PRD branch from master. This branch will be used for ALL tasks in the PRD.

1. Ensure master is up-to-date: `git pull origin master`
2. Create PRD branch: `git checkout -b PRD/<prd-short-description> master`
3. Use naming convention: `PRD/<prd-short-description>` (e.g., `PRD/user-authentication`)
4. This is the only branch you will use—all tasks are committed here

---

## Phase 2: Commit Workflow

**All tasks are committed directly to the single PRD branch. No feature branches.**

1. Stay on the PRD branch for all development (all tasks use this one branch)
2. Stage only modified files: `git add <file>`
3. Write clear commit messages: `TASK-<id>: <description>`
4. Commit after completing each task (one commit per task, all on the same branch)
5. Review staged changes: `git diff --staged`
6. Never commit generated files, build artifacts, or `.gitignore` entries
7. Push regularly to remote: `git push origin PRD/<prd-short-description>`
8. Continue committing tasks until ALL tasks in the PRD are complete

---

## Phase 3: Test Verification

1. Run tests: `pytest`
2. All tests must pass (exit code 0) before merging
3. Fix failures and re-run until passing
4. Never skip or disable tests
5. Include tests for new functionality

---

## Phase 4: Merge to Master (MANDATORY)

**After completing ALL tasks in the PRD, you MUST merge to master and push immediately.**

**Important:** Do NOT merge to master until every task in the PRD is finished. The PRD branch accumulates all task commits, and the merge happens only once—at the very end.

**Prerequisites:**
- ALL tasks in the PRD are complete (not just some)
- All commits are pushed to the PRD branch
- All tests pass on the PRD branch

**Steps:**

1. Verify all tests pass on the PRD branch
2. Switch to master: `git checkout master`
3. Pull latest: `git pull origin master`
4. Merge PRD branch: `git merge PRD/<prd-short-description>`
5. Resolve conflicts if needed, then re-run tests
6. Push to master: `git push origin master`
7. Delete PRD branch:
   - Local: `git branch -d PRD/<prd-short-description>`
   - Remote: `git push origin --delete PRD/<prd-short-description>`

**This step is NOT optional. Every completed PRD must be merged to master.**

---

## Phase 5: Conflict Resolution

### Steps

1. Identify conflicts: `git status` (look for "both modified")
2. Locate conflict markers in files:
   ```
   <<<<<<< HEAD
   (current branch changes)
   =======
   (incoming branch changes)
   >>>>>>> PRD/<prd-short-description>
   ```
3. Resolve by choosing, combining, or rewriting
4. Remove all conflict markers
5. Stage resolved files: `git add <file>`
6. Complete merge: `git commit`
7. Re-run tests to verify
8. Push to master: `git push origin master`

### Common Scenarios

| Scenario | Resolution |
|----------|------------|
| Same line edited | Compare and keep correct logic |
| Adjacent changes | Often both can be kept |
| Deleted vs modified | Decide if deletion or modification is correct |
| File renamed/deleted | Determine if file should exist and under which name |

### Prevention Tips

- Pull frequently: `git pull origin master --rebase`
- Keep PRD branches short-lived
- Communicate about overlapping work

---
