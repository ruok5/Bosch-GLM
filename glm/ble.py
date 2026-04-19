"""Bleak transport: discover, connect, notify, write."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

from .protocol.frame import Frame, FrameDecoder, encode

logger = logging.getLogger(__name__)

CHAR_UUID = "02a6c0d1-0451-4000-b000-fb3210111989"
SERVICE_PREFIX = "02a6c0"


async def find_glm():
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    for addr, (d, adv) in found.items():
        logger.debug("found: %s name=%r rssi=%s services=%s",
                     addr, d.name, adv.rssi, adv.service_uuids)
    for _addr, (d, adv) in found.items():
        if any(u.lower().startswith(SERVICE_PREFIX) for u in (adv.service_uuids or [])):
            return d
    return None


async def stream_frames(
    on_connect: Callable[[BleakClient], None] | None = None,
) -> AsyncIterator[Frame]:
    """Discover, connect, enable autosync, yield decoded Frames forever.

    Reconnects automatically with backoff on failure or device disconnect.
    """
    backoff = 1.0
    queue: asyncio.Queue[Frame] = asyncio.Queue()
    decoder = FrameDecoder()

    def handle_notification(_char: BleakGATTCharacteristic, data: bytearray) -> None:
        logger.debug("rx: %s", data.hex())
        for frame in decoder.feed(bytes(data)):
            queue.put_nowait(frame)

    while True:
        try:
            logger.info("scanning for GLM...")
            device = await find_glm()
            if device is None:
                logger.warning("no GLM found — retrying in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 10.0)
                continue
            backoff = 1.0
            logger.info("connecting to %s (%s)...", device.name, device.address)
            async with BleakClient(device) as client:
                if on_connect:
                    on_connect(client)
                await asyncio.sleep(0.5)
                await client.start_notify(CHAR_UUID, handle_notification)
                await asyncio.sleep(0.5)
                # Enable autosync via typed message
                from .protocol.messages import edc_set_autosync
                await client.write_gatt_char(CHAR_UUID, encode(edc_set_autosync(True)), True)
                while client.is_connected:
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=1.0)
                        yield frame
                    except asyncio.TimeoutError:
                        continue
                logger.info("disconnected — reconnecting...")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("session error: %s — reconnecting in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 10.0)
