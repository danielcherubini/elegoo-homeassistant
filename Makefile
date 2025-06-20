SHELL := /bin/bash

.PHONY: all run debug devcontainer

all: setup

setup:
	curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync --all-extras --dev --active

start:
	./scripts/start

debug:
	DEBUG=true python3 -m debug

devcontainer:
	devcontainer exec --workspace-folder . ./scripts/start

format:
	uv run ruff format .

check:
	uv run ruff check . --fix

ruff: format check

# Clean up any other build artifacts (add your own)
clean:
	# Add other clean commands here, e.g., removing __pycache__ directories
	find . -name "*.pyc" -delete  # Remove .pyc files
	find . -name "__pycache__" -type d -exec rm -r {} \; # Remove __pycache__ directories

