# Spoolman Integration

Track filament usage per spool by pushing data from this integration into [Spoolman](https://github.com/Donkie/Spoolman) via the [Spoolman Home Assistant](https://github.com/Disane87/spoolman-homeassistant) integration.

There are three approaches depending on your printer and firmware:

| Printer | Firmware | Method | Per-Slot | Live Tracking |
|---------|----------|--------|----------|---------------|
| Centauri Carbon 2 | Stock | [Automated via gcode capture proxy](#centauri-carbon-2-automated-per-slot-tracking) | Yes (4 Canvas slots) | No (on complete) |
| Centauri Carbon | OpenCentauri | [Total Extrusion sensor](#centauri-carbon-with-opencentauri-firmware) | No (single spool) | Yes (every 100mm) |
| Centauri Carbon | Stock | [Filename template workaround](#centauri-carbon-stock-firmware-filename-workaround) | No (single spool) | No (on complete) |

All approaches require:

* [Spoolman Home Assistant](https://github.com/Disane87/spoolman-homeassistant) integration installed and configured
* Spoolman filament `name` and `ext_id` values that match the filament preset names in ElegooSlicer

---

## Centauri Carbon 2 — Automated Per-Slot Tracking

### Architecture

```mermaid
flowchart LR
  subgraph cc2Flow [CC2 - Per-Slot via Proxy]
    Slicer2[ElegooSlicer] -->|upload| Proxy[GCode Capture Proxy]
    Proxy -->|JSON metadata| HAElegoo2[HA Elegoo Integration]
    CC2Printer[CC2 Printer] -->|MQTT| HAElegoo2
    HAElegoo2 -->|"A1-A4 sensors"| Cache2["Cache Automation (input_text JSON)"]
    Cache2 -->|"on complete"| Auto2[Spoolman Push Automation]
  end

  subgraph cc1Flow [CC1 - Filename Workaround]
    Slicer1["ElegooSlicer (custom filename)"] -->|upload| CC1Printer[CC1 Printer]
    CC1Printer -->|MQTT| HAElegoo1[HA Elegoo Integration]
    HAElegoo1 -->|"filename cached by existing automation"| Auto1[Spoolman Push Automation]
  end

  Auto2 -->|"match filament name"| SpoolmanHA[Spoolman HA Integration]
  Auto1 -->|"match filament name"| SpoolmanHA
  SpoolmanHA -->|"spoolman.use_spool_filament"| SpoolmanServer["Spoolman Server (192.168.50.49:7912)"]
```

Uses the [cc2-gcode-capture-proxy](https://github.com/lantern-eight/cc2-gcode-capture-proxy) to capture per-slot filament data from each uploaded gcode file. The proxy parses `; filament used [g]` and `; filament_settings_id` from the gcode to determine how much each Canvas slot uses and what filament is loaded.

The integration exposes this data as sensors when the proxy URL is configured (Settings → Integrations → Elegoo → Configure):

* `sensor.centauri_carbon_2_a1_grams` through `a4_grams` — per-slot planned weight
* `sensor.centauri_carbon_2_a1_name` through `a4_name` — filament preset name per slot

### How it works

1. ElegooSlicer uploads gcode through the proxy → proxy saves per-slot metadata
2. HA integration fetches proxy data when a print starts → per-slot sensors populate
3. A cache automation saves the slot data to an `input_text` helper (slot sensors clear to "unknown" on print complete)
4. On print complete, the push automation reads the cached data, matches each filament name to a Spoolman spool, and calls `spoolman.use_spool_filament`

### Prerequisites

* [cc2-gcode-capture-proxy](https://github.com/lantern-eight/cc2-gcode-capture-proxy) running and configured
* Proxy URL configured in the integration options
* Spoolman filament names matching ElegooSlicer filament preset names

### Required helper

Create in Settings → Devices & Services → Helpers:

* **Cached CC2 Slot Data** — Input Text (`input_text.centauri_carbon_2_cached_slot_data`) with max length 255

### Shared script: Update Spoolman From Filament Name

Both the CC2 and CC1 automations use this reusable script. It finds a non-archived Spoolman spool matching the filament name and deducts usage. When multiple spools share the same filament name, the spool with the lowest remaining weight is chosen.

```yaml
update_spoolman_from_filament_name:
  alias: Update Spoolman From Filament Name
  mode: queued
  max: 10
  fields:
    filament_name:
      required: true
      name: Filament Name
      selector:
        text:
    use_weight:
      required: true
      name: Use Weight (grams)
      selector:
        number:
          min: 0
          max: 100000
          step: 0.01
          unit_of_measurement: g
  sequence:
    - variables:
        matched_spool_id: >
          {% set spools = integration_entities('spoolman')
             | select('match', 'sensor.spoolman_spool_[0-9]+$') | list %}
          {% set ns = namespace(best_id=none, best_weight=none) %}
          {% for spool in spools %}
            {% if state_attr(spool, 'filament_name') == filament_name
               and not state_attr(spool, 'archived') %}
              {% set w = state_attr(spool, 'remaining_weight') | float(99999) %}
              {% if ns.best_id is none or w < ns.best_weight %}
                {% set ns.best_id = state_attr(spool, 'id') %}
                {% set ns.best_weight = w %}
              {% endif %}
            {% endif %}
          {% endfor %}
          {{ ns.best_id }}
    - choose:
        - conditions:
            - condition: template
              value_template: >
                {{ matched_spool_id not in [none, 'None', 'null', ''] }}
          sequence:
            - action: spoolman.use_spool_filament
              data:
                id: "{{ matched_spool_id | int }}"
                use_weight: "{{ use_weight | float }}"
      default:
        - action: persistent_notification.create
          data:
            title: "Spoolman: No matching spool"
            message: >
              Could not find a non-archived Spoolman spool with filament name
              "{{ filament_name }}". The print used {{ use_weight }}g that was
              not tracked.
```

### Automation: Cache CC2 Slot Data

Caches the per-slot filament names and grams as JSON into the `input_text` helper whenever the proxy sensors populate. Required because the sensors clear to "unknown" on print complete.

```yaml
alias: Cache CC2 Slot Data
mode: single
triggers:
  - entity_id:
      - sensor.centauri_carbon_2_a1_grams
    trigger: state
conditions:
  - condition: template
    value_template: >
      {{ trigger.to_state.state not in ['unknown', 'unavailable'] }}
actions:
  - action: input_text.set_value
    target:
      entity_id: input_text.centauri_carbon_2_cached_slot_data
    data:
      value: >
        {% set n1 = states('sensor.centauri_carbon_2_a1_name') %}
        {% set n2 = states('sensor.centauri_carbon_2_a2_name') %}
        {% set n3 = states('sensor.centauri_carbon_2_a3_name') %}
        {% set n4 = states('sensor.centauri_carbon_2_a4_name') %}
        {{ {
          'a1': {'name': '' if n1 in ['unknown','unavailable'] else n1,
                 'grams': states('sensor.centauri_carbon_2_a1_grams') | float(0)},
          'a2': {'name': '' if n2 in ['unknown','unavailable'] else n2,
                 'grams': states('sensor.centauri_carbon_2_a2_grams') | float(0)},
          'a3': {'name': '' if n3 in ['unknown','unavailable'] else n3,
                 'grams': states('sensor.centauri_carbon_2_a3_grams') | float(0)},
          'a4': {'name': '' if n4 in ['unknown','unavailable'] else n4,
                 'grams': states('sensor.centauri_carbon_2_a4_grams') | float(0)}
        } | to_json }}
```

### Automation: CC2 Spoolman Update

On print complete, reads the cached slot data and pushes usage to Spoolman for each slot that consumed filament.

```yaml
alias: CC2 Spoolman Update
mode: single
triggers:
  - entity_id:
      - sensor.centauri_carbon_2_print_status
    to: complete
    trigger: state
conditions: []
actions:
  - variables:
      raw_data: "{{ states('input_text.centauri_carbon_2_cached_slot_data') }}"
  - choose:
      - conditions:
          - condition: template
            value_template: >
              {{ raw_data not in ['unknown', 'unavailable', '', 'None'] }}
        sequence:
          - variables:
              slot_data: "{{ raw_data | from_json }}"
          - repeat:
              for_each:
                - a1
                - a2
                - a3
                - a4
              sequence:
                - choose:
                    - conditions:
                        - condition: template
                          value_template: >
                            {% set slot = slot_data[repeat.item] %}
                            {{ slot.grams | float(0) > 0
                               and slot.name not in ['', none, 'None'] }}
                      sequence:
                        - action: script.update_spoolman_from_filament_name
                          data:
                            filament_name: "{{ slot_data[repeat.item].name }}"
                            use_weight: "{{ slot_data[repeat.item].grams | float }}"
    default:
      - action: persistent_notification.create
        data:
          title: "Spoolman: CC2 no cached slot data"
          message: >
            CC2 print completed but no cached slot data was found.
            Filament usage was not tracked. Make sure the gcode capture
            proxy is configured and the "Cache CC2 Slot Data" automation
            is enabled.
```

---

## Centauri Carbon — Stock Firmware Filename Workaround

For the Centauri Carbon on stock firmware (no OpenCentauri), there is no Total Extrusion sensor. This workaround embeds the filament name and planned weight into the filename using ElegooSlicer template variables, then parses them on print complete.

### ElegooSlicer setup

Set the output filename format in your CC1 printer profile to:

```
[anything]_{filament_preset[0]}_{total_weight}g
```

This produces filenames like `CC1_benchy_ElegooPLA-Basic-White_2.50g.gcode`. The `CC1_` prefix comes from naming your model files with that prefix (e.g. `CC1_benchy.stl`).

**Parsing relies on filament preset names being hyphenated (no underscores).** Model names can contain underscores. The parser splits from the right: the last `_`-separated token is the weight (`2.50g`), the second-to-last is the filament name (`ElegooPLA-Basic-White`).

The `{total_weight}` and `{filament_preset[0]}` variables are available in ElegooSlicer filename templates.

### Prerequisites

* The existing "Cache 3D Print Filename" automation that saves the filename to `input_text.centauri_carbon_last_file` (the filename sensor clears on print complete)
* The shared `script.update_spoolman_from_filament_name` from the [CC2 section above](#shared-script-update-spoolman-from-filament-name)

### Automation: CC1 Spoolman Update

```yaml
alias: CC1 Spoolman Update
mode: single
triggers:
  - entity_id:
      - sensor.centauri_carbon_print_status
    to: complete
    trigger: state
conditions: []
actions:
  - variables:
      filename: >
        {{ states('input_text.centauri_carbon_last_file') | replace('.gcode', '') }}
      parts: "{{ filename.split('_') }}"
      weight_grams: "{{ parts[-1] | replace('g', '') | float(0) }}"
      filament_name: "{{ parts[-2] | default('') }}"
  - choose:
      - conditions:
          - condition: template
            value_template: >
              {{ weight_grams > 0
                 and filament_name != ''
                 and '-' in filament_name }}
        sequence:
          - action: script.update_spoolman_from_filament_name
            data:
              filament_name: "{{ filament_name }}"
              use_weight: "{{ weight_grams }}"
    default:
      - action: persistent_notification.create
        data:
          title: "Spoolman: CC1 filename parse failed"
          message: >
            Could not parse filament name and weight from filename
            "{{ states('input_text.centauri_carbon_last_file') }}".
            Expected format: [model]_[FilamentName-With-Hyphens]_[weight]g.gcode
```

---

## Centauri Carbon with OpenCentauri Firmware

The Total Filament Used sensor is available on the Centauri Carbon with the [OpenCentauri](https://docs.opencentauri.cc/patched-firmware/) firmware (and on the stock Centauri Carbon 2 firmware). This enables live tracking — usage is pushed to Spoolman every 100mm of extruded filament during a print.

When using the Centauri Carbon, install OpenCentauri firmware first. Only the patched version is required. If you already had this integration installed before installing OpenCentauri, remove and re-add your printer so the Total Filament Used sensor is made available.

> **Note:** The automations below use manual spool ID selection. They have not been tested with the Centauri Carbon 2 and filament change is not automated. For the CC2, consider using the [automated per-slot tracking](#centauri-carbon-2-automated-per-slot-tracking) instead.

### Required helpers

* **Current Spool ID** — Input Number (`input_number.current_spool_id`) with min 0, max 1000000
* **Current Spool** — Template Select (`select.current_spool`) for user-friendly spool selection:
    * State:
    ```jinja
     {%
     set spool = 'sensor.spoolman_spool_' ~ states('input_number.current_spool_id') | default(0) | int 
     %}
     {% if spool == 'sensor.spoolman_spool_0' %}
     {{ '0: Unknown' }}
     {% else %}
     {% set id = state_attr(spool, 'id') | default(0) | int %}
     {% set vendor = state_attr(spool, 'filament_vendor_name') | default('') %}
     {% set material = state_attr(spool, 'filament_material') | default('') %}
     {% set name = state_attr(spool, 'filament_name') | default('') %}
     {% set location = state_attr(spool, 'location') %}
     {% set weight = state_attr(spool, 'remaining_weight')|round(0) ~ ' g' %}
     {% if location %}
     {% set extra = weight + ' - ' + location %}
     {% else %}
     {% set extra = weight %}
     {% endif %}
     {{ id ~ ': ' + vendor + ' ' + material + ' ' + name + ' (' + extra + ')' }}
     {% endif %}
    ```
    * Available Options:
    ```jinja
     {% set spools = integration_entities('spoolman') | select('match', 'sensor.spoolman_spool_[0-9]+$') | list %}
     {% set ns = namespace(x=['0: Unknown']) %}
     {% for spool in spools -%}
     {% set location = state_attr(spool, 'location') %}
     {% set text = state_attr(spool, 'id') ~ ': ' + state_attr(spool, 'filament_vendor_name')|default('') + ' ' + state_attr(spool, 'filament_material')|default('') + ' ' + state_attr(spool, 'filament_name')|default('') %}
     {% if location %}
     {% set extra = ' (' ~ state_attr(spool, 'remaining_weight')|round(0) + ' g - ' + location + ')' %}
     {% else %}
     {% set extra = ' (' ~ state_attr(spool, 'remaining_weight')|round(0) + ' g)' %}
     {% endif %}
     
     {% set ns.x = ns.x + [text + extra] %}
     {%- endfor %}
     {{ ns.x }}
    ```
    * Actions on Select
       - Action: Input Number: Set
          - Target: Current Spool ID
          - Value:
           ```jinja2
           {{ option.split(':')[0]|int }}
           ```

* **Accumulated Spool Usage** — Input Number (`input_number.accumulated_spool_usage`) with min 0, max 1000000
* **Diff Accumulated Spool Usage** — Template Number (`number.diff_accumulated_spool_usage`), min 0, max 1000000, unit mm:
    ```jinja
    {{ states('sensor.centauri_carbon_total_extrusion')  | float(0) - states('input_number.accumulated_spool_usage') | float(0) }}
    ```

### Script: Update Spool Usage

```yaml
sequence:
  - action: spoolman.use_spool_filament
    metadata: {}
    data:
      id: "{{ states(current_spool_id) }}"
      use_length: "{{ states(current_extrusion) }}"
fields:
  current_spool_id:
    required: true
    selector:
      entity:
        multiple: false
        filter:
          domain: input_number
    name: Current Spool ID
  current_extrusion:
    required: true
    selector:
      entity:
        multiple: false
        filter:
          domain: number
    name: Current Extrusion
alias: Update Spool Usage
description: ""
mode: queued
max: 100
```

### Automation: Update Spool Usage

Updates every 100mm of extruded filament during a print, and pushes the final usage when printing is done:

```yaml
alias: Update Spool Usage
description: ""
triggers:
  - trigger: state
    entity_id:
      - sensor.centauri_carbon_total_extrusion
    id: extrusion
  - trigger: state
    entity_id:
      - sensor.centauri_carbon_print_status
    id: idle
    from:
      - printing
    to:
      - idle
  - trigger: state
    entity_id:
      - sensor.centauri_carbon_print_status
    id: complete
    from:
      - printing
    to:
      - complete
  - trigger: state
    entity_id:
      - sensor.centauri_carbon_print_status
    id: stopped
    to:
      - stopped
conditions:
  - condition: numeric_state
    entity_id: input_number.current_spool_id
    above: 0
actions:
  - choose:
      - conditions:
          - condition: numeric_state
            entity_id: number.diff_accumulated_spool_usage
            above: 100
          - condition: trigger
            id:
              - extrusion
        sequence:
          - action: script.update_spool_usage
            metadata: {}
            data:
              current_spool_id: input_number.current_spool_id
              current_extrusion: number.diff_accumulated_spool_usage
          - action: input_number.set_value
            metadata: {}
            target:
              entity_id: input_number.accumulated_spool_usage
            data:
              value: >-
                {{ states('sensor.centauri_carbon_total_extrusion') | float(0)
                }}
      - conditions:
          - condition: trigger
            id:
              - idle
              - complete
              - stopped
          - condition: numeric_state
            entity_id: number.diff_accumulated_spool_usage
            above: 0
        sequence:
          - action: script.update_spool_usage
            metadata: {}
            data:
              current_spool_id: input_number.current_spool_id
              current_extrusion: number.diff_accumulated_spool_usage
          - action: input_number.set_value
            metadata: {}
            target:
              entity_id: input_number.accumulated_spool_usage
            data:
              value: "{{ float(0) }}"
        alias: If it's no longer printing
mode: queued
max: 1000
```

### Automation: Auto Reset Spool ID

Resets the spool ID when filament loading/unloading is detected (prevents tracking usage against the wrong spool):

```yaml
alias: Auto Reset Spool ID
description: ""
triggers:
  - trigger: state
    entity_id:
      - sensor.centauri_carbon_current_status
    to:
      - loading_unloading
conditions: []
actions:
  - action: input_number.set_value
    metadata: {}
    target:
      entity_id: input_number.current_spool_id
    data:
      value: 0
mode: single
```

### Automation: Prompt User For Spool ID Before Printing

Pauses the print at the beginning if no spool ID is set:

```yaml
alias: Prompt User For Spool ID Before Printing
description: ""
triggers:
  - trigger: state
    entity_id:
      - sensor.centauri_carbon_print_status
    to:
      - printing
conditions:
  - condition: numeric_state
    entity_id: input_number.current_spool_id
    below: 1
actions:
  - action: button.press
    metadata: {}
    target:
      entity_id: button.centauri_carbon_pause_print
    data: {}
  - action: notify.mobile_app_XXX
    metadata: {}
    data:
      message: "3D Printer is starting a print, but the spool ID has not been set. Please set it before resuming the print."
mode: single
```

To make for a more complex message with actions, I recommend using this [Notification Blueprint](https://github.com/MB901/t-house-blueprints/blob/main/notifications.yaml). With it you can allow the user perform an action right from the message.

### Automation: Resume Printing on Spool ID Change

Automatically resumes the print once the spool ID is set:

```yaml
alias: Resume Printing on Spool ID Change
description: ""
triggers:
  - trigger: state
    entity_id:
      - input_number.current_spool_id
conditions:
  - condition: state
    entity_id: sensor.centauri_carbon_print_status
    state:
      - paused
actions:
  - action: button.press
    metadata: {}
    target:
      entity_id: button.centauri_carbon_resume_print
    data: {}
mode: single
```

---

## Dashboard Display

A nice way to list your available spools (requires the [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) and the [entity-progress-card](https://github.com/francois-le-ko4la/lovelace-entity-progress-card/)):

```yaml
type: custom:auto-entities
filter:
  include:
    - integration: "*spoolman*"
      sort:
        method: attribute
        attribute: filament_material
        reverse: false
      attributes:
        archived: false
      options:
        type: custom:entity-progress-card-template
        color: "#{{ state_attr(entity, 'filament_color_hex') }}"
        icon: mdi:printer-3d-nozzle
        bar_effect: gradient
        bar_position: bottom
        percent: "{{ 100 - state_attr(entity, 'used_percentage') | float(0) }}"
        badge_icon: >
          {% if state_attr(entity, 'id') ==
          states('input_number.current_spool_id')|int %}
            mdi:check-circle
          {% endif %}
        badge_color: >
          {% if state_attr(entity, 'id') ==
          states('input_number.current_spool_id')|int %}
            green
          {% else %}
            default_color
          {% endif %}
        name: |
          {% set vendor = state_attr(entity, 'filament_vendor_name') %}
          {% set name = state_attr(entity, 'filament_name') %}
          {% set material = state_attr(entity, 'filament_material') %}
          {% if vendor and name and material %}
            {{ material }} {{ vendor }} {{ name }}
          {% elif name and material %}
            {{ name }} ({{ material }})
          {% elif name %}
            {{ name }}
          {% else %}
            Unknown Spool
          {% endif %}
        secondary: |
          {% set location = state_attr(entity, 'location') %} {% if location %}
            {{ (state_attr(entity, 'remaining_weight') | float)  | round(2) }} g ({{ location }})
          {% else %}
            {{ (state_attr(entity, 'remaining_weight') | float)  | round(2) }} g
          {% endif %}
        custom_theme:
          - min: 0
            max: 10
            bar_color: red
          - min: 10
            max: 25
            bar_color: yellow
          - min: 25
            max: 100
            bar_color: var(--state-icon-color)
        tap_action:
          action: more-info
sort:
  method: attribute
  attribute: last_used
  reverse: false
card:
  type: grid
  columns: 1
  square: false
card_param: cards
```
- Preview:
<img width="583" height="350" alt="image" src="https://github.com/user-attachments/assets/1a1f181b-2eaf-49f8-8fb1-375943306fae" />

* For more examples / ideas checkout [Display Example](https://github.com/Disane87/spoolman-homeassistant?tab=readme-ov-file#spool-display-example)
