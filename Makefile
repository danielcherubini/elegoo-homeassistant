#
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

# Define the virtual environment directory. Can be overridden from the command line.
# e.g., make setup VENV=my_custom_venv
VENV ?= .venv

# --- PHONY TARGETS ---
# .PHONY ensures that make will run the command even if a file with the same
# name as the target exists.
.PHONY: all setup start debug devcontainer format check ruff clean help

# --- DEFAULT TARGET ---
# The default target that runs when you just type 'make'
all: help

# --- SETUP AND INSTALLATION ---
# Installs uv if not present, creates the virtual environment, and syncs dependencies.
# The venv directory can be overridden, e.g., 'make setup VENV=my-env'
setup:
	@if ! command -v uv &> /dev/null; then \
		echo "--> uv not found. Installing uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		echo "--> uv installed successfully."; \
	else \
		echo "--> uv is already installed."; \
	fi
	@echo "--> Creating virtual environment in [$(VENV)]..."
	@uv venv $(VENV) --python $(PYTHON)
	@echo "--> Syncing dependencies into [$(VENV)]..."
	@VIRTUAL_ENV=$(VENV) uv sync --active --all-extras --dev --locked
	@echo "--> Setup complete. Environment is ready."

# --- DEVELOPMENT TASKS ---
# Runs the main application script within the uv-managed environment.
start:
	@echo "--> Starting the application..."
	@VIRTUAL_ENV=$(VENV) uv run --active ./scripts/start

# Runs the application in debug mode within the uv-managed environment.
debug:
	@echo "--> Starting the application in DEBUG mode..."
	@DEBUG=true VIRTUAL_ENV=$(VENV) uv run $(PYTHON) -m debug

# Executes the start script within a devcontainer.
devcontainer:
	@echo "--> Running start script inside devcontainer..."
	@devcontainer exec --workspace-folder . ./scripts/start

# --- LINTING AND FORMATTING ---
# Formats the code using Ruff.
format:
	@echo "--> Formatting code with Ruff..."
	@VIRTUAL_ENV=$(VENV) uv run ruff format .

# Checks for linting errors with Ruff and attempts to fix them.
check:
	@echo "--> Checking and fixing code with Ruff..."
	@VIRTUAL_ENV=$(VENV) uv run ruff check . --fix

# A convenience target to run both format and check.
ruff: format check

# --- CLEANUP ---
# Cleans up Python bytecode, cache directories, and the virtual environment.
clean:
	@echo "--> Cleaning up Python artifacts..."
	@find . -type f -name "*.py[co]" -delete
	@find . -type d -name "__pycache__" -exec rm -r {} +
	@if [ -d "$(VENV)" ]; then \
		echo "--> Removing virtual environment [$(VENV)]..."; \
		rm -rf $(VENV); \
	fi
	@echo "--> Cleanup complete."

# --- HELP ---
# Displays a helpful list of available commands.
help:
	@echo "Makefile Commands:"
	@echo ""
	@echo "  setup                Install uv, create venv, and sync dependencies."
	@echo "                       Override venv name with 'make setup VENV=my-env'."
	@echo "  start                Run the application in the virtual environment."
	@echo "  debug                Run the application in debug mode."
	@echo "  devcontainer         Run the application within a devcontainer."
	@echo "  format               Format code using Ruff."
	@echo "  check                Check for linting errors using Ruff."
	@echo "  ruff                 Run both format and check."
	@echo "  clean                Remove Python artifacts and the virtual environment."
	@echo "  help                 Show this help message."
	@echo ""

