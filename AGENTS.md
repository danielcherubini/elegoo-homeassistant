# AGENTS.md

## Project
- **Name:** Elegoo Printer Home Assistant Integration
- **Type:** Home Assistant Custom Component
- **Language:** Python
- **Location:** `custom_components/elegoo_printer/`

## Setup
```bash
make setup      # Install dependencies (uv)
make lint       # Check code quality
make format     # Format code
make fix        # Auto-fix issues
make test       # Run tests
make start      # Start development server
make debug      # Start debug server
```

When checking code quality, validate with make format, make lint, and make test. `format` and `lint` are both considered linting commands, run both.

## Versioning
Update `manifest.json` and `pyproject.toml` versions together before releasing.

## Repository
- `.venv/`: Python virtual environment
- `config/`: Local HA config for testing
- `custom_components/elegoo_printer/`: Core component
- `tests/`: Unit/integration tests
- `blueprints/`: HA automation blueprints

## RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Logging
The Home Assistant logs are located at `config/home-assistant.log`.
