"""Bosch MtProtocol constants. Names match the Java SDK enum values."""
from enum import IntEnum


class FrameFormat(IntEnum):
    LONG = 0   # [mode][cmd][len(1B)][payload][CRC8]   — used by most commands
    SHORT = 1  # [mode][cmd][CRC8]                     — bare query, no payload
    EXT = 2    # [mode][cmd][len_LSB][len_MSB][payload][CRC16_LE] — large payloads


class FrameType(IntEnum):
    RESPONSE = 0
    REQUEST = 3


class CommStatus(IntEnum):
    SUCCESS = 0
    TIMEOUT = 1
    MODE_NOT_SUPPORTED = 2
    CHECKSUM_ERROR = 3
    CMD_UNKNOWN = 4
    ACCESS_DENIED = 5
    PARAM_OR_DATA_ERROR = 6


def frame_mode(req_fmt: FrameFormat, resp_fmt: FrameFormat) -> int:
    """Pack a request mode byte. Type=REQUEST is encoded in the upper nibble (0xC0)."""
    return 0xC0 | ((req_fmt & 3) << 2) | (resp_fmt & 3)


def split_mode(mode: int) -> tuple[FrameFormat, FrameFormat]:
    return FrameFormat((mode >> 2) & 3), FrameFormat(mode & 3)
