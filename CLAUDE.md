# Ralph - Autonomous Software Development Agent
Ralph is an autonomous agent designed to iteratively build software projects by following a structured loop of exploration, planning, and action. It leverages file-based memory and a project plan to manage its tasks effectively.
Based on [Ralph Wiggum as a "Software engineer"](https://ghuntley.com/ralph/).

## 📂 The .ralph Context
The agent state is strictly file-based in `.ralph/`.

| Path | Purpose | Interaction Strategy |
|---|---|---|
| `.ralph/prd.json` | **Project Plan** | Read `userStories` array to find the current "pending" task. |
| `.ralph/memory/` | **Knowledge Base** | `ls` this dir first. Then `cat` specific files (e.g., `architecture.md`). |
| `.ralph/ralph_log.txt` | **Audit Trail** | Contains full prompts/responses. Use `tail -n 50` to debug errors. |
| `.ralph/progress.txt` | **Error State** | Exists only if the current task is failing. Contains the exact error. |

## 🛠️ CLI Interactions
Assumes `ralph` is in `$PATH`.

- **Run Agent**: `ralph` (Starts/Resumes the loop)
- **Hard Reset**: `rm -rf .ralph/` (Clears memory and plan)
- **Re-Plan**: `rm .ralph/prd.json` (Keeps memory, regenerates user stories)
- **Skip Task**: Edit `.ralph/prd.json`, change status to `"completed"`.

## 🧠 Knowledge Injection
To teach Ralph something without wasting prompt tokens repeatedly:
1. Create a markdown file in `.ralph/memory/` (e.g., `manual_api_docs.md`).
2. Paste the context there.
3. Ralph will see it in the file list on the next turn and read it if relevant.