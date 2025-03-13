SHELL := /bin/bash

.PHONY: all run debug devcontainer

all: setup

setup:
	uv venv
	source .venv/bin/activate
	uv pip install -r pyproject.toml

start:
	source .venv/bin/activate && ./scripts/start

debug:
	DEBUG=true python3 -m debug

devcontainer-build:
	devcontainer up --workspace-folder .
	devcontainer exec --workspace-folder . make 

devcontainer:
	devcontainer exec --workspace-folder . make start

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

