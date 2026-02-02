"""
CC2 (Centauri Carbon 2) status mapping models.

This module maps CC2 status format to the existing PrinterStatus/PrinterAttributes
models used by the rest of the integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.elegoo_printer.sdcp.models.attributes import PrinterAttributes
from custom_components.elegoo_printer.sdcp.models.enums import (
    ElegooMachineStatus,
    ElegooPrintError,
    ElegooPrintStatus,
)
from custom_components.elegoo_printer.sdcp.models.status import (
    CurrentFanSpeed,
    LightStatus,
    PrinterStatus,
    PrintInfo,
)

from .const import (
    CC2_STATUS_AUTO_LEVELING,
    CC2_STATUS_EMERGENCY_STOP,
    CC2_STATUS_EXTRUDER_OPERATING,
    CC2_STATUS_FILAMENT_OPERATING,
    CC2_STATUS_FILAMENT_OPERATING_2,
    CC2_STATUS_FILE_TRANSFERRING,
    CC2_STATUS_HOMING,
    CC2_STATUS_IDLE,
    CC2_STATUS_INITIALIZING,
    CC2_STATUS_PID_CALIBRATING,
    CC2_STATUS_POWER_LOSS_RECOVERY,
    CC2_STATUS_PRINTING,
    CC2_STATUS_RESONANCE_TESTING,
    CC2_STATUS_SELF_CHECKING,
    CC2_STATUS_UPDATING,
    CC2_STATUS_VIDEO_COMPOSING,
    CC2_SUBSTATUS_AUTO_LEVELING,
    CC2_SUBSTATUS_AUTO_LEVELING_COMPLETED,
    CC2_SUBSTATUS_BED_PREHEATING,
    CC2_SUBSTATUS_BED_PREHEATING_2,
    CC2_SUBSTATUS_EXTRUDER_PREHEATING,
    CC2_SUBSTATUS_EXTRUDER_PREHEATING_2,
    CC2_SUBSTATUS_HOMING,
    CC2_SUBSTATUS_HOMING_COMPLETED,
    CC2_SUBSTATUS_PAUSED,
    CC2_SUBSTATUS_PAUSED_2,
    CC2_SUBSTATUS_PAUSING,
    CC2_SUBSTATUS_PRINTING,
    CC2_SUBSTATUS_PRINTING_COMPLETED,
    CC2_SUBSTATUS_RESUMING,
    CC2_SUBSTATUS_RESUMING_COMPLETED,
    CC2_SUBSTATUS_STOPPED,
    CC2_SUBSTATUS_STOPPING,
)

if TYPE_CHECKING:
    from custom_components.elegoo_printer.sdcp.models.enums import PrinterType


class CC2StatusMapper:
    """Maps CC2 status format to PrinterStatus."""

    # Map CC2 machine status codes to ElegooMachineStatus
    MACHINE_STATUS_MAP: ClassVar[dict[int, ElegooMachineStatus]] = {
        CC2_STATUS_INITIALIZING: ElegooMachineStatus.IDLE,
        CC2_STATUS_IDLE: ElegooMachineStatus.IDLE,
        CC2_STATUS_PRINTING: ElegooMachineStatus.PRINTING,
        CC2_STATUS_FILAMENT_OPERATING: ElegooMachineStatus.LOADING_UNLOADING,
        CC2_STATUS_FILAMENT_OPERATING_2: ElegooMachineStatus.LOADING_UNLOADING,
        CC2_STATUS_AUTO_LEVELING: ElegooMachineStatus.LEVELING,
        CC2_STATUS_PID_CALIBRATING: ElegooMachineStatus.PID_TUNING,
        CC2_STATUS_RESONANCE_TESTING: ElegooMachineStatus.INPUT_SHAPING,
        CC2_STATUS_SELF_CHECKING: ElegooMachineStatus.DEVICES_TESTING,
        CC2_STATUS_UPDATING: ElegooMachineStatus.IDLE,
        CC2_STATUS_HOMING: ElegooMachineStatus.HOMING,
        CC2_STATUS_FILE_TRANSFERRING: ElegooMachineStatus.FILE_TRANSFERRING,
        CC2_STATUS_VIDEO_COMPOSING: ElegooMachineStatus.IDLE,
        CC2_STATUS_EXTRUDER_OPERATING: ElegooMachineStatus.LOADING_UNLOADING,
        CC2_STATUS_EMERGENCY_STOP: ElegooMachineStatus.STOPPED,
        CC2_STATUS_POWER_LOSS_RECOVERY: ElegooMachineStatus.RECOVERY,
    }

    # Map CC2 sub-status codes to ElegooPrintStatus
    # Based on elegoo-link elegoo_fdm_cc2_message_adapter.cpp
    PRINT_STATUS_MAP: ClassVar[dict[int, ElegooPrintStatus]] = {
        # Preheating states
        CC2_SUBSTATUS_EXTRUDER_PREHEATING: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_EXTRUDER_PREHEATING_2: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_BED_PREHEATING: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_BED_PREHEATING_2: ElegooPrintStatus.PREHEATING,
        # Printing states
        CC2_SUBSTATUS_PRINTING: ElegooPrintStatus.PRINTING,
        CC2_SUBSTATUS_PRINTING_COMPLETED: ElegooPrintStatus.COMPLETE,
        # Pause/resume states
        CC2_SUBSTATUS_PAUSING: ElegooPrintStatus.PAUSING,
        CC2_SUBSTATUS_PAUSED: ElegooPrintStatus.PAUSED,
        CC2_SUBSTATUS_PAUSED_2: ElegooPrintStatus.PAUSED,
        CC2_SUBSTATUS_RESUMING: ElegooPrintStatus.PRINTING,
        CC2_SUBSTATUS_RESUMING_COMPLETED: ElegooPrintStatus.PRINTING,
        # Stop states
        CC2_SUBSTATUS_STOPPING: ElegooPrintStatus.STOPPING,
        CC2_SUBSTATUS_STOPPED: ElegooPrintStatus.STOPPED,
        # Homing during print
        CC2_SUBSTATUS_HOMING: ElegooPrintStatus.PRINTING,
        CC2_SUBSTATUS_HOMING_COMPLETED: ElegooPrintStatus.PRINTING,
        # Leveling during print
        CC2_SUBSTATUS_AUTO_LEVELING: ElegooPrintStatus.LEVELING,
        CC2_SUBSTATUS_AUTO_LEVELING_COMPLETED: ElegooPrintStatus.LEVELING,
    }

    @classmethod
    def map_status(
        cls,
        cc2_data: dict[str, Any],
        printer_type: PrinterType | None = None,
    ) -> PrinterStatus:
        """
        Map CC2 status data to PrinterStatus.

        Arguments:
            cc2_data: The raw CC2 status data (real CC2 nested format).
            printer_type: The type of printer (for FDM-specific handling).

        Returns:
            A PrinterStatus object compatible with the existing integration.

        """
        # Create status object with mapped data
        status = PrinterStatus()

        # Map machine status from nested structure
        machine_status = cc2_data.get("machine_status", {})
        cc2_status = machine_status.get("status", CC2_STATUS_IDLE)
        status.current_status = cls.MACHINE_STATUS_MAP.get(
            cc2_status, ElegooMachineStatus.IDLE
        )

        # Map temperatures from nested structure
        extruder = cc2_data.get("extruder", {})
        heater_bed = cc2_data.get("heater_bed", {})
        status.temp_of_nozzle = round(extruder.get("temperature", 0), 2)
        status.temp_target_nozzle = round(extruder.get("target", 0), 2)
        status.temp_of_hotbed = round(heater_bed.get("temperature", 0), 2)
        status.temp_target_hotbed = round(heater_bed.get("target", 0), 2)
        # Box temperature may be in ztemperature_sensor or separate field
        ztemp = cc2_data.get("ztemperature_sensor", {})
        status.temp_of_box = round(ztemp.get("temperature", 0), 2)
        status.temp_target_box = 0.0  # CC2 may not have box target

        # Map fan speeds from nested structure (CC2 uses 0-255, convert to %)
        fans = cc2_data.get("fans", {})
        fan_speed = fans.get("fan", {}).get("speed", 0)
        aux_fan_speed = fans.get("aux_fan", {}).get("speed", 0)
        box_fan_speed = fans.get("box_fan", {}).get("speed", 0)

        # Convert 0-255 to percentage (0-100)
        def to_pct(val: float) -> int:
            return round(val / 255 * 100) if val else 0

        status.current_fan_speed = CurrentFanSpeed(
            {
                "ModelFan": to_pct(fan_speed),
                "AuxiliaryFan": to_pct(aux_fan_speed),
                "BoxFan": to_pct(box_fan_speed),
            }
        )

        # Map light status from nested structure
        led = cc2_data.get("led", {})
        led_status = led.get("status", 0)
        # Convert LED brightness (0-255) to on/off state
        status.light_status = LightStatus(
            {
                "SecondLight": 1 if led_status > 0 else 0,
                "RgbLight": [led_status, led_status, led_status],
            }
        )

        # Map print info
        print_info = cls._map_print_info(cc2_data, printer_type)
        status.print_info = print_info

        # Map position - try gcode_move_inf first (official), fallback to gcode_move
        pos = cc2_data.get("gcode_move_inf", {})
        if not pos:
            pos = cc2_data.get("gcode_move", {})
        x = pos.get("x", 0)
        y = pos.get("y", 0)
        z = pos.get("z", 0)
        status.current_coord = f"{x:.2f},{y:.2f},{z:.2f}"
        status.z_offset = cc2_data.get("z_offset", 0.0)

        return status

    @classmethod
    def _map_print_info(
        cls,
        cc2_data: dict[str, Any],
        printer_type: PrinterType | None = None,  # noqa: ARG003
    ) -> PrintInfo:
        """
        Map CC2 print info to PrintInfo.

        Arguments:
            cc2_data: The raw CC2 status data (real CC2 nested format).
            printer_type: The type of printer.

        Returns:
            A PrintInfo object.

        """
        print_info = PrintInfo()

        # Map sub-status to print status from nested structure
        machine_status = cc2_data.get("machine_status", {})
        sub_status = machine_status.get("sub_status", 0)
        print_info.status = cls.PRINT_STATUS_MAP.get(sub_status, ElegooPrintStatus.IDLE)

        # Map print data from print_status (real CC2 structure)
        print_status = cc2_data.get("print_status", {})

        print_info.filename = print_status.get("filename")
        # Use uuid if available, otherwise generate from filename
        task_uuid = print_status.get("uuid")
        if task_uuid:
            print_info.task_id = task_uuid
        elif print_info.filename:
            print_info.task_id = f"cc2_{hash(print_info.filename) & 0xFFFFFFFF:08x}"

        # Map layer info
        print_info.current_layer = print_status.get("current_layer")
        print_info.total_layers = print_status.get("total_layer")
        if print_info.current_layer is not None and print_info.total_layers is not None:
            print_info.remaining_layers = max(
                0, print_info.total_layers - print_info.current_layer
            )

        # Map time info (CC2 uses seconds, convert to ms)
        current_time = print_status.get("print_duration")
        total_time = print_status.get("total_duration")
        remaining_time = print_status.get("remaining_time_sec")

        # FDM printers report time in seconds, convert to ms
        print_info.current_ticks = (
            int(current_time * 1000) if current_time is not None else None
        )
        print_info.total_ticks = (
            int(total_time * 1000) if total_time is not None else None
        )
        # Use remaining_time_sec directly if available
        if remaining_time is not None:
            print_info.remaining_ticks = int(remaining_time * 1000)
        elif (
            print_info.current_ticks is not None and print_info.total_ticks is not None
        ):
            print_info.remaining_ticks = max(
                0, print_info.total_ticks - print_info.current_ticks
            )

        # Map progress - check both print_status and machine_status
        progress = print_status.get("progress")
        if progress is None:
            progress = machine_status.get("progress")
        print_info.progress = int(progress) if progress is not None else None

        # Calculate percent complete
        active_statuses = {
            ElegooPrintStatus.PRINTING,
            ElegooPrintStatus.PAUSED,
            ElegooPrintStatus.PAUSING,
            ElegooPrintStatus.PREHEATING,
            ElegooPrintStatus.LEVELING,
        }
        if print_info.status in active_statuses:
            if print_info.progress is not None:
                print_info.percent_complete = max(0, min(100, int(print_info.progress)))
            elif (
                print_info.current_layer is not None
                and print_info.total_layers is not None
                and print_info.total_layers > 0
            ):
                print_info.percent_complete = max(
                    0,
                    min(
                        100,
                        round(print_info.current_layer / print_info.total_layers * 100),
                    ),
                )
        else:
            print_info.percent_complete = None

        # Map print speed from gcode_move/gcode_move_inf speed_mode
        gcode_move = cc2_data.get("gcode_move_inf", {})
        if not gcode_move:
            gcode_move = cc2_data.get("gcode_move", {})
        speed_mode = gcode_move.get("speed_mode", 1)
        # 0=Silent(50%), 1=Balanced(100%), 2=Sport(150%), 3=Ludicrous(200%)
        speed_map = {0: 50, 1: 100, 2: 150, 3: 200}
        print_info.print_speed_pct = speed_map.get(speed_mode, 100)

        # Map error
        error_code = cc2_data.get("error_code", 0)
        print_info.error_number = ElegooPrintError.from_int(error_code)

        # Map extrusion data
        print_info.current_extrusion = gcode_move.get("e") or gcode_move.get("extruder")

        return print_info

    @classmethod
    def map_attributes(cls, cc2_data: dict[str, Any]) -> PrinterAttributes:
        """
        Map CC2 attributes data to PrinterAttributes.

        Arguments:
            cc2_data: The raw CC2 attributes data (real CC2 nested format).

        Returns:
            A PrinterAttributes object.

        """
        # Extract firmware version from nested structure
        software_version = cc2_data.get("software_version", {})
        firmware = software_version.get("ota_version", "")

        # Create a dictionary in the expected format
        attrs_dict = {
            "Attributes": {
                "Name": cc2_data.get("hostname", ""),
                "MachineName": cc2_data.get("machine_model", ""),
                "BrandName": "ELEGOO",
                "ProtocolVersion": "CC2",
                "FirmwareVersion": firmware,
                "Resolution": cc2_data.get("resolution", ""),
                "XYZsize": cc2_data.get("xyz_size", ""),
                "MainboardIP": cc2_data.get("ip", ""),
                "MainboardID": cc2_data.get("sn", ""),
                "NumberOfVideoStreamConnected": cc2_data.get("video_connections", 0),
                "MaximumVideoStreamAllowed": cc2_data.get("max_video_connections", 1),
                "NetworkStatus": cc2_data.get("network_type", ""),
                "MainboardMAC": cc2_data.get("mac", ""),
                "UsbDiskStatus": 1 if cc2_data.get("usb_connected") else 0,
                "CameraStatus": 1 if cc2_data.get("camera_connected") else 0,
                "RemainingMemory": cc2_data.get("remaining_memory", 0),
                "SDCPStatus": 1,  # Always connected if we're talking to it
            }
        }

        return PrinterAttributes(attrs_dict)
