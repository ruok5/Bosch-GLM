import argparse
import asyncio
import logging
import struct
import subprocess
from datetime import datetime
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

charUUID = '02a6c0d1-0451-4000-b000-fb3210111989'
IN_PER_M = 39.3700787

logger = logging.getLogger(__name__)


def notice(msg: str) -> None:
    print(f"\033[1m{msg}\033[0m", flush=True)


def format_imperial(meters: float) -> str:
    half_inches = round(meters * IN_PER_M * 2)
    feet, rem = divmod(half_inches, 24)
    whole_in, frac = divmod(rem, 2)
    return f"{feet}'-{whole_in} 1/2\"" if frac else f"{feet}'-{whole_in}\""


def displayed_inches(meters: float) -> float:
    return round(meters * IN_PER_M * 2) / 2


BIG_FONT = {
    '0': ["███", "█ █", "█ █", "█ █", "███"],
    '1': [" █ ", "██ ", " █ ", " █ ", "███"],
    '2': ["███", "  █", "███", "█  ", "███"],
    '3': ["███", "  █", "███", "  █", "███"],
    '4': ["█ █", "█ █", "███", "  █", "  █"],
    '5': ["███", "█  ", "███", "  █", "███"],
    '6': ["███", "█  ", "███", "█ █", "███"],
    '7': ["███", "  █", "  █", "  █", "  █"],
    '8': ["███", "█ █", "███", "█ █", "███"],
    '9': ["███", "█ █", "███", "  █", "███"],
    "'": [" █ ", " █ ", "   ", "   ", "   "],
    '"': ["█ █", "█ █", "   ", "   ", "   "],
    '-': ["   ", "   ", "███", "   ", "   "],
    '/': ["  █", "  █", " █ ", "█  ", "█  "],
    ' ': ["   ", "   ", "   ", "   ", "   "],
}


def render_big(text: str) -> str:
    rows = ["", "", "", "", ""]
    for ch in text:
        glyph = BIG_FONT.get(ch, BIG_FONT[' '])
        for i in range(5):
            rows[i] += glyph[i] + " "
    return "\n".join(rows)


def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning("pbcopy failed: %s", e)


def make_handler(copy_format: str | None, offset_in: float):
    offset_m = offset_in / IN_PER_M

    def notification_handler(characteristic: BleakGATTCharacteristic, data: bytearray):
        logger.debug("%s: %s", characteristic.uuid, data.hex())
        if not data.startswith(b'\xc0\x55\x10\x06'):
            return
        raw_m = struct.unpack('<f', data[7:11])[0]
        adj_m = raw_m + offset_m
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        imperial = format_imperial(adj_m)

        if offset_in:
            raw_in = raw_m * IN_PER_M
            adj_in = adj_m * IN_PER_M
            sign = '+' if offset_in >= 0 else '-'
            small = (f"[{ts}]  {raw_m:.4f} m  →  "
                     f"{raw_in:.2f}\" {sign} {abs(offset_in):g}\" = {adj_in:.2f}\"")
        else:
            small = f"[{ts}]  {raw_m:.4f} m"

        big_color = "1;93" if offset_in else "1;96"
        print(f"\n\033[2m{small}\033[0m", flush=True)
        print(f"\033[{big_color}m{render_big(imperial)}\033[0m", flush=True)

        if copy_format == "in":
            copy_to_clipboard(f"{displayed_inches(adj_m):g}")
        elif copy_format == "arch":
            copy_to_clipboard(imperial)
        elif copy_format == "m":
            copy_to_clipboard(f"{adj_m:.4f}")
        elif copy_format == "mm":
            copy_to_clipboard(str(round(adj_m * 1000)))

    return notification_handler


async def find_glm():
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    for addr, (d, adv) in found.items():
        logger.debug("found: %s  name=%r  rssi=%s  services=%s",
                     addr, d.name, adv.rssi, adv.service_uuids)
    for _addr, (d, adv) in found.items():
        if any(u.lower().startswith('02a6c0') for u in (adv.service_uuids or [])):
            return d
    return None


async def run_session(device, copy_format: str | None, offset_in: float):
    logger.info("connecting to %s (%s)...", device.name, device.address)
    async with BleakClient(device) as client:
        name = device.name or "GLM"
        notice(f"Connected to {name}. Press the measure button (Ctrl-C to quit).")
        await asyncio.sleep(0.5)
        await client.start_notify(charUUID, make_handler(copy_format, offset_in))
        await asyncio.sleep(0.5)
        # magic byte sequence to make indications carry measurement data
        await client.write_gatt_char(charUUID, bytearray([0xc0, 0x55, 0x02, 0x01, 0x00, 0x1a]), True)
        while client.is_connected:
            await asyncio.sleep(1)
        notice("Disconnected — reconnecting...")


async def main(copy_format: str | None, offset_in: float):
    notice("Looking for your GLM...")
    backoff = 1.0
    while True:
        try:
            logger.info("scanning for GLM...")
            device = await find_glm()
            if device is None:
                logger.warning("no GLM found — retrying in %.0fs (make sure Bluetooth is enabled on the device)", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 10.0)
                continue
            backoff = 1.0
            await run_session(device, copy_format, offset_in)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("session error: %s — reconnecting in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 10.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream measurements from a Bosch GLM rangefinder.")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose output: -v for info/warnings, -vv for full debug (bleak + raw bytes)")
    parser.add_argument("-c", "--clipboard", nargs="?", const="in", default=None,
                        type=str.lower,
                        choices=["in", "arch", "m", "mm"],
                        help="copy each measurement to the clipboard via pbcopy. "
                             "in=decimal inches of displayed value (default), "
                             "arch=feet-inches string, m=meters, mm=millimeters")
    parser.add_argument("--offset", type=float, default=0.0, metavar="INCHES",
                        help="static offset in decimal inches added to every measurement (e.g. 2.5 or -0.75)")
    args = parser.parse_args()

    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose >= 1:
        level = logging.INFO
    else:
        level = logging.CRITICAL

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    if args.verbose < 2:
        logging.getLogger("bleak").setLevel(logging.WARNING)

    try:
        asyncio.run(main(args.clipboard, args.offset))
    except KeyboardInterrupt:
        pass
