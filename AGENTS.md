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

### 5. Curate the release notes (after the release is created)
Once the workflow has created the release, the notes are a flat PR list. **Update them immediately after the release is created** with `gh release edit vX.Y.Z --notes-file <notes.md>`.

**Draft from git, never from `CHANGELOG.md`.** Its `## [Unreleased]` section is *rolling* — it still lists items already shipped in earlier releases, so notes written from it overstate the release. The real content of a release is:

```bash
git fetch --tags -q origin
git log v<prev>..v<cur> --pretty='%an|%h|%s'          # what actually shipped, + authors
gh pr list --state merged --json number,title,author  # PR numbers + who opened them
```

#### Structure (in this order)

1. **One-line lede** — what this release is for, and whether a migration is needed.
2. **Headline section** — anything new or structural gets its own `##` section (e.g. `## New option: "Printer is often switched off" (#171)`), before the list of smaller changes.
3. **`## Other changes`** — one bullet per change: bold the user-visible outcome, then one clause of *why* it mattered. Link each to its PR or issue.
4. **`## Thanks to contributors`** — see below.

#### Rules

- **Keep every bullet to one or two lines.** If a change needs more, it wants a sub-bullet or it does not belong in the notes. Reference shape: the `v2.15.0` release (~2.7 KB). A wall of prose is a failed release note, however accurate.
- **Explain the *why*, not the mechanism.** Users care about outcomes: "a dropped printer now shows Unavailable instead of stale 0 °C readings that automations acted on", not how the poll gate works.
- **Link every change** to its PR/issue (`#427` — GitHub auto-links those refs). Work pushed straight to `main` with no PR needs a **full URL**, since bare SHAs do not auto-link: `https://github.com/danielcherubini/elegoo-homeassistant/commit/<sha7>`.
- **No internal jargon or lineage.** No `entry.options`, no function or file names, no plan/ADR/roadmap document references, no internal markers like raw translation keys unless that key is what the user literally sees on screen. Describe what the user gets.
- **State known gaps plainly, briefly** — one sub-bullet, not a paragraph of justification. Users finding the gap themselves is worse than reading it here.
- **Cover every issue handled in the cycle**, not just the ones with code changes (one closed pending logs, one closed as not planned), so reporters see their report was acted on.

#### Thanks

- **Never thank the maintainer** (`@danielcherubini`) — that section is for people outside the project.
- **Thank issue reporters, not just code authors** — this repo lives or dies on good bug reports, so name the reporters too (each fix usually exists because someone filed a good report). Per person, tied to their issue/PR number, saying specifically what they contributed: "the diagnosis", "the report that made the fix possible", "the smart-plug use case that set the default".
- New contributors in the cycle get a mention even if their PR was heavily reworked.

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
