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
    CC2_SUBSTATUS_BED_PREHEATING,
    CC2_SUBSTATUS_BED_PREHEATING_2,
    CC2_SUBSTATUS_EXTRUDER_PREHEATING,
    CC2_SUBSTATUS_EXTRUDER_PREHEATING_2,
    CC2_SUBSTATUS_PAUSED,
    CC2_SUBSTATUS_PAUSED_2,
    CC2_SUBSTATUS_PAUSING,
    CC2_SUBSTATUS_PRINTING,
    CC2_SUBSTATUS_PRINTING_COMPLETED,
    CC2_SUBSTATUS_RESUMING,
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
    PRINT_STATUS_MAP: ClassVar[dict[int, ElegooPrintStatus]] = {
        CC2_SUBSTATUS_EXTRUDER_PREHEATING: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_EXTRUDER_PREHEATING_2: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_BED_PREHEATING: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_BED_PREHEATING_2: ElegooPrintStatus.PREHEATING,
        CC2_SUBSTATUS_PRINTING: ElegooPrintStatus.PRINTING,
        CC2_SUBSTATUS_PRINTING_COMPLETED: ElegooPrintStatus.COMPLETE,
        CC2_SUBSTATUS_PAUSING: ElegooPrintStatus.PAUSING,
        CC2_SUBSTATUS_PAUSED: ElegooPrintStatus.PAUSED,
        CC2_SUBSTATUS_PAUSED_2: ElegooPrintStatus.PAUSED,
        CC2_SUBSTATUS_RESUMING: ElegooPrintStatus.PRINTING,
        CC2_SUBSTATUS_STOPPING: ElegooPrintStatus.STOPPING,
        CC2_SUBSTATUS_STOPPED: ElegooPrintStatus.STOPPED,
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
            cc2_data: The raw CC2 status data.
            printer_type: The type of printer (for FDM-specific handling).

        Returns:
            A PrinterStatus object compatible with the existing integration.

        """
        # Create status object with mapped data
        status = PrinterStatus()

        # Map machine status
        cc2_status = cc2_data.get("status", CC2_STATUS_IDLE)
        status.current_status = cls.MACHINE_STATUS_MAP.get(
            cc2_status, ElegooMachineStatus.IDLE
        )

        # Map temperatures
        status.temp_of_nozzle = round(cc2_data.get("temp_extruder", 0), 2)
        status.temp_target_nozzle = round(cc2_data.get("temp_extruder_target", 0), 2)
        status.temp_of_hotbed = round(cc2_data.get("temp_heater_bed", 0), 2)
        status.temp_target_hotbed = round(cc2_data.get("temp_heater_bed_target", 0), 2)
        status.temp_of_box = round(cc2_data.get("temp_box", 0), 2)
        status.temp_target_box = round(cc2_data.get("temp_box_target", 0), 2)

        # Map fan speeds
        fan_data = cc2_data.get("fan_speeds", {})
        status.current_fan_speed = CurrentFanSpeed({
            "ModelFan": fan_data.get("fan", 0),
            "AuxiliaryFan": fan_data.get("aux_fan", 0),
            "BoxFan": fan_data.get("box_fan", 0),
        })

        # Map light status
        light_data = cc2_data.get("light_status", {})
        status.light_status = LightStatus({
            "SecondLight": light_data.get("enabled", 0),
            "RgbLight": light_data.get("rgb", [0, 0, 0]),
        })

        # Map print info
        print_info = cls._map_print_info(cc2_data, printer_type)
        status.print_info = print_info

        # Map position
        pos = cc2_data.get("position", {})
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
            cc2_data: The raw CC2 status data.
            printer_type: The type of printer.

        Returns:
            A PrintInfo object.

        """
        print_info = PrintInfo()

        # Map sub-status to print status
        sub_status = cc2_data.get("sub_status", 0)
        print_info.status = cls.PRINT_STATUS_MAP.get(
            sub_status, ElegooPrintStatus.IDLE
        )

        # Map print job data
        job_data = cc2_data.get("print_job", {})

        print_info.filename = job_data.get("file_name")
        print_info.task_id = job_data.get("task_id")

        # Map layer info
        print_info.current_layer = job_data.get("current_layer")
        print_info.total_layers = job_data.get("total_layers")
        if print_info.current_layer is not None and print_info.total_layers is not None:
            print_info.remaining_layers = max(
                0, print_info.total_layers - print_info.current_layer
            )

        # Map time info (CC2 uses seconds, convert to ms)
        current_time = job_data.get("print_time", 0)
        total_time = job_data.get("total_time", 0)

        # FDM printers report time in seconds, convert to ms
        print_info.current_ticks = int(current_time * 1000) if current_time else None
        print_info.total_ticks = int(total_time * 1000) if total_time else None
        if print_info.current_ticks is not None and print_info.total_ticks is not None:
            print_info.remaining_ticks = max(
                0, print_info.total_ticks - print_info.current_ticks
            )

        # Map progress
        progress = job_data.get("progress")
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
                        round(
                            print_info.current_layer / print_info.total_layers * 100
                        ),
                    ),
                )
        else:
            print_info.percent_complete = None

        # Map print speed
        print_info.print_speed_pct = cc2_data.get("print_speed", 100)

        # Map error
        error_code = job_data.get("error_code", 0)
        print_info.error_number = ElegooPrintError.from_int(error_code)

        # Map extrusion data
        print_info.total_extrusion = job_data.get("total_extrusion")
        print_info.current_extrusion = job_data.get("current_extrusion")

        return print_info

    @classmethod
    def map_attributes(cls, cc2_data: dict[str, Any]) -> PrinterAttributes:
        """
        Map CC2 attributes data to PrinterAttributes.

        Arguments:
            cc2_data: The raw CC2 attributes data.

        Returns:
            A PrinterAttributes object.

        """
        # Create a dictionary in the expected format
        attrs_dict = {
            "Attributes": {
                "Name": cc2_data.get("host_name", ""),
                "MachineName": cc2_data.get("machine_model", ""),
                "BrandName": "ELEGOO",
                "ProtocolVersion": "CC2",
                "FirmwareVersion": cc2_data.get("firmware_version", ""),
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
