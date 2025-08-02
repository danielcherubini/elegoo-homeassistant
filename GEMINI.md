# Gemini Project Helper

This document provides instructions and context for the Gemini agent to effectively assist with development in this repository.

## Project Overview

- **Project Name:** Elegoo Printer Home Assistant Integration
- **Type:** Home Assistant Custom Component
- **Language:** Python
- **Primary Code Location:** `custom_components/elegoo_printer/`

## Development Environment

This project uses `uv` for Python environment and dependency management. The virtual environment is expected to be located at `.venv/`.

- **Setup:** To set up the development environment and install all dependencies (including dev dependencies), run:
  ```bash
  make setup
  ```

## Key Commands

The `Makefile` contains all the necessary commands for common development tasks.

- **Linting:** To check the code for style and quality issues, run:
  ```bash
  make lint
  ```

- **Formatting:** To automatically format the code, run:
  ```bash
  make format
  ```

- **Fixing:** To automatically fix linting issues, run:
  ```bash
  make fix
  ```

- **Testing:** To run the automated test suite, run:
  ```bash
  make test
  ```

- **Running the development server:** To start the Home Assistant instance with the custom component for development, run:
  ```bash
  make start
  ```

- **Running the debug server:** To start the Home Assistant instance in debug mode, run:
  ```bash
  make debug
  ```

## Committing and Contributions

- Before committing any changes, please ensure that the code passes both the linter and the test suite.
- Run `make fix` and `make test` to validate your changes.
- Follow the existing code style and conventions.
