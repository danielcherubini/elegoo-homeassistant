"""Attributes models for Elegoo Printer."""

from typing import Any


class PrinterAttributes:
    """
    Represents the attributes of a 3D printer.

    Attributes:
        name (str): The name of the printer.
        machine_name (str): The machine name of the printer.
        brand_name (str): The brand name of the printer.
        protocol_version (str): The protocol version of the printer.
        firmware_version (str): The firmware version of the printer.
        resolution (str): The resolution of the printer.
        xyz_size (str): The XYZ size of the printer.
        mainboard_ip (str): The IP address of the printer's mainboard.
        mainboard_id (str): The ID of the printer's mainboard.
        num_video_stream_connected (int): Number of Connected Video Streams.
        max_video_stream_allowed (int): Maximum Number of Connections for Video Streams.
        num_cloud_sdcp_services_connected (int): Number of Connected Cloud SDCP Services.
        max_cloud_sdcp_services_allowed (int): Maximum Number of Connections for Cloud SDCP Services.
        network_status (str): Network Connection Status, WiFi/Ethernet Port.
        mainboard_mac (str): The MAC address of the printer's mainboard.
        usb_disk_status (int): USB Drive Connection Status. 0: Disconnected, 1: Connected.
        capabilities (List[str]): Supported Sub-protocols on the Motherboard.
        support_file_types (List[str]): Supported File Types.
        devices_status (Dict[str, int]): Device Self-Check Status.
        release_film_max (int): Maximum number of uses (service life) for the release film.
        temp_of_uvled_max (int): Maximum operating temperature for UVLED(℃).
        camera_status (int): Camera Connection Status, 0: Disconnected, 1: Connected
        remaining_memory (int): Remaining File Storage Space Size(bit).
        sdcp_status (int): SDCP Service Status. 0: Disconnected, 1: Connected.
        tlp_no_cap_pos (float): Model height threshold for not performing time-lapse photography (mm).
        tlp_start_cap_pos (float): The print height at which time-lapse photography begins (mm).
        tlp_inter_layers (int): Time-lapse photography shooting interval layers.
        mainboard_id_root (str): Motherboard ID.
        timestamp (int): Timestamp.
        topic (str): Topic, used to distinguish the type of reported message.

    Example usage:

    >>> printer_data_json = '''
    ... {
    ...     "Attributes": {
    ...         "Name": "PrinterName",
    ...         "MachineName": "MachineModel",
    ...         "BrandName": "CBD",
    ...         "ProtocolVersion": "V3.0.0",
    ...         "FirmwareVersion": "V1.0.0",
    ...         "Resolution": "7680x4320",
    ...         "XYZsize": "210x140x100",
    ...         "MainboardIP": "192.168.1.1",
    ...         "MainboardID": "000000000001d354",
    ...         "NumberOfVideoStreamConnected": 1,
    ...         "MaximumVideoStreamAllowed": 1,
    ...         "NumberOfCloudSDCPServicesConnected": 0,
    ...         "MaximumCloudSDCPSercicesAllowed": 1,
    ...         "NetworkStatus": "wlan",
    ...         "MainboardMAC": "00:11:22:33:44:55",
    ...         "UsbDiskStatus": 0,
    ...         "Capabilities": [
    ...             "FILE_TRANSFER",
    ...             "PRINT_CONTROL",
    ...             "VIDEO_STREAM"
    ...         ],
    ...         "SupportFileType": [
    ...             "CTB"
    ...         ],
    ... "DevicesStatus": {
    ...             "TempSensorStatusOfUVLED": 0,
    ...             "LCDStatus": 0,
    ...             "SgStatus": 0,
    ...             "ZMotorStatus": 0,
    ...             "RotateMotorStatus": 0,
    ...             "RelaseFilmState": 0,
    ...             "XMotorStatus": 0
    ...         },
    ...         "ReleaseFilmMax": 0,
    ...         "TempOfUVLEDMax": 0,
    ...         "CameraStatus": 0,
    ...         "RemainingMemory": 123455,
    ...         "SDCPStatus": 1,
    ...         "TLPNoCapPos": 50.0,
    ...         "TLPStartCapPos": 30.0,
    ...         "TLPInterLayers": 20
    ...     },
    ...     "MainboardID": "ffffffff",
    ...     "TimeStamp": 1687069655,
    ...     "Topic": "sdcp/attributes/$MainboardID"
    ... }
    ... '''
    >>> printer_attributes = PrinterAttributes.from_json(printer_data_json)
    >>> print(printer_attributes.name)  # Output: PrinterName

    """  # noqa: E501

    def __init__(
        self, data: dict[str, Any] | None = None
    ) -> None:  # Make 'data' optional
        """
        Initialize a new PrinterAttributes object from a dictionary.

        Args:
            data (Dict[str, Any], optional): A dictionary containing printer attribute
                                             data. Defaults to an empty dictionary.

        """
        if data is None:
            data = {}
        attributes = data.get("Attributes", {})
        self.name: str = attributes.get("Name", "")
        self.machine_name: str = attributes.get("MachineName", "")
        self.brand_name: str = attributes.get("BrandName", "")
        self.protocol_version: str = attributes.get("ProtocolVersion", "")
        self.firmware_version: str = attributes.get("FirmwareVersion", "")
        self.resolution: str = attributes.get("Resolution", "")
        self.xyz_size: str = attributes.get("XYZsize", "")
        self.mainboard_ip: str = attributes.get("MainboardIP", "")
        self.mainboard_id: str = attributes.get("MainboardID", "")
        self.num_video_stream_connected: int = attributes.get(
            "NumberOfVideoStreamConnected", 0
        )
        self.max_video_stream_allowed: int = attributes.get(
            "MaximumVideoStreamAllowed", 0
        )
        self.num_cloud_sdcp_services_connected: int = attributes.get(
            "NumberOfCloudSDCPServicesConnected", 0
        )
        # Note: Typo in the key below is intentional to match the API
        self.max_cloud_sdcp_services_allowed: int = attributes.get(
            "MaximumCloudSDCPSercicesAllowed", 0
        )
        self.network_status: str = attributes.get("NetworkStatus", "")
        self.mainboard_mac: str = attributes.get("MainboardMAC", "")
        self.usb_disk_status: int = attributes.get("UsbDiskStatus", 0)
        self.capabilities: list[str] = attributes.get("Capabilities")
        self.support_file_types: list[str] = attributes.get("SupportFileType")
        self.devices_status: dict[str, int] = attributes.get("DevicesStatus", {})
        self.release_film_max: int = attributes.get("ReleaseFilmMax", 0)
        self.temp_of_uvled_max: int = attributes.get("TempOfUVLEDMax", 0)
        self.camera_status: int = attributes.get("CameraStatus", 0)
        self.remaining_memory: int = attributes.get("RemainingMemory", 0)
        self.sdcp_status: int = attributes.get("SDCPStatus", 0)
        self.tlp_no_cap_pos: float = attributes.get("TLPNoCapPos", 0.0)
        self.tlp_start_cap_pos: float = attributes.get("TLPStartCapPos", 0.0)
        self.tlp_inter_layers: int = attributes.get("TLPInterLayers", 0)
        self.mainboard_id_root: str = data.get("MainboardID", "")
        self.timestamp: int = data.get("TimeStamp", 0)
        self.topic: str = data.get("Topic", "")

    @classmethod
    def from_json(cls, json_string: str) -> "PrinterAttributes":
        """
        Create a PrinterAttributes object from a JSON string.

        Args:
            json_string (str): A JSON string containing printer attribute data.

        Returns:
            PrinterAttributes: A new PrinterAttributes object.

        """
        import json

        try:
            data: dict[str, Any] = json.loads(json_string)
        except json.JSONDecodeError:
            data: dict[
                str, Any
            ] = {}  # Return an empty object or handle the error as needed
        return cls(data)
