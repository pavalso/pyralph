---
type: wiki
title: Agent Prompt Templates
created: 2026-01-20
---

# Agent Prompt Templates

Ralph uses three specialized agent roles that execute sequentially. Each role has a specific prompt structure.

## Overview

```
start() -> Architect -> Planner -> Developer (loop)
```

## 1. Architect Role

**Purpose**: Initialize the `.ralph/memory/` knowledge base.

**Trigger**: When memory directory is empty.

**Prompt Template**:

```
ROLE: Senior Architect. TASK: Initialize .ralph/memory/
INTENT: "{user_intent}"
FILES: {file_tree}

STRICT RULES:
1. Output ONLY markdown.
2. Define Tech Stack & Test Command.
3. Use YAML frontmatter with type: wiki.

ACTION: Create `architecture.md` in .ralph/memory/.
```

**Inputs**:
- `user_intent`: User's project description
- `file_tree`: Output of `tree -L 2` or fallback file listing

**Expected Output**: Creates `architecture.md` with tech stack and test command.

**Failure Handling**: Exits with error if memory remains empty after execution.

## 2. Planner Role

**Purpose**: Generate the PRD (Product Requirements Document) with user stories.

**Trigger**: When `.ralph/prd.json` does not exist.

**Prompt Template**:

```
ROLE: Product Manager.
TASK: Create PRD JSON for "{user_intent}".

AVAILABLE FILES:
{memory_tree}

INSTRUCTIONS:
1. EXPLORE: Understand the project.
2. THINK: Plan user stories.
3. ACT: Output the PRD JSON.

STRICT RULES:
1. Output ONLY valid JSON.
2. Schema: {
     "featureBranch": "str",
     "userStories": [ {
       "id": "TASK-001",
       "description": "...",
       "acceptanceCriteria": ["..."],
       "status": "pending"
     } ]
   }
```

**Inputs**:
- `user_intent`: User's project description
- `memory_tree`: List of files in `.ralph/memory/`

**Expected Output**: Valid JSON written to `.ralph/prd.json`.

**Failure Handling**: Retries up to 3 times if JSON parsing fails.

## 3. Developer Role

**Purpose**: Implement each task from the PRD.

**Trigger**: For each pending user story in `prd.json`.

**Prompt Template**:

```
ROLE: Developer (Ralph). TASK: {task_id}
DESC: {description}
CRITERIA: {acceptance_criteria}

CONTEXT:
You have access to documentation in:
{memory_tree}

INSTRUCTIONS:
1. PLAN your approach.
2. IMPLEMENT the code.
3. RUN '{test_cmd}' to verify.
4. Only output "STATUS: SUCCESS" if tests pass.
5. Update the relevant .ralph/memory/ for the next agent.

MEMORY RULES:
- You MUST keep up-to-date documentation.
- Keep the documentation concise. Keep task references minimal.
- Split your knowledge into the appropriate markdown files.
- Use YAML frontmatter with type: wiki.
- The file names MUST be unique and descriptive.
- You can create directories under .ralph/memory/ if needed.

FEEDBACK: {previous_errors}
```

**Inputs**:
- `task_id`: e.g., "TASK-001"
- `description`: Task description from PRD
- `acceptance_criteria`: List of criteria from PRD
- `memory_tree`: Available documentation files
- `test_cmd`: Extracted from memory (default: `pytest`)
- `previous_errors`: Contents of `progress.txt` if exists

**Success Signal**: Agent must output "STATUS: SUCCESS" when tests pass.

## Verification & Retry Mechanism

### Verification Flow

```
Agent claims "STATUS: SUCCESS"
         |
         v
   Run test command
         |
    +----+----+
    |         |
 pass       fail
    |         |
    v         v
 commit    retry
```

### Retry Logic

| Setting | Value |
|---------|-------|
| Max Retries | 3 |
| Timeout | 600 seconds |

**On Failure**:
1. Error written to `.ralph/progress.txt`
2. Error passed in `FEEDBACK` field on next attempt
3. After 3 failures, execution halts

### Failure Recording

Failures are persisted to `progress.txt` with format:

```
Attempt {n} Failed: {reason}
{error_detail}
```

This feedback loop allows the agent to learn from previous errors.

### Git Integration

On successful verification:
1. Task status set to "completed" in `prd.json`
2. Commit created: `git commit -am "Ralph: {TASK-ID}"`
3. `progress.txt` deleted if exists

## Prompt Design Principles

1. **Role Assignment**: Each prompt starts with explicit role definition
2. **Context Injection**: Memory files listed for agent reference
3. **Strict Output Format**: Rules enforce parseable output (JSON, markdown)
4. **Verification Gate**: Agent claims are validated by running actual tests
5. **Error Feedback**: Previous failures inform retry attempts
