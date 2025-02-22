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
	python3 -m pip install -r requirements.txt

format:
	ruff format .
	ruff check . --fix

# Example of adding dependencies and cleaning:

# Define your Python dependencies (adjust as needed)
PYTHON_DEPS = requirements.txt

# Create a virtual environment (optional but recommended)
venv:
	python3 -m venv venv
	source venv/bin/activate
	pip install -r $(PYTHON_DEPS)

# Run with virtual environment
run-venv: venv
	source venv/bin/activate
	./scripts/start

debug-venv: venv
	source venv/bin/activate
	./scripts/debug

devcontainer-venv: venv
	source venv/bin/activate
	./scripts/devcontainer.sh

# Clean up the virtual environment
clean-venv:
	rm -rf venv

# Clean up any other build artifacts (add your own)
clean: clean-venv
	# Add other clean commands here, e.g., removing __pycache__ directories
	find . -name "*.pyc" -delete  # Remove .pyc files
	find . -name "__pycache__" -type d -exec rm -r {} \; # Remove __pycache__ directories

