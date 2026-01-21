#!/usr/bin/env python3
import json
import subprocess
import sys
import re
import shutil
import datetime
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Tuple

# ==============================================================================
# CONFIGURATION
# ==============================================================================

@dataclass
class Config:
    # CRITICAL CHANGE: Use Path.cwd() so it works on the folder you are IN,
    # not the folder where the script IS.
    BASE_DIR: Path = Path.cwd()
    ROOT_DIR: Path = BASE_DIR / ".ralph"
    MEMORY_DIR: Path = ROOT_DIR / "memory"
    ARCHIVE_DIR: Path = ROOT_DIR / "archive"
    PRD_FILE: Path = ROOT_DIR / "prd.json"
    PROGRESS_FILE: Path = ROOT_DIR / "progress.txt"
    LOG_FILE: Path = ROOT_DIR / "ralph_log.txt"
    
    # Limits
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 600

    def ensure_directories(self):
        for path in [self.ROOT_DIR, self.MEMORY_DIR, self.ARCHIVE_DIR]:
            path.mkdir(exist_ok=True, parents=True)

CONF = Config()

# ==============================================================================
# UTILITIES & LOGGING
# ==============================================================================

class Logger:
    """Handles console output and persistent file logging."""
    
    COLORS = {
        "RESET": "\033[0m", "GREEN": "\033[92m", "RED": "\033[91m",
        "CYAN": "\033[96m", "YELLOW": "\033[93m", "MAGENTA": "\033[95m"
    }

    @staticmethod
    def info(msg: str, color: str = "RESET"):
        c_code = Logger.COLORS.get(color, Logger.COLORS["RESET"])
        print(f"{c_code}{msg}{Logger.COLORS['RESET']}")

    @staticmethod
    def file_log(content: str, type: str, tag: str = "UNKNOWN"):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        icons = {"PROMPT": "➡️", "RESPONSE": "⬅️", "ERROR": "❌", "INFO": "ℹ️"}
        icon = icons.get(type, "❓")
        
        entry = (
            f"\n{'='*60}\n"
            f"{icon} [{timestamp}] TYPE: {type} | TAG: {tag}\n"
            f"{'='*60}\n{content}\n"
        )
        try:
            with open(CONF.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"⚠️ Log Error: {e}")

class GitUtils:
    """Git-related utilities for workflow management."""

    @staticmethod
    def detect_default_branch() -> str:
        """
        Detect the default branch of the repository.

        First tries to get the remote HEAD, falls back to current branch.
        Returns branch name without 'origin/' prefix (e.g., 'main', 'master').
        """
        # Try to get remote HEAD
        stdout, stderr, code = Shell.run("git symbolic-ref refs/remotes/origin/HEAD")
        if code == 0:
            # Output format: "ref: refs/remotes/origin/main"
            match = re.search(r'refs/remotes/origin/(.+)$', stdout.strip())
            if match:
                return match.group(1)

        # Fallback: get current branch
        stdout, stderr, code = Shell.run("git rev-parse --abbrev-ref HEAD")
        if code == 0:
            branch = stdout.strip()
            if branch != "HEAD":  # Not detached
                return branch

        # Last resort default
        return "main"

    @staticmethod
    def create_feature_branch(base_branch: str, feature_branch_name: str) -> bool:
        """
        Create and checkout a new feature branch from the specified base branch.

        Args:
            base_branch: The branch to branch from (e.g., 'main', 'master')
            feature_branch_name: The name for the new feature branch (e.g., 'feature/task-001')

        Returns:
            True if successful, False otherwise.
        """
        # Ensure we're on the base branch first
        stdout, stderr, code = Shell.run(f"git checkout {base_branch}")
        if code != 0:
            return False

        # Create and checkout the feature branch
        stdout, stderr, code = Shell.run(f"git checkout -b {feature_branch_name}")
        return code == 0

    @staticmethod
    def create_task_branch(prd_branch: str, task_id: str, description: str) -> Tuple[bool, str]:
        """
        Create and checkout a task-specific branch from the PRD branch.

        Args:
            prd_branch: The PRD feature branch to branch from (e.g., 'feature/git-workflow-prd')
            task_id: The task ID (e.g., 'TASK-003')
            description: The task description to convert to slug

        Returns:
            Tuple of (success: bool, branch_name: str)
        """
        # Convert description to slug (lowercase, replace spaces with hyphens)
        slug = description.lower().replace(' ', '-').replace('_', '-')
        # Remove any characters that aren't alphanumeric or hyphens
        slug = re.sub(r'[^a-z0-9-]', '', slug)
        # Remove duplicate hyphens
        slug = re.sub(r'-+', '-', slug)
        # Remove leading/trailing hyphens
        slug = slug.strip('-')

        task_branch = f"task/{task_id.lower()}-{slug}"

        # Ensure we're on the PRD branch first
        stdout, stderr, code = Shell.run(f"git checkout {prd_branch}")
        if code != 0:
            return False, task_branch

        # Create and checkout the task branch
        stdout, stderr, code = Shell.run(f"git checkout -b {task_branch}")
        if code == 0:
            return True, task_branch
        return False, task_branch

    @staticmethod
    def merge_task_to_prd(prd_branch: str, task_branch: str, task_id: str, description: str) -> bool:
        """
        Merge a task branch back to the PRD branch with --no-ff flag.

        Args:
            prd_branch: The PRD feature branch to merge into (e.g., 'feature/git-workflow-prd')
            task_branch: The task branch to merge (e.g., 'task/task-003-describe')
            task_id: The task ID for commit message (e.g., 'TASK-003')
            description: The task description for commit message

        Returns:
            True if successful, False otherwise.
        """
        # Checkout the PRD branch
        stdout, stderr, code = Shell.run(f"git checkout {prd_branch}")
        if code != 0:
            return False

        # Merge with --no-ff to create a merge commit
        commit_message = f"Merge {task_id}: {description}"
        cmd = f'git merge --no-ff {task_branch} -m "{commit_message}"'
        stdout, stderr, code = Shell.run(cmd)
        return code == 0

    @staticmethod
    def merge_prd_to_default(default_branch: str, prd_branch: str) -> bool:
        """
        Merge the PRD feature branch to the default branch with --no-ff flag.

        Args:
            default_branch: The default branch (e.g., 'main', 'master')
            prd_branch: The PRD feature branch to merge (e.g., 'feature/git-workflow-prd')

        Returns:
            True if successful, False otherwise.
        """
        # Checkout the default branch
        stdout, stderr, code = Shell.run(f"git checkout {default_branch}")
        if code != 0:
            return False

        # Merge with --no-ff to create a merge commit
        commit_message = f"Merge PRD: {prd_branch}"
        cmd = f'git merge --no-ff {prd_branch} -m "{commit_message}"'
        stdout, stderr, code = Shell.run(cmd)
        return code == 0

class Shell:
    """Safe wrapper for subprocess calls."""

    @staticmethod
    def check_dependencies():
        if not shutil.which("claude"):
            Logger.info("❌ Error: 'claude' CLI not found.", "RED")
            sys.exit(1)
        if not shutil.which("git"):
            Logger.info("❌ Error: 'git' not found.", "RED")
            sys.exit(1)

    @staticmethod
    def run(command: str, timeout: int = 30) -> Tuple[str, str, int]:
        try:
            # shell=True defaults to CWD, which is what we want
            result = subprocess.run(
                command, shell=True, capture_output=True, 
                text=True, encoding='utf-8', timeout=timeout
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command Timed Out", 1
        except Exception as e:
            return "", str(e), 1

    @staticmethod
    def get_file_tree() -> str:
        # We explicitly list '.' to ensure we are looking at CWD
        stdout, _, code = Shell.run("tree -L 2 --noreport -I 'node_modules|venv|.git|.ralph|__pycache__'")
        if code == 0 and stdout.strip(): return stdout
        
        # Fallback python walker using CWD
        lines = []
        for path in CONF.BASE_DIR.glob('*'):
            if path.name not in ['node_modules', 'venv', '.git', '.ralph', '__pycache__']:
                lines.append(f"├── {path.name}")
        return "\n".join(lines)

class JsonUtils:
    """Robust JSON parsing for LLM outputs."""
    
    @staticmethod
    def parse(text: str) -> dict:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match: text = match.group(1)
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end != -1: text = text[start:end+1]
        text = re.sub(r"//.*", "", text)
        return json.loads(text)

# ==============================================================================
# CORE COMPONENTS
# ==============================================================================

class MemoryManager:
    """Manages the agent's wiki and context window."""

    @staticmethod
    def get_structure() -> str:
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            return "(Memory Empty)"
        
        files = []
        for p in CONF.MEMORY_DIR.rglob('*'):
            if p.is_file() and not p.name.startswith('.'):
                try:
                    # Provide path relative to the Project Root (CWD)
                    rel_path = p.relative_to(CONF.BASE_DIR)
                    files.append(f"- {rel_path}")
                except ValueError:
                    continue # Should not happen if paths are correct
        return "\n".join(files)

    @staticmethod
    def extract_test_command() -> str:
        full_text = ""
        for path in CONF.MEMORY_DIR.rglob('*'):
             if path.suffix in ['.md', '.txt']:
                 try: full_text += path.read_text(encoding='utf-8')
                 except: continue
        
        match = re.search(r"Test Command.*?`([^`]+)`", full_text, re.IGNORECASE)
        if match: return match.group(1)
        if (CONF.BASE_DIR / "package.json").exists(): return "npm test"
        return "pytest"

class ClaudeAgent:
    """The Interface to the AI Model."""
    
    def run(self, prompt: str, tag: str) -> Tuple[bool, str]:
        Logger.file_log(prompt, "PROMPT", tag)
        cmd_str = "claude -p --dangerously-skip-permissions"
        
        try:
            result = subprocess.run(
                cmd_str, input=prompt, capture_output=True, text=True,
                encoding='utf-8', shell=(sys.platform == 'win32'), timeout=CONF.TIMEOUT_SECONDS
            )
            
            log_content = result.stdout
            if result.stderr.strip():
                log_content += f"\n\n--- [CLI STDERR] ---\n{result.stderr}"

            if result.returncode != 0:
                Logger.file_log(log_content, "ERROR", tag)
                return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                
            Logger.file_log(log_content, "RESPONSE", tag)
            return True, result.stdout
            
        except Exception as e:
            Logger.file_log(str(e), "SYSTEM_EXCEPTION", tag)
            return False, str(e)

# ==============================================================================
# ORCHESTRATOR
# ==============================================================================

class RalphOrchestrator:
    def __init__(self):
        self.agent = ClaudeAgent()
        self.memory = MemoryManager()
        CONF.ensure_directories()
        Shell.check_dependencies()

    def run_architect(self, user_intent: str):
        Logger.info("\n🕵️  Architect: Initializing Memory...", "CYAN")
        prompt = f"""
        ROLE: Senior Architect. TASK: Initialize .ralph/memory/
        INTENT: "{user_intent}"
        FILES: {Shell.get_file_tree()}

        STRICT RULES:
        1. Output ONLY markdown.
        2. Define Tech Stack & Test Command.
        3. Use YAML frontmatter with type: wiki.

        ACTION: Create `architecture.md` in .ralph/memory/.
        """
        success, _ = self.agent.run(prompt, "ARCHITECT")
        if not success or not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("⚠️ Architect failed.", "RED")
            sys.exit(1)

        # Detect and store default branch
        default_branch = GitUtils.detect_default_branch()
        git_workflow_content = f"""---
type: wiki
title: Git Workflow Context
created: {datetime.datetime.now().strftime('%Y-%m-%d')}
---

# Git Workflow

## Default Branch
- **Name**: `{default_branch}`

This branch is detected at the start of the workflow and used as the base for feature development.
"""
        (CONF.MEMORY_DIR / "git_workflow.md").write_text(git_workflow_content, encoding='utf-8')

        Logger.info("✅ Memory Initialized.", "GREEN")

    def run_planner(self, user_intent: str):
        Logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        memory_map = self.memory.get_structure()
        
        prompt = f"""
        ROLE: Product Manager. 
        TASK: Create PRD JSON for "{user_intent}".
        
        AVAILABLE FILES:
        {memory_map}
        
        INSTRUCTIONS:
        1. EXPLORE: Understand the project.
        2. THINK: Plan user stories.
        3. ACT: Output the PRD JSON.
        
        STRICT RULES:
        1. Output ONLY valid JSON.
        2. Schema: {{ "featureBranch": "str", "userStories": [ {{ "id": "TASK-001", "description": "...", "acceptanceCriteria": ["..."], "status": "pending" }} ] }}
        """
        
        for attempt in range(3):
            success, raw = self.agent.run(prompt, "PLANNER")
            if not success: continue

            try:
                data = JsonUtils.parse(raw)
                if "userStories" not in data: raise ValueError("Missing userStories")
                CONF.PRD_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
                Logger.info(f"✅ PRD Created ({len(data['userStories'])} stories).", "GREEN")
                return
            except Exception as e:
                Logger.info(f"⚠️ JSON Error (Attempt {attempt+1}): {e}", "YELLOW")
        
        Logger.info("❌ Planning Failed.", "RED")
        sys.exit(1)

    def execute_loop(self):
        prd = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        test_cmd = self.memory.extract_test_command()

        Logger.info(f"\n🚀 Starting Loop. Verify Command: '{test_cmd}'", "YELLOW")

        for task in prd.get('userStories', []):
            if task.get('status') == 'completed': continue

            Logger.info(f"\n▶️  Task {task['id']}: {task['description']}", "CYAN")
            self._execute_task(task, test_cmd)

            # Save state
            CONF.PRD_FILE.write_text(json.dumps(prd, indent=2), encoding='utf-8')

        Logger.info("\n🎉 All Tasks Complete.", "GREEN")

        # Merge PRD branch to default branch
        default_branch = GitUtils.detect_default_branch()
        prd_branch = prd.get('featureBranch', 'feature/task-002')
        Logger.info(f"\n📦 Merging {prd_branch} to {default_branch}...", "CYAN")
        if GitUtils.merge_prd_to_default(default_branch, prd_branch):
            Logger.info(f"✅ PRD merged to default branch with merge commit.", "GREEN")
        else:
            Logger.info(f"⚠️ PRD merge failed, but all tasks completed.", "YELLOW")

        self._archive_prd()

    def _execute_task(self, task: dict, test_cmd: str):
        retries = 0
        task_branch = None  # Store task branch name across retries

        while retries < CONF.MAX_RETRIES:
            memory_tree = self.memory.get_structure()
            prev_errors = CONF.PROGRESS_FILE.read_text(encoding='utf-8') if CONF.PROGRESS_FILE.exists() else ""

            # On first retry (retries == 0), create feature branch and task branch
            if retries == 0:
                prd = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
                default_branch = GitUtils.detect_default_branch()
                feature_branch = prd.get('featureBranch', f"feature/task-002")

                Logger.info(f"   📦 Creating feature branch: {feature_branch}", "CYAN")
                if GitUtils.create_feature_branch(default_branch, feature_branch):
                    Logger.info(f"   ✅ Feature branch created and checked out.", "GREEN")
                else:
                    self._record_failure(retries, "Feature Branch Creation Failed", f"Failed to create {feature_branch}")
                    retries += 1
                    continue

                # Create task-specific branch from PRD branch
                task_id = task['id']
                description = task['description']
                Logger.info(f"   📦 Creating task branch from PRD branch: {feature_branch}", "CYAN")
                success, task_branch = GitUtils.create_task_branch(feature_branch, task_id, description)
                if success:
                    Logger.info(f"   ✅ Task branch created and checked out: {task_branch}", "GREEN")
                else:
                    self._record_failure(retries, "Task Branch Creation Failed", f"Failed to create task branch from {feature_branch}")
                    retries += 1
                    continue

            prompt = f"""
            ROLE: Developer (Ralph). TASK: {task['id']}
            DESC: {task['description']}
            CRITERIA: {task['acceptanceCriteria']}

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

            FEEDBACK: {prev_errors}
            """

            success, output = self.agent.run(prompt, f"WORKER-{task['id']}")

            if not success:
                self._record_failure(retries, "CLI Crash", output)
                retries += 1
                continue

            if "STATUS: SUCCESS" in output:
                Logger.info("   🔒 Verifying Agent's Claim...", "YELLOW")
                stdout, stderr, code = Shell.run(test_cmd)
                verify_log = f"CMD: {test_cmd}\nEXIT CODE: {code}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                Logger.file_log(verify_log, "VERIFICATION", f"WORKER-{task['id']}")

                if code == 0:
                    Logger.info(f"   ✅ Verified.", "GREEN")

                    # Merge task branch to PRD branch (only if we have a task_branch)
                    if task_branch:
                        prd = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
                        prd_branch = prd.get('featureBranch', f"feature/task-002")
                        task_id = task['id']
                        description = task['description']

                        Logger.info(f"   📦 Merging {task_branch} to {prd_branch}...", "CYAN")
                        if GitUtils.merge_task_to_prd(prd_branch, task_branch, task_id, description):
                            Logger.info(f"   ✅ Merged with merge commit.", "GREEN")
                        else:
                            Logger.info(f"   ⚠️ Merge failed, but task is verified.", "YELLOW")

                    task['status'] = 'completed'
                    Shell.run(f'git commit -am "Ralph: {task["id"]}" --allow-empty')
                    if CONF.PROGRESS_FILE.exists(): CONF.PROGRESS_FILE.unlink()
                    return
                else:
                    Logger.info("   🛑 Agent Hallucinated Success.", "RED")
                    self._record_failure(retries, "Verification Failed", stderr[-1000:])
            else:
                self._record_failure(retries, "Agent Reported Failure", output[-1000:])

            retries += 1

        Logger.info(f"🛑 Max retries for {task['id']}.", "RED")
        sys.exit(1)

    def _record_failure(self, retry, reason, detail):
        msg = f"Attempt {retry+1} Failed: {reason}\n{detail}"
        CONF.PROGRESS_FILE.write_text(msg, encoding='utf-8')
        Logger.file_log(msg, "FAILURE_RECORD", f"RETRY-{retry+1}")
        Logger.info(f"   ⚠️ Retry {retry+1}/{CONF.MAX_RETRIES}: {reason}", "RED")

    def _archive_prd(self):
        if not CONF.PRD_FILE.exists(): return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = CONF.ARCHIVE_DIR / f"prd_{ts}.json"
        shutil.move(str(CONF.PRD_FILE), str(dest))
        Logger.info(f"📦 PRD Archived to {dest}", "MAGENTA")

    def start(self, phase: str = "all", accept_all: bool = False):
        Logger.info(f"🤖 Ralph Agent active in: {CONF.BASE_DIR}", "GREEN")

        # Handle phase-specific execution
        if phase == "architect":
            Logger.info("📋 Phase specified: architect only", "YELLOW")
            user_intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
            if not user_intent: sys.exit(0)
            self.run_architect(user_intent)
            Logger.info("✅ Architect phase complete.", "GREEN")
            return

        elif phase == "planner":
            Logger.info("📋 Phase specified: planner only", "YELLOW")
            if not any(CONF.MEMORY_DIR.iterdir()):
                Logger.info("❌ Memory does not exist. Run architect phase first.", "RED")
                sys.exit(1)
            user_intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
            if not user_intent: sys.exit(0)
            self.run_planner(user_intent)
            Logger.info("✅ Planner phase complete.", "GREEN")
            return

        elif phase == "execute":
            Logger.info("📋 Phase specified: execute only", "YELLOW")
            if not CONF.PRD_FILE.exists():
                Logger.info("❌ PRD does not exist. Run planner phase first.", "RED")
                sys.exit(1)
            self.execute_loop()
            Logger.info("✅ Execute phase complete.", "GREEN")
            return

        elif phase == "all":
            Logger.info("📋 Running all phases...", "YELLOW")

            # Step 1: Architect
            if not any(CONF.MEMORY_DIR.iterdir()):
                user_intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
                if not user_intent: sys.exit(0)
                self.run_architect(user_intent)
            else:
                Logger.info("📋 Memory already exists, skipping architect phase.", "YELLOW")

            # Step 2: Planner
            if not CONF.PRD_FILE.exists():
                user_intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
                if not user_intent: sys.exit(0)
                self.run_planner(user_intent)
            else:
                Logger.info("📋 PRD already exists, skipping planner phase.", "YELLOW")

            # Step 3: Execute
            self.execute_loop()
            Logger.info("✅ All phases complete.", "GREEN")
            return

        # Invalid phase (should not reach here with argparse validation)
        Logger.info(f"❌ Unknown phase: {phase}", "RED")
        sys.exit(1)

def main():
    """Entry point for the ralph CLI."""
    parser = argparse.ArgumentParser(
        description="Ralph - Autonomous Software Development Agent",
        epilog="Examples:\n"
               "  ralph                          # Run all phases\n"
               "  ralph --phase architect        # Run architect phase only\n"
               "  ralph --phase planner          # Run planner phase only\n"
               "  ralph --phase execute          # Run execute phase only\n"
               "  ralph --accept-all             # Run all phases without prompts\n"
               "  ralph --phase execute --accept-all  # Execute with no prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--phase",
        choices=["architect", "planner", "execute", "all"],
        default="all",
        help="Select which phase to run (default: all)"
    )

    parser.add_argument(
        "--accept-all",
        action="store_true",
        help="Skip user feedback prompts and proceed with all phases"
    )

    args = parser.parse_args()
    RalphOrchestrator().start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()