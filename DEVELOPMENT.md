# Development Guide

This guide covers setting up a development environment to contribute to or test the Elegoo Printer Home Assistant integration.

## Prerequisites

- **Python 3.13+**
- **Git**
- **uv** (Python package manager) - [Installation instructions](https://docs.astral.sh/uv/getting-started/installation/)

## Quick Start (Linux/macOS)

```bash
git clone https://github.com/danielcherubini/elegoo-homeassistant.git
cd elegoo-homeassistant
make setup
make test    # Run tests
make start   # Start Home Assistant dev server
```

## Setup Options

### Option 1: Linux / macOS (Recommended)

1. **Install uv:**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone and setup:**
   ```bash
   git clone https://github.com/danielcherubini/elegoo-homeassistant.git
   cd elegoo-homeassistant
   make setup
   ```

3. **Run the development server:**
   ```bash
   make start
   ```
   Home Assistant will be available at http://localhost:8123

### Option 2: Windows (Native)

Windows requires some additional setup due to native dependencies.

#### Step 1: Install Python 3.13+

Download and install from [python.org](https://www.python.org/downloads/). During installation, check **"Add Python to PATH"**.

#### Step 2: Install Visual C++ Build Tools

Some dependencies require compilation. Download **Visual Studio Build Tools** from:
https://visualstudio.microsoft.com/visual-cpp-build-tools/

Run the installer and select **"Desktop development with C++"** workload.

#### Step 3: Enable Long Path Support

Windows has a 260 character path limit that can cause build failures. To enable long paths:

1. Open **PowerShell as Administrator**
2. Run:
   ```powershell
   New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
   ```
3. **Restart your computer**

#### Step 4: Install uv

Open PowerShell and run:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen PowerShell after installation.

#### Step 5: Clone and Setup

Use a short path to avoid path length issues:

```powershell
cd C:\
git clone https://github.com/danielcherubini/elegoo-homeassistant.git elegoo
cd elegoo
uv sync --all-groups
```

#### Step 6: Run Commands

```powershell
# Run tests
uv run pytest

# Start Home Assistant dev server
uv run hass -c config

# Run the debug script
$env:PRINTER_IP="YOUR_PRINTER_IP"
uv run python debug.py
```

### Option 3: Dev Container (VS Code + Docker)

If you have Docker and VS Code, this is the easiest option as it handles all dependencies automatically.

**Requirements:**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [VS Code](https://code.visualstudio.com/)
- [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)

> **Note:** Docker Desktop requires hardware virtualization (VT-x/AMD-V) enabled in BIOS. If you have BitLocker enabled, changing BIOS settings may trigger recovery mode.

**Setup:**

1. Clone the repository
2. Open the folder in VS Code
3. Click **"Reopen in Container"** when prompted (or run `Dev Containers: Reopen in Container` from the command palette)
4. Wait for the container to build and `make setup` to complete

The dev container automatically:
- Installs Python 3.13 and all dependencies
- Installs required system packages (ffmpeg, libturbojpeg, libpcap)
- Forwards ports 8123, 3000, 3030, 3031

## Development Commands

| Command | Description |
|---------|-------------|
| `make setup` | Install all dependencies |
| `make start` | Start Home Assistant dev server (http://localhost:8123) |
| `make debug` | Run the printer debug script |
| `make test` | Run the test suite |
| `make lint` | Check code style |
| `make format` | Auto-format code |
| `make fix` | Auto-fix linting issues |

## Debug Script

The debug script connects directly to a printer and displays real-time status information. This is useful for testing protocol changes or debugging printer communication.

```bash
# Linux/macOS
PRINTER_IP=192.168.1.100 make debug

# Windows PowerShell
$env:PRINTER_IP="192.168.1.100"
uv run python debug.py
```

The script will:
1. Discover printers on the network
2. Let you select which printer to monitor
3. Display real-time status updates

## Alternative: MQTT Explorer

If you can't set up the full development environment but want to help debug printer communication, you can capture raw MQTT messages:

1. Download [MQTT Explorer](https://mqtt-explorer.com/)
2. Connect to your printer:
   - **Host:** Your printer's IP address
   - **Port:** 1883
   - **Username/Password:** See table below
3. Subscribe to `elegoo/#` to see all topics
4. Share screenshots or exports of the messages you see

### MQTT Credentials by Printer Model

| Printer | Username | Password |
|---------|----------|----------|
| **CC1** (Centauri Carbon 1) | `bblp` | (empty or access code) |
| **CC2** (Centauri Carbon 2) | `admin` | Check Elegoo Slicer logs* |

*CC2 generates a unique password. To find it:
1. Open Elegoo Slicer and connect to your CC2
2. Check the slicer's log files for MQTT credentials
3. The password is typically a numeric string (e.g., `20250604`)

> **Note:** CC2 connections may drop after a short time. This is expected behavior we're still investigating.

## Project Structure

```
elegoo-homeassistant/
├── custom_components/elegoo_printer/   # Main integration code
│   ├── sdcp/                           # SDCP protocol implementation
│   ├── mqtt/                           # MQTT client/server
│   ├── websocket/                      # WebSocket client
│   └── cc2/                            # Centauri Carbon 2 support
├── config/                             # Home Assistant dev config
├── tests/                              # Test suite
├── debug.py                            # Printer debug script
└── Makefile                            # Development commands
```

## Running Tests

```bash
make test
```

Or run specific tests:
```bash
uv run pytest custom_components/elegoo_printer/sdcp/tests/ -v
```

## Code Style

This project uses [Ruff](https://github.com/astral-sh/ruff) for linting and formatting.

```bash
# Check for issues
make lint

# Auto-fix issues
make fix

# Format code
make format
```

Always run `make fix` and `make test` before submitting a pull request.

## Troubleshooting

### Windows: `pyspeex-noise` or `pymicro-vad` build fails

These packages require C++ compilation. Install Visual C++ Build Tools (see Windows setup above).

### Windows: `Cannot open compiler generated file` error

This is a path length issue. You have three options:

1. **Enable long paths** (see Windows setup above)
2. **Clone to a shorter path** like `C:\elegoo`
3. **Use a shorter uv cache path:**
   ```powershell
   $env:UV_CACHE_DIR = "C:\uv"
   uv sync --all-groups
   ```

### Windows: `uv` command not found

Close and reopen PowerShell after installing uv. Make sure the uv installation path is in your PATH.

### Dev Container: Virtualization error

Docker requires hardware virtualization. If you can't enable it in BIOS (e.g., due to BitLocker), use the native Windows setup instead.

### Printer not discovered

1. Ensure your computer is on the same network/subnet as the printer
2. Check that the printer is powered on and connected to WiFi
3. Try specifying the IP directly: `PRINTER_IP=x.x.x.x make debug`
4. Check firewall settings - allow Python/Home Assistant through

### Connection timeout

- The printer may have too many active connections (limit of 4)
- Try closing Elegoo Slicer or other apps connected to the printer
- The proxy server can help with connection limits

## Getting Help

- [Open an issue](https://github.com/danielcherubini/elegoo-homeassistant/issues) for bugs or feature requests
- [Join discussions](https://github.com/danielcherubini/elegoo-homeassistant/discussions) for questions and community support
