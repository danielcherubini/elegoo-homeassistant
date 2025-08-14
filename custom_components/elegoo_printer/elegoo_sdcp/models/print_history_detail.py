"""Print History Detail for Elegoo SDCP."""

from typing import Any

from custom_components.elegoo_printer.elegoo_sdcp.models.enums import (
    ElegooErrorStatusReason,
)


class PrintHistoryDetail:
    """Represents the details of a print history entry."""

    def __init__(self, data: dict[str, Any]) -> None:
        """
        Initialize a PrintHistoryDetail instance with print job details from a dictionary.

        The input dictionary should contain keys corresponding to print job attributes such as thumbnail, task name, timing, status, slice information, print layers, task ID, MD5 hash, current layer volume, time-lapse video details, and error status reason. Missing keys default to None or zero where applicable.
        """
        self.thumbnail: str | None = data.get("Thumbnail")
        self.task_name: str | None = data.get("TaskName")
        self.begin_time: int | None = data.get("BeginTime")
        self.end_time: int | None = data.get("EndTime")
        self.task_status: int | None = data.get("TaskStatus")
        self.slice_information: SliceInformation = SliceInformation(
            data.get("SliceInformation", {})
        )
        self.already_print_layer: int | None = data.get("AlreadyPrintLayer")
        self.task_id: str | None = data.get("TaskId")
        self.MD5: str | None = data.get("MD5")
        self.current_layer_tal_volume: float | None = data.get(
            "CurrentLayerTalVolume"
        )  # Or int, depending on actual type
        self.time_lapse_video_status: int | None = data.get("TimeLapseVideoStatus")
        self.time_lapse_video_url: str | None = data.get("TimeLapseVideoUrl")

        _error_status_reason: int = data.get("ErrorStatusReason", 0)
        self.error_status_reason: ElegooErrorStatusReason | None = (
            ElegooErrorStatusReason.from_int(_error_status_reason)
        )

    def __repr__(self) -> str:
        """
        Return a string representation of the instance's attributes as a dictionary.
        """
        return str(self.__dict__)


class SliceInformation:
    """Represent the slice information of a print job."""

    def __init__(self, data: dict[str, Any]) -> None:  # noqa: PLR0915
        """
        Initialize a SliceInformation object.

        Args:
            data: A dictionary containing the slice information data.

        """
        self.resolution_x: int | None = data.get("resolution_x")
        self.resolution_y: int | None = data.get("resolution_y")
        self.layer_height: float | None = data.get("layer_height")
        self.total_layer_numbers: int | None = data.get("total_layer_numbers")
        self.machine_size_x: float | None = data.get("machine_size_x")
        self.machine_size_y: float | None = data.get("machine_size_y")
        self.machine_size_z: float | None = data.get("machine_size_z")
        self.model_size_x: float | None = data.get("model_size_x")
        self.model_size_y: float | None = data.get("model_size_y")
        self.model_size_z: float | None = data.get("model_size_z")
        self.volume: float | None = data.get("volume")
        self.weight: float | None = data.get("weight")
        self.price: float | None = data.get("price")
        self.print_time: int | None = data.get("print_time")
        self.machine_name: str | None = data.get("machine_name")
        self.independent_supports: int | None = data.get("independent_supports")
        self.resin_color: int | None = data.get("resin_color")
        self.resin_type: str | None = data.get("resin_type")
        self.resin_name: str | None = data.get("resin_name")
        self.profile_name: str | None = data.get("profile_name")
        self.resin_density: float | None = data.get("resin_density")
        self.bottom_layer_numbers: int | None = data.get("bottom_layer_numbers")
        self.transition_layer_numbers: int | None = data.get("transition_layer_numbers")
        self.transition_type: int | None = data.get("transition_type")
        self.bottom_layer_lift_height: float | None = data.get(
            "bottom_layer_lift_height"
        )
        self.bottom_layer_lift_height2: float | None = data.get(
            "bottom_layer_lift_height2"
        )
        self.bottom_layer_drop_height2: float | None = data.get(
            "bottom_layer_drop_height2"
        )
        self.bottom_layer_lift_speed: float | None = data.get("bottom_layer_lift_speed")
        self.bottom_layer_lift_speed2: float | None = data.get(
            "bottom_layer_lift_speed2"
        )
        self.bottom_layer_drop_speed: float | None = data.get("bottom_layer_drop_speed")
        self.bottom_layer_drop_speed2: float | None = data.get(
            "bottom_layer_drop_speed2"
        )
        self.bottom_layer_exposure_time: int | None = data.get(
            "bottom_layer_exposure_time"
        )
        self.bottom_layer_pwm: int | None = data.get("bottom_layer_pwm")
        self.bottom_layer_light_off_time: int | None = data.get(
            "bottom_layer_light_off_time"
        )
        self.bottom_layer_rest_time_after_drop: int | None = data.get(
            "bottom_layer_rest_time_after_drop"
        )
        self.bottom_layer_rest_time_after_lift: int | None = data.get(
            "bottom_layer_rest_time_after_lift"
        )
        self.bottom_layer_rest_time_before_lift: int | None = data.get(
            "bottom_layer_rest_time_before_lift"
        )
        self.normal_layer_lift_height: float | None = data.get(
            "normal_layer_lift_height"
        )
        self.normal_layer_lift_height2: float | None = data.get(
            "normal_layer_lift_height2"
        )
        self.normal_layer_drop_height2: float | None = data.get(
            "normal_layer_drop_height2"
        )
        self.normal_layer_lift_speed: float | None = data.get("normal_layer_lift_speed")
        self.normal_layer_lift_speed2: float | None = data.get(
            "normal_layer_lift_speed2"
        )
        self.normal_layer_drop_speed: float | None = data.get("normal_layer_drop_speed")
        self.normal_layer_drop_speed2: float | None = data.get(
            "normal_layer_drop_speed2"
        )
        self.normal_layer_exposure_time: int | None = data.get(
            "normal_layer_exposure_time"
        )
        self.normal_layer_pwm: int | None = data.get("normal_layer_pwm")
        self.normal_layer_light_off_time: int | None = data.get(
            "normal_layer_light_off_time"
        )
        self.normal_layer_rest_time_after_drop: int | None = data.get(
            "normal_layer_rest_time_after_drop"
        )
        self.normal_layer_rest_time_after_lift: int | None = data.get(
            "normal_layer_rest_time_after_lift"
        )
        self.normal_layer_rest_time_before_lift: int | None = data.get(
            "normal_layer_rest_time_before_lift"
        )

    def __repr__(self) -> str:
        """
        Return a string representation of the object.

        Returns:
            A string representation of the object's attributes.

        """
        return str(self.__dict__)
