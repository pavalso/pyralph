# Gitflow Lifecycle

Complete workflow from task start to merge completion.

## Phases

| Phase | Description |
|-------|-------------|
| 1. PRD Branch Setup | Create PRD branch from master for PRD-scoped work |
| 2. Feature Branch Management | Create feature branches from PRD branch |
| 3. Commit Workflow | Make commits during development |
| 4. Test Verification | Ensure all tests pass |
| 5. Merge-Back | Merge feature branches to PRD, then PRD to master |
| 6. Conflict Resolution | Resolve merge conflicts (if needed) |

## Branch Hierarchy

```
master
  └── PRD/<prd-id>
        ├── feature/TASK-<id>-<description>
        ├── feature/TASK-<id>-<description>
        └── ...
```

---

## Phase 1: PRD Branch Setup

When starting work on a PRD, create a dedicated PRD branch from master.

1. Ensure master is up-to-date: `git pull origin master`
2. Create PRD branch: `git checkout -b PRD/<prd-id> master`
3. Use naming convention: `PRD/<prd-id>` (e.g., `PRD/user-authentication`)

---

## Phase 2: Feature Branch Management

Feature branches are created from the PRD branch, **not** from master.

1. Ensure the PRD branch exists and is up-to-date
2. Create feature branch from PRD: `git checkout -b feature/TASK-<id>-<description> PRD/<prd-id>`
3. Use naming convention: `feature/TASK-<id>-<short-description>`
4. Commit changes to the feature branch
5. Merge back to the PRD branch (not master) after completion and verification

---

## Phase 3: Commit Workflow

1. Stage only modified files: `git add <file>`
2. Write clear commit messages: `TASK-<id>: <description>`
3. Commit at logical checkpoints
4. Review staged changes: `git diff --staged`
5. Never commit generated files, build artifacts, or `.gitignore` entries

---

## Phase 4: Test Verification

1. Run tests: `pytest`
2. All tests must pass (exit code 0) before merging
3. Fix failures and re-run until passing
4. Never skip or disable tests
5. Include tests for new functionality

---

## Phase 5: Merge-Back

The merge-back process follows a strict two-tier sequence:

### 5a. Feature Branch → PRD Branch

When a feature is complete, merge it back to the parent PRD branch.

1. Verify all tests pass on the feature branch
2. Switch to PRD branch: `git checkout PRD/<prd-id>`
3. Pull latest: `git pull origin PRD/<prd-id>`
4. Merge feature branch: `git merge feature/TASK-<id>-<short-description>`
5. Resolve conflicts if needed, then re-run tests
6. Push: `git push origin PRD/<prd-id>`
7. Delete feature branch (optional):
   - Local: `git branch -d feature/TASK-<id>-<short-description>`
   - Remote: `git push origin --delete feature/TASK-<id>-<short-description>`

### 5b. PRD Branch → Master

When all features in a PRD are complete and merged, merge the PRD branch to master.

**Prerequisites:**
- All feature branches have been merged to the PRD branch
- All tests pass on the PRD branch
- No pending tasks remain in the PRD scope

**Steps:**

1. Verify all tests pass on the PRD branch
2. Switch to master: `git checkout master`
3. Pull latest: `git pull origin master`
4. Merge PRD branch: `git merge PRD/<prd-id>`
5. Resolve conflicts if needed, then re-run tests
6. Push: `git push origin master`
7. Delete PRD branch:
   - Local: `git branch -d PRD/<prd-id>`
   - Remote: `git push origin --delete PRD/<prd-id>`

---

## Phase 6: Conflict Resolution

### Steps

1. Identify conflicts: `git status` (look for "both modified")
2. Locate conflict markers in files:
   ```
   <<<<<<< HEAD
   (current branch changes)
   =======
   (incoming branch changes)
   >>>>>>> feature/TASK-<id>
   ```
3. Resolve by choosing, combining, or rewriting
4. Remove all conflict markers
5. Stage resolved files: `git add <file>`
6. Complete merge: `git commit`
7. Re-run tests to verify

### Common Scenarios

| Scenario | Resolution |
|----------|------------|
| Same line edited | Compare and keep correct logic |
| Adjacent changes | Often both can be kept |
| Deleted vs modified | Decide if deletion or modification is correct |
| File renamed/deleted | Determine if file should exist and under which name |

### Prevention Tips

- Pull frequently: `git pull origin main --rebase`
- Keep feature branches short-lived
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
