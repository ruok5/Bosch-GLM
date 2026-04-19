from glm.protocol.crc import crc8, crc16


def test_crc8_known_outgoing_frame():
    """The hand-derived magic outgoing frame `c0 55 02 01 00 1a` is known to
    work against a real GLM. The trailing 0x1a must be CRC8 of the preceding
    bytes for the SHORT frame format hypothesis to hold."""
    assert crc8(bytes.fromhex("c055020100")) == 0x1A


def test_crc8_empty_is_init():
    assert crc8(b"") == 0xAA


def test_crc16_empty_is_init():
    assert crc16(b"") == 0xAAAA
