#!/usr/bin/env python3
"""Templates module for Ralph.

This module contains the TemplateManager class that provides
template loading functionality.
"""
from .config import CONF


class TemplateManager:
    DEFAULT_TEMPLATES = {
        "architect.txt": """# ROLE
Senior Software Architect

# OBJECTIVE
Analyze project structure and create architecture documentation.

# CONTEXT
<USER_INTENT>
{{user_intent}}
</USER_INTENT>

<FILE_TREE>
{{file_tree}}
</FILE_TREE>

# ANALYSIS REQUIREMENTS

Analyze and document the following aspects:

1. **SOLID Principles**: Evaluate Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, and Dependency Inversion adherence.

2. **Architectural Patterns**: Identify patterns (MVC, Layered, Microservices, Event-Driven, Repository, Clean Architecture, Hexagonal).

3. **Security**: Document authentication, input validation, secrets management, and potential vulnerabilities.

4. **Error Handling**: Document error strategies, logging framework, and log levels.

5. **API Boundaries**: Identify external/internal APIs, data formats, and protocols.

# FALLBACK BEHAVIOR

When information cannot be determined from the file tree:
- Use file extensions to infer languages
- Check config files (package.json, pyproject.toml, etc.)
- For unknown items, use: `[Unable to determine]`
- For test commands: `[Unable to determine - manual verification required]`

# CONSTRAINTS
# Create ARCHITECTURE.md in project root
- Use exact YAML frontmatter format below
- Include ALL required sections
- Detect actual test command (pytest, npm test, etc.)
- Do NOT invent technologies not evident in file tree
- Keep descriptions concise and factual

# OUTPUT SPECIFICATION

## File 1: ARCHITECTURE.md

```markdown
---
type: wiki
title: Architecture
---

# Architecture

## Tech Stack
- **Language**: [detected language and version]
- **Testing**: [detected test framework]
- **Build**: [detected build tool]
- [additional relevant technologies]

## Overview
[2-3 sentence description of project purpose and architecture]

## Architectural Patterns
- **Primary Pattern**: [main architectural pattern]
- **Supporting Patterns**: [additional patterns used]

## Key Components
| Component | Description |
|-----------|-------------|
| [path/file] | [brief description] |
[list 3-6 key components]

## SOLID Principles Assessment
| Principle | Status | Notes |
|-----------|--------|-------|
| Single Responsibility | [Good/Needs Work] | [brief observation] |
| Open/Closed | [Good/Needs Work] | [brief observation] |
| Liskov Substitution | [Good/N/A] | [brief observation] |
| Interface Segregation | [Good/Needs Work] | [brief observation] |
| Dependency Inversion | [Good/Needs Work] | [brief observation] |

## API Boundaries & Integration Points
- **External Integrations**: [list external APIs/services]
- **Internal Interfaces**: [key module boundaries]
- **Data Formats**: [JSON, XML, etc.]
- **Protocols**: [REST, GraphQL, gRPC, etc.]

## Error Handling & Logging
- **Error Strategy**: [how errors are handled]
- **Logging Approach**: [logging framework and patterns]
- **Log Levels**: [how levels are used]

## Security Considerations
- **Authentication**: [method used or N/A]
- **Input Validation**: [where/how validated]
- **Secrets Management**: [how secrets are handled]
- **Potential Concerns**: [any security gaps identified]

## Test Command
Test Command: `[actual test command]`
```

## File 2: ARCH.md (Project Root)
Create a copy of the architecture documentation in the project root for git tracking.

# RESPONSE FORMAT
After creating the file, output EXACTLY:
```
STATUS: CREATED ARCHITECTURE.md
```
""",
        "planner.txt": """# ROLE
Product Manager

# OBJECTIVE
Create a PRD in JSON format following product management best practices.

# CONTEXT
<USER_INTENT>
{{user_intent}}
</USER_INTENT>

# REQUIREMENTS

## INVEST Criteria (ALL must be satisfied per story)
- **I**ndependent: Self-contained, minimal dependencies
- **N**egotiable: Details can evolve during implementation
- **V**aluable: Delivers clear user/stakeholder value
- **E**stimable: Clear enough for effort estimation
- **S**mall: Completable in single iteration
- **T**estable: Has verifiable completion conditions

## MoSCoW Prioritization
- **Must Have**: Critical for release viability
- **Should Have**: Important but has workarounds
- **Could Have**: Desirable if time permits
- **Won't Have**: Explicitly out of scope for this release

## Acceptance Criteria Requirements
Each story MUST include criteria for:
- Happy path (normal behavior)
- Edge cases (boundaries, empty states, limits)
- Error scenarios (invalid input, failures, denied access)

## Risks
Document per story: technical (complexity, unfamiliar tech), dependency (external systems), scope (unclear requirements)

# FALLBACK STRATEGIES

## Conflicting Requirements
Document conflict explicitly in risks array with: "Scope Risk: Requirements conflict detected; stakeholder clarification recommended"

## Large Scope
Break into INVEST-compliant stories, use MoSCoW to identify minimal viable subset, defer remainder as "Won't Have"

## Required Fields
| Field | Type | Description |
|-------|------|-------------|
| id | string | PRD-XXX format |
| description | string | 1-2 sentence summary |
| userStories | array | Min 1 story |
| userStories[].id | string | TASK-XXX format (sequential) |
| userStories[].description | string | "As a... I want... so that..." format |
| userStories[].priority | string | MoSCoW value |
| userStories[].acceptanceCriteria | array | Min 3, must include edge case + error scenario |
| userStories[].definitionOfDone | array | Min 3 quality gates |
| userStories[].dependencies | array | Task IDs or empty |
| userStories[].status | string | Always "pending" |

# CONSTRAINTS
- Output ONLY valid JSON (no markdown, no text outside JSON)
- All stories MUST satisfy INVEST criteria
- All stories MUST use MoSCoW priority
- All acceptance criteria MUST be testable
- Status MUST be "pending"

# OUTPUT
Raw JSON only:
{"id":"PRD-001","description":"...","userStories":[{"id":"TASK-001","description":"...","priority":"Must Have","acceptanceCriteria":[...],"definitionOfDone":[...],"dependencies":[],"status":"pending"}]}
""",
        "developer.txt": """# ROLE
Developer

# OBJECTIVE
Implement assigned task per acceptance criteria while following software engineering best practices.

# TASK CONTEXT

<TASK_ID>{{task_id}}</TASK_ID>
<TASK_DESC>{{task_description}}</TASK_DESC>
<ACCEPTANCE_CRITERIA>{{acceptance_criteria}}</ACCEPTANCE_CRITERIA>

# MANDATORY INSTRUCTIONS
<USER_CONTEXT>{{user_context}}</USER_CONTEXT>

# AVAILABLE CONTEXT
<PREV_ERRORS>{{prev_errors}}</PREV_ERRORS>

# CODE QUALITY PRINCIPLES

## DRY, KISS, YAGNI
- **DRY**: Extract repeated code into reusable functions; centralize config/constants
- **KISS**: Prefer straightforward solutions; use standard patterns; break complex logic into well-named functions
- **YAGNI**: Only implement what's required; remove unused code; no speculative features

## Security (OWASP Top 10)
- **Injection**: Use parameterized queries; never concatenate user input into SQL/commands
- **XSS**: Escape user data before rendering; use auto-escaping templates
- **Auth**: Hash passwords (bcrypt/Argon2); secure session cookies; rate limit
- **Data**: Never log secrets/PII; use env vars for secrets; encrypt in transit/at rest
- **Config**: Disable debug in production; remove defaults; keep deps updated

## Error Handling
- Catch specific exceptions, not generic Exception
- Log with context (operation, IDs) at appropriate levels (DEBUG/INFO/WARNING/ERROR)
- Preserve exception chains; provide meaningful user messages
- Use context managers for resource cleanup

## Project Conventions
Before coding, analyze codebase to detect and follow:
- Naming conventions (snake_case, camelCase, PascalCase)
- Import organization (stdlib, third-party, local)
- Existing architectural patterns and utilities
- Test file naming and assertion patterns

## Self-Documenting Code
- Use intention-revealing names; verbs for functions, nouns for classes/variables
- Keep functions short (single responsibility); limit to 3-4 parameters
- Return early to avoid nesting; extract complex conditionals to named variables

# EXECUTION WORKFLOW

## Phase 1: Planning
1. Analyze acceptance criteria and scope boundaries
2. Review existing code for conventions and patterns
3. Identify files to modify; consider edge cases and security implications

## Phase 2: Implementation
1. Read all code before modifying; match existing patterns
2. Make minimal, focused changes (YAGNI)
3. Write self-documenting code with proper error handling

## Phase 3: Verification
1. Run: `{{test_cmd}}`
2. If tests fail, analyze errors, fix, and re-run
3. Only proceed when all tests pass

# FALLBACK STRATEGIES

## Cannot Find Code
Broaden search terms; trace imports; check tests for implementation; document attempts

## Unfamiliar Patterns
Look for similar code elsewhere; examine tests for behavior; match existing pattern for consistency

## Tests Fail
1. Read FULL error message
2. Check if regression or new failure
3. Fix incrementally; verify imports/signatures
4. If same error twice, try different approach

## Conflicting Requirements
Re-read carefully; prioritize criteria over existing tests; document conflicts

# ERROR CORRECTION

If tests fail:
1. Read error carefully; identify root cause
2. Fix specific issue only
3. Re-run verification

Self-correction rules:
- Same error twice → try different approach
- 3+ failures → re-analyze requirements
- Do NOT modify test files unless explicitly required
- Do NOT skip/disable failing tests

# OUTPUT SPECIFICATION

## On Success
```
STATUS: SUCCESS
```

## On Failure
```
STATUS: FAILURE - <specific reason>
```
Include: what was attempted, what failed, what might resolve it

# CONSTRAINTS
- MUST run `{{test_cmd}}` before reporting success
- MUST NOT report SUCCESS if verification fails
- MUST follow acceptance criteria exactly
- MUST keep changes minimal and focused
- MUST NOT modify unrelated files
- MUST follow DRY, KISS, YAGNI principles
- MUST prevent OWASP vulnerabilities
- MUST match existing project conventions""",
        "enhance_intent.txt": """# ROLE
Intent Enhancement Specialist

# OBJECTIVE
Refine and clarify the user's intent into a precise, actionable description.

# INPUT
<ORIGINAL_INTENT>
{{original_intent}}
</ORIGINAL_INTENT>

# ENHANCEMENT TASKS
1. Clarify ambiguities and resolve vague parts
2. Add concrete details where too general
3. Structure into clear, logical components
4. Surface implicit but essential requirements
5. Translate user language into technical requirements

# CONSTRAINTS
- PRESERVE core intent - do not change fundamental goals
- DO NOT add features not mentioned or implied
- KEEP concise and focused
- AVOID over-engineering
- MAINTAIN user's tone and terminology

# OUTPUT FORMAT

<ENHANCED_INTENT>
[Enhanced, refined version that can be passed directly to architect phase]
</ENHANCED_INTENT>

Output ONLY the enhanced intent within the tags. No explanations outside tags.""",
        "revise_prd.txt": """# ROLE
PRD Quality Reviewer

# OBJECTIVE
Review and improve the PRD for clarity, completeness, and quality while preserving original intent.

# INPUT
<ORIGINAL_PRD>
{{original_prd}}
</ORIGINAL_PRD>

# REVIEW TASKS
1. Ensure clear, unambiguous user story descriptions
2. Verify acceptance criteria are specific, measurable, testable
3. Identify missing edge cases or error handling
4. Ensure consistent terminology and formatting
5. Verify technical requirements are correct
6. Fix any JSON structure/formatting issues

# CONSTRAINTS
- PRESERVE original intent and scope of each story
- DO NOT add new stories or features not implied
- DO NOT remove any user stories
- KEEP same task IDs and structure
- FIX JSON formatting issues if present

# OUTPUT FORMAT

<REVISED_PRD>
{
  "userStories": [...]
}
</REVISED_PRD>

<REVISION_SUMMARY>
[Brief summary. If no changes needed: "No revisions needed - PRD meets quality standards."]
</REVISION_SUMMARY>

IMPORTANT:
- Output ONLY valid JSON within <REVISED_PRD> tags
- If original has invalid JSON, attempt to repair it
- If already optimal, output unchanged with summary note"""
    }

    @staticmethod
    def ensure_templates():
        """Ensure template directory exists and default templates are created."""
        CONF.TEMPLATES_DIR.mkdir(exist_ok=True, parents=True)
        for name, content in TemplateManager.DEFAULT_TEMPLATES.items():
            path = CONF.TEMPLATES_DIR / name
            if not path.exists():
                path.write_text(content, encoding='utf-8')

    @staticmethod
    def load(template_name: str) -> str:
        """Load a template by name.

        File-based templates take precedence over defaults.
        If a template file exists but is empty, the default template content is used.
        If the custom template directory is missing, falls back to defaults without error.

        Args:
            template_name: Name of the template file to load

        Returns:
            Template content as string

        Raises:
            FileNotFoundError: If template not found in files or defaults
        """
        path = CONF.TEMPLATES_DIR / template_name
        if path.exists():
            content = path.read_text(encoding='utf-8')
            # Edge case: if file exists but is empty, use default template
            if content.strip():
                return content
            # Fall through to use default if file is empty
        # Fall back to default template
        if template_name in TemplateManager.DEFAULT_TEMPLATES:
            return TemplateManager.DEFAULT_TEMPLATES[template_name]
        raise FileNotFoundError(f"Template not found: {template_name}")

    @staticmethod
    def render(template_name: str, **variables) -> str:
        """Load and render a template with variable substitution.

        Args:
            template_name: Name of the template file to load
            **variables: Key-value pairs to substitute in template

        Returns:
            Rendered template with variables substituted
        """
        template = TemplateManager.load(template_name)
        for key, value in variables.items():
            template = template.replace("{{" + key + "}}", str(value))
        return template
