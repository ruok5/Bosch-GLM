"""Audio + visual feedback primitives via undocumented GLM commands.

Discovered empirically from byte-sweep probing the GLM165-27C6 firmware:

  0x45 = BeeperOn   (continuous tone until stopped)
  0x46 = BeeperOff
  0x47 = DisplayOn
  0x48 = DisplayOff

These are NOT in the decompiled MeasureOn SDK — they're internal Bosch
commands the GLM accepts but the official app doesn't use. They produce
audible/visual signals with no laser, no measurement, and no entry in
the device's history list — making them ideal for app-level feedback.

All functions take a connected `bleak.BleakClient` and return when the
sequence is complete. They never raise on BLE errors (logged at debug)
since feedback failures shouldn't break a flow.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .ble import CHAR_UUID
from .protocol.constants import FrameFormat
from .protocol.frame import Frame, encode

logger = logging.getLogger(__name__)

CMD_BEEPER_ON = 0x45
CMD_BEEPER_OFF = 0x46
CMD_DISPLAY_ON = 0x47
CMD_DISPLAY_OFF = 0x48

# Empirically: 30ms is the shortest cleanly audible beep. 60–200ms gaps
# between beeps all read clearly as separate beeps to a human ear.
SHORT_MS = 30
SHORT_GAP_MS = 80


def _frame_bytes(cmd: int) -> bytes:
    return encode(Frame.request(cmd=cmd, payload=b"",
                                req_fmt=FrameFormat.LONG,
                                resp_fmt=FrameFormat.LONG))


_ON = _frame_bytes(CMD_BEEPER_ON)
_OFF = _frame_bytes(CMD_BEEPER_OFF)
_DISP_ON = _frame_bytes(CMD_DISPLAY_ON)
_DISP_OFF = _frame_bytes(CMD_DISPLAY_OFF)


async def _safe_write(client: Any, raw: bytes) -> bool:
    try:
        await client.write_gatt_char(CHAR_UUID, raw, True)
        return True
    except Exception as e:
        logger.debug("feedback write failed: %s", e)
        return False


async def beep(client: Any, ms: int = SHORT_MS) -> None:
    """Single beep of the given duration."""
    if not await _safe_write(client, _ON):
        return
    await asyncio.sleep(ms / 1000.0)
    await _safe_write(client, _OFF)


async def double_beep(client: Any, ms: int = SHORT_MS,
                      gap_ms: int = SHORT_GAP_MS) -> None:
    await beep(client, ms)
    await asyncio.sleep(gap_ms / 1000.0)
    await beep(client, ms)


async def triple_beep(client: Any, ms: int = SHORT_MS,
                      gap_ms: int = SHORT_GAP_MS) -> None:
    await beep(client, ms)
    await asyncio.sleep(gap_ms / 1000.0)
    await beep(client, ms)
    await asyncio.sleep(gap_ms / 1000.0)
    await beep(client, ms)


async def play_pattern(client: Any, intervals_ms: list[int]) -> None:
    """Play an arbitrary on/off pattern — alternating durations starting with
    an ON. Must be odd-length and end on an ON."""
    if not intervals_ms or len(intervals_ms) % 2 == 0:
        raise ValueError("pattern must be odd-length, alternating on/off, starting on")
    for i, dur in enumerate(intervals_ms):
        if i % 2 == 0:
            await _safe_write(client, _ON)
            await asyncio.sleep(dur / 1000.0)
            await _safe_write(client, _OFF)
        else:
            await asyncio.sleep(dur / 1000.0)


async def display_off(client: Any) -> None:
    await _safe_write(client, _DISP_OFF)


async def display_on(client: Any) -> None:
    await _safe_write(client, _DISP_ON)


async def display_blink(client: Any, off_ms: int = 200) -> None:
    """Brief screen-off → on flash as a silent visual confirmation."""
    await _safe_write(client, _DISP_OFF)
    await asyncio.sleep(off_ms / 1000.0)
    await _safe_write(client, _DISP_ON)
