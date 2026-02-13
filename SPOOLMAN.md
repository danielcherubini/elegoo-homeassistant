# Spoolman Integration

To integrate the filament usage provided by the total extrusion sensor (only available for the Elegoo Centauri Carbon), the following steps are needed:

* OpenCentauri firmware installed. Instructions [here](https://docs.opencentauri.cc/patched-firmware/). Only the patched version is required.
    * If you already had [this integration](https://github.com/danielcherubini/elegoo-homeassistant) installed before installing the OpenCentauri firmware, you have to remove and re-add your printer so the Total Extrusion sensor is made available.

* [Spoolman Home Assistant](https://github.com/Disane87/spoolman-homeassistant) integration installed and configured

* As there's no active spool control (provided by a full Klipper implementation), helpers are needed to store the active spool ID. For this, two helpers are recommended:

    * Current Spool ID - Input Number Helper (input_number.current_spool_id) with a minimum value of 0 and maximum value of 1000000
    * Current Spool - Template Select Helper (select.current_spool) - To provide a more user-friendly way to swap the current active spool without having to memorize IDs. Just add this select on the dashboard for the user to easily select the current spool.
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

* To update the spool usage in a more frequent manner then once per print, a couple more helpers are needed:

    * Accumulated Spool Usage (input_number.accumulated_spool_usage) with a minimum value of 0 and maximum value of 1000000
    * Diff Accumulated Spool Usage - Template Number (number.diff_accumulated_spool_usage) - To report the difference between what has been updated to spoolman and the current usage
        * State:
        ```jinja
        {{ states('sensor.centauri_carbon_total_extrusion') | float(0) - states('input_number.accumulated_spool_usage') | float(0) }}        
        ```
        With a minimum value of 0, a maximum value of 1000000 and unit of measurement mm

* A simple script to update the spool usage using the service provided by the spoolman home assistant integration:

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

* An automation to update the spool usage. This automation updates the spool usage every 100mm of extruded filament, and updates the final usage when the printing is done

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
            entity_id: input_number.accumulated_spool_usage
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

* A recommended automation is auto reset filament id, so when an load/unloading of filament is detected (to prevent using the id of the previous spool):

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

* A nice way to list your available spools (requires the [auto-entities](https://github.com/thomasloven/lovelace-auto-entities) and the [entity-progress-card](https://github.com/francois-le-ko4la/lovelace-entity-progress-card/):

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

