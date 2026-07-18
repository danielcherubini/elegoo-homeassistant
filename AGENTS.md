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

## Releasing

The release is automated via `.github/workflows/release.yml`, which validates, tags, and publishes a GitHub Release on every push to `main` that changes `custom_components/elegoo_printer/manifest.json`. The workflow's design creates the tag **and** the release together — so the commit/push order matters.

### 1. Pre-flight
- Confirm `main` is green: latest `Lint`, `Test`, and `Validate` runs all succeeded (`gh run list --workflow release.yml` etc.)
- Confirm working tree is clean: `git status`

### 2. Bump the version in all of:
- `custom_components/elegoo_printer/manifest.json` — `"version": "x.y.z"`
- `pyproject.toml` — `version = "x.y.z"`
- `uv.lock` — **don't edit by hand**; auto-updated by the first `make` target you run below (`uv run` syncs the project before executing)

Use semver: bump `major`, `minor`, or `patch` per the change scope.

### 3. Validate
All three must pass before committing:
```bash
make format   # ruff format
make lint     # ruff check
make test     # pytest
```

### 4. Publish
1. Commit the bump: `chore: bump version to vX.Y.Z` (include the `uv.lock` change)
2. Push to `main` only — **do not push a tag** (see Recovery below)
3. The `release.yml` workflow triggers automatically, validates versions match, re-runs lint + tests, generates release notes, and creates both the `vX.Y.Z` tag and the GitHub Release

### 5. Curate the release notes
The auto-generated notes are a flat PR list. For a polished user-facing release, replace them with a curated summary:
- Group by impact: Features / Fixes / Documentation / Migration
- Explain the *why*, not just the *what* — users care about outcomes
- Thank new contributors prominently (GitHub's auto-notes list them but don't explain their work)
- Use `gh release edit vX.Y.Z --notes-file <notes.md>`

### 6. Verify
```bash
git ls-remote --tags origin | grep "vX.Y.Z"   # tag on remote
gh release view vX.Y.Z                       # release page with notes
git status                                   # working tree clean
```

### Recovery: if you pushed the tag manually
The workflow's `Check if tag exists` step will see the existing tag and skip release creation (visible in the run logs as "Skip release (tag exists)"). To fix:
1. Delete the tag: `git push origin :refs/tags/vX.Y.Z && git tag -d vX.Y.Z`
2. Re-trigger the workflow — either:
   - `gh run rerun <run-id>` (from `gh run list --workflow release.yml`)
   - Or push another commit to `main` (the workflow re-fires on `manifest.json` changes)

The workflow will then create the tag + release together. The version-bump commit is untouched.

## Repository
- `.venv/`: Python virtual environment
- `config/`: Local HA config for testing
- `custom_components/elegoo_printer/`: Core component
- `tests/`: Unit/integration tests
- `blueprints/`: HA automation blueprints

## Logging
The Home Assistant logs are located at `config/home-assistant.log`.
