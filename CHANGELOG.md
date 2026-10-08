# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Firmware update entity for SDCP V3 printers: new printer firmware now shows up in **Settings → Updates** with Elegoo's changelog as release notes, instead of only as the diagnostic Firmware Update Available binary sensor (which stays). Read-only - SDCP has no install command, so the update is still installed from the printer's screen.
- Print file and print tray selects with Refresh File List and Print Selected File buttons for the Centauri Carbon 2: the files in the printer's local storage (method 1044), a tray to print from, and a button to start. Choosing never starts a print; the button does.
- Canvas (AMS) support for the Centauri Carbon (CC1): per-slot filament sensors, active tray, and filament colors, matching the existing CC2 support. Canvas presence is auto-detected during setup and stored per printer.
- Per-slot filament usage sensors for the CC1 via the gcode capture proxy. The proxy URL is now configured in the WebSocket printer options, and works with or without a Canvas installed.
- New FDM print states reported verbatim from the printer — auto leveling, resonance testing, preheating/homing/leveling completed, auto feeding, and filament unload states — with an explicit `unrecognized` fallback so unknown codes can no longer freeze the status sensor.
- New `upload_gcode` service for the Centauri Carbon 2: pick a G-code file in the Developer Tools UI, have it uploaded to the printer's storage over the LAN, and optionally start it right away.
- New `start_print` service for the Centauri Carbon 2: start a G-code file already in the printer's storage from the Developer Tools UI or an automation, optionally on a chosen Canvas tray, with auto bed leveling on by default as in the slicer; the response reports whether the printer accepted the job.
- New `update_ip` service: change a printer's IP address from the Developer Tools UI or an automation (e.g. an external IP-detection script) without deleting and re-adding the integration; the config entry reloads with the new address and the service reports whether the printer is reachable at it.
- New **Proxy Host** option for the Centauri Carbon 2: point the whole integration at a forward proxy (e.g. [elegoo-printer-proxy](https://github.com/lantern-eight/elegoo-printer-proxy)) instead of only borrowing its parsed G-code data. MQTT control, G-code uploads and the camera all then go to the proxy on the printer's usual ports, which is what you want when Home Assistant can only reach the printer through the proxy (different network or VLAN) or when you would rather the camera came from the proxy too. It does not reduce the number of connections the printer sees — the proxy is a plain pass-through, one printer connection per client connection — so it is not a fix for the printer's connection limit. Discovery does not answer on a proxy, so it is skipped and the serial is learned from the printer's own MQTT status messages, with a prompt if the printer pushes nothing. Leave the field blank to connect directly, as before.
- Filled in 21 previously-untranslated UI strings (proxy host, serial entry,
     MQTT options, file-select entities) across all 17 non-English locales.
     Machine-translated and structurally validated; not yet reviewed by native
     speakers — corrections welcome (PR to the `translations/` directory).
- Translation parity tests: every code-referenced error key must exist in
     `en.json`, and all locale files must carry the same key set as `en.json`.

### Changed

- FDM print status codes now map 1:1 from the printer's own status table instead of being approximated through resin states; mid-print milestones no longer surface as misleading states like "leveling".
- Config entries migrate automatically (v4 → v5) to record the per-printer Canvas flag; no action is needed.

### Fixed

- A failed firmware check (e.g. Elegoo's server unreachable) no longer resets Firmware Update Available to "no update" until the next check 12 hours later; the last known result is kept.
- WebSocket discovery now recognizes hostnames by their resolved IPv4 address, and options updates validate the newly submitted address.
- CC1 print status no longer sticks on a stale state for the remainder of a job when the printer holds an unmapped milestone code.
- The Canvas auto-detection step in setup is bounded by a timeout, so a device that accepts connections but never answers can no longer hang the config flow.
- `bed_leveling: false` on the CC2 now sends `printer_check: false` instead of leaving the key out. Measured on firmware 02.01.00.00: the printer remembers the last value it was given, so an omitted key means "carry on as before" rather than "off" - a job that omits it after one that sent true levels again, even with the bed temperature unchanged. An explicit false skips leveling (first layer at 180 s instead of 398 s). ElegooSlicer sends false the same way when its own bed-leveling box is unchecked.
- The "invalid IP" error during manual IP entry now shows a proper message
     instead of a raw translation key (the key was missing from the English base
     and every locale).

### Breaking Changes

- The unique ID format for entities has changed to include the machine ID. This may cause Home Assistant to create new entities. The integration will attempt to migrate existing entities to the new format, but this may not be successful in all cases. If you experience issues, you may need to remove and re-add the integration.
