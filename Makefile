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
.PHONY: all setup start debug devcontainer test test-server test-mqtt-printer test-mqtt-broker format lint fix clean help

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
	@VIRTUAL_ENV=$(VENV) uv sync --active --all-extras --dev
	@echo "--> Setup complete. Environment is ready."

# --- DEVELOPMENT TASKS ---
# Runs the main application script within the uv-managed environment.
start:
	@echo "--> Starting the application..."
	@VIRTUAL_ENV=$(VENV) uv run --active ./scripts/start

# Runs the application in debug mode within the uv-managed environment.
debug:
	@echo "--> Starting the application in DEBUG mode..."
	@DEBUG=true VIRTUAL_ENV=$(VENV) uv run --active $(PYTHON) debug.py

# Executes the start script within a devcontainer.
devcontainer:
	@echo "--> Running start script inside devcontainer..."
	@devcontainer exec --workspace-folder . ./scripts/start

# Runs the test server for development and testing.
test-server:
	@echo "--> Starting the test server..."
	@VIRTUAL_ENV=$(VENV) uv run --active $(PYTHON) scripts/test_server.py

# Runs the MQTT test printer.
test-mqtt-printer:
	@echo "--> Starting the MQTT test printer..."
	@VIRTUAL_ENV=$(VENV) uv run --active $(PYTHON) scripts/test_mqtt_printer.py

# Runs the embedded MQTT broker test.
test-mqtt-broker:
	@echo "--> Starting the embedded MQTT broker..."
	@VIRTUAL_ENV=$(VENV) uv run --active $(PYTHON) scripts/test_embedded_mqtt_broker.py

# --- LINTING AND FORMATTING ---
# Formats the code using Ruff.
format:
	@echo "--> Formatting code with Ruff..."
	@VIRTUAL_ENV=$(VENV) uv run ruff format .

# Checks for linting errors with Ruff and attempts to fix them.
lint:
	@echo "--> Linting code with Ruff..."
	@VIRTUAL_ENV=$(VENV) uv run ruff check .

# Run tests using pytest
test:
	@echo "--> Running tests with pytest..."
	@VIRTUAL_ENV=$(VENV) uv run pytest

# Fixes any problems found in the code
fix: 
	@echo "--> Fixing code with Ruff..."
	@VIRTUAL_ENV=$(VENV) uv run ruff check . --fix

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
	@echo "  test-server          Run the WebSocket test server for development."
	@echo "  test-mqtt-printer    Run the MQTT test printer."
	@echo "  test-mqtt-broker     Run the embedded MQTT broker test."
	@echo "  format               Format code using Ruff."
	@echo "  lint                 Check for linting errors using Ruff."
	@echo "  fix                  Fixes any issues it finds."
	@echo "  test                 Run the test suite."
	@echo "  clean                Remove Python artifacts and the virtual environment."
	@echo "  help                 Show this help message."
	@echo ""

