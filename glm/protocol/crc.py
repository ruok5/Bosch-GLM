"""Bosch MtProtocol CRC. Init 0xAA[AA[AAAA]]; non-reflected, MSB-first, no final XOR."""

CRC8_INIT = 0xAA
CRC8_POLY = 0xA6
CRC16_INIT = 0xAAAA
CRC16_POLY = 0xBAAD


def crc8(data: bytes, init: int = CRC8_INIT, poly: int = CRC8_POLY) -> int:
    reg = init & 0xFF
    for byte in data:
        for i in range(8):
            top = (reg & 0x80) != 0
            bit = ((byte >> (7 - i)) & 1) == 1
            reg = ((reg << 1) ^ poly) & 0xFF if top != bit else (reg << 1) & 0xFF
    return reg


def crc16(data: bytes, init: int = CRC16_INIT, poly: int = CRC16_POLY) -> int:
    reg = init & 0xFFFF
    for byte in data:
        for i in range(8):
            top = (reg & 0x8000) != 0
            bit = ((byte >> (7 - i)) & 1) == 1
            reg = ((reg << 1) ^ poly) & 0xFFFF if top != bit else (reg << 1) & 0xFFFF
    return reg
