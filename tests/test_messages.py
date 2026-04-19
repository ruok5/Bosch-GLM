import struct

from glm.format import format_imperial_quarter
from glm.protocol.frame import encode
from glm.protocol.messages import (
    DeviceSettings, EDCMeasurement, UNIT_FT_IN_FRACT, BACKLIGHT_AUTO,
    ANGLE_UNIT_DEGREE, edc_request_history_item, edc_set_autosync,
    get_settings_request, set_settings_request,
)


def test_autosync_on_matches_known_bytes():
    """The hand-derived `c0 55 02 01 00 1a` is exactly EDCOutputMessage(syncControl=1)."""
    assert encode(edc_set_autosync(True)) == bytes.fromhex("c055020100" + "1a")


def test_decode_edc_measurement_single_distance():
    # Construct a synthetic 16-byte EDC payload representing a 1.234 m reading
    # in MODE_SINGLE_DISTANCE (1) with REF_EDGE_DISTANCE_FRONT (0):
    devmode_ref = (1 << 2) | 0          # devMode=1, refEdge=0
    dev_status = 0x01                   # laser on, no warnings, metric, status=0
    meas_id = struct.pack("<H", 42)
    result = struct.pack("<f", 1.234)
    comp1 = struct.pack("<f", 1.234)
    comp2 = struct.pack("<f", 0.0)
    payload = bytes([devmode_ref, dev_status]) + meas_id + result + comp1 + comp2
    assert len(payload) == 16

    m = EDCMeasurement.from_payload(payload)
    assert m.dev_mode == 1
    assert m.ref_edge == 0
    assert m.laser_on is True
    assert m.batt_warning is False
    assert m.config_units == 0
    assert m.meas_id == 42
    assert abs(m.result - 1.234) < 1e-6
    assert abs(m.comp1 - 1.234) < 1e-6
    assert m.comp2 == 0.0


def test_quarter_inch_rounding():
    # 1.0 m = 39.37 in ≈ 3'-3 3/8" → rounds to 3'-3 1/4" (closest quarter)
    assert format_imperial_quarter(1.0) == "3'-3 1/4\""
    # 0.5 m = 19.685 in ≈ 1'-7 11/16" → rounds to 1'-7 3/4"
    assert format_imperial_quarter(0.5) == "1'-7 3/4\""
    # Whole-inch boundary: 0.0254 m = 1.0 in
    assert format_imperial_quarter(0.0254) == "0'-1\""
    # Half-inch: 0.0127 m = 0.5 in
    assert format_imperial_quarter(0.0127) == "0'-0 1/2\""
    # Zero
    assert format_imperial_quarter(0.0) == "0'-0\""


def test_decode_edc_measurement_with_warnings():
    payload = bytes([0, 0b00000110]) + b"\x00\x00" + b"\x00" * 12  # temp+batt warnings
    m = EDCMeasurement.from_payload(payload)
    assert m.laser_on is False
    assert m.temp_warning is True
    assert m.batt_warning is True


def test_edc_error_response_detected():
    # Real bytes captured from device when laser fails to range a target:
    # devMode=63, refEdge=2 → byte0 = (63<<2)|2 = 0xFE; result = 1.0 (error code 1)
    payload = bytes([0xFE, 0x00, 0x12, 0x03,
                     0x00, 0x00, 0x80, 0x3F,  # float 1.0 LE
                     0, 0, 0, 0, 0, 0, 0, 0])
    m = EDCMeasurement.from_payload(payload)
    assert m.dev_mode == 63
    assert m.ref_edge == 2
    assert m.is_error
    assert not m.is_meaningful
    assert int(m.result) == 1


def test_no_action_heartbeat_not_meaningful():
    payload = bytes([0, 0]) + b"\x00\x00" + b"\x00" * 12
    m = EDCMeasurement.from_payload(payload)
    assert m.dev_mode == 0
    assert not m.is_meaningful
    assert not m.is_error


def test_get_settings_request_bytes():
    """LONG req: [mode=0xC0][cmd=0x53][len=0][CRC8]. Total 4 bytes."""
    raw = encode(get_settings_request())
    assert len(raw) == 4
    assert raw[0] == 0xC0
    assert raw[1] == 0x53
    assert raw[2] == 0x00
    # CRC8 computed over [c0 53 00] should match the trailing byte
    from glm.protocol.crc import crc8
    assert raw[3] == crc8(bytes([0xC0, 0x53, 0x00]))


def test_settings_payload_roundtrip():
    s = DeviceSettings(spirit_level=True, disp_rotation=False, speaker=True,
                       laser_pointer=False, backlight=BACKLIGHT_AUTO,
                       angle_unit=ANGLE_UNIT_DEGREE,
                       measurement_unit=UNIT_FT_IN_FRACT,
                       dev_configuration=0, last_used_list_index=42)
    payload = s.to_payload()
    assert len(payload) == 9
    s2 = DeviceSettings.from_payload(payload)
    assert s2 == s


def test_set_settings_request_includes_all_fields():
    s = DeviceSettings(spirit_level=False, disp_rotation=False, speaker=True,
                       laser_pointer=True, backlight=BACKLIGHT_AUTO,
                       angle_unit=ANGLE_UNIT_DEGREE,
                       measurement_unit=UNIT_FT_IN_FRACT,
                       dev_configuration=0, last_used_list_index=0)
    f = set_settings_request(s)
    raw = encode(f)
    # LONG req: [c0][54][len=9][9 payload bytes][crc8]
    assert raw[0] == 0xC0
    assert raw[1] == 0x54
    assert raw[2] == 9
    assert len(raw) == 13


def test_edc_request_history_item_listIndex_packing():
    # listIndex 5 with indicator 0: remoteCtrl byte = 5
    f = edc_request_history_item(5, 0)
    assert f.payload[1] == 5
    # listIndex 5 with indicator 3 (timestamp): remoteCtrl = 5 | (3<<6) = 5|192 = 197
    f = edc_request_history_item(5, 3)
    assert f.payload[1] == 5 | (3 << 6)
    # listIndex truncated to 6 bits
    f = edc_request_history_item(80, 0)
    assert f.payload[1] == 80 & 0x3F  # = 16
    # devMode 58 in headers byte: syncControl=1, kbypass=0, devMode=58 → 1 | (58<<2) = 233
    assert f.payload[0] == 1 | (58 << 2)
