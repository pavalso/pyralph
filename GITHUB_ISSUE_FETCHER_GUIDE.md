# GitHub Issue Fetcher Guide

The GitHub issue fetcher is a system that automatically fetches issues from GitHub and processes them through Ralph's orchestration workflow to generate PRDs and execute tasks.

## Prerequisites

1. **GitHub CLI (gh)** must be installed and authenticated:
   ```bash
   gh auth status
   ```

2. **Ralph must have been initialized** (architect phase completed) - requires `.ralph/memory/` directory to exist.

---

## CLI Commands

### 1. `fetch-issues` - Simple Issue Fetching

Fetches and displays issues with a specific label.

```bash
# Fetch issues with "ready" label (default) as JSON
fetch-issues

# Fetch issues with custom label
fetch-issues --label "needs-prd"

# Output as human-readable text
fetch-issues --format text

# Skip authentication check
fetch-issues --no-check
```

**Options:**
| Option | Description | Default |
|--------|-------------|---------|
| `--label LABEL` | Filter by label | `ready` |
| `--format FORMAT` | Output format (`json` or `text`) | `json` |
| `--verbose` | Verbose output | off |
| `--no-check` | Skip gh auth check | off |

---

### 2. `ralph-batch` - Batch Processing

Fetches all ready issues and runs full Ralph orchestration on each.

```bash
# Process all "ready" issues
ralph-batch

# Dry run - generate PRDs but don't execute
ralph-batch --dry-run

# Custom label and verbose output
ralph-batch --label "approved" --verbose

# Don't mark issues as processed on GitHub
ralph-batch --no-mark
```

**Options:**
| Option | Description | Default |
|--------|-------------|---------|
| `--label LABEL` | Filter by label | `ready` |
| `--verbose` | Detailed progress logging | off |
| `--dry-run` | Generate PRDs only, skip execution | off |
| `--agent NAME` | AI agent name | `claude` |
| `--no-hooks` | Disable Ralph hooks | off |
| `--no-mark` | Don't update GitHub labels | off |

**Output:** Returns statistics including issues found, processed, failed, and task completion counts.

---

### 3. `ralph-watch` - Daemon Mode

Runs a background watcher that polls for new issues and processes them automatically.

#### Start the watcher:
```bash
# Start with default settings (polls every 60s)
ralph-watch start

# Custom poll interval (30 seconds)
ralph-watch start --interval 30

# Start without auto-processing (just queue issues)
ralph-watch start --no-auto-process

# Mark issues on GitHub when completed
ralph-watch start --mark-issues
```

#### Check status:
```bash
ralph-watch status
ralph-watch status --json
```

#### Stop the watcher:
```bash
ralph-watch stop
```

#### Manual operations:
```bash
# Poll once without starting daemon
ralph-watch poll

# Process all pending issues in queue
ralph-watch process
```

---

## How It Works

### Data Flow
```
GitHub Issues (label: "ready")
       |
  fetch_ready_issues()  <- uses `gh issue list`
       |
  IssueStore (.ralph/issues/)  <- persists for audit
       |
  ProcessingQueue (.ralph/queue/)  <- FIFO ordering
       |
  PromptTransformer  <- formats as "TASK-###: title"
       |
  RalphOrchestrator  <- architect -> planner -> execute
       |
  mark_issue_processed()  <- swaps "ready" -> "processed" label
```

### Directory Structure
```
.ralph/
├── issues/
│   └── issues/
│       ├── 1.json
│       └── 2.json
├── queue/
│   └── queue.json
├── watcher.pid
└── watcher.log
```

---

## GitHub Label Workflow

1. **Create issue** with `draft` label
2. **Mark as `ready`** when approved for processing
3. **System processes** the issue through Ralph
4. **Label changes** to `processed` on completion

---

## Examples

### Process a single batch of issues
```bash
# Check what's ready
fetch-issues --format text

# Process all ready issues
ralph-batch --verbose
```

### Run continuous processing
```bash
# Start watcher daemon
ralph-watch start --interval 120 --mark-issues

# Check it's running
ralph-watch status

# Stop when done
ralph-watch stop
```

### Test without execution
```bash
# Dry run to see what would be processed
ralph-batch --dry-run --verbose
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "gh CLI not authenticated" | Run `gh auth login` |
| "MEMORY_DIR not found" | Run Ralph's architect phase first |
| Issues not being fetched | Check label spelling matches exactly |
| Watcher not processing | Check `ralph-watch status` for queue state |
