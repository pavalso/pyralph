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

class EasterEggs:
    """Easter egg messages for successful task completions."""

    MESSAGES = [
        "🥚 I didn't choose the task life, the task life chose me.",
        "🥚 Ralph: The Gift That Keeps On Giving™",
        "🥚 I'm making this up as I go along, but I'm really good at it.",
        "🥚 Excellent work! Ralph is pleased.",
        "🥚 Task complete. Have you tried debugging with ✨ crystals ✨?",
        "🥚 Success! Ralph would give you a medal, but he's an AI.",
        "🥚 📋 This task completion brought to you by trial and error.",
        "🥚 Even I'm impressed! And I'm an AI.",
        "🥚 One small task for Ralph, one giant leap for your codebase.",
        "🥚 Why do programmers prefer dark mode? Because light attracts bugs!",
    ]

    @staticmethod
    def get_random_message(seed_value: int = 0) -> str:
        """
        Get a random easter egg message.

        Args:
            seed_value: Optional seed for deterministic behavior (for testing)

        Returns:
            A random easter egg message string
        """
        import random as rand_module
        rand_module.seed(seed_value)
        return rand_module.choice(EasterEggs.MESSAGES)

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

class ClaudeAgent:
    """The Interface to the AI Model."""

    def run(self, prompt: str, tag: str) -> Tuple[bool, str]:
        Logger.file_log(prompt, "PROMPT", tag)

        # Display prompt in verbose mode
        if Logger.verbose:
            Logger.debug(f"=== CLAUDE PROMPT [{tag}] ===", "CYAN")
            Logger.debug(prompt, "CYAN")
            Logger.debug("=" * 40, "CYAN")

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
                # Display error in verbose mode
                if Logger.verbose:
                    Logger.debug(f"=== CLAUDE ERROR [{tag}] ===", "RED")
                    Logger.debug(f"STDOUT:\n{result.stdout}", "RED")
                    Logger.debug(f"STDERR:\n{result.stderr}", "RED")
                    Logger.debug("=" * 40, "RED")
                return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

            Logger.file_log(log_content, "RESPONSE", tag)

            # Display response in verbose mode
            if Logger.verbose:
                Logger.debug(f"=== CLAUDE RESPONSE [{tag}] ===", "GREEN")
                Logger.debug(result.stdout, "GREEN")
                Logger.debug("=" * 40, "GREEN")

            return True, result.stdout

        except Exception as e:
            Logger.file_log(str(e), "SYSTEM_EXCEPTION", tag)
            if Logger.verbose:
                Logger.debug(f"=== CLAUDE EXCEPTION [{tag}] ===", "RED")
                Logger.debug(str(e), "RED")
                Logger.debug("=" * 40, "RED")
            return False, str(e)

# ==============================================================================
# ORCHESTRATOR
# ==============================================================================

class RalphOrchestrator:
    def __init__(self, easter_eggs: bool = True):
        self.agent = ClaudeAgent()
        self.memory = MemoryManager()
        self.easter_eggs = easter_eggs
        CONF.ensure_directories()
        Shell.check_dependencies()
        self._validate_memory_on_startup()

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

                # Create task-specific branch from PRD branch
                task_id = task['id']
                description = task['description']

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

                    # Append easter egg message if enabled
                    if self.easter_eggs:
                        egg_message = EasterEggs.get_random_message(hash(task['id']) % 10000)
                        Logger.info(f"   {egg_message}", "MAGENTA")

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
               "  ralph --phase architect        # Run architect phase only\n"
               "  ralph --phase planner          # Run planner phase only\n"
               "  ralph --phase execute          # Run execute phase only\n"
               "  ralph --accept-all             # Run all phases without prompts\n"
               "  ralph --phase execute --accept-all  # Execute with no prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Ralph {get_version()}"
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

    parser.add_argument(
        "--no-easter-eggs",
        action="store_true",
        help="Disable easter egg messages on successful task completion"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging and display Claude CLI prompts and responses"
    )

    args = parser.parse_args()
    easter_eggs = not args.no_easter_eggs
    Logger.set_verbose(args.verbose)
    RalphOrchestrator(easter_eggs=easter_eggs).start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()