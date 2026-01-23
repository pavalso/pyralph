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

# Memory Management

When managing your memory, consider the following strategies:
1. Create or update memory entries with clear and concise information. The file name created should be in the format `<tag1>_<tag2>.md`.
2. Use relevant tags to categorize memory entries effectively.
3. Regularly review and prune memory entries to ensure they remain relevant and useful.
4. When retrieving memory, use specific tags to get the most relevant information.
5. Priorize updating existing memory entries over creating new ones to avoid redundancy.
6. Keep memory entries focused on actionable information that can assist in future tasks.