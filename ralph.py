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
from typing import Any, Dict, List, Optional, Tuple

# Import agents
from agents import get_agent, list_agents
from agents.base import AgentError

# ==============================================================================
# CONFIGURATION
# ==============================================================================

@dataclass
class Config:
    BASE_DIR: Path = Path.cwd()
    ROOT_DIR: Path = BASE_DIR / ".ralph"
    MEMORY_DIR: Path = ROOT_DIR / "memory"
    ARCHIVE_DIR: Path = ROOT_DIR / "archive"
    TEMPLATES_DIR: Path = ROOT_DIR / "templates"
    PRD_FILE: Path = ROOT_DIR / "prd.json"
    PROGRESS_FILE: Path = ROOT_DIR / "progress.txt"
    LOG_FILE: Path = ROOT_DIR / "ralph_log.txt"

    # Limits
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 600

    def ensure_directories(self) -> None:
        for path in [self.ROOT_DIR, self.MEMORY_DIR, self.ARCHIVE_DIR, self.TEMPLATES_DIR]:
            path.mkdir(exist_ok=True, parents=True)

CONF = Config()

# ==============================================================================
# UTILITIES & LOGGING
# ==============================================================================

class Logger:
    COLORS = {"RESET": "\033[0m", "GREEN": "\033[92m", "RED": "\033[91m",
              "CYAN": "\033[96m", "YELLOW": "\033[93m", "MAGENTA": "\033[95m"}
    verbose = False
    no_color = False

    @staticmethod
    def set_no_color(enabled: bool): Logger.no_color = enabled
    @staticmethod
    def set_verbose(enabled: bool): Logger.verbose = enabled

    @staticmethod
    def _print_colored(msg: str, color: str = "RESET", prefix: str = ""):
        text = f"{prefix}{msg}" if prefix else msg
        if Logger.no_color: print(text)
        else: print(f"{Logger.COLORS.get(color, Logger.COLORS['RESET'])}{text}{Logger.COLORS['RESET']}")

    @staticmethod
    def info(msg: str, color: str = "RESET"): Logger._print_colored(msg, color)
    @staticmethod
    def debug(msg: str, color: str = "RESET"):
        if Logger.verbose: Logger._print_colored(msg, color, prefix="[DEBUG] ")
    @staticmethod
    def warning(msg: str): Logger._print_colored(msg, "YELLOW", prefix="[WARNING] ")
    @staticmethod
    def error(msg: str): Logger._print_colored(msg, "RED", prefix="[ERROR] ")

    @staticmethod
    def file_log(content: str, type: str, tag: str = "UNKNOWN") -> None:
        icons = {"PROMPT": "➡️", "RESPONSE": "⬅️", "ERROR": "❌", "INFO": "ℹ️"}
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n{'='*60}\n{icons.get(type, '❓')} [{ts}] TYPE: {type} | TAG: {tag}\n{'='*60}\n{content}\n"
        try:
            with open(CONF.LOG_FILE, "a", encoding="utf-8") as f: f.write(entry)
        except Exception: print(f"⚠️ Log Error")

class Shell:
    """Safe wrapper for subprocess calls."""

    @staticmethod
    def run(command: str, timeout: int = 30) -> Tuple[str, str, int]:
        """
        Execute a shell command and capture its output.

        Args:
            command: The shell command to execute
            timeout: Maximum seconds to wait for command completion

        Returns:
            Tuple of (stdout, stderr, return_code)
        """
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
        """
        Generate a file tree representation of the project directory.

        Returns:
            String representation of the directory tree (depth 2)
        """
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
    def parse(text: str) -> Dict[str, Any]:
        """
        Parse JSON from LLM output, handling markdown fences and comments.

        Args:
            text: Raw text potentially containing JSON with markdown fences

        Returns:
            Parsed JSON as a dictionary

        Raises:
            json.JSONDecodeError: If the text cannot be parsed as valid JSON
        """
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
    @staticmethod
    def validate_memory() -> Dict[str, Any]:
        result = {'valid': True, 'corrupted': [], 'empty': [], 'total': 0}
        if not CONF.MEMORY_DIR.exists(): return result
        for path in CONF.MEMORY_DIR.rglob('*'):
            if not path.is_file() or path.name.startswith('.'): continue
            result['total'] += 1
            try:
                if not path.read_text(encoding='utf-8').strip():
                    result['empty'].append(str(path.relative_to(CONF.BASE_DIR))); result['valid'] = False
            except Exception:
                result['corrupted'].append(str(path.relative_to(CONF.BASE_DIR))); result['valid'] = False
        return result

    @staticmethod
    def get_structure() -> str:
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()): return "(Memory Empty)"
        files = []
        for p in CONF.MEMORY_DIR.rglob('*'):
            if p.is_file() and not p.name.startswith('.'):
                try: files.append(f"- {p.relative_to(CONF.BASE_DIR)}")
                except ValueError: continue
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
        return "npm test" if (CONF.BASE_DIR / "package.json").exists() else "pytest"


class TemplateManager:
    DEFAULT_TEMPLATES = {
        "architect.txt": "ROLE: Senior Architect\nOBJECTIVE: Initialize .ralph/memory/ for: {{user_intent}}\nFILE TREE: {{file_tree}}\nDELIVERABLE: Create .ralph/memory/architecture.md with YAML frontmatter (type:wiki, title:Architecture) and sections: Tech Stack, Overview, Key Components, Risks, Test Command (format: Test Command: `CMD`).\nOUTPUT: Print STATUS: CREATED .ralph/memory/architecture.md",
        "planner.txt": "ROLE: Product Manager\nTASK: Create PRD JSON for: {{user_intent}}\nMEMORY: {{memory_map}}\nOUTPUT: Raw JSON only. Schema: {\"id\":\"PRD-001\",\"description\":\"...\",\"userStories\":[{\"id\":\"TASK-001\",\"description\":\"As a...\",\"acceptanceCriteria\":[\"...\",\"...\",\"...\"],\"status\":\"pending\"}]}",
        "developer.txt": "ROLE: Developer\nTASK: {{task_id}} - {{task_description}}\nCONTEXT: {{memory_tree}}\nPREFS: {{user_context}}\nFLOW: Plan, Implement, Verify ({{test_cmd}}), Print STATUS: SUCCESS or FAILURE - <reason>\nRETRY: {{prev_errors}}"
    }

    @staticmethod
    def ensure_templates():
        CONF.TEMPLATES_DIR.mkdir(exist_ok=True, parents=True)
        for name, content in TemplateManager.DEFAULT_TEMPLATES.items():
            path = CONF.TEMPLATES_DIR / name
            if not path.exists(): path.write_text(content, encoding='utf-8')

    @staticmethod
    def load(template_name: str) -> str:
        path = CONF.TEMPLATES_DIR / template_name
        if not path.exists():
            if template_name in TemplateManager.DEFAULT_TEMPLATES: return TemplateManager.DEFAULT_TEMPLATES[template_name]
            raise FileNotFoundError(f"Template not found: {template_name}")
        return path.read_text(encoding='utf-8')

    @staticmethod
    def render(template_name: str, **variables) -> str:
        template = TemplateManager.load(template_name)
        for key, value in variables.items(): template = template.replace("{{" + key + "}}", str(value))
        return template


# ==============================================================================
# ORCHESTRATOR
# ==============================================================================

class RalphOrchestrator:
    def __init__(self, agent_name: str = "claude") -> None:
        self.agent = get_agent(agent_name, timeout_seconds=CONF.TIMEOUT_SECONDS)
        if hasattr(self.agent, 'set_logger'): self.agent.set_logger(Logger)
        if hasattr(self.agent, 'set_config'): self.agent.set_config(CONF)
        if not self.agent.check_dependencies():
            Logger.info(f"❌ Agent '{self.agent.get_name()}' dependencies not satisfied.", "RED"); sys.exit(1)
        self.memory = MemoryManager()
        CONF.ensure_directories()
        self._validate_memory_on_startup()

    def run_architect(self, user_intent: str) -> None:
        """
        Run the architect phase to initialize project memory.

        Creates both .ralph/memory/architecture.md (internal memory) and
        ARCH.md (git-tracked documentation) with project structure,
        tech stack, and test command configuration.

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🕵️  Architect: Initializing Memory...", "CYAN")

        prompt = TemplateManager.render(
            "architect.txt",
            user_intent=user_intent,
            file_tree=Shell.get_file_tree()
        )

        success, _, _ = self.agent.run(prompt, "ARCHITECT")
        if not success or not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("⚠️ Architect failed.", "RED")
            sys.exit(1)

        arch_md_path = CONF.BASE_DIR / "ARCH.md"
        if not arch_md_path.exists():
            Logger.info("⚠️ Architect failed: ARCH.md was not created.", "RED")
            sys.exit(1)

        Logger.info("✅ Memory Initialized.", "GREEN")

    def run_planner(self, user_intent: str) -> None:
        """
        Run the planner phase to create a Product Requirements Document.

        Generates a PRD with user stories and acceptance criteria,
        saved to .ralph/prd.json.

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        memory_map = self.memory.get_structure()

        prompt = TemplateManager.render(
            "planner.txt",
            user_intent=user_intent,
            memory_map=memory_map
        )

        for attempt in range(3):
            success, raw, _ = self.agent.run(prompt, "PLANNER")
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

    def execute_loop(self) -> None:
        """
        Execute all pending tasks from the PRD.

        Iterates through user stories, executing each pending task
        with verification. Continues to next task on failure instead
        of terminating. Archives the PRD upon completion.
        """
        prd = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        test_cmd = self.memory.extract_test_command()

        Logger.info(f"\n🚀 Starting Loop. Verify Command: '{test_cmd}'", "YELLOW")

        failed_tasks: List[str] = []

        for task in prd.get('userStories', []):
            if task.get('status') == 'completed':
                continue
            # Reset failed tasks to pending so they can be retried
            if task.get('status') == 'failed':
                task['status'] = 'pending'

            Logger.info(f"\n▶️  Task {task['id']}: {task['description']}", "CYAN")
            success = self._execute_task(prd, task, test_cmd)

            # Save state after each task attempt
            CONF.PRD_FILE.write_text(json.dumps(prd, indent=2), encoding='utf-8')

            if not success:
                failed_tasks.append(task['id'])

        # Report summary
        if failed_tasks:
            Logger.info(f"\n⚠️  {len(failed_tasks)} task(s) failed: {', '.join(failed_tasks)}", "YELLOW")
            Logger.info("Run 'ralph execute' again to retry failed tasks.", "YELLOW")
        else:
            Logger.info("\n🎉 All Tasks Complete.", "GREEN")

        self._archive_prd()

    def _execute_task(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> bool:
        """
        Execute a single task with retries.

        Returns:
            True if task completed successfully, False if max retries exhausted.
        """
        retries = 0

        safe_prd_id = "".join(c for c in prd['id'] if c.isalnum() or c in ('-', '_'))
        safe_task_id = "".join(c for c in task['id'] if c.isalnum() or c in ('-', '_'))

        while retries < CONF.MAX_RETRIES:
            memory_tree = self.memory.get_structure()
            prev_errors = CONF.PROGRESS_FILE.read_text(encoding='utf-8') if CONF.PROGRESS_FILE.exists() else ""

            # Load and Prepare User Context (Soft Guidelines)
            user_context = "No specific user preferences provided."
            prompt_md_path = CONF.BASE_DIR / "prompt.md"

            if prompt_md_path.exists():
                raw_text = prompt_md_path.read_text(encoding='utf-8')
                # Inject variables so the user can reference them if they want to
                user_context = raw_text.replace("{{PRD_ID}}", safe_prd_id)
                user_context = user_context.replace("{{PRD_DESCRIPTION}}", prd['description'])
                user_context = user_context.replace("{{TASK_ID}}", safe_task_id)
                user_context = user_context.replace("{{TASK_DESCRIPTION}}", task['description'])
                user_context = user_context.replace("{{TEST_CMD}}", test_cmd)

            # Construct the Prompt using template
            prompt = TemplateManager.render(
                "developer.txt",
                task_id=task['id'],
                task_description=task['description'],
                memory_tree=memory_tree,
                user_context=user_context,
                test_cmd=test_cmd,
                prev_errors=prev_errors
            )

            success, output, agent_error = self.agent.run(prompt, f"WORKER-{task['id']}")

            if not success:
                self._record_failure(retries, "CLI Crash", output, agent_error=agent_error)
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
                    return True
                else:
                    Logger.info("   🛑 Agent Hallucinated Success.", "RED")
                    error = AgentError(
                        exception_type="VerificationError",
                        message=f"Test command '{test_cmd}' failed with exit code {code}",
                        stack_trace=f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}",
                        timestamp=datetime.datetime.now().isoformat(),
                        agent_name=self.agent.get_name(),
                        task_id=task['id'],
                    )
                    self._record_failure(retries, "Verification Failed", output[-1000:], agent_error=error)
            else:
                error = AgentError(
                    exception_type="AgentReportedFailure",
                    message="Agent did not report STATUS: SUCCESS",
                    stack_trace=f"Agent output (last 2000 chars):\n{output[-2000:]}",
                    timestamp=datetime.datetime.now().isoformat(),
                    agent_name=self.agent.get_name(),
                    task_id=task['id'],
                )
                self._record_failure(retries, "Agent Reported Failure", output[-1000:], agent_error=error)

            retries += 1

        Logger.info(f"🛑 Max retries for {task['id']}. Marking as failed and continuing.", "RED")
        task['status'] = 'failed'
        return False

    def _record_failure(self, retry: int, reason: str, detail: str, agent_error: Optional[AgentError] = None) -> None:
        if agent_error:
            msg = (
                f"Attempt {retry+1} Failed: {reason}\n"
                f"--- Structured Error Context ---\n"
                f"{agent_error.format_log_entry()}\n"
                f"--- Agent Output (last 1000 chars) ---\n"
                f"{detail}"
            )
        else:
            msg = f"Attempt {retry+1} Failed: {reason}\n{detail}"
        CONF.PROGRESS_FILE.write_text(msg, encoding='utf-8')
        Logger.file_log(msg, "FAILURE_RECORD", f"RETRY-{retry+1}")
        Logger.info(f"   ⚠️ Retry {retry+1}/{CONF.MAX_RETRIES}: {reason}", "RED")

    def _archive_prd(self) -> None:
        if not CONF.PRD_FILE.exists(): return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = CONF.ARCHIVE_DIR / f"prd_{ts}.json"
        shutil.move(str(CONF.PRD_FILE), str(dest))
        Logger.info(f"📦 PRD Archived to {dest}", "MAGENTA")

    def _validate_memory_on_startup(self) -> None:
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()): return
        result = self.memory.validate_memory()
        if result['total'] == 0: return
        for key, label in [('corrupted', 'corrupted'), ('empty', 'empty')]:
            if result[key]:
                Logger.info(f"⚠️ Memory: {len(result[key])} {label} file(s): {', '.join(result[key])}", "YELLOW")
        if result['valid']: Logger.debug(f"✅ Memory OK ({result['total']} files)", "GREEN")

    def _prompt_user_for_phase(self, phase_name: str) -> bool:
        return input(f"{Logger.COLORS['YELLOW']}Run {phase_name} phase? (y/n): {Logger.COLORS['RESET']}").strip().lower() == 'y'

    def _get_intent(self, user_intent=None):
        if user_intent: return user_intent
        intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
        if not intent: sys.exit(0)
        return intent

    def start(self, phase: str = "all", accept_all: bool = False) -> None:
        """
        Start the Ralph orchestrator.

        Args:
            phase: Which phase to run ("architect", "planner", "execute", or "all")
            accept_all: If True, skip user confirmation prompts
        """
        Logger.info(f"🤖 Ralph {self.agent.get_name()} Agent active in: {CONF.BASE_DIR}", "GREEN")
        user_intent = None

        if phase in ("architect", "planner", "execute"):
            Logger.info(f"📋 Phase: {phase} only", "YELLOW")
            if phase == "planner" and not any(CONF.MEMORY_DIR.iterdir()):
                Logger.info("❌ Memory missing. Run architect first.", "RED"); sys.exit(1)
            if phase == "execute" and not CONF.PRD_FILE.exists():
                Logger.info("❌ PRD missing. Run planner first.", "RED"); sys.exit(1)
            if phase != "execute": user_intent = self._get_intent()
            {"architect": self.run_architect, "planner": self.run_planner, "execute": self.execute_loop}[phase](user_intent) if phase != "execute" else self.execute_loop()
            Logger.info(f"✅ {phase.title()} complete.", "GREEN"); return

        Logger.info("📋 Running all phases...", "YELLOW")
        if not any(CONF.MEMORY_DIR.iterdir()):
            if accept_all or self._prompt_user_for_phase("Architect"):
                user_intent = self._get_intent(); self.run_architect(user_intent)
            else: Logger.info("⏭️ Skipping architect.", "YELLOW")
        else: Logger.info("📋 Memory exists, skipping architect.", "YELLOW")

        if not CONF.PRD_FILE.exists():
            if accept_all or self._prompt_user_for_phase("Planner"):
                user_intent = self._get_intent(user_intent); self.run_planner(user_intent)
            else: Logger.info("⏭️ Skipping planner.", "YELLOW")
        else: Logger.info("📋 PRD exists, skipping planner.", "YELLOW")

        if accept_all or self._prompt_user_for_phase("Execute"): self.execute_loop()
        else: Logger.info("⏭️ Skipping execute.", "YELLOW")
        Logger.info("✅ All phases complete.", "GREEN")

def get_version() -> str:
    try:
        with open(Path(__file__).parent / "pyproject.toml", "r") as f:
            for line in f:
                if line.startswith("version"):
                    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', line)
                    if match: return match.group(1)
    except Exception: pass
    return "unknown"

def main() -> None:
    """Entry point for the ralph CLI."""
    agent = list_agents()[0]
    parser = argparse.ArgumentParser(description="Ralph - Autonomous Software Development Agent",
        epilog="Examples: ralph | ralph architect | ralph -y execute", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?", help="Phase to run")
    parser.add_argument("--version", action="version", version=f"Ralph {get_version()}")
    parser.add_argument("--accept-all", "-y", action="store_true", help="Skip prompts")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    parser.add_argument("--no-color", action="store_true", help="Disable colors")
    parser.add_argument("--agent", choices=list_agents(), default=agent, help=f"Agent (default: {agent})")
    args = parser.parse_args()
    Logger.set_verbose(args.verbose); Logger.set_no_color(args.no_color)
    RalphOrchestrator(agent_name=args.agent).start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()
