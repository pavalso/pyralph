# Gitflow Lifecycle

Complete workflow from task start to merge completion.

## Phases

| Phase | Description |
|-------|-------------|
| 1. PRD Branch Setup | Create PRD branch from master for PRD-scoped work |
| 2. Commit Workflow | Commit all features directly to the PRD branch |
| 3. Test Verification | Ensure all tests pass |
| 4. Merge to Master | Merge PRD branch to master and push (MANDATORY) |
| 5. Conflict Resolution | Resolve merge conflicts (if needed) |

## Branch Hierarchy

```
master
  └── PRD/<prd-id>
        (all commits go directly here)
```

---

## Phase 1: PRD Branch Setup

When starting work on a PRD, create a dedicated PRD branch from master.

1. Ensure master is up-to-date: `git pull origin master`
2. Create PRD branch: `git checkout -b PRD/<prd-id> master`
3. Use naming convention: `PRD/<prd-id>` (e.g., `PRD/user-authentication`)

---

## Phase 2: Commit Workflow

**All work is committed directly to the PRD branch. No feature branches.**

1. Stay on the PRD branch for all development
2. Stage only modified files: `git add <file>`
3. Write clear commit messages: `TASK-<id>: <description>`
4. Commit at logical checkpoints after completing each task
5. Review staged changes: `git diff --staged`
6. Never commit generated files, build artifacts, or `.gitignore` entries
7. Push regularly to remote: `git push origin PRD/<prd-id>`

---

## Phase 3: Test Verification

1. Run tests: `pytest`
2. All tests must pass (exit code 0) before merging
3. Fix failures and re-run until passing
4. Never skip or disable tests
5. Include tests for new functionality

---

## Phase 4: Merge to Master (MANDATORY)

**After completing the PRD, you MUST merge to master and push immediately.**

**Prerequisites:**
- All tasks in the PRD are complete
- All commits are pushed to the PRD branch
- All tests pass on the PRD branch

**Steps:**

1. Verify all tests pass on the PRD branch
2. Switch to master: `git checkout master`
3. Pull latest: `git pull origin master`
4. Merge PRD branch: `git merge PRD/<prd-id>`
5. Resolve conflicts if needed, then re-run tests
6. Push to master: `git push origin master`
7. Delete PRD branch:
   - Local: `git branch -d PRD/<prd-id>`
   - Remote: `git push origin --delete PRD/<prd-id>`

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
   >>>>>>> PRD/<prd-id>
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

## Supplemental: Memory Management

Guidelines for managing memory entries:

1. Use filename format: `<tag1>_<tag2>.md`
2. Categorize with relevant tags
3. Review and prune regularly
4. Use specific tags when retrieving
5. Prioritize updating existing entries over creating new ones
6. Focus on actionable information
