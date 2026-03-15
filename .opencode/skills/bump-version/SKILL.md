---
name: bump-version
description: Bump version in pyproject.toml and manifest.json according to semver
license: MIT
compatibility: opencode
metadata:
  audience: maintainers
  workflow: release
---

## What I do

- Read current versions from `pyproject.toml` and `custom_components/elegoo_printer/manifest.json`
- Verify both files have matching versions
- Bump version according to semver (major, minor, or patch)
- Write updated versions back to both files

## When to use me

Use this when preparing a release or updating the project version. I'll ask which bump type you need before making changes.
