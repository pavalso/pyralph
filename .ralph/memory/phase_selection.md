---
type: wiki
title: Phase Selection Logic
created: 2026-01-21
---

# RalphOrchestrator Phase Selection

## Overview

The `start()` method now supports granular phase selection via the `phase` parameter, allowing selective execution of the three Ralph phases: architect, planner, and execute.

## Phase Parameters

### `phase='architect'`
Runs only the architect phase:
- Prompts user for project description
- Initializes `.ralph/memory/` with architecture context
- Returns immediately after completion
- **Prerequisite**: None
- **Result**: Memory structure created

### `phase='planner'`
Runs only the planner phase:
- Checks that memory exists (`.ralph/memory/` is populated)
- If memory missing: Logs error and exits with code 1
- Prompts user for project description
- Generates PRD JSON with user stories
- Returns immediately after completion
- **Prerequisite**: Memory must exist (run architect first)
- **Result**: PRD file created at `.ralph/prd.json`

### `phase='execute'`
Runs only the execute phase:
- Checks that PRD exists (`.ralph/prd.json`)
- If PRD missing: Logs error and exits with code 1
- Runs task execution loop
- Processes all pending tasks sequentially
- Merges completed tasks to default branch
- Archives PRD after completion
- **Prerequisite**: PRD must exist (run planner first)
- **Result**: All pending tasks executed and completed

### `phase='all'` (default)
Runs all three phases sequentially:
1. **Architect**: Only if memory doesn't exist
   - Prompts user: `Run Architect phase? (y/n)`
   - If user confirms (y): Prompts for project description, creates memory structure
   - If user declines (n): Skips architect phase without error
   - Logs and skips if memory already exists
   - Prompt is skipped if `accept_all=True`
2. **Planner**: Only if PRD doesn't exist
   - Prompts user: `Run Planner phase? (y/n)`
   - If user confirms (y): Prompts for project description, creates PRD from memory context
   - If user declines (n): Skips planner phase without error
   - Logs and skips if PRD already exists
   - Prompt is skipped if `accept_all=True`
3. **Execute**: Always runs (when memory and PRD exist)
   - Prompts user: `Run Execute phase? (y/n)`
   - If user confirms (y): Executes task loop, merges to default branch, archives PRD
   - If user declines (n): Skips execute phase without error
   - Prompt is skipped if `accept_all=True`

## Implementation Details

### User Prompts
- Each phase that needs user input prompts with: ">> What are we building?"
- User can cancel by providing empty input (exits with code 0)
- Prompts are conditional based on prerequisites

### Error Handling
- **Memory Missing (planner phase)**: `❌ Memory does not exist. Run architect phase first.` → exits with code 1
- **PRD Missing (execute phase)**: `❌ PRD does not exist. Run planner phase first.` → exits with code 1
- All errors are logged before exit

### Logging
Each phase transition logs clear status messages:
- Phase start: `📋 Phase specified: {phase}`
- Phase skip: `📋 {Resource} already exists, skipping {phase} phase.`
- Phase complete: `✅ {Phase} phase complete.`

## Usage Examples

### Start fresh from scratch
```bash
ralph --phase architect
```
Initializes memory, then separately:
```bash
ralph --phase planner
```
Generates PRD, then:
```bash
ralph --phase execute
```
Runs tasks

### Run all phases automatically
```bash
ralph
# or
ralph --phase all
```
Prompts once for description, runs architect → planner → execute sequentially

### Resume from PRD
```bash
ralph --phase execute
```
Continues task execution from existing PRD (skips architect and planner)

### Skip prompts
The `accept_all` parameter is available but not yet fully utilized. Future enhancement: auto-provide default descriptions when `accept_all=True`.

## Criteria Met

✅ `start()` method accepts optional `phase` parameter (default='all')
✅ `start()` accepts optional `accept_all` parameter (default=False)
✅ When `phase='architect'`, only runs `run_architect()` and returns
✅ When `phase='planner'`, checks memory exists before running `run_planner()`, skips with error if missing
✅ When `phase='execute'`, checks PRD exists before running `execute_loop()`, skips with error if missing
✅ When `phase='all'`, runs all three sequentially (current behavior)
✅ Logic for handling missing prerequisites is clear and logged
