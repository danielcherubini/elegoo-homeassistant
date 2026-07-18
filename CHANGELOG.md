# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Canvas (AMS) support for the Centauri Carbon (CC1): per-slot filament sensors, active tray, and filament colors, matching the existing CC2 support. Canvas presence is auto-detected during setup and stored per printer.
- Per-slot filament usage sensors for the CC1 via the gcode capture proxy. The proxy URL is now configured in the WebSocket printer options, and works with or without a Canvas installed.
- New FDM print states reported verbatim from the printer — auto leveling, resonance testing, preheating/homing/leveling completed, auto feeding, and filament unload states — with an explicit `unrecognized` fallback so unknown codes can no longer freeze the status sensor.

### Changed

- FDM print status codes now map 1:1 from the printer's own status table instead of being approximated through resin states; mid-print milestones no longer surface as misleading states like "leveling".
- Config entries migrate automatically (v4 → v5) to record the per-printer Canvas flag; no action is needed.

### Fixed

- WebSocket discovery now recognizes hostnames by their resolved IPv4 address, and options updates validate the newly submitted address.
- CC1 print status no longer sticks on a stale state for the remainder of a job when the printer holds an unmapped milestone code.
- The Canvas auto-detection step in setup is bounded by a timeout, so a device that accepts connections but never answers can no longer hang the config flow.

### Breaking Changes

- The unique ID format for entities has changed to include the machine ID. This may cause Home Assistant to create new entities. The integration will attempt to migrate existing entities to the new format, but this may not be successful in all cases. If you experience issues, you may need to remove and re-add the integration.
