from typing import Any, Dict, List


class LightStatus:
    """A class to represent the status of printer lights."""

    def __init__(self, second_light: bool, rgb_light: List[int]):
        """
        Initialize the LightStatus object.

        Args:
            second_light (bool): The on/off status of the secondary light.
            rgb_light (List[int]): A list of three integers [R, G, B] for the RGB light.
                                   Each value should be between 0 and 255.
        """
        if not isinstance(second_light, bool):
            raise TypeError("second_light must be a boolean.")

        if not (
            isinstance(rgb_light, list)
            and len(rgb_light) == 3
            and all(isinstance(i, int) for i in rgb_light)
        ):
            raise TypeError("rgb_light must be a list of three integers.")

        self.second_light = second_light
        self.rgb_light = rgb_light

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LightStatus":
        """Creates a LightStatus instance from a dictionary."""
        light_status_data = data.get("LightStatus", {})
        return cls(
            second_light=light_status_data.get("SecondLight", False),
            rgb_light=light_status_data.get("RgbLight", [0, 0, 0]),
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Returns a dictionary representation of the LightStatus object,
        matching the original JSON structure.
        """
        return {
            "LightStatus": {
                "SecondLight": self.second_light,
                "RgbLight": self.rgb_light,
            }
        }

    def __repr__(self) -> str:
        """Provides a developer-friendly string representation."""
        return (
            f"LightStatus(second_light={self.second_light}, rgb_light={self.rgb_light})"
        )

    def __str__(self) -> str:
        """Provides a user-friendly string representation."""
        return f"Secondary Light: {'On' if self.second_light else 'Off'}, RGB: {self.rgb_light}"
