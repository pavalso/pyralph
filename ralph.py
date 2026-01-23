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

# Import hooks
from hooks import HookManager, Event, EventType

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
    HOOKS_DIR: Path = ROOT_DIR / "hooks"
    PRD_FILE: Path = ROOT_DIR / "prd.json"
    PROGRESS_FILE: Path = ROOT_DIR / "progress.txt"
    LOG_FILE: Path = ROOT_DIR / "ralph_log.txt"

    # Limits
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 600

    def ensure_directories(self) -> None:
        for path in [self.ROOT_DIR, self.MEMORY_DIR, self.ARCHIVE_DIR, self.TEMPLATES_DIR, self.HOOKS_DIR]:
            path.mkdir(exist_ok=True, parents=True)

CONF = Config()

# ==============================================================================
# UTILITIES & LOGGING
# ==============================================================================

class Logger:
    COLORS = {"RESET": "\033[0m", "GREEN": "\033[92m", "RED": "\033[91m",
              "CYAN": "\033[96m", "YELLOW": "\033[93m", "MAGENTA": "\033[95m"}
    # Verbosity levels: 0=normal, 1=verbose (-v), 2=very verbose (-vv), 3=debug (-vvv)
    verbosity = 0
    verbose = False  # Backwards compatibility (synced with verbosity >= 1)
    no_color = False
    quiet = False
    no_emoji = False
    # Log level control: debug=10, info=20, warn=30, error=40
    LOG_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
    log_level = 20  # Default: info
    # Output format control
    json_output = False
    ndjson_output = False
    # Custom log file path (None = use default CONF.LOG_FILE)
    custom_log_file: Optional[Path] = None

    @staticmethod
    def set_no_color(enabled: bool) -> None:
        Logger.no_color = enabled

    @staticmethod
    def set_verbose(enabled: bool) -> None:
        """Set verbose mode (backwards compatible, sets verbosity to 1 or 0)."""
        Logger.verbose = enabled
        Logger.verbosity = 1 if enabled else 0
        # Auto-set log_level to debug when verbose is enabled for backwards compat
        if enabled:
            Logger.log_level = Logger.LOG_LEVELS["debug"]

    @staticmethod
    def set_verbosity(level: int) -> None:
        """Set verbosity level (0=normal, 1=verbose, 2=very verbose, 3=debug).

        When verbosity >= 1, log_level is automatically set to debug to allow
        debug/trace/ultra messages to appear. This maintains backwards compatibility
        with existing -v/-vv/-vvv behavior.
        """
        Logger.verbosity = max(0, min(3, level))
        Logger.verbose = Logger.verbosity >= 1
        # Auto-set log_level to debug when verbosity is enabled for backwards compat
        if Logger.verbosity >= 1:
            Logger.log_level = Logger.LOG_LEVELS["debug"]

    @staticmethod
    def set_quiet(enabled: bool) -> None:
        """Set quiet mode (suppresses all non-error output)."""
        Logger.quiet = enabled

    @staticmethod
    def set_no_emoji(enabled: bool) -> None:
        """Set no-emoji mode (replaces emojis with text equivalents)."""
        Logger.no_emoji = enabled

    @staticmethod
    def set_log_level(level: str) -> None:
        """Set log level (debug, info, warn, error)."""
        if level in Logger.LOG_LEVELS:
            Logger.log_level = Logger.LOG_LEVELS[level]

    @staticmethod
    def set_json_output(enabled: bool) -> None:
        """Enable JSON output format."""
        Logger.json_output = enabled

    @staticmethod
    def set_ndjson_output(enabled: bool) -> None:
        """Enable newline-delimited JSON output format."""
        Logger.ndjson_output = enabled

    @staticmethod
    def set_log_file(path: Optional[str]) -> None:
        """Set custom log file path."""
        Logger.custom_log_file = Path(path) if path else None

    @staticmethod
    def get_log_file() -> Path:
        """Get the effective log file path (custom or default)."""
        return Logger.custom_log_file if Logger.custom_log_file else CONF.LOG_FILE

    @staticmethod
    def _should_log(level: int) -> bool:
        """Check if a message at the given level should be logged."""
        return level >= Logger.log_level

    @staticmethod
    def _format_json_message(msg: str, level: str, **kwargs) -> str:
        """Format a log message as JSON."""
        data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "level": level,
            "message": msg,
            **kwargs
        }
        return json.dumps(data)

    @staticmethod
    def _strip_emoji(msg: str) -> str:
        """Replace emojis with text equivalents."""
        emoji_map = {
            "🤖": "[BOT]", "🕵️": "[ARCH]", "🧠": "[PLAN]", "🚀": "[EXEC]",
            "✅": "[OK]", "❌": "[FAIL]", "⚠️": "[WARN]", "▶️": "[>]",
            "🔒": "[VERIFY]", "🛑": "[STOP]", "⏭️": "[SKIP]", "📋": "[LIST]",
            "📦": "[PKG]", "🎉": "[DONE]", "➡️": "[->]", "⬅️": "[<-]",
            "ℹ️": "[INFO]", "❓": "[?]",
        }
        for emoji, text in emoji_map.items():
            msg = msg.replace(emoji, text)
        return msg

    @staticmethod
    def _print_colored(msg: str, color: str = "RESET", prefix: str = ""):
        if Logger.no_emoji:
            msg = Logger._strip_emoji(msg)
        text = f"{prefix}{msg}" if prefix else msg
        if Logger.no_color:
            output = text
        else:
            output = f"{Logger.COLORS.get(color, Logger.COLORS['RESET'])}{text}{Logger.COLORS['RESET']}"
        try:
            print(output)
        except UnicodeEncodeError:
            print(output.encode('ascii', errors='replace').decode('ascii'))

    @staticmethod
    def info(msg: str, color: str = "RESET") -> None:
        """Print info message (suppressed in quiet mode or if log level > info)."""
        if not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["info"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "info"))
            else:
                Logger._print_colored(msg, color)

    @staticmethod
    def debug(msg: str, color: str = "RESET") -> None:
        """Print debug message (requires verbosity >= 1 and log level <= debug)."""
        if Logger.verbosity >= 1 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "debug"))
            else:
                Logger._print_colored(msg, color, prefix="[DEBUG] ")

    @staticmethod
    def trace(msg: str, color: str = "RESET") -> None:
        """Print trace message (requires verbosity >= 2 and log level <= debug)."""
        if Logger.verbosity >= 2 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "trace"))
            else:
                Logger._print_colored(msg, color, prefix="[TRACE] ")

    @staticmethod
    def ultra(msg: str, color: str = "RESET") -> None:
        """Print ultra-verbose message (requires verbosity >= 3 and log level <= debug)."""
        if Logger.verbosity >= 3 and not Logger.quiet and Logger._should_log(Logger.LOG_LEVELS["debug"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "ultra"))
            else:
                Logger._print_colored(msg, color, prefix="[ULTRA] ")

    @staticmethod
    def warning(msg: str) -> None:
        """Print warning message (shown even in quiet mode, respects log level)."""
        if Logger._should_log(Logger.LOG_LEVELS["warn"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "warn"))
            else:
                Logger._print_colored(msg, "YELLOW", prefix="[WARNING] ")

    @staticmethod
    def error(msg: str) -> None:
        """Print error message (always shown, respects log level)."""
        if Logger._should_log(Logger.LOG_LEVELS["error"]):
            if Logger.json_output or Logger.ndjson_output:
                print(Logger._format_json_message(msg, "error"))
            else:
                Logger._print_colored(msg, "RED", prefix="[ERROR] ")

    @staticmethod
    def file_log(content: str, type: str, tag: str = "UNKNOWN") -> None:
        icons = {"PROMPT": "➡️", "RESPONSE": "⬅️", "ERROR": "❌", "INFO": "ℹ️"}
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_file = Logger.get_log_file()
        entry = f"\n{'='*60}\n{icons.get(type, '❓')} [{ts}] TYPE: {type} | TAG: {tag}\n{'='*60}\n{content}\n"
        try:
            with open(log_file, "a", encoding="utf-8") as f: f.write(entry)
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

    # Default exclusion patterns for file tree
    DEFAULT_TREE_IGNORE = ['node_modules', 'venv', '.git', '.ralph', '__pycache__']

    @staticmethod
    def get_file_tree(depth: int = 2, ignore: Optional[List[str]] = None) -> str:
        """
        Generate a file tree representation of the project directory.

        Args:
            depth: Maximum directory depth to traverse (default: 2)
            ignore: List of directory/file patterns to exclude (default: node_modules, venv, .git, .ralph, __pycache__)

        Returns:
            String representation of the directory tree
        """
        if ignore is None:
            ignore = Shell.DEFAULT_TREE_IGNORE

        # Build the ignore pattern for tree command
        ignore_pattern = '|'.join(ignore) if ignore else ''

        # We explicitly list '.' to ensure we are looking at CWD
        cmd = f"tree -L {depth} --noreport"
        if ignore_pattern:
            cmd += f" -I '{ignore_pattern}'"
        stdout, _, code = Shell.run(cmd)
        if code == 0 and stdout.strip():
            return stdout

        # Fallback python walker using CWD
        ignore_set = set(ignore) if ignore else set()
        lines = []
        for path in CONF.BASE_DIR.glob('*'):
            if path.name not in ignore_set:
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
    def _matches_pattern(path: Path, pattern: str) -> bool:
        """Check if a path matches a glob pattern."""
        from fnmatch import fnmatch
        # Try matching against full path and just the filename
        path_str = str(path)
        name = path.name
        return fnmatch(path_str, pattern) or fnmatch(name, pattern) or fnmatch(path_str, f"*/{pattern}") or fnmatch(path_str, f"*\\{pattern}")

    @staticmethod
    def get_filtered_files(include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                           limit: Optional[int] = None) -> List[Path]:
        """
        Get filtered list of memory files based on include/exclude patterns and limit.

        Args:
            include: Glob patterns to include (if specified, only matching files are included)
            exclude: Glob patterns to exclude (matching files are removed)
            limit: Maximum number of files to return

        Returns:
            List of filtered file paths
        """
        if not CONF.MEMORY_DIR.exists():
            return []

        files = []
        for p in CONF.MEMORY_DIR.rglob('*'):
            if p.is_file() and not p.name.startswith('.'):
                files.append(p)

        # Apply include filter (if specified, only keep matching files)
        if include:
            filtered = []
            for f in files:
                for pattern in include:
                    if MemoryManager._matches_pattern(f, pattern):
                        filtered.append(f)
                        break
            files = filtered

        # Apply exclude filter (remove matching files)
        if exclude:
            filtered = []
            for f in files:
                excluded = False
                for pattern in exclude:
                    if MemoryManager._matches_pattern(f, pattern):
                        excluded = True
                        break
                if not excluded:
                    filtered.append(f)
            files = filtered

        # Sort by path for deterministic ordering
        files.sort(key=lambda p: str(p))

        # Apply limit
        if limit is not None and limit > 0:
            files = files[:limit]

        return files

    @staticmethod
    def get_structure(include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                      limit: Optional[int] = None) -> str:
        """
        Get a formatted list of memory files with optional filtering.

        Args:
            include: Glob patterns to include (if specified, only matching files are included)
            exclude: Glob patterns to exclude (matching files are removed)
            limit: Maximum number of files to include

        Returns:
            Formatted string listing memory files, or "(Memory Empty)" if none found
        """
        if not CONF.MEMORY_DIR.exists() or not any(CONF.MEMORY_DIR.iterdir()):
            return "(Memory Empty)"

        filtered_files = MemoryManager.get_filtered_files(include, exclude, limit)
        if not filtered_files:
            return "(No matching memory files)"

        output = []
        for p in filtered_files:
            try:
                output.append(f"- {p.relative_to(CONF.BASE_DIR)}")
            except ValueError:
                continue
        return "\n".join(output) if output else "(No matching memory files)"

    @staticmethod
    def extract_test_command() -> str:
        texts = []
        for path in CONF.MEMORY_DIR.rglob('*'):
            if path.suffix in ('.md', '.txt'):
                try:
                    texts.append(path.read_text(encoding='utf-8'))
                except Exception:
                    continue
        full_text = ''.join(texts)
        match = re.search(r"Test Command.*?`([^`]+)`", full_text, re.IGNORECASE)
        if match:
            return match.group(1)
        return "npm test" if (CONF.BASE_DIR / "package.json").exists() else "pytest"


class TemplateManager:
    DEFAULT_TEMPLATES = {
        "architect.txt": "ROLE: Senior Architect\nOBJECTIVE: Initialize .ralph/memory/ for: {{user_intent}}\nFILE TREE: {{file_tree}}\nDELIVERABLE: Create .ralph/memory/architecture.md with YAML frontmatter (type:wiki, title:Architecture) and sections: Tech Stack, Overview, Key Components, Risks, Test Command (format: Test Command: `CMD`).\nOUTPUT: Print STATUS: CREATED .ralph/memory/architecture.md",
        "planner.txt": "ROLE: Product Manager\nTASK: Create PRD JSON for: {{user_intent}}\nMEMORY: {{memory_map}}\nOUTPUT: Raw JSON only. Schema: {\"id\":\"PRD-001\",\"description\":\"...\",\"userStories\":[{\"id\":\"TASK-001\",\"description\":\"As a...\",\"acceptanceCriteria\":[\"...\",\"...\",\"...\"],\"status\":\"pending\"}]}",
        "developer.txt": "ROLE: Developer\nTASK: {{task_id}} - {{task_description}}\n\n## MANDATORY INSTRUCTIONS (MUST FOLLOW)\nThe following user preferences are REQUIRED. You MUST strictly adhere to these instructions:\n{{user_context}}\n## END MANDATORY INSTRUCTIONS\n\nCONTEXT: {{memory_tree}}\nFLOW: Plan, Implement, Verify ({{test_cmd}}), Print STATUS: SUCCESS or FAILURE - <reason>\nRETRY: {{prev_errors}}"
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
    def __init__(self, agent_name: str = "claude", enable_hooks: bool = True, enabled_hook_names: Optional[List[str]] = None,
                 intent: Optional[str] = None, intent_file: Optional[str] = None, prompt_file: Optional[str] = None,
                 tree_depth: int = 2, tree_ignore: Optional[List[str]] = None, memory_out: Optional[str] = None,
                 test_cmd: Optional[str] = None, skip_verify: bool = False, retries: Optional[int] = None,
                 timeout: Optional[int] = None, only: Optional[List[str]] = None, except_tasks: Optional[List[str]] = None,
                 resume: Optional[str] = None, include: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
                 context_limit: Optional[int] = None, git: bool = True, git_message: Optional[str] = None,
                 git_branch: Optional[str] = None, write_allow: Optional[List[str]] = None,
                 write_deny: Optional[List[str]] = None, dry_run: bool = False,
                 model: Optional[str] = None, temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None, seed: Optional[int] = None,
                 log_file: Optional[str] = None, log_level: Optional[str] = None,
                 json_output: bool = False, ndjson_output: bool = False,
                 print_prd: bool = False, prd_out: Optional[str] = None, archive: bool = True) -> None:
        # Use --timeout override if provided, otherwise use config default
        agent_timeout = timeout if timeout is not None else CONF.TIMEOUT_SECONDS
        self.agent = get_agent(agent_name, timeout_seconds=agent_timeout,
                               model=model, temperature=temperature,
                               max_tokens=max_tokens, seed=seed)
        if hasattr(self.agent, 'set_logger'): self.agent.set_logger(Logger)
        if hasattr(self.agent, 'set_config'): self.agent.set_config(CONF)
        if not self.agent.check_dependencies():
            Logger.info(f"❌ Agent '{self.agent.get_name()}' dependencies not satisfied.", "RED"); sys.exit(1)
        self.memory = MemoryManager()
        CONF.ensure_directories()
        self._validate_memory_on_startup()
        # Initialize hook system
        self.hooks = HookManager(CONF.HOOKS_DIR, Logger)
        if not enable_hooks:
            self.hooks.disable()
        elif enabled_hook_names is not None:
            self.hooks.set_enabled_hooks(enabled_hook_names)
        # Store intent flags for non-interactive runs
        self._intent = intent
        self._intent_file = intent_file
        self._prompt_file_override = prompt_file
        # Store architect control flags
        self._tree_depth = tree_depth
        self._tree_ignore = tree_ignore
        self._memory_out = memory_out
        # Store execution and verification flags
        self._test_cmd_override = test_cmd
        self._skip_verify = skip_verify
        self._retries_override = retries
        self._timeout_override = timeout
        self._only_tasks = only
        self._except_tasks = except_tasks
        self._resume_from = resume
        # Store context and memory control flags
        self._include_patterns = include
        self._exclude_patterns = exclude
        self._context_limit = context_limit
        # Store safety, isolation and git control flags
        self._git_enabled = git
        self._git_message = git_message
        self._git_branch = git_branch
        self._write_allow = write_allow
        self._write_deny = write_deny
        self._dry_run = dry_run
        # Store I/O, logging and output flags
        self._log_file = log_file
        self._log_level = log_level
        self._json_output = json_output
        self._ndjson_output = ndjson_output
        self._print_prd_flag = print_prd
        self._prd_out = prd_out
        self._archive = archive

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
        self.hooks.emit(Event(EventType.PHASE_START, phase="architect"))
        self.hooks.emit(Event(EventType.ARCHITECT_START, phase="architect"))

        # Generate file tree with customizable depth and ignore patterns
        file_tree = Shell.get_file_tree(depth=self._tree_depth, ignore=self._tree_ignore)

        prompt = TemplateManager.render(
            "architect.txt",
            user_intent=user_intent,
            file_tree=file_tree
        )

        success, _, _ = self.agent.run(prompt, "ARCHITECT")
        if not success or not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("⚠️ Architect failed.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            sys.exit(1)

        arch_md_path = CONF.BASE_DIR / "ARCH.md"
        if not arch_md_path.exists():
            Logger.info("⚠️ Architect failed: ARCH.md was not created.", "RED")
            self.hooks.emit(Event(EventType.ARCHITECT_FAILURE, phase="architect"))
            self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))
            sys.exit(1)

        # Export memory to --memory-out path if specified
        if self._memory_out:
            self._export_memory(self._memory_out)

        Logger.info("✅ Memory Initialized.", "GREEN")
        self.hooks.emit(Event(EventType.ARCHITECT_SUCCESS, phase="architect"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="architect"))

    def run_planner(self, user_intent: str) -> None:
        """
        Run the planner phase to create a Product Requirements Document.

        Generates a PRD with user stories and acceptance criteria,
        saved to .ralph/prd.json.

        Args:
            user_intent: Description of what the user wants to build
        """
        Logger.info("\n🧠 Planner: Creating PRD...", "CYAN")
        self.hooks.emit(Event(EventType.PHASE_START, phase="planner"))
        self.hooks.emit(Event(EventType.PLANNER_START, phase="planner"))
        memory_map = self.memory.get_structure(
            include=self._include_patterns,
            exclude=self._exclude_patterns,
            limit=self._context_limit
        )

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
                self.hooks.emit(Event(EventType.PRD_CREATED, phase="planner", prd_path=str(CONF.PRD_FILE)))
                self.hooks.emit(Event(EventType.PLANNER_SUCCESS, phase="planner"))
                self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
                return
            except Exception as e:
                Logger.info(f"⚠️ JSON Error (Attempt {attempt+1}): {e}", "YELLOW")

        Logger.info("❌ Planning Failed.", "RED")
        self.hooks.emit(Event(EventType.PLANNER_FAILURE, phase="planner"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="planner"))
        sys.exit(1)

    def execute_loop(self) -> None:
        """
        Execute all pending tasks from the PRD.

        Iterates through user stories, executing each pending task
        with verification. Continues to next task on failure instead
        of terminating. Archives the PRD upon completion.

        Respects the following flags:
        - --test-cmd: Override the test command from memory
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --only: Execute only specified task IDs
        - --except: Skip specified task IDs
        - --resume: Resume execution from a specific task ID
        """
        prd = json.loads(CONF.PRD_FILE.read_text(encoding='utf-8'))
        # Use --test-cmd override if provided, otherwise extract from memory
        test_cmd = self._test_cmd_override if self._test_cmd_override else self.memory.extract_test_command()

        Logger.info(f"\n🚀 Starting Loop. Verify Command: '{test_cmd}'", "YELLOW")
        if self._skip_verify:
            Logger.info("   ⏭️  Verification will be skipped (--skip-verify)", "YELLOW")
        self.hooks.emit(Event(EventType.PHASE_START, phase="execute"))
        self.hooks.emit(Event(EventType.EXECUTE_START, phase="execute", verification_command=test_cmd))

        failed_tasks: List[str] = []
        resume_found = self._resume_from is None  # If no --resume, start immediately

        for task in prd.get('userStories', []):
            task_id = task['id']

            # Handle --resume: skip tasks until we find the resume target
            if not resume_found:
                if task_id == self._resume_from:
                    resume_found = True
                    Logger.info(f"   ➡️  Resuming from task {task_id}", "CYAN")
                else:
                    Logger.debug(f"   ⏭️  Skipping {task_id} (before resume point)")
                    continue

            # Handle --only: execute only specified tasks
            if self._only_tasks and task_id not in self._only_tasks:
                Logger.debug(f"   ⏭️  Skipping {task_id} (not in --only list)")
                continue

            # Handle --except: skip specified tasks
            if self._except_tasks and task_id in self._except_tasks:
                Logger.info(f"   ⏭️  Skipping {task_id} (in --except list)", "YELLOW")
                continue

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

        # Check if --resume target was not found
        if not resume_found:
            Logger.warning(f"Resume task '{self._resume_from}' not found in PRD. No tasks executed.")

        # Report summary
        if failed_tasks:
            Logger.info(f"\n⚠️  {len(failed_tasks)} task(s) failed: {', '.join(failed_tasks)}", "YELLOW")
            Logger.info("Run 'ralph execute' again to retry failed tasks.", "YELLOW")
        else:
            Logger.info("\n🎉 All Tasks Complete.", "GREEN")

        self._archive_prd()
        self.hooks.emit(Event(EventType.EXECUTE_END, phase="execute"))
        self.hooks.emit(Event(EventType.PHASE_END, phase="execute"))

    def _sanitize_id(self, text: str) -> str:
        """Sanitize an ID string to contain only alphanumeric chars and hyphens/underscores."""
        return "".join(c for c in text if c.isalnum() or c in '-_')

    def _load_user_context(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> str:
        """Load and prepare user context from prompt.md with variable substitution."""
        # Use --prompt-file override if provided, otherwise default to prompt.md
        if self._prompt_file_override:
            prompt_md_path = Path(self._prompt_file_override)
            if not prompt_md_path.exists():
                Logger.error(f"Prompt file not found: {self._prompt_file_override}")
                sys.exit(1)
        else:
            prompt_md_path = CONF.BASE_DIR / "prompt.md"
            if not prompt_md_path.exists():
                return "No specific user preferences provided."

        raw_text = prompt_md_path.read_text(encoding='utf-8')
        # Only use prompt file if it has non-empty content
        if not raw_text.strip():
            if self._prompt_file_override:
                Logger.warning(f"Prompt file is empty: {self._prompt_file_override}, using default user context.")
            else:
                Logger.warning("prompt.md exists but is empty, using default user context.")
            return "No specific user preferences provided."

        replacements = {
            "{{PRD_ID}}": self._sanitize_id(prd['id']),
            "{{PRD_DESCRIPTION}}": prd['description'],
            "{{TASK_ID}}": self._sanitize_id(task['id']),
            "{{TASK_DESCRIPTION}}": task['description'],
            "{{TEST_CMD}}": test_cmd,
        }
        for placeholder, value in replacements.items():
            raw_text = raw_text.replace(placeholder, value)
        return raw_text

    def _verify_task(self, task: Dict[str, Any], test_cmd: str) -> Tuple[bool, Optional[AgentError]]:
        """Run verification and return (success, error_if_failed)."""
        Logger.info("   🔒 Verifying Agent's Claim...", "YELLOW")
        self.hooks.emit(Event(
            EventType.VERIFICATION_START, phase="execute",
            task_id=task['id'], verification_command=test_cmd
        ))

        stdout, stderr, code = Shell.run(test_cmd)
        verify_log = f"CMD: {test_cmd}\nEXIT CODE: {code}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        Logger.file_log(verify_log, "VERIFICATION", f"WORKER-{task['id']}")

        if code == 0:
            Logger.info("   ✅ Verified.", "GREEN")
            self.hooks.emit(Event(
                EventType.VERIFICATION_SUCCESS, phase="execute",
                task_id=task['id'], verification_command=test_cmd, verification_exit_code=code
            ))
            return True, None

        Logger.info("   🛑 Agent Hallucinated Success.", "RED")
        error = AgentError(
            exception_type="VerificationError",
            message=f"Test command '{test_cmd}' failed with exit code {code}",
            stack_trace=f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            timestamp=datetime.datetime.now().isoformat(),
            agent_name=self.agent.get_name(),
            task_id=task['id'],
        )
        self.hooks.emit(Event(
            EventType.VERIFICATION_FAILURE, phase="execute",
            task_id=task['id'], verification_command=test_cmd,
            verification_exit_code=code, error=error
        ))
        return False, error

    def _execute_task(self, prd: Dict[str, Any], task: Dict[str, Any], test_cmd: str) -> bool:
        """
        Execute a single task with retries.

        Respects the following flags:
        - --skip-verify: Skip verification step after task execution
        - --retries: Override max retry count
        - --timeout: Override agent timeout

        Returns:
            True if task completed successfully, False if max retries exhausted.
        """
        # Use --retries override if provided, otherwise use config default
        max_retries = self._retries_override if self._retries_override is not None else CONF.MAX_RETRIES

        self.hooks.emit(Event(
            EventType.TASK_START, phase="execute",
            task_id=task['id'], task_description=task['description'], max_retries=max_retries
        ))

        for retry in range(max_retries):
            prev_errors = CONF.PROGRESS_FILE.read_text(encoding='utf-8') if CONF.PROGRESS_FILE.exists() else ""
            prompt = TemplateManager.render(
                "developer.txt",
                task_id=task['id'], task_description=task['description'],
                memory_tree=self.memory.get_structure(
                    include=self._include_patterns,
                    exclude=self._exclude_patterns,
                    limit=self._context_limit
                ),
                user_context=self._load_user_context(prd, task, test_cmd),
                test_cmd=test_cmd, prev_errors=prev_errors
            )

            success, output, agent_error = self.agent.run(prompt, f"WORKER-{task['id']}")

            if not success:
                self._record_failure(retry, "CLI Crash", output, agent_error=agent_error, task_id=task['id'])
                self._emit_retry_event(task, retry)
                continue

            if "STATUS: SUCCESS" in output:
                # Handle --skip-verify: skip verification step if flag is set
                if self._skip_verify:
                    Logger.info("   ⏭️  Skipping verification (--skip-verify)", "YELLOW")
                    task['status'] = 'completed'
                    if CONF.PROGRESS_FILE.exists():
                        CONF.PROGRESS_FILE.unlink()
                    self.hooks.emit(Event(
                        EventType.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True

                verified, verify_error = self._verify_task(task, test_cmd)
                if verified:
                    task['status'] = 'completed'
                    if CONF.PROGRESS_FILE.exists():
                        CONF.PROGRESS_FILE.unlink()
                    self.hooks.emit(Event(
                        EventType.TASK_SUCCESS, phase="execute",
                        task_id=task['id'], task_description=task['description']
                    ))
                    return True
                self._record_failure(retry, "Verification Failed", output[-1000:], agent_error=verify_error, task_id=task['id'])
            else:
                error = AgentError(
                    exception_type="AgentReportedFailure",
                    message="Agent did not report STATUS: SUCCESS",
                    stack_trace=f"Agent output (last 2000 chars):\n{output[-2000:]}",
                    timestamp=datetime.datetime.now().isoformat(),
                    agent_name=self.agent.get_name(),
                    task_id=task['id'],
                )
                self._record_failure(retry, "Agent Reported Failure", output[-1000:], agent_error=error, task_id=task['id'])

            self._emit_retry_event(task, retry, max_retries)

        Logger.info(f"🛑 Max retries for {task['id']}. Marking as failed and continuing.", "RED")
        task['status'] = 'failed'
        self.hooks.emit(Event(
            EventType.TASK_FAILURE, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=max_retries, max_retries=max_retries
        ))
        return False

    def _emit_retry_event(self, task: Dict[str, Any], retry: int, max_retries: Optional[int] = None) -> None:
        """Emit a task retry event."""
        if max_retries is None:
            max_retries = self._retries_override if self._retries_override is not None else CONF.MAX_RETRIES
        self.hooks.emit(Event(
            EventType.TASK_RETRY, phase="execute",
            task_id=task['id'], task_description=task['description'],
            retry_count=retry + 1, max_retries=max_retries
        ))

    def _record_failure(self, retry: int, reason: str, detail: str, agent_error: Optional[AgentError] = None, task_id: Optional[str] = None) -> None:
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
        self.hooks.emit(Event(
            EventType.ERROR,
            phase="execute",
            task_id=task_id or (agent_error.task_id if agent_error else None),
            error=agent_error,
            metadata={"reason": reason, "retry": retry + 1}
        ))

    def _archive_prd(self) -> None:
        if not CONF.PRD_FILE.exists(): return
        # Respect --archive flag (default: True)
        if not self._archive:
            Logger.debug("Skipping PRD archival (--no-archive)")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = CONF.ARCHIVE_DIR / f"prd_{ts}.json"
        shutil.move(str(CONF.PRD_FILE), str(dest))
        Logger.info(f"📦 PRD Archived to {dest}", "MAGENTA")
        self.hooks.emit(Event(EventType.PRD_ARCHIVED, prd_path=str(dest)))

    def _export_memory(self, output_path: str) -> None:
        """
        Export memory contents to a file.

        Concatenates all memory files into a single output file for external use.

        Args:
            output_path: Path to write the exported memory content
        """
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        content_parts = []
        for path in sorted(CONF.MEMORY_DIR.rglob('*')):
            if path.is_file() and not path.name.startswith('.'):
                try:
                    rel_path = path.relative_to(CONF.MEMORY_DIR)
                    file_content = path.read_text(encoding='utf-8')
                    content_parts.append(f"# {rel_path}\n\n{file_content}")
                except Exception as e:
                    Logger.warning(f"Could not read memory file {path}: {e}")

        if content_parts:
            out_path.write_text("\n\n---\n\n".join(content_parts), encoding='utf-8')
            Logger.info(f"📋 Memory exported to {out_path}", "MAGENTA")
        else:
            Logger.warning("No memory files to export.")

    def _print_prd(self) -> None:
        """
        Print the PRD contents to stdout.

        Outputs the PRD as formatted JSON for inspection without execution.
        """
        if not CONF.PRD_FILE.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        prd_content = CONF.PRD_FILE.read_text(encoding='utf-8')
        if self._json_output or self._ndjson_output:
            # For JSON/NDJSON mode, output as-is (already JSON)
            print(prd_content)
        else:
            # Pretty print with indentation
            prd_data = json.loads(prd_content)
            print(json.dumps(prd_data, indent=2))

    def _export_prd(self, output_path: str) -> None:
        """
        Export the PRD to a specified file.

        Args:
            output_path: Path to write the PRD content
        """
        if not CONF.PRD_FILE.exists():
            Logger.error("No PRD file found. Run planner first.")
            sys.exit(1)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        prd_content = CONF.PRD_FILE.read_text(encoding='utf-8')
        out_path.write_text(prd_content, encoding='utf-8')
        Logger.info(f"📋 PRD exported to {out_path}", "MAGENTA")

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
        """Get intent from flags or interactive prompt."""
        if user_intent:
            return user_intent
        # Check --intent flag
        if self._intent:
            return self._intent
        # Check --intent-file flag
        if self._intent_file:
            intent_path = Path(self._intent_file)
            if not intent_path.exists():
                Logger.error(f"Intent file not found: {self._intent_file}")
                sys.exit(1)
            content = intent_path.read_text(encoding='utf-8').strip()
            if not content:
                Logger.error(f"Intent file is empty: {self._intent_file}")
                sys.exit(1)
            return content
        # Interactive prompt
        intent = input(f"{Logger.COLORS['YELLOW']}>> What are we building? {Logger.COLORS['RESET']}").strip()
        if not intent:
            sys.exit(0)
        return intent

    def _run_single_phase(self, phase: str) -> None:
        """Run a single specified phase with prerequisite checks."""
        Logger.info(f"📋 Phase: {phase} only", "YELLOW")

        if phase == "planner" and not any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("❌ Memory missing. Run architect first.", "RED")
            sys.exit(1)
        if phase == "execute" and not CONF.PRD_FILE.exists():
            Logger.info("❌ PRD missing. Run planner first.", "RED")
            sys.exit(1)

        if phase == "execute":
            self.execute_loop()
        else:
            user_intent = self._get_intent()
            if phase == "architect":
                self.run_architect(user_intent)
            else:
                self.run_planner(user_intent)

        Logger.info(f"✅ {phase.title()} complete.", "GREEN")

    def _run_all_phases(self, accept_all: bool) -> None:
        """Run all phases with optional user confirmation."""
        Logger.info("📋 Running all phases...", "YELLOW")
        user_intent = None

        # Architect phase
        if any(CONF.MEMORY_DIR.iterdir()):
            Logger.info("📋 Memory exists, skipping architect.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Architect"):
            user_intent = self._get_intent()
            self.run_architect(user_intent)
        else:
            Logger.info("⏭️ Skipping architect.", "YELLOW")

        # Planner phase
        if CONF.PRD_FILE.exists():
            Logger.info("📋 PRD exists, skipping planner.", "YELLOW")
        elif accept_all or self._prompt_user_for_phase("Planner"):
            user_intent = self._get_intent(user_intent)
            self.run_planner(user_intent)
        else:
            Logger.info("⏭️ Skipping planner.", "YELLOW")

        # Execute phase
        if accept_all or self._prompt_user_for_phase("Execute"):
            self.execute_loop()
        else:
            Logger.info("⏭️ Skipping execute.", "YELLOW")

        Logger.info("✅ All phases complete.", "GREEN")

    def start(self, phase: str = "all", accept_all: bool = False) -> None:
        """
        Start the Ralph orchestrator.

        Args:
            phase: Which phase to run ("architect", "planner", "execute", or "all")
            accept_all: If True, skip user confirmation prompts

        Respects the following flags:
        - --print-prd: Print PRD contents and exit without executing
        - --prd-out: Export PRD to specified file and continue
        """
        # Handle --print-prd flag: print PRD and exit
        if self._print_prd_flag:
            self._print_prd()
            return

        # Handle --prd-out flag: export PRD to file
        if self._prd_out:
            self._export_prd(self._prd_out)

        Logger.info(f"🤖 Ralph {self.agent.get_name()} Agent active in: {CONF.BASE_DIR}", "GREEN")

        if phase in ("architect", "planner", "execute"):
            self._run_single_phase(phase)
        else:
            self._run_all_phases(accept_all)

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
        epilog="Examples: ralph | ralph architect | ralph -y execute | ralph -vvv --no-emoji execute",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["architect", "planner", "execute", "all"], default="all", nargs="?", help="Phase to run")
    parser.add_argument("--version", action="version", version=f"Ralph {get_version()}")
    parser.add_argument("--accept-all", "-y", action="store_true", help="Skip prompts")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity (-v, -vv, -vvv)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress non-essential output")
    # Color options (mutually exclusive)
    color_group = parser.add_mutually_exclusive_group()
    color_group.add_argument("--no-color", action="store_true", help="Disable colored output")
    color_group.add_argument("--color", action="store_true", help="Force colored output")
    parser.add_argument("--no-emoji", action="store_true", help="Replace emojis with text equivalents")
    parser.add_argument("--no-hooks", action="store_true", help="Disable hook execution")
    parser.add_argument("--hooks", nargs="+", metavar="NAME", help="Enable only specified hooks by name")
    parser.add_argument("--agent", choices=list_agents(), default=agent, help=f"Agent (default: {agent})")
    # Intent and input flags for non-interactive runs
    parser.add_argument("--intent", type=str, metavar="TEXT", help="Provide intent inline (what to build)")
    parser.add_argument("--intent-file", type=str, metavar="FILE", help="Load intent from a file")
    parser.add_argument("--prompt-file", type=str, metavar="FILE", help="Override prompt.md path for user context")
    # Architect control flags for context generation
    parser.add_argument("--tree-depth", type=int, default=2, metavar="N", help="File tree depth for architect (default: 2)")
    parser.add_argument("--tree-ignore", nargs="+", metavar="PATTERN", help="Patterns to ignore in file tree (default: node_modules, venv, .git, .ralph, __pycache__)")
    parser.add_argument("--memory-out", type=str, metavar="FILE", help="Export memory contents to file after architect phase")
    # Execution and verification flags for task control
    parser.add_argument("--test-cmd", type=str, metavar="CMD", help="Override test command for verification")
    parser.add_argument("--skip-verify", action="store_true", help="Skip verification step after task execution")
    parser.add_argument("--retries", type=int, metavar="N", help="Override max retries per task (default: 3)")
    parser.add_argument("--timeout", type=int, metavar="SECS", help="Override agent timeout in seconds (default: 600)")
    parser.add_argument("--only", nargs="+", metavar="TASK_ID", help="Execute only specified task IDs")
    parser.add_argument("--except", dest="except_tasks", nargs="+", metavar="TASK_ID", help="Skip specified task IDs")
    parser.add_argument("--resume", type=str, metavar="TASK_ID", help="Resume execution from a specific task ID")
    # Context and memory control flags for file filtering
    parser.add_argument("--include", nargs="+", metavar="PATTERN", help="Include only files matching these glob patterns in context")
    parser.add_argument("--exclude", nargs="+", metavar="PATTERN", help="Exclude files matching these glob patterns from context")
    parser.add_argument("--context-limit", type=int, metavar="N", help="Limit maximum number of context files considered")
    # Safety, isolation and git control flags
    git_group = parser.add_mutually_exclusive_group()
    git_group.add_argument("--git", action="store_true", dest="git_enabled", default=True, help="Enable git operations (default)")
    git_group.add_argument("--no-git", action="store_false", dest="git_enabled", help="Disable git operations")
    parser.add_argument("--git-message", type=str, metavar="MSG", help="Custom git commit message template")
    parser.add_argument("--git-branch", type=str, metavar="BRANCH", help="Target git branch for operations")
    parser.add_argument("--write-allow", nargs="+", metavar="PATTERN", help="Allow writes only to paths matching these glob patterns")
    parser.add_argument("--write-deny", nargs="+", metavar="PATTERN", help="Deny writes to paths matching these glob patterns")
    parser.add_argument("--dry-run", action="store_true", help="Simulate file writes without actually writing")
    # Model and prompting flags for LLM customization
    parser.add_argument("--model", type=str, metavar="MODEL", help="Model identifier for LLM requests (e.g., claude-3-opus)")
    parser.add_argument("--temperature", type=float, metavar="TEMP", help="Sampling temperature (0.0-1.0) for response generation")
    parser.add_argument("--max-tokens", type=int, metavar="N", help="Maximum number of tokens in the LLM response")
    parser.add_argument("--seed", type=int, metavar="N", help="Random seed for reproducible outputs")
    # I/O, logging and output flags
    parser.add_argument("--log-file", type=str, metavar="FILE", help="Redirect log output to specified file")
    parser.add_argument("--log-level", type=str, choices=["debug", "info", "warn", "error"], metavar="LEVEL", help="Set log level (debug, info, warn, error)")
    # Output format flags (mutually exclusive)
    output_format_group = parser.add_mutually_exclusive_group()
    output_format_group.add_argument("--json", dest="json_output", action="store_true", help="Output in JSON format")
    output_format_group.add_argument("--ndjson", dest="ndjson_output", action="store_true", help="Output in newline-delimited JSON format")
    # PRD output flags
    parser.add_argument("--print-prd", action="store_true", help="Print PRD contents and exit without executing")
    parser.add_argument("--prd-out", type=str, metavar="FILE", help="Export PRD to specified file")
    # Archive control flags (mutually exclusive)
    archive_group = parser.add_mutually_exclusive_group()
    archive_group.add_argument("--archive", action="store_true", dest="archive_enabled", default=True, help="Archive PRD after execution (default)")
    archive_group.add_argument("--no-archive", action="store_false", dest="archive_enabled", help="Skip PRD archival after execution")
    args = parser.parse_args()

    # Configure logger settings
    Logger.set_verbosity(args.verbose)
    Logger.set_quiet(args.quiet)
    Logger.set_no_emoji(args.no_emoji)
    # Handle color: --no-color disables, --color forces enable (default: auto/enabled)
    if args.no_color:
        Logger.set_no_color(True)
    elif args.color:
        Logger.set_no_color(False)
    # else: leave default (colors enabled)
    # Configure I/O and output format settings
    if args.log_file:
        Logger.set_log_file(args.log_file)
    if args.log_level:
        Logger.set_log_level(args.log_level)
    if args.json_output:
        Logger.set_json_output(True)
    if args.ndjson_output:
        Logger.set_ndjson_output(True)

    # Determine hook configuration
    enable_hooks = not args.no_hooks
    enabled_hook_names = args.hooks if args.hooks else None

    # Validate mutually exclusive intent options
    if args.intent and args.intent_file:
        Logger.error("Cannot use both --intent and --intent-file together.")
        sys.exit(1)

    RalphOrchestrator(
        agent_name=args.agent,
        enable_hooks=enable_hooks,
        enabled_hook_names=enabled_hook_names,
        intent=args.intent,
        intent_file=args.intent_file,
        prompt_file=args.prompt_file,
        tree_depth=args.tree_depth,
        tree_ignore=args.tree_ignore,
        memory_out=args.memory_out,
        test_cmd=args.test_cmd,
        skip_verify=args.skip_verify,
        retries=args.retries,
        timeout=args.timeout,
        only=args.only,
        except_tasks=args.except_tasks,
        resume=args.resume,
        include=args.include,
        exclude=args.exclude,
        context_limit=args.context_limit,
        git=args.git_enabled,
        git_message=args.git_message,
        git_branch=args.git_branch,
        write_allow=args.write_allow,
        write_deny=args.write_deny,
        dry_run=args.dry_run,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        seed=args.seed,
        log_file=args.log_file,
        log_level=args.log_level,
        json_output=args.json_output,
        ndjson_output=args.ndjson_output,
        print_prd=args.print_prd,
        prd_out=args.prd_out,
        archive=args.archive_enabled
    ).start(phase=args.phase, accept_all=args.accept_all)

if __name__ == "__main__":
    main()
