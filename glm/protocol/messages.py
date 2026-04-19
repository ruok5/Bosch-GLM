"""Typed messages over the MtProtocol wire layer.

Bit fields are packed first-declared = LSB, matching Bosch's BitField semantics.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .frame import Frame

CMD_EDC = 0x55                # External Device Control — measurements + autosync
CMD_GET_SETTINGS = 0x53       # Read device settings
CMD_SET_SETTINGS = 0x54       # Write device settings

DEV_MODE_NO_ACTION = 0
DEV_MODE_ERROR_RESPONSE = 63

# Backlight modes
BACKLIGHT_AUTO = 0
BACKLIGHT_ON = 1
BACKLIGHT_OFF = 2

# Angle unit codes
ANGLE_UNIT_DEGREE = 16
ANGLE_UNIT_PERCENT = 17
ANGLE_UNIT_MM_M = 18
ANGLE_UNIT_IN_FT = 19

# Measurement unit codes
UNIT_M = 2
UNIT_CM = 3
UNIT_MM = 4
UNIT_YD = 5
UNIT_FT = 6
UNIT_FT_IN_FRACT = 7   # 3'-7 1/2"
UNIT_INCH = 8
UNIT_INCH_FRACT = 9    # 43 1/2"

UNIT_NAMES = {
    UNIT_M: "m", UNIT_CM: "cm", UNIT_MM: "mm",
    UNIT_YD: "yd", UNIT_FT: "ft",
    UNIT_FT_IN_FRACT: "ft-in", UNIT_INCH: "in",
    UNIT_INCH_FRACT: "in (frac)",
}
ANGLE_UNIT_NAMES = {
    ANGLE_UNIT_DEGREE: "°", ANGLE_UNIT_PERCENT: "%",
    ANGLE_UNIT_MM_M: "mm/m", ANGLE_UNIT_IN_FT: "in/ft",
}
BACKLIGHT_NAMES = {BACKLIGHT_AUTO: "auto", BACKLIGHT_ON: "on", BACKLIGHT_OFF: "off"}


@dataclass
class EDCMeasurement:
    """Live measurement notification from the device (input/request from GLM)."""
    ref_edge: int
    dev_mode: int
    laser_on: bool
    temp_warning: bool
    batt_warning: bool
    config_units: int     # 0=metric, 1=imperial
    device_status: int    # 4-bit error/state field
    meas_id: int
    result: float         # final/displayed value (meters for distance modes)
    comp1: float          # first component (e.g. raw distance, height for wall area)
    comp2: float          # second component (e.g. width)

    @property
    def is_error(self) -> bool:
        """True if this is an error response (devMode 63). The result field
        then holds the error code as an int; 1 typically = signal too weak."""
        return self.dev_mode == DEV_MODE_ERROR_RESPONSE

    @property
    def is_meaningful(self) -> bool:
        """True if this represents a real distance measurement (not an error,
        not a no-action heartbeat, not an empty catchup response)."""
        return not self.is_error and self.dev_mode != DEV_MODE_NO_ACTION and self.result > 0.0

    @classmethod
    def from_payload(cls, payload: bytes) -> "EDCMeasurement":
        if len(payload) < 16:
            raise ValueError(f"EDC payload too short: {len(payload)} bytes")
        b0, b1 = payload[0], payload[1]
        meas_id = struct.unpack("<H", payload[2:4])[0]
        result, comp1, comp2 = struct.unpack("<fff", payload[4:16])
        return cls(
            ref_edge=b0 & 0x03,
            dev_mode=(b0 >> 2) & 0x3F,
            laser_on=bool(b1 & 0x01),
            temp_warning=bool(b1 & 0x02),
            batt_warning=bool(b1 & 0x04),
            config_units=(b1 >> 3) & 0x01,
            device_status=(b1 >> 4) & 0x0F,
            meas_id=meas_id,
            result=result,
            comp1=comp1,
            comp2=comp2,
        )


def edc_set_autosync(on: bool) -> Frame:
    """Enable/disable continuous measurement notifications."""
    headers = (1 if on else 0)  # syncControl=bit 0; keypadBypass=0; devMode=0
    return Frame.request(cmd=CMD_EDC, payload=bytes([headers, 0]))


@dataclass
class DeviceSettings:
    """Device settings reported by the GLM (cmd 0x53 response).

    Payload layout (11 bytes, all uint8):
      0: spirit_level_enabled
      1: disp_rotation_enabled
      2: speaker_enabled
      3: laser_pointer_enabled
      4: backlight_mode (0=auto, 1=on, 2=off)
      5: angle_unit (16=°, 17=%, 18=mm/m, 19=in/ft)
      6: measurement_unit (2=m, 3=cm, ..., see UNIT_* constants)
      7: dev_configuration
      8: last_used_list_index
      9-10: reserved/unknown
    """
    spirit_level: bool
    disp_rotation: bool
    speaker: bool
    laser_pointer: bool
    backlight: int
    angle_unit: int
    measurement_unit: int
    dev_configuration: int
    last_used_list_index: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "DeviceSettings":
        if len(payload) < 9:
            raise ValueError(f"Settings payload too short: {len(payload)} bytes")
        return cls(
            spirit_level=bool(payload[0]),
            disp_rotation=bool(payload[1]),
            speaker=bool(payload[2]),
            laser_pointer=bool(payload[3]),
            backlight=payload[4],
            angle_unit=payload[5],
            measurement_unit=payload[6],
            dev_configuration=payload[7],
            last_used_list_index=payload[8],
        )

    def to_payload(self) -> bytes:
        return bytes([
            int(self.spirit_level), int(self.disp_rotation),
            int(self.speaker), int(self.laser_pointer),
            self.backlight & 0xFF,
            self.angle_unit & 0xFF,
            self.measurement_unit & 0xFF,
            self.dev_configuration & 0xFF,
            self.last_used_list_index & 0xFF,
        ])


def get_settings_request() -> Frame:
    """Read the device's current settings. Empty LONG-format request."""
    return Frame.request(cmd=CMD_GET_SETTINGS, payload=b"")


def set_settings_request(s: DeviceSettings) -> Frame:
    """Push new settings to the device."""
    return Frame.request(cmd=CMD_SET_SETTINGS, payload=s.to_payload())


def edc_request_history_item(index: int, indicator: int = 0) -> Frame:
    """Request a stored measurement by list index. devMode=58 = GET_LIST_ITEM_BY_INDEX.

    Indicator selects which packet of the response to fetch:
      0 = FINAL (the measurement itself)
      1 = FIRST_NON_FINAL (multi-packet measurements only)
      2 = SECOND_NON_FINAL (multi-packet measurements only)
      3 = TIMESTAMP (yields a separate response with devMode=57)
    """
    dev_mode = 58
    sync_control = 1
    keypad_bypass = 0
    headers = (sync_control & 1) | ((keypad_bypass & 1) << 1) | ((dev_mode & 0x3F) << 2)
    # RemoteCtrlByte: listIndex(6 bits LSB) + indicator(2 bits)
    remote_ctrl = (index & 0x3F) | ((indicator & 0x03) << 6)
    return Frame.request(cmd=CMD_EDC, payload=bytes([headers, remote_ctrl]))
