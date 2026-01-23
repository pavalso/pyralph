---
type: wiki
title: Memory File Validation Feature
created: 2026-01-21
---

# Memory File Validation Logic

## Overview
Ralph validates all memory files in `.ralph/memory/` at startup to ensure the knowledge base is healthy. It alerts users to corrupted or empty files but does not block execution.

## Validation Logic

### MemoryManager.validate_memory()
- Recursively scans `.ralph/memory/` for files (excluding hidden files and directories).
- Checks read access for `.md`, `.txt`, and all other files.
- Flags files as corrupted if unreadable (encoding/permission errors).
- Flags files as empty if they contain only whitespace.
- Returns a dictionary:
  ```python
  {
      'valid': bool,              # True if all files are readable
      'corrupted': list[str],     # Paths to unreadable files
      'empty': list[str],         # Paths to empty/whitespace-only files
      'total': int                # Total files checked
  }
  ```
- Ignores hidden files (starting with `.`) and directories.
- Handles missing memory directory gracefully.

### RalphOrchestrator._validate_memory_on_startup()
- Invoked during agent initialization.
- Skips validation if memory directory is empty.
- Logs warnings (yellow) for corrupted or empty files.
- Logs success in debug mode if all files are valid.
- Never halts execution due to validation warnings.

## Design Principles
- **Non-blocking:** Warnings do not stop the agent; users can fix files manually.
- **Graceful degradation:** Only critical files are essential; partial memory is allowed.
- **Clear messaging:** Warnings specify problematic files for user action.
- **Recursive:** Supports nested directories for future-proofing.

## Example Output
```
⚠️ Memory Validation: 1 file(s) empty:
   - .ralph/memory/notes.md
⚠️ Memory Validation: 1 file(s) corrupted or unreadable:
   - .ralph/memory/bad.txt
```

## Test Coverage
- Unit tests cover empty directories, readable files, empty/whitespace files, unreadable files, hidden files, directories, return structure, and subdirectories.
- Integration tests ensure validation is called on startup, warnings are logged, and execution continues on warnings.
