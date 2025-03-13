SHELL := /bin/bash

.PHONY: all run debug devcontainer

all: setup

start:
	./scripts/start

debug:
	DEBUG=true python3 -m debug

devcontainer:
	devcontainer exec --workspace-folder . ./scripts/develop

setup:
	uv sync

format:
	uv run ruff format .
	uv run ruff check . --fix


# Clean up any other build artifacts (add your own)
clean: clean-venv
	# Add other clean commands here, e.g., removing __pycache__ directories
	find . -name "*.pyc" -delete  # Remove .pyc files
	find . -name "__pycache__" -type d -exec rm -r {} \; # Remove __pycache__ directories

