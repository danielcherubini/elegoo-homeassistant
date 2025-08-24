# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration for monitoring and controlling Elegoo 3D printers (resin and FDM models) through the SDCP protocol. The integration provides real-time status monitoring, camera feeds, print control, and automation capabilities.

## Development Commands

**Setup:**
```bash
make setup          # Install dependencies and create virtual environment
```

**Development:**
```bash
make start          # Run Home Assistant with integration in development mode
make debug          # Run in debug mode
make test-server    # Start test server for development
```

**Code Quality:**
```bash
make test           # Run pytest tests
make lint           # Run Ruff linting
make format         # Format code with Ruff
make fix            # Auto-fix code issues
```

**Package Manager:** Uses `uv` (modern Python package manager) with `pyproject.toml`

## Architecture Overview

**Core Components:**
- **API Client** (`api.py`) - Main interface for printer communication via SDCP protocol
- **Coordinator** (`coordinator.py`) - Manages data updates with 2-second polling intervals
- **Config Flow** (`config_flow.py`) - Handles integration setup and configuration UI
- **SDCP Protocol** (`sdcp/` directory) - Structured models for printer status and communication
- **WebSocket Support** (`websocket/` directory) - Real-time communication with printer firmware
- **Proxy Server** - Optional component to bypass printer's 4-connection limit

**Entity Structure:**
The integration creates multiple Home Assistant entity types (sensors, cameras, buttons, lights, fans, selects, numbers, binary sensors, images) based on printer capabilities and status.

**Configuration Migration:**
The integration uses a migration system (currently at config version 4) to handle breaking changes between versions. Migration logic is in `__init__.py`.

## Logging and Debugging

**Log Location:**
Home Assistant logs are located at `config/home-assistant.log` in the project directory.

**Viewing Logs:**
```bash
# View recent logs
tail -f config/home-assistant.log

# Search for integration-specific logs
grep "elegoo_printer" config/home-assistant.log

# View last 100 lines
tail -100 config/home-assistant.log
```

## Key Directories

- `custom_components/elegoo_printer/` - Main integration code
- `custom_components/elegoo_printer/sdcp/` - SDCP protocol models and communication
- `custom_components/elegoo_printer/websocket/` - WebSocket client/server implementation
- `config/` - Home Assistant configuration for development
- `scripts/` - Development and maintenance utilities

## Development Environment

- **Python Version:** 3.13+
- **Home Assistant Version:** 2025.4.0
- **Devcontainer Support:** Available for containerized development
- **PYTHONPATH:** Custom configuration loads integration during development

## Printer Support

**Resin Printers:** Mars 4, Saturn 3/4 series
**FDM Printers:** Centauri Carbon

The integration auto-discovers printers on the network and supports both printer types through the same SDCP protocol interface.

## Testing Strategy

Tests are located in the repository and can be run with `make test`. The integration includes comprehensive testing for SDCP protocol communication, entity state management, and configuration flows.