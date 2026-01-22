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

# Import agents
from agents import get_agent, list_agents

# ==============================================================================
# CONFIGURATION
# ==============================================================================

@dataclass
class Config:
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

    # Class-level verbose flag
    verbose = False

    @staticmethod
    def set_verbose(enabled: bool):
        """Enable or disable verbose mode."""
        Logger.verbose = enabled

    @staticmethod
    def info(msg: str, color: str = "RESET"):
        c_code = Logger.COLORS.get(color, Logger.COLORS["RESET"])
        print(f"{c_code}{msg}{Logger.COLORS['RESET']}")

    @staticmethod
    def debug(msg: str, color: str = "RESET"):
        """Print debug message only in verbose mode."""
        if Logger.verbose:
            c_code = Logger.COLORS.get(color, Logger.COLORS["RESET"])
            print(f"{c_code}[DEBUG] {msg}{Logger.COLORS['RESET']}")

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
    def validate_memory() -> dict:
        """
        Validate all memory files are readable and not empty.

        Returns:
            dict with keys:
                - 'valid': bool (all files readable)
                - 'corrupted': list of file paths that failed to read
                - 'empty': list of file paths that are empty
                - 'total': int (total files checked)
        """
        result = {
            'valid': True,
            'corrupted': [],
            'empty': [],
            'total': 0
        }

        if not CONF.MEMORY_DIR.exists():
            return result

        for path in CONF.MEMORY_DIR.rglob('*'):
            if not path.is_file() or path.name.startswith('.'):
                continue

            result['total'] += 1

            try:
                content = path.read_text(encoding='utf-8')
                if not content.strip():
                    result['empty'].append(str(path.relative_to(CONF.BASE_DIR)))
                    result['valid'] = False
            except Exception as e:
                result['corrupted'].append(str(path.relative_to(CONF.BASE_DIR)))
                result['valid'] = False

        return result

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


# ==============================================================================
# ORCHESTRATOR
# ==============================================================================

class RalphOrchestrator:
    def __init__(self, agent_name: str = "claude"):
        """
        Initialize the Ralph orchestrator.

        Args:
            agent_name: Name of the agent to use (e.g., "claude")
        """
        # Initialize agent
        self.agent = get_agent(agent_name, timeout_seconds=CONF.TIMEOUT_SECONDS)

        # Set logger and config for the agent (if supported)
        if hasattr(self.agent, 'set_logger'):
            self.agent.set_logger(Logger)
        if hasattr(self.agent, 'set_config'):
            self.agent.set_config(CONF)

        # Check agent dependencies
        if not self.agent.check_dependencies():
            Logger.info(f"❌ Error: Agent '{self.agent.get_name()}' dependencies not satisfied.", "RED")
            sys.exit(1)

        self.memory = MemoryManager()
        CONF.ensure_directories()
        Shell.check_dependencies()
        self._validate_memory_on_startup()

    def run_architect(self, user_intent: str):
        Logger.info("\n🕵️  Architect: Initializing Memory...", "CYAN")

        prompt = f"""
ROLE: Senior Architect
OBJECTIVE: Initialize the project wiki in .ralph/memory/ for the intent below and ensure a deterministic test command is discoverable.

INTENT:
{user_intent}

PROJECT FILE TREE (depth 2):
{Shell.get_file_tree()}

CAPABILITIES & EXECUTION ENV:
- You can read/write files and run shell commands in the current working directory.
- All file paths are relative to the project root (CWD).
- Create files directly on disk; do not rely on stdout for file content.

DELIVERABLE:
- Create the file: .ralph/memory/architecture.md

CONTENT REQUIREMENTS for architecture.md:
- Use YAML frontmatter:
  ---
  type: wiki
  title: Architecture
  ---
- Include sections:
  1) Tech Stack
  2) Overview
  3) Key Components
  4) Risks & Assumptions
  5) **Test Command** — MUST include a literal line of the form:
     Test Command: `YOUR_TEST_COMMAND_HERE`
     (Exactly this label and backtick format so an automated regex can extract it.)
- Keep content concise and actionable.

OUTPUT RULES:
- Do NOT print the file content to stdout.
- You may print a one-line confirmation like:
  STATUS: CREATED .ralph/memory/architecture.md"""

        success, _ = self.agent.run(prompt, "ARCHITECT")
        if not success or not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("⚠️ Architect failed.", "RED")
            sys.exit(1)

        Logger.info("✅ Memory Initialized.", "GREEN")

    def run_planner(self, user_intent: str):
        Logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        memory_map = self.memory.get_structure()

        prompt = f"""
ROLE: Product Manager
TASK: Create a PRD for the intent below.

INTENT:
{user_intent}

AVAILABLE WIKI/MEMORY FILES (paths only):
{memory_map}

PROCESS:
1) EXPLORE: Infer the scope from available artifacts and the intent.
2) THINK: Define minimal, testable user stories that can be validated by an automated test command.
3) ACT: Output the PRD as strict JSON adhering to the schema below.

STRICT OUTPUT RULES:
- Output ONLY raw JSON (no markdown fences, no comments, no prose).
- Ensure the JSON is syntactically valid and UTF-8 safe.
- All IDs must be unique.
- Each user story MUST have a non-empty description, at least 3 objective acceptance criteria, and "status": "pending".

SCHEMA (example shape, not a template):
{{
  "id": "PRD-001",
  "description": "Short summary of the product or feature.",
  "userStories": [
    {{
      "id": "TASK-001",
      "description": "As a <user>, I want <capability> so that <outcome>.",
      "acceptanceCriteria": [
        "Given <context>, when <action>, then <verifiable outcome>",
        "Non-ambiguous criteria 2",
        "Non-ambiguous criteria 3"
      ],
      "status": "pending"
    }}
  ]
}}"""
        
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
            self._execute_task(prd, task, test_cmd)

            # Save state
            CONF.PRD_FILE.write_text(json.dumps(prd, indent=2), encoding='utf-8')

        Logger.info("\n🎉 All Tasks Complete.", "GREEN")

        self._archive_prd()

    def _execute_task(self, prd: dict, task: dict, test_cmd: str):
        retries = 0

        safe_prd_id = "".join(c for c in prd['id'] if c.isalnum() or c in ('-', '_'))
        safe_task_id = "".join(c for c in task['id'] if c.isalnum() or c in ('-', '_'))

        while retries < CONF.MAX_RETRIES:
            memory_tree = self.memory.get_structure()
            prev_errors = CONF.PROGRESS_FILE.read_text(encoding='utf-8') if CONF.PROGRESS_FILE.exists() else ""

            # 2. Load and Prepare User Context (Soft Guidelines)
            user_context = "No specific user preferences provided."
            prompt_md_path = CONF.BASE_DIR / "prompt.md"
            
            if prompt_md_path.exists():
                raw_text = prompt_md_path.read_text(encoding='utf-8')
                # Inject variables so the user can reference them if they want to
                user_context = raw_text.replace("{{PRD_ID}}", safe_prd_id)
                user_context = raw_text.replace("{{PRD_DESCRIPTION}}", prd['description'])
                user_context = raw_text.replace("{{TASK_ID}}", safe_task_id)
                user_context = raw_text.replace("{{TASK_DESCRIPTION}}", task['description'])
                user_context = raw_text.replace("{{TEST_CMD}}", test_cmd)

            # 3. Construct the Prompt
            prompt = f"""
ROLE: Developer (Ralph)
TASK ID: {task['id']}
OBJECTIVE: {task['description']}

CONTEXT FILES (paths only, under .ralph/memory/):
{memory_tree}

USER PREFERENCES & WORKFLOW (soft constraints; overrides defaults):
{user_context}

DEFAULT CAPABILITIES:
- You may read and write files within the current working directory (project root).
- You may run local build/test commands as part of verification.
- Do NOT perform any version control (e.g., git), networking, or package installation
  unless explicitly requested in USER PREFERENCES & WORKFLOW above.

EXECUTION FLOW (follow exactly):
1) PLAN:
   - Summarize the minimal, concrete code changes required to satisfy the task’s acceptance criteria.
2) IMPLEMENT:
   - Apply the necessary file edits to implement the plan.
3) VERIFY:
   - Run the test command:
     {test_cmd}
   - Only proceed if the command exits with code 0.
4) FINALIZE:
   - If and only if verification passed, print exactly:
     STATUS: SUCCESS
   - Otherwise, print exactly one line:
     STATUS: FAILURE - <brief reason>

NOTES:
- Keep output minimal and task-focused.
- If USER PREFERENCES & WORKFLOW requires special steps (e.g., git actions, environment setup),
  follow them explicitly; otherwise do not perform them.
- Do not include code fences around shell commands or file contents.

RETRY CONTEXT (from previous attempt, if any):
{prev_errors}"""

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

                    task['status'] = 'completed'
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

    def _validate_memory_on_startup(self):
        """
        Validate all memory files on startup.
        Warns user about corrupted or empty files, but continues gracefully.
        """
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            return

        result = self.memory.validate_memory()

        if result['total'] == 0:
            return

        if result['corrupted']:
            Logger.info(
                f"⚠️ Memory Validation: {len(result['corrupted'])} file(s) corrupted or unreadable:",
                "YELLOW"
            )
            for file_path in result['corrupted']:
                Logger.info(f"   - {file_path}", "YELLOW")

        if result['empty']:
            Logger.info(
                f"⚠️ Memory Validation: {len(result['empty'])} file(s) empty:",
                "YELLOW"
            )
            for file_path in result['empty']:
                Logger.info(f"   - {file_path}", "YELLOW")

        if result['valid']:
            Logger.debug(f"✅ Memory validation passed ({result['total']} files)", "GREEN")

    def _prompt_user_for_phase(self, phase_name: str) -> bool:
        """
        Prompt user to confirm running a phase. Returns True if user confirms (y), False if user declines (n).
        """
        response = input(f"{Logger.COLORS['YELLOW']}Run {phase_name} phase? (y/n): {Logger.COLORS['RESET']}").strip().lower()
        return response == 'y'

    def start(self, phase: str = "all", accept_all: bool = False):
        Logger.info(f"🤖 Ralph {self.agent.get_name()} Agent active in: {CONF.BASE_DIR}", "GREEN")

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
                # Prompt user before running architect phase (unless accept_all is True)
                if not accept_all and not self._prompt_user_for_phase("Architect"):
                    Logger.info("⏭️  Skipping architect phase.", "YELLOW")
                else:
                    user_intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
                    if not user_intent: sys.exit(0)
                    self.run_architect(user_intent)
            else:
                Logger.info("📋 Memory already exists, skipping architect phase.", "YELLOW")

            # Step 2: Planner
            if not CONF.PRD_FILE.exists():
                # Prompt user before running planner phase (unless accept_all is True)
                if not accept_all and not self._prompt_user_for_phase("Planner"):
                    Logger.info("⏭️  Skipping planner phase.", "YELLOW")
                else:
                    user_intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
                    if not user_intent: sys.exit(0)
                    self.run_planner(user_intent)
            else:
                Logger.info("📋 PRD already exists, skipping planner phase.", "YELLOW")

            # Step 3: Execute
            # Prompt user before running execute phase (unless accept_all is True)
            if not accept_all and not self._prompt_user_for_phase("Execute"):
                Logger.info("⏭️  Skipping execute phase.", "YELLOW")
            else:
                self.execute_loop()

            Logger.info("✅ All phases complete.", "GREEN")
            return

        # Invalid phase (should not reach here with argparse validation)
        Logger.info(f"❌ Unknown phase: {phase}", "RED")
        sys.exit(1)

def get_version() -> str:
    """Extract version from pyproject.toml."""
    try:
        pyproject_path = Path(__file__).parent / "pyproject.toml"
        with open(pyproject_path, "r") as f:
            for line in f:
                if line.startswith("version"):
                    # Extract version from line like: version = "0.1.0"
                    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', line)
                    if match:
                        return match.group(1)
    except Exception:
        pass
    return "unknown"

def main():
    """Entry point for the ralph CLI."""
    parser = argparse.ArgumentParser(
        description="Ralph - Autonomous Software Development Agent",
        epilog="Examples:\n"
               "  ralph                          # Run all phases\n"
               "  ralph architect        # Run architect phase only\n"
               "  ralph planner          # Run planner phase only\n"
               "  ralph execute          # Run execute phase only\n"
               "  ralph --accept-all             # Run all phases without prompts\n"
               "  ralph execute --accept-all  # Execute with no prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "phase",
        choices=["architect", "planner", "execute", "all"],
        default="all",
        nargs="?",
        help="Select which phase to run"
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Ralph {get_version()}"
    )

    parser.add_argument(
        "--accept-all",
        action="store_true",
        help="Skip user feedback prompts and proceed with all phases"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging and display Claude CLI prompts and responses"
    )

    parser.add_argument(
        "--agent",
        choices=list_agents(),
        default="claude",
        help="Select which agent to use (default: claude)"
    )

    args = parser.parse_args()
    Logger.set_verbose(args.verbose)
    RalphOrchestrator(agent_name=args.agent).start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()