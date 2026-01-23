# Gitflow Lifecycle

Complete workflow from task start to merge completion.

## Phases

| Phase | Description |
|-------|-------------|
| 1. Branch Management | Create feature branch from main |
| 2. Commit Workflow | Make commits during development |
| 3. Test Verification | Ensure all tests pass |
| 4. Merge-Back | Merge feature branch to main |
| 5. Conflict Resolution | Resolve merge conflicts (if needed) |

---

## Phase 1: Branch Management

1. Branch from `main` or `master`
2. Use naming convention: `feature/TASK-<id>-<short-description>`
3. Commit changes to this feature branch
4. Merge back to main after completion and verification

---

## Phase 2: Commit Workflow

1. Stage only modified files: `git add <file>`
2. Write clear commit messages: `TASK-<id>: <description>`
3. Commit at logical checkpoints
4. Review staged changes: `git diff --staged`
5. Never commit generated files, build artifacts, or `.gitignore` entries

---

## Phase 3: Test Verification

1. Run tests: `pytest`
2. All tests must pass (exit code 0) before merging
3. Fix failures and re-run until passing
4. Never skip or disable tests
5. Include tests for new functionality

---

## Phase 4: Merge-Back

1. Verify all tests pass
2. Switch to main: `git checkout main`
3. Pull latest: `git pull origin main`
4. Merge feature branch: `git merge feature/TASK-<id>-<short-description>`
5. Resolve conflicts if needed, then re-run tests
6. Push: `git push origin main`
7. Delete feature branch (optional):
   - Local: `git branch -d feature/TASK-<id>-<short-description>`
   - Remote: `git push origin --delete feature/TASK-<id>-<short-description>`

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
