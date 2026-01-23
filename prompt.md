# Branch Management

Before starting each task, create a dedicated feature branch:
1. Branch from the main branch (e.g., `main` or `master`).
2. Use the naming convention `feature/TASK-<id>-<short-description>` (e.g., `feature/TASK-001-add-login`).
3. Commit changes to this feature branch during task implementation.
4. After task completion and verification, the branch can be merged back to main.

# Commit Workflow

When making changes during task execution, follow this commit workflow:
1. Stage only the files that were modified for the current task using `git add <file>`.
2. Write clear, concise commit messages that describe what was changed and why.
3. Use the format: `TASK-<id>: <brief description of change>` (e.g., `TASK-001: Add login validation`).
4. Commit frequently at logical checkpoints (e.g., after implementing a feature, fixing a bug, or adding tests).
5. Before committing, review staged changes with `git diff --staged` to ensure only intended changes are included.
6. Do not commit generated files, build artifacts, or files listed in `.gitignore`.

# Test Verification

Before merging any changes, ensure all tests pass:
1. Run the test suite using `pytest` from the project root.
2. All tests must pass (exit code 0) before changes can be merged.
3. If tests fail, fix the issues and re-run until all tests pass.
4. Do not skip or disable tests to make the suite pass.
5. If new functionality is added, ensure appropriate tests are included.

# Merge-Back Workflow

After task completion and verification, merge the feature branch back to main:
1. Ensure all tests pass before merging (see Test Verification above).
2. Switch to the main branch: `git checkout main` (or `master`).
3. Pull the latest changes: `git pull origin main`.
4. Merge the feature branch: `git merge feature/TASK-<id>-<short-description>`.
5. Resolve any merge conflicts if they arise, then re-run tests to verify.
6. Push the updated main branch: `git push origin main`.
7. Optionally, delete the feature branch after successful merge:
   - Local: `git branch -d feature/TASK-<id>-<short-description>`
   - Remote: `git push origin --delete feature/TASK-<id>-<short-description>`

# Conflict Resolution

When merge conflicts occur during the merge-back workflow, follow these steps:
1. Identify conflicted files by running `git status` (files with "both modified" status).
2. Open each conflicted file and locate conflict markers:
   - `<<<<<<< HEAD` marks the start of your current branch's changes
   - `=======` separates the two versions
   - `>>>>>>> feature/TASK-<id>` marks the end of the incoming branch's changes
3. Resolve each conflict by:
   - Understanding both versions of the code
   - Choosing one version, combining both, or writing a new solution
   - Removing all conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
4. After resolving conflicts in a file, stage it: `git add <resolved-file>`.
5. Once all conflicts are resolved and staged, complete the merge: `git commit`.
6. Re-run the test suite to verify the merged code works correctly.
7. If tests fail after conflict resolution, fix the issues before pushing.

Common conflict scenarios:
- **Same line edited**: Compare changes and keep the correct logic.
- **Adjacent changes**: Often both changes can be kept; ensure they work together.
- **Deleted vs modified**: Decide if the deletion or modification is correct.
- **File renamed/deleted**: Check if the file should exist and under which name.

Tips for avoiding conflicts:
- Pull from main frequently during development: `git pull origin main --rebase`.
- Keep feature branches short-lived and focused.
- Communicate with team members about overlapping work areas.

# Memory Management

When managing your memory, consider the following strategies:
1. Create or update memory entries with clear and concise information. The file name created should be in the format `<tag1>_<tag2>.md`.
2. Use relevant tags to categorize memory entries effectively.
3. Regularly review and prune memory entries to ensure they remain relevant and useful.
4. When retrieving memory, use specific tags to get the most relevant information.
5. Priorize updating existing memory entries over creating new ones to avoid redundancy.
6. Keep memory entries focused on actionable information that can assist in future tasks.