# Makefile
#
# This Makefile provides a set of useful commands for setting up and managing
# this Python project. It uses 'uv' for fast dependency management.
#

# Use bash as the shell
SHELL := /bin/bash

# Define the Python interpreter
# This makes it easy to switch between python versions if needed
PYTHON := python3

# --- PHONY TARGETS ---
# .PHONY ensures that make will run the command even if a file with the same
# name as the target exists.
.PHONY: all setup start debug devcontainer format check ruff clean help

# --- DEFAULT TARGET ---
# The default target that runs when you just type 'make'
all: help

# --- SETUP AND INSTALLATION ---
# Sets up the development environment.
# It checks if 'uv' is installed. If not, it installs it.
# Then, it syncs the Python dependencies from your pyproject.toml.
setup:
	@if ! command -v uv &> /dev/null; then \
		echo "--> uv not found. Installing uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		echo "--> uv installed successfully."; \
	else \
		echo "--> uv is already installed."; \
	fi
	@echo "--> Syncing dependencies with uv..."
	@uv sync --all-extras --dev --active
	@echo "--> Setup complete. Environment is ready."

# --- DEVELOPMENT TASKS ---
# Runs the main application script.
start:
	@echo "--> Starting the application..."
	@./scripts/start

# Runs the application in debug mode.
debug:
	@echo "--> Starting the application in DEBUG mode..."
	@DEBUG=true $(PYTHON) -m debug

# Executes the start script within a devcontainer.
devcontainer:
	@echo "--> Running start script inside devcontainer..."
	@devcontainer exec --workspace-folder . ./scripts/start

# --- LINTING AND FORMATTING ---
# Formats the code using Ruff.
format:
	@echo "--> Formatting code with Ruff..."
	@uv run ruff format .

# Checks for linting errors with Ruff and attempts to fix them.
check:
	@echo "--> Checking and fixing code with Ruff..."
	@uv run ruff check . --fix

# A convenience target to run both format and check.
ruff: format check

# --- CLEANUP ---
# Cleans up Python bytecode and cache directories.
clean:
	@echo "--> Cleaning up Python artifacts..."
	@find . -type f -name "*.py[co]" -delete
	@find . -type d -name "__pycache__" -exec rm -r {} +
	@echo "--> Cleanup complete."

# --- HELP ---
# Displays a helpful list of available commands.
help:
	@echo "Makefile Commands:"
	@echo ""
	@echo "  setup         Install uv (if needed) and sync dependencies."
	@echo "  start         Run the application."
	@echo "  debug         Run the application in debug mode."
	@echo "  devcontainer  Run the application within a devcontainer."
	@echo "  format        Format code using Ruff."
	@echo "  check         Check for linting errors using Ruff."
	@echo "  ruff          Run both format and check."
	@echo "  clean         Remove Python bytecode and cache files."
	@echo "  help          Show this help message."
	@echo ""

