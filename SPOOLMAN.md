# Spoolman Integration

To integrate the filament usage provided by the total extrusion sensor (only available for the Elegoo Centauri Carbon), the following steps are needed:

* OpenCentauri firmware installed. Instructions [here](https://docs.opencentauri.cc/patched-firmware/). Only the patched version is required.
    * If you already had [this integration](https://github.com/danielcherubini/elegoo-homeassistant) installed before installing the OpenCentauri firmware, you have to remove and re-add your printer so the Total Extrusion sensor is made available.

* [Spoolman Home Assistant](https://github.com/Disane87/spoolman-homeassistant) integration installed and configured

* As there's no active spool control (provided by a full Klipper implementation), helpers are needed to store the active filament ID. For this, two helpers are recommended:

    * Current Filament ID - Input Number Helper (input_number.current_filament_id) with a minimum value of 0 and maximum value of 1000000
    * Current Filament - Template Select Helper (select.current_filament) - To provide a more user friendly way to swap the current active filament without having to memorize IDs. Just add this select on the dashboard for the user to easily select the current filament.
        * State:
        ```jinja
        {%
        set spool = 'sensor.spoolman_filament_' ~ states('input_number.current_filament_id') | default(0) | int 
        %}
        {% if spool == 'sensor.spoolman_filament_0' %}
        {{ '0: Unknown' }}
        {% else %}
        {% set id = state_attr(spool, 'id') | default(0) | int %}
        {% set vendor = state_attr(spool, 'vendor_name') | default('') %}
        {% set material = state_attr(spool, 'material') | default('') %}
        {% set name = state_attr(spool, 'name') | default('')%}
        {{ id ~ ': ' + vendor + ' ' + material + ' ' + name }}
        {% endif %}
        ```
        * Available Options:
        ```jinja
        {% set filaments = integration_entities('spoolman') | select('match', 'sensor.spoolman_filament_') | list %}
        {% set ns = namespace(x=['0: Unknown']) %}
        {% for filament in filaments -%}
        {% set ns.x = ns.x + [state_attr(filament, 'id') ~ ': ' + state_attr(filament, 'vendor_name')|default('') + ' ' + state_attr(filament, 'material')|default('') + ' ' + state_attr(filament, 'name')|default('')] %}
        {%- endfor %}
        {{ ns.x }}
        ```

* To update the filament usage in a more frequent manner then once per print, a couple more helpers are needed:

    * Accumulated Filament Usage (input_number.accumulated_filament_usage) with a minimum value of 0 and maximum value of 1000000
    * Diff Accumulated Filament Usage - Template Number (number.diff_accumulated_filament_usage) - To report the difference between what has been updated to spoolman and the current usage
        * State:
        ```jinja
        {{ states('sensor.centauri_carbon_total_extrusion') | float(0) - states('input_number.accumulated_filament_usage') | float(0) }}        
        ```
        With a minimum value of 0, a maximum value of 1000000 and unit of measurement mm

* A simple script to update the filament usage using the service provided by the spoolman home assistant integration:

```yaml
sequence:
- action: spoolman.use_spool_filament
    metadata: {}
    data:
    id: "{{ states(current_filament_id) }}"
    use_length: "{{ states(current_extrusion) }}"
fields:
current_filament_id:
    required: true
    selector:
    entity:
        multiple: false
        filter:
        domain: input_number
    name: Current Filament ID
current_extrusion:
    required: true
    selector:
    entity:
        multiple: false
        filter:
        domain: number
    name: Current Extrusion
alias: Update Filament Usage
description: ""
mode: queued
max: 100
```

* An automation to update the filament usage. This automation updates the spool usage every 100mm of extruded filament, and updates the final usage when the printing is done

```yaml
alias: Update Filament Usage
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
conditions: []
actions:
  - choose:
      - conditions:
          - condition: numeric_state
            entity_id: number.diff_accumulated_filament_usage
            above: 100
          - condition: trigger
            id:
              - extrusion
        sequence:
          - action: script.update_filament_usage
            metadata: {}
            data:
              current_filament_id: input_number.current_filament_id
              current_extrusion: number.diff_accumulated_filament_usage
          - action: input_number.set_value
            metadata: {}
            target:
              entity_id: input_number.accumulated_filament_usage
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
            entity_id: input_number.accumulated_filament_usage
            above: 0
        sequence:
          - action: script.update_filament_usage
            metadata: {}
            data:
              current_filament_id: input_number.current_filament_id
              current_extrusion: number.diff_accumulated_filament_usage
          - action: input_number.set_value
            metadata: {}
            target:
              entity_id: input_number.accumulated_filament_usage
            data:
              value: "{{ float(0) }}"
        alias: If it's no longer printing
mode: queued
max: 1000
```

* A recommended automation is auto reset filament id, so when an load/unloading of filament is detected (to prevent using the id of the previous spool):

```yaml
alias: Auto Reset Filament ID
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
      entity_id: input_number.current_filament_id
    data:
      value: 0
mode: single
```
