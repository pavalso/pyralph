---
type: wiki
title: Memory File Validation Feature
created: 2026-01-21
---

# Memory File Validation on Startup

## Overview
Ralph validates all memory files in `.ralph/memory/` when the agent starts up. This ensures that the knowledge base is healthy and alerts users to any corrupted or empty files.

## Implementation

### MemoryManager.validate_memory()
Validates all files in `.ralph/memory/` directory.

**Returns:** Dictionary with structure:
```python
{
    'valid': bool,              # True if all files are readable
    'corrupted': list[str],     # Paths to unreadable files
    'empty': list[str],         # Paths to empty/whitespace-only files
    'total': int                # Total files checked
}
```

**Behavior:**
- Ignores hidden files (starting with `.`)
- Ignores directories
- Checks for read access on all `.md`, `.txt`, and other files
- Flags files that cannot be read (encoding errors, permission issues)
- Flags files that are empty or contain only whitespace
- Returns gracefully if memory directory doesn't exist

### RalphOrchestrator._validate_memory_on_startup()
Called during initialization to validate memory and warn user.

**Behavior:**
- Skips if memory directory is empty (no validation needed)
- Logs warnings about corrupted files (yellow color)
- Logs warnings about empty files (yellow color)
- Logs success message in debug mode
- Continues execution gracefully (no exit on warnings)

## Test Coverage

### Validation Tests (8 tests)
- `test_validate_memory_empty_directory` - Empty directory passes
- `test_validate_memory_with_readable_files` - All readable files pass
- `test_validate_memory_with_empty_files` - Detects empty/whitespace files
- `test_validate_memory_with_unreadable_files` - Detects read errors
- `test_validate_memory_ignores_hidden_files` - Skips `.` prefixed files
- `test_validate_memory_ignores_directories` - Skips directory entries
- `test_validate_memory_returns_correct_structure` - Validates return dict structure
- `test_validate_memory_with_subdirectories` - Handles nested files

### Integration Tests (3 tests)
- `test_orchestrator_validates_memory_on_startup` - Validation called on init
- `test_orchestrator_warns_on_corrupted_memory` - Warnings displayed for bad files
- `test_orchestrator_continues_gracefully_with_warnings` - No exit on warnings

## Design Decisions

1. **Non-blocking validation**: Warnings don't halt execution
   - Agent can still proceed even with corrupted/empty files
   - Users can investigate and fix files manually

2. **Graceful degradation**: Missing/empty non-critical files are acceptable
   - Only critical files (like architecture.md) are essential
   - Partial memory is better than none

3. **Clear messaging**: Warnings specify which files have issues
   - User can identify and fix problems
   - Yellow color highlights warnings without being critical

4. **Recursive scanning**: Supports nested directories in memory
   - Future-proof for organized memory structures
   - Validates all files regardless of nesting level

## Usage Example

When Ralph starts with corrupted memory:
```
⚠️ Memory Validation: 1 file(s) empty:
   - .ralph/memory/notes.md
⚠️ Memory Validation: 1 file(s) corrupted or unreadable:
   - .ralph/memory/bad.txt
```

User can then:
1. Check and repair the files
2. Restart Ralph to re-validate
3. Continue with the partial memory if needed
