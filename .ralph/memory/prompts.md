---
type: wiki
title: Agent Prompt Templates
created: 2026-01-20
---

# Agent Prompt Templates

This document describes the available prompt templates used by Ralph agents and explains their intended usage for users and developers.

## Overview

Ralph operates in three main agent roles, each triggered at a specific phase of the project lifecycle. Each role uses a distinct prompt template to ensure clarity, reproducibility, and automation.

### Agent Roles and Prompts

#### 1. Architect Role
- **Purpose:** Initializes the `.ralph/memory/` knowledge base with project architecture and test command.
- **When Used:** When the memory directory is empty (first run or reset).
- **Inputs:**
  - `user_intent`: Project description provided by the user.
  - `file_tree`: Output of `tree -L 2` or a fallback file listing.
- **Prompt Structure:**
  - Explicitly defines the role and task.
  - Requires output in markdown with YAML frontmatter.
  - Must define tech stack and test command.
- **Expected Output:** Creates `architecture.md` in `.ralph/memory/`.
- **Failure Handling:** Exits with error if memory remains empty after execution.

#### 2. Planner Role
- **Purpose:** Generates the Product Requirements Document (PRD) as a JSON file with user stories.
- **When Used:** When `.ralph/prd.json` does not exist.
- **Inputs:**
  - `user_intent`: Project description.
  - `memory_tree`: List of files in `.ralph/memory/`.
- **Prompt Structure:**
  - Role: Product Manager.
  - Outputs only valid JSON matching the required schema.
  - Lists available memory files for context.
- **Expected Output:** Valid PRD JSON written to `.ralph/prd.json`.
- **Failure Handling:** Retries up to 3 times if JSON parsing fails.

#### 3. Developer Role
- **Purpose:** Implements each task from the PRD.
- **When Used:** For each pending user story in `prd.json`.
- **Inputs:**
  - `task_id`, `description`, `acceptance_criteria` (from PRD)
  - `memory_tree`: Available documentation files
  - `test_cmd`: Extracted from memory (default: `pytest`)
  - `previous_errors`: Contents of `progress.txt` if exists
- **Prompt Structure:**
  - Role: Developer (Ralph)
  - Step-by-step instructions: plan, implement, verify, and document
  - Must output "STATUS: SUCCESS" only if tests pass
  - Must update documentation for the next agent
- **Expected Output:** Code changes, updated documentation, and status signal
- **Failure Handling:** Error feedback loop with up to 3 retries, using `progress.txt` for error details

## Usage Guidance
- **When to Use Each Prompt:**
  - Use the Architect prompt to (re)initialize project memory.
  - Use the Planner prompt to generate or update the PRD.
  - Use the Developer prompt for each implementation task.
- **How to Leverage Prompts:**
  - Review the prompt structure and required inputs before triggering an agent phase.
  - Ensure outputs match the strict format requirements (markdown, JSON, or status signals).
  - Use the error feedback mechanism to improve reliability and traceability.

## Prompt Design Principles
- Each prompt starts with an explicit role definition.
- Context is injected by listing relevant memory files.
- Output formats are strictly enforced for automation.
- Verification gates (test runs) ensure correctness.
- Error feedback is provided for retries and learning.

Refer to this document whenever you need to understand or customize the agent prompt templates or their intended usage.