---
type: wiki
title: Ralph CLI Usage
created: 2026-01-20
---

# Ralph CLI Usage

Assumes `ralph` is available in `$PATH`.

## Commands

### Run All Phases (Default)

```bash
ralph
```

Starts or resumes the agent loop with all three phases (architect, planner, execute). If no PRD exists, prompts for project description first.

### Phase Selection

```bash
ralph --phase architect
ralph --phase planner
ralph --phase execute
ralph --phase all
```

Run specific phases individually:
- `architect`: Initialize memory with project context
- `planner`: Generate PRD with user stories
- `execute`: Run task execution loop
- `all`: Run all three phases sequentially (default)

### Accept-All Flag

```bash
ralph --accept-all
ralph --phase execute --accept-all
```

Skip user feedback prompts before each phase and proceed automatically. When `phase='all'`, automatically runs all phases without asking for confirmation. When combined with `--phase`, skips the phase-specific prompt if applicable.

**Behavior**:
- `ralph --accept-all`: Skips prompts "Run Architect phase? (y/n)" and "Run Planner phase? (y/n)" and "Run Execute phase? (y/n)"
- `ralph --phase execute --accept-all`: Runs execute phase without any prompts
- `ralph --phase architect --accept-all`: Runs architect phase (no prompt applies to specific phases)

Useful for unattended execution and CI/CD pipelines.

### Verbose Flag

```bash
ralph --verbose
ralph --verbose --phase execute
ralph --phase planner --verbose
```

Enable debug-level logging to see all Claude CLI prompts and responses. Displays:
- `[DEBUG]` messages with Claude prompts in cyan
- Claude responses in green
- Errors in red
- All prompts and responses are still logged to `.ralph/ralph_log.txt`

Useful for debugging agent behavior and understanding how prompts flow through Claude.

### Version

```bash
ralph --version
```

Displays the current version of Ralph in format: `Ralph <version>`

### Help Text

```bash
ralph --help
ralph -h
```

Displays comprehensive help with all available options and usage examples.

### Run Agent

```bash
ralph
```

Starts or resumes the agent loop. If no PRD exists, prompts for project description first.

### Hard Reset

```bash
rm -rf .ralph/
```

Clears all agent state including memory and plan. Use when starting fresh.

### Re-Plan

```bash
rm .ralph/prd.json
```

Removes only the project plan while keeping memory intact. On next run, regenerates user stories from existing context.

### Skip Task

Edit `.ralph/prd.json` directly and change the current task's status:

```json
{
  "status": "completed"
}
```

Useful for bypassing a stuck or irrelevant task.

## State Files

| File | Purpose |
|------|---------|
| `.ralph/prd.json` | Project plan with user stories |
| `.ralph/memory/` | Knowledge base (wiki files) |
| `.ralph/progress.txt` | Error state (exists only during failures) |
| `.ralph/ralph_log.txt` | Full audit trail |

## Debug

View recent log entries:

```bash
tail -n 50 .ralph/ralph_log.txt
```

## Examples

### Starting Fresh

```bash
ralph
```
Prompts for project description, runs architect, planner, and execute phases.

### Running Only Architect Phase

```bash
ralph --phase architect
```
Initializes memory structure without running planner or execute.

### Running Only Execute Phase

```bash
ralph --phase execute
```
Skips architect and planner, runs only the task execution loop. Requires existing PRD.

### Running All Phases Without Prompts

```bash
ralph --accept-all
```
Runs all three phases sequentially without any user feedback prompts. When architect phase is needed, runs it automatically. When planner phase is needed, runs it automatically. When execute phase is reached, runs it automatically.

### Interactive Phase Selection

```bash
ralph
# or
ralph --phase all
```
Runs all phases with user prompts before each phase execution:
- "Run Architect phase? (y/n)" - if memory doesn't exist
- "Run Planner phase? (y/n)" - if PRD doesn't exist
- "Run Execute phase? (y/n)" - always asked

User can answer 'n' to skip a phase without error.

### Executing with Full Automation

```bash
ralph --phase execute --accept-all
```
Runs only the execute phase without prompts. Ideal for CI/CD pipelines.

### Debugging Agent Behavior

```bash
ralph --verbose
```
Runs all phases with debug-level logging enabled. Shows:
- All Claude prompts sent (with `[DEBUG]` prefix in cyan)
- All Claude responses received (in green)
- Any errors or exceptions (in red)

Perfect for understanding why tasks fail or how the agent interprets instructions.

## Knowledge Injection

Add context without repeating in prompts:

1. Create a `.md` file in `.ralph/memory/`
2. Agent reads it on the next turn if relevant
