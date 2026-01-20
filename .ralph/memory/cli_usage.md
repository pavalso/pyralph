---
type: wiki
title: Ralph CLI Usage
created: 2026-01-20
---

# Ralph CLI Usage

Assumes `ralph` is available in `$PATH`.

## Commands

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

## Knowledge Injection

Add context without repeating in prompts:

1. Create a `.md` file in `.ralph/memory/`
2. Agent reads it on the next turn if relevant
