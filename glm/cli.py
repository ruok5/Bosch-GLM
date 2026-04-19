"""CLI entrypoints. `headless` preserves the original main.py behavior."""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from .ble import CHAR_UUID, stream_frames
from . import feedback
from .format import (
    IN_PER_M, copy_to_clipboard, displayed_inches, format_imperial, render_big,
)
from .gestures import ErrorErrorTracker, SoftDeleteTrigger
from .protocol.constants import FrameType
from .protocol.frame import encode
from .protocol.messages import (
    ANGLE_UNIT_NAMES, BACKLIGHT_AUTO, BACKLIGHT_NAMES, BACKLIGHT_OFF, BACKLIGHT_ON,
    CMD_EDC, DeviceSettings, EDCMeasurement, UNIT_FT, UNIT_FT_IN_FRACT,
    UNIT_INCH, UNIT_INCH_FRACT, UNIT_M, UNIT_MM, UNIT_NAMES, UNIT_YD,
    edc_request_history_item, get_settings_request, set_settings_request,
)
from .sites import Site, load_sites, nearest_site
from .station import StationClosed, StationOpened, StationTracker
from .store import LocationFix, Store


_UNIT_CHOICES = {
    "m": UNIT_M, "mm": UNIT_MM, "yd": UNIT_YD, "ft": UNIT_FT,
    "in": UNIT_INCH, "in-frac": UNIT_INCH_FRACT, "ft-in": UNIT_FT_IN_FRACT,
}
_BACKLIGHT_CHOICES = {"auto": BACKLIGHT_AUTO, "on": BACKLIGHT_ON, "off": BACKLIGHT_OFF}
_ONOFF_CHOICES = {"on": True, "off": False}

CATCHUP_STARTUP_DELAY_S = 1.5   # let autosync settle before sending requests
CATCHUP_RESPONSE_TIMEOUT_S = 1.5
MAX_LIST_INDEX = 63  # device's listIndex field is 6 bits

logger = logging.getLogger(__name__)


def notice(msg: str) -> None:
    print(f"\033[1m{msg}\033[0m", flush=True)


def _print_measurement(m: EDCMeasurement, copy_format: str | None, offset_in: float,
                       big_color: str | None = None) -> None:
    offset_m = offset_in / IN_PER_M
    raw_m = m.result
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

    if big_color is None:
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


def _drain(queue: asyncio.Queue) -> int:
    n = 0
    while not queue.empty():
        queue.get_nowait()
        n += 1
    return n


async def _request_history_packet(client, queue: asyncio.Queue[EDCMeasurement],
                                   list_idx: int, indicator: int) -> EDCMeasurement | None:
    """Send one history request, wait for the response. Returns None on timeout."""
    _drain(queue)
    request_bytes = encode(edc_request_history_item(list_idx, indicator))
    logger.debug("catchup: tx listIndex=%d ind=%d  bytes=%s",
                 list_idx, indicator, request_bytes.hex())
    try:
        await client.write_gatt_char(CHAR_UUID, request_bytes, True)
    except Exception as e:
        logger.warning("catchup: write failed at listIndex %d ind %d: %s", list_idx, indicator, e)
        return None
    try:
        m = await asyncio.wait_for(queue.get(), timeout=CATCHUP_RESPONSE_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.info("catchup: timeout waiting for listIndex %d ind %d", list_idx, indicator)
        return None
    return m


async def _catchup(client, store: Store, address: str,
                   queue: asyncio.Queue[EDCMeasurement],
                   offset_in: float, state: dict | None = None) -> None:
    """History catch-up with value-based dedup. The device's listIndex field is
    6 bits (max 63 stored), and the GLM165 doesn't expose per-entry timestamps
    (the indicator=3 / devMode=57 response comes back empty), so we dedup on
    the bit-exact (dev_mode, ref_edge, result, comp1, comp2) tuple."""
    logger.info("catchup: scheduled, sleeping %.1fs to let autosync settle", CATCHUP_STARTUP_DELAY_S)
    await asyncio.sleep(CATCHUP_STARTUP_DELAY_S)
    notice("Catchup: probing device history...")
    logger.info("catchup: starting probe of listIndex 1..%d, address=%s",
                MAX_LIST_INDEX, address)
    recovered = 0
    scanned = 0
    for list_idx in range(1, MAX_LIST_INDEX + 1):
        m = await _request_history_packet(client, queue, list_idx, indicator=0)
        if m is None:
            logger.info("catchup: stopping at listIndex %d (no response)", list_idx)
            break
        logger.debug("catchup: rx listIndex=%d  measID=%d devMode=%d refEdge=%d result=%.4f comp1=%.4f comp2=%.4f",
                     list_idx, m.meas_id, m.dev_mode, m.ref_edge, m.result, m.comp1, m.comp2)
        if not m.is_meaningful:
            logger.info("catchup: empty/error at listIndex %d (devMode=%d, result=%.4f), stopping",
                        list_idx, m.dev_mode, m.result)
            break
        scanned += 1
        loc = state.get("location") if state else None
        site = state.get("site_name") if state else None
        if store.insert_history(address, m, offset_in=offset_in,
                                  location=loc, site_name=site):
            recovered += 1
            logger.info("catchup: recovered new measurement at listIndex %d (result=%.4f m)",
                        list_idx, m.result)
            # Replay visually in magenta so recovered readings are distinct
            # from live (cyan) and offset-live (yellow). No clipboard copy.
            _print_measurement(m, copy_format=None, offset_in=offset_in, big_color="1;95")
        else:
            logger.debug("catchup: dup (value already in store) at listIndex %d", list_idx)

    notice(f"Catchup: scanned {scanned} entries, recovered {recovered} new measurement{'s' if recovered != 1 else ''}.")


async def _resolve_location(use_location: bool,
                             sites: list) -> tuple[LocationFix | None, str | None]:
    """Look up the device's geolocation (if requested) and the nearest site."""
    if not use_location:
        return None, None
    from .location import get_fix
    fix = await get_fix(timeout_s=4.0)
    if fix is None:
        logger.info("location: unavailable (denied, off, or timed out)")
        return None, None
    site_name = None
    if sites:
        match = nearest_site((fix.latitude, fix.longitude), sites)
        if match:
            site_name = match[0].name
            logger.info("location: matched site %r at %.0fm", site_name, match[1])
    logger.info("location: %.6f,%.6f (±%.0fm)", fix.latitude, fix.longitude,
                fix.accuracy_m or -1)
    return fix, site_name


async def _run_headless(copy_format: str | None, offset_in: float,
                        store: Store | None, catchup: bool,
                        use_location: bool, sites_path: Path | None,
                        station_idle_s: float = 60.0,
                        gestures: bool = True) -> None:
    notice("Looking for your GLM...")
    state: dict = {"address": None, "connected": False,
                   "catchup_queue": None, "catchup_task": None,
                   "location": None, "site_name": None,
                   "client": None}

    sites = load_sites(sites_path) if use_location else []
    if sites:
        notice(f"Loaded {len(sites)} site(s) from {sites_path or 'default sites file'}.")

    station = StationTracker(idle_window_ms=int(station_idle_s * 1000))
    err_tracker = ErrorErrorTracker(window_ms=3000) if gestures else None
    state["close_task"] = None

    def _on_station_closed(ev) -> None:
        notice(f"  → station {ev.station_id} closed ({len(ev.member_meas_ids)} members) "
               f"— review with: python station.py show {ev.station_id}")
        if state["client"] is not None:
            asyncio.create_task(feedback.beep(state["client"]))

    def _schedule_idle_close() -> None:
        old = state.get("close_task")
        if old and not old.done():
            old.cancel()

        async def _check() -> None:
            try:
                await asyncio.sleep(station_idle_s + 0.1)
                if station.is_open:
                    ev = station.force_close()
                    if ev is not None and len(ev.member_meas_ids) > 1:
                        _on_station_closed(ev)
            except asyncio.CancelledError:
                pass

        state["close_task"] = asyncio.create_task(_check())

    # Background location lookup on connect
    async def _refresh_location() -> None:
        loc, site = await _resolve_location(use_location, sites)
        state["location"] = loc
        state["site_name"] = site
        if site:
            notice(f"Location matched site: {site}")
        elif loc:
            notice(f"Location: {loc.latitude:.5f},{loc.longitude:.5f} (no site match)")

    def on_connect(client) -> None:
        state["address"] = client.address
        state["connected"] = False
        state["client"] = client
        if use_location:
            asyncio.create_task(_refresh_location())
        if catchup and store is not None:
            old_task = state["catchup_task"]
            if old_task and not old_task.done():
                old_task.cancel()
            state["catchup_queue"] = asyncio.Queue()
            state["catchup_task"] = asyncio.create_task(
                _catchup(client, store, client.address, state["catchup_queue"],
                         offset_in, state))

    async for frame in stream_frames(on_connect=on_connect):
        if not state["connected"]:
            notice("Connected. Press the measure button (Ctrl-C to quit).")
            state["connected"] = True
        # Live measurements arrive as REQUEST frames with cmd=0x55.
        # History-fetch results arrive as RESPONSE frames (cmd implicit, status=0).
        is_live_edc = (frame.type == FrameType.REQUEST and frame.cmd == CMD_EDC and len(frame.payload) >= 16)
        is_history_response = (frame.type == FrameType.RESPONSE and len(frame.payload) >= 16)
        if not (is_live_edc or is_history_response):
            logger.debug("non-EDC frame: type=%s cmd=%#x len=%d", frame.type, frame.cmd, len(frame.payload))
            continue
        m = EDCMeasurement.from_payload(frame.payload)
        logger.debug("rx EDC %s: measID=%d devMode=%d refEdge=%d result=%.4f",
                     "live" if is_live_edc else "history", m.meas_id, m.dev_mode, m.ref_edge, m.result)
        # Tee to catchup queue — it needs both kinds of frames (history for the
        # actual responses, and result=0 ones to know when to stop).
        if state["catchup_queue"] is not None:
            state["catchup_queue"].put_nowait(m)
        if is_history_response:
            # Catchup task owns these — don't store or print here.
            continue
        # Live measurement path:
        now_ms = int(datetime.now().timestamp() * 1000)
        if m.is_error:
            ts = datetime.now().strftime('%H:%M:%S')
            err = int(m.result)
            print(f"\033[1;91m[{ts}]  measurement error (code {err})\033[0m", flush=True)
            # Error-error gesture handling
            if err_tracker is not None:
                trigger = err_tracker.on_error(now_ms)
                if trigger is not None and store is not None and trigger.device_address:
                    if store.soft_delete(trigger.device_address, trigger.meas_id):
                        print(f"\033[1;95m  → soft-deleted measurement #{trigger.meas_id} "
                              f"(error-error gesture)\033[0m", flush=True)
                        if state["client"] is not None:
                            asyncio.create_task(feedback.double_beep(state["client"]))
            continue
        if not m.is_meaningful:
            continue  # laser-on / no-action heartbeat

        # Station tracking — group consecutive shots into one observation
        events = station.feed(m.meas_id, now_ms)
        for ev in events:
            if isinstance(ev, StationClosed) and len(ev.member_meas_ids) > 1:
                _on_station_closed(ev)
        # Arm/reset the idle-close timer so the station closes even if no
        # further measurements arrive.
        _schedule_idle_close()
        # Tag this insert with the open station id (or None if just one shot)
        sid = station._open_id  # accessing internal: the just-added member belongs here
        if store is not None and state["address"]:
            store.insert(state["address"], m, offset_in=offset_in,
                         location=state["location"], site_name=state["site_name"],
                         station_id=sid)
        if err_tracker is not None and state["address"]:
            err_tracker.on_good(m.meas_id, state["address"], now_ms)
        _print_measurement(m, copy_format, offset_in)


async def _read_settings(timeout_s: float = 5.0) -> DeviceSettings:
    """Connect, send a get-settings request, await the response, disconnect."""
    from bleak import BleakClient
    from .ble import find_glm
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")
    settings_future: asyncio.Future = asyncio.get_event_loop().create_future()

    from .protocol.frame import FrameDecoder
    decoder = FrameDecoder()

    def on_notify(_char, data: bytearray) -> None:
        for frame in decoder.feed(bytes(data)):
            if frame.type == FrameType.RESPONSE and 9 <= len(frame.payload) <= 16:
                if not settings_future.done():
                    try:
                        settings_future.set_result(DeviceSettings.from_payload(frame.payload))
                    except Exception as e:
                        settings_future.set_exception(e)

    async with BleakClient(device) as client:
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.3)
        await client.write_gatt_char(CHAR_UUID, encode(get_settings_request()), True)
        return await asyncio.wait_for(settings_future, timeout=timeout_s)


async def _write_settings(new: DeviceSettings, timeout_s: float = 5.0) -> DeviceSettings:
    """Connect, write settings, read back, return the updated state."""
    from bleak import BleakClient
    from .ble import find_glm
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")

    from .protocol.frame import FrameDecoder
    decoder = FrameDecoder()
    settings_future: asyncio.Future = asyncio.get_event_loop().create_future()

    def on_notify(_char, data: bytearray) -> None:
        for frame in decoder.feed(bytes(data)):
            if frame.type == FrameType.RESPONSE and 9 <= len(frame.payload) <= 16:
                if not settings_future.done():
                    try:
                        settings_future.set_result(DeviceSettings.from_payload(frame.payload))
                    except Exception as e:
                        settings_future.set_exception(e)

    async with BleakClient(device) as client:
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.3)
        await client.write_gatt_char(CHAR_UUID, encode(set_settings_request(new)), True)
        await asyncio.sleep(0.3)
        # Re-read to confirm
        await client.write_gatt_char(CHAR_UUID, encode(get_settings_request()), True)
        return await asyncio.wait_for(settings_future, timeout=timeout_s)


async def _test_beep(toggles: int = 2, delay_s: float = 0.25,
                     settle_s: float = 5.0,
                     timeout_s: float = 15.0) -> None:
    """Connect once, toggle the speaker setting `toggles` times with `delay_s`
    between writes, then restore the original state. Logs each step.

    The point of holding one connection open is so the BLE/protocol overhead
    doesn't swamp the toggle interval — we want the writes themselves to be
    the only delays. If the device emits an audible beep on speaker-on
    transitions (or any settings write), this will surface it."""
    from bleak import BleakClient
    from .ble import find_glm
    from .protocol.frame import FrameDecoder

    notice(f"Looking for your GLM... (will toggle speaker {toggles}x with {int(delay_s*1000)}ms delay)")
    # find_glm has its own 8s scan; don't wrap it in a tighter timeout
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan — make sure it's on and Bluetooth is enabled")

    decoder = FrameDecoder()
    settings_responses: list[DeviceSettings] = []

    def on_notify(_char, data: bytearray) -> None:
        logger.debug("rx: %s", data.hex())
        for frame in decoder.feed(bytes(data)):
            logger.debug("frame: type=%s len=%d", frame.type, len(frame.payload))
            if frame.type == FrameType.RESPONSE and 9 <= len(frame.payload) <= 16:
                try:
                    s = DeviceSettings.from_payload(frame.payload)
                    settings_responses.append(s)
                    logger.info("settings response: speaker=%s", s.speaker)
                except Exception as e:
                    logger.debug("settings parse failed: %s", e)

    async with BleakClient(device) as client:
        logger.info("beep-test: connected to %s", device.address)
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.3)

        # Read the initial state
        await client.write_gatt_char(CHAR_UUID, encode(get_settings_request()), True)
        await asyncio.sleep(0.4)
        if not settings_responses:
            raise RuntimeError("no initial settings response received")
        original = settings_responses[-1]
        logger.info("beep-test: original speaker=%s", original.speaker)
        notice(f"Original beep state: {'on' if original.speaker else 'off'}")

        # Settle: let any device-side state catch up so the first write isn't
        # racing with the initial connection / settings dance.
        if settle_s > 0:
            notice(f"Settling for {settle_s:.1f}s before first toggle...")
            logger.info("beep-test: settling %.1fs", settle_s)
            await asyncio.sleep(settle_s)

        # Toggle N times
        current_state = original.speaker
        for i in range(toggles):
            target = not current_state
            new = DeviceSettings(
                spirit_level=original.spirit_level,
                disp_rotation=original.disp_rotation,
                speaker=target,
                laser_pointer=original.laser_pointer,
                backlight=original.backlight,
                angle_unit=original.angle_unit,
                measurement_unit=original.measurement_unit,
                dev_configuration=original.dev_configuration,
                last_used_list_index=original.last_used_list_index,
            )
            ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            logger.info("beep-test: toggle %d/%d → speaker=%s at %s",
                        i + 1, toggles, target, ts)
            print(f"\033[1m[{ts}] toggle {i+1}/{toggles}: speaker → {target}\033[0m",
                  flush=True)
            await client.write_gatt_char(CHAR_UUID, encode(set_settings_request(new)), True)
            current_state = target
            await asyncio.sleep(delay_s)

        # Restore original if we ended somewhere else
        if current_state != original.speaker:
            logger.info("beep-test: restoring original speaker=%s", original.speaker)
            restore = DeviceSettings(
                spirit_level=original.spirit_level,
                disp_rotation=original.disp_rotation,
                speaker=original.speaker,
                laser_pointer=original.laser_pointer,
                backlight=original.backlight,
                angle_unit=original.angle_unit,
                measurement_unit=original.measurement_unit,
                dev_configuration=original.dev_configuration,
                last_used_list_index=original.last_used_list_index,
            )
            await client.write_gatt_char(CHAR_UUID, encode(set_settings_request(restore)), True)
            await asyncio.sleep(0.3)

        notice("Beep test done. Did you hear anything?")


# Catalog of "let's see if it beeps" probes derived from the decompiled SDK.
# Format: (cmd_byte, payload_bytes, label, notes).
#
# Findings so far:
#   - KeypadPattern (0x1B) → ACCESS_DENIED
#   - TraceData (0x2E) → CRASHES the BT connection
#   - EDCDoRemote(button=0) → SUCCESS + beep + laser (bundled)
#   - EDCDoRemote(button=1..3) → PARAM_ERROR
#   - LaserOn/Off, DoEcho → SUCCESS, silent
#   - SetLasers/RemoteControlKey/AccessLock (linelaser/rotation cmds) → CMD_UNKNOWN
#   - cmds 0x11/0x12/0x13/0x14 → all ACCESS_DENIED (exist but locked)
#   - GCLDevInfo (0x46) with empty payload → SUCCESS with empty body
#
# Round 6 focus: verify the on/off pair hypothesis. 0x41/0x42 = LaserOn/Off,
# 0x45 was confirmed beeper-CONTINUOUS-on, 0x48 was confirmed display-off.
# By symmetry, 0x46 should be BeeperOff and 0x47 DisplayOn. Each test below
# fires the suspected ON, sleeps briefly, then fires the suspected OFF — if
# the symmetry holds, the result is a clean single beep / a display blink.
#
# Also retesting 0x39, 0x20, 0x2d to find which one switched units to meters.
_PROBE_COMMANDS = [
    (0x45, b"",                     "BEEPER ON (0x45)",
     "should start a continuous tone"),
    (0x46, b"",                     "BEEPER OFF? (0x46)",
     "if symmetric to 0x42 LaserOff, this stops the tone — listen for silence"),
    (0x47, b"",                     "DISPLAY ON? (0x47)",
     "control case before testing display-off"),
    (0x48, b"",                     "DISPLAY OFF (0x48)",
     "screen goes blank"),
    (0x47, b"",                     "DISPLAY ON? (0x47) — should wake screen",
     "if 0x47 is the wake cmd, screen comes back without button press"),
    (0x39, b"",                     "Test 0x39",
     "watch the GLM display — does the unit toggle to meters?"),
    (0x20, b"",                     "Test 0x20",
     "watch the GLM display"),
    (0x2d, b"",                     "Test 0x2d",
     "watch the GLM display"),
]


async def _play_patterns(patterns: list[list[int]],
                         settle_s: float = 5.0,
                         gap_between_patterns_s: float = 2.0,
                         timeout_s: float = 15.0) -> None:
    """Play a sequence of beep patterns. Each pattern is a list of millisecond
    intervals: on, off, on, off, …, on. So `[30, 80, 30]` is "30ms beep,
    80ms silence, 30ms beep" → audible double-beep.

    Plays each pattern in order with `gap_between_patterns_s` between them so
    the listener can attribute audible patterns to their declared structure."""
    from bleak import BleakClient
    from .ble import find_glm
    from .protocol.frame import Frame, FrameDecoder
    from .protocol.constants import FrameFormat

    notice("Looking for your GLM...")
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")

    decoder = FrameDecoder()

    def on_notify(_char, data: bytearray) -> None:
        for _ in decoder.feed(bytes(data)):
            pass

    on_bytes = encode(Frame.request(cmd=0x45, payload=b"",
                                     req_fmt=FrameFormat.LONG, resp_fmt=FrameFormat.LONG))
    off_bytes = encode(Frame.request(cmd=0x46, payload=b"",
                                      req_fmt=FrameFormat.LONG, resp_fmt=FrameFormat.LONG))

    async with BleakClient(device) as client:
        notice(f"Connected to {device.address}.")
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.4)
        if settle_s > 0:
            notice(f"Settling {settle_s:.1f}s...")
            await asyncio.sleep(settle_s)

        for i, pattern in enumerate(patterns, start=1):
            if len(pattern) % 2 == 0 or not pattern:
                print(f"\033[1;91m[skip pattern {i}: must have odd length and ≥1 element]\033[0m")
                continue
            on_count = (len(pattern) + 1) // 2
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            label = "single" if on_count == 1 else "double" if on_count == 2 else \
                    "triple" if on_count == 3 else f"{on_count}-beep"
            print(f"\n\033[1;96m[{ts}] pattern {i}/{len(patterns)}: {label} — "
                  f"{','.join(str(x) for x in pattern)}ms\033[0m", flush=True)
            logger.info("pattern %d (%s): %s", i, label, pattern)

            for j, interval_ms in enumerate(pattern):
                if j % 2 == 0:
                    # ON phase
                    await client.write_gatt_char(CHAR_UUID, on_bytes, True)
                    await asyncio.sleep(interval_ms / 1000.0)
                    await client.write_gatt_char(CHAR_UUID, off_bytes, True)
                else:
                    # OFF (silence) phase
                    await asyncio.sleep(interval_ms / 1000.0)
            await asyncio.sleep(gap_between_patterns_s)

        # Belt and suspenders
        await client.write_gatt_char(CHAR_UUID, off_bytes, True)
        notice("Done.")


async def _single_beep(durations_ms: list[int],
                       settle_s: float = 5.0,
                       gap_between_s: float = 1.5,
                       timeout_s: float = 15.0) -> None:
    """Fire BeeperOn (0x45) → wait `duration_ms` → BeeperOff (0x46) for each
    duration in the list. Lets us empirically find the shortest beep that's
    still cleanly audible.

    Discovered via probe sweeps: 0x45 starts a continuous tone, 0x46 stops it.
    No laser, no measurement, no entry in the device's history list."""
    from bleak import BleakClient
    from .ble import find_glm
    from .protocol.frame import Frame, FrameDecoder
    from .protocol.constants import FrameFormat

    notice("Looking for your GLM...")
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")

    decoder = FrameDecoder()

    def on_notify(_char, data: bytearray) -> None:
        logger.debug("rx: %s", data.hex())
        for _ in decoder.feed(bytes(data)):
            pass

    on_bytes = encode(Frame.request(cmd=0x45, payload=b"",
                                     req_fmt=FrameFormat.LONG,
                                     resp_fmt=FrameFormat.LONG))
    off_bytes = encode(Frame.request(cmd=0x46, payload=b"",
                                      req_fmt=FrameFormat.LONG,
                                      resp_fmt=FrameFormat.LONG))

    async with BleakClient(device) as client:
        notice(f"Connected to {device.address}.")
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.4)
        if settle_s > 0:
            notice(f"Settling {settle_s:.1f}s...")
            await asyncio.sleep(settle_s)

        for i, dur_ms in enumerate(durations_ms, start=1):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n\033[1;96m[{ts}] beep {i}/{len(durations_ms)}: "
                  f"{dur_ms}ms tone\033[0m", flush=True)
            logger.info("single-beep: tx 0x45 (on), waiting %dms", dur_ms)
            await client.write_gatt_char(CHAR_UUID, on_bytes, True)
            await asyncio.sleep(dur_ms / 1000.0)
            logger.info("single-beep: tx 0x46 (off)")
            await client.write_gatt_char(CHAR_UUID, off_bytes, True)
            await asyncio.sleep(gap_between_s)

        # Belt-and-suspenders: ensure the beeper is off when we're done.
        logger.info("single-beep: final 0x46 to ensure beeper off")
        await client.write_gatt_char(CHAR_UUID, off_bytes, True)
        notice("Done. Tell me which durations were audible / pleasant.")


async def _test_stealth_beep(settle_s: float = 5.0, repeats: int = 3,
                              gap_s: float = 0.5,
                              timeout_s: float = 15.0) -> None:
    """Fire EDCDoRemote(measure) → LaserOff back-to-back with no delay.

    Hypothesis: the measure trigger emits the beep, and an immediate LaserOff
    might cancel or shorten the laser pulse — yielding "beep without obvious
    laser" usable as gesture feedback.

    Repeats the pair `repeats` times with `gap_s` between pairs so we can hear
    the cadence."""
    from bleak import BleakClient
    from .ble import find_glm
    from .protocol.frame import Frame, FrameDecoder
    from .protocol.constants import FrameFormat

    notice("Looking for your GLM...")
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")

    decoder = FrameDecoder()

    def on_notify(_char, data: bytearray) -> None:
        logger.info("rx: %s", data.hex())
        for frame in decoder.feed(bytes(data)):
            logger.debug("  frame: type=%s cmd=%#x payload=%s",
                         frame.type, frame.cmd, frame.payload.hex())

    trigger_bytes = encode(Frame.request(
        cmd=0x56, payload=bytes([0]),
        req_fmt=FrameFormat.LONG, resp_fmt=FrameFormat.LONG))
    laser_off_bytes = encode(Frame.request(
        cmd=0x42, payload=b"",
        req_fmt=FrameFormat.LONG, resp_fmt=FrameFormat.LONG))

    async with BleakClient(device) as client:
        notice(f"Connected to {device.address}.")
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.4)
        if settle_s > 0:
            notice(f"Settling for {settle_s:.1f}s...")
            await asyncio.sleep(settle_s)

        for i in range(1, repeats + 1):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n\033[1;96m[{ts}] pair {i}/{repeats}: "
                  f"trigger → laser-off (no delay)\033[0m", flush=True)
            logger.info("stealth-beep pair %d: tx trigger %s", i, trigger_bytes.hex())
            await client.write_gatt_char(CHAR_UUID, trigger_bytes, True)
            logger.info("stealth-beep pair %d: tx laser-off %s",
                        i, laser_off_bytes.hex())
            await client.write_gatt_char(CHAR_UUID, laser_off_bytes, True)
            await asyncio.sleep(gap_s)

        notice("Done. Did you hear beeps without the usual laser flash?")


async def _sweep_commands(start: int, end: int,
                           skip: set[int] | None = None,
                           per_probe_delay_s: float = 0.4,
                           settle_s: float = 5.0,
                           timeout_s: float = 15.0) -> None:
    """Brute-force every cmd byte in [start, end] with an empty LONG payload,
    printing the response status code for each. Lets us map the firmware's
    handler space. Skips any byte in `skip` (e.g. known-dangerous like 0x2E).

    Status codes seen:
      00=SUCCESS, 01=TIMEOUT, 02=MODE_NOT_SUPPORTED, 03=CHECKSUM_ERROR,
      04=CMD_UNKNOWN, 05=ACCESS_DENIED, 06=PARAM_OR_DATA_ERROR
    """
    from bleak import BleakClient
    from .ble import find_glm
    from .protocol.frame import Frame, FrameDecoder
    from .protocol.constants import FrameFormat

    skip = skip or set()
    notice("Looking for your GLM...")
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")

    decoder = FrameDecoder()
    last_status: dict[int, int | None] = {}
    last_payload: dict[int, bytes] = {}
    pending_cmd: list[int] = []

    def on_notify(_char, data: bytearray) -> None:
        logger.debug("rx: %s", data.hex())
        for frame in decoder.feed(bytes(data)):
            if frame.type == FrameType.RESPONSE and pending_cmd:
                cmd = pending_cmd[0]
                last_status[cmd] = frame.status
                last_payload[cmd] = frame.payload
                logger.info("sweep: 0x%02x → status=0x%02x payload=%s",
                            cmd, frame.status, frame.payload.hex() or "(empty)")

    async with BleakClient(device) as client:
        notice(f"Connected to {device.address}.")
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.4)
        if settle_s > 0:
            notice(f"Settling for {settle_s:.1f}s...")
            await asyncio.sleep(settle_s)

        notice(f"Sweeping cmd bytes 0x{start:02x}..0x{end:02x} "
               f"({end - start + 1} probes, ~{(end - start + 1) * per_probe_delay_s:.0f}s)...")
        for cmd in range(start, end + 1):
            if cmd in skip:
                logger.info("sweep: skipping 0x%02x (skip list)", cmd)
                continue
            if not client.is_connected:
                print(f"\n\033[1;91m[disconnected at cmd 0x{cmd:02x}]\033[0m")
                break
            pending_cmd.clear()
            pending_cmd.append(cmd)
            raw = encode(Frame.request(cmd=cmd, payload=b"",
                                        req_fmt=FrameFormat.LONG,
                                        resp_fmt=FrameFormat.LONG))
            logger.debug("sweep: tx 0x%02x  %s", cmd, raw.hex())
            try:
                await client.write_gatt_char(CHAR_UUID, raw, True)
            except Exception as e:
                logger.warning("sweep: write failed at 0x%02x: %s", cmd, e)
                continue
            await asyncio.sleep(per_probe_delay_s)

        # Summarize by status code
        by_status: dict[int | None, list[int]] = {}
        for cmd, status in last_status.items():
            by_status.setdefault(status, []).append(cmd)
        status_names = {0: "SUCCESS", 1: "TIMEOUT", 2: "MODE_INVALID", 3: "CHECKSUM",
                         4: "CMD_UNKNOWN", 5: "ACCESS_DENIED", 6: "PARAM_ERROR",
                         None: "(no response)"}
        print("\n\033[1mSweep summary:\033[0m")
        for status in sorted(by_status.keys(), key=lambda x: (x is None, x or 0)):
            cmds = sorted(by_status[status])
            name = status_names.get(status, f"unknown({status})")
            cmd_strs = ", ".join(f"0x{c:02x}" for c in cmds)
            tag = "\033[1;92m" if status == 0 else "\033[1;93m" if status == 5 else "\033[2m"
            print(f"  {tag}{name:<14}\033[0m  {len(cmds):>3} cmds: {cmd_strs}")
        # Flag any that returned a non-empty payload (interesting!)
        with_payload = [(c, p) for c, p in last_payload.items() if p]
        if with_payload:
            print("\n\033[1;92mCommands that returned payload data:\033[0m")
            for c, p in sorted(with_payload):
                print(f"  0x{c:02x}: {p.hex()}")
        if not client.is_connected:
            print("\n\033[1;91mNote: device disconnected during sweep.\033[0m")


async def _probe_commands(per_probe_delay_s: float = 3.0,
                           settle_s: float = 5.0,
                           timeout_s: float = 15.0) -> None:
    """Send each probe in sequence with a delay; user listens for beeps and
    we log every byte we send and every byte we receive so we can correlate
    afterward."""
    from bleak import BleakClient
    from .ble import find_glm
    from .protocol.frame import Frame, FrameDecoder
    from .protocol.constants import FrameFormat

    notice("Looking for your GLM...")
    device = await find_glm()
    if device is None:
        raise RuntimeError("no GLM found in BLE scan")

    decoder = FrameDecoder()
    rx_log: list[tuple[float, bytes]] = []

    def on_notify(_char, data: bytearray) -> None:
        ts = asyncio.get_event_loop().time()
        rx_log.append((ts, bytes(data)))
        logger.info("rx: %s", data.hex())
        for frame in decoder.feed(bytes(data)):
            logger.info("  frame: type=%s cmd=%#x payload=%s",
                        frame.type, frame.cmd, frame.payload.hex())

    async with BleakClient(device) as client:
        logger.info("probe: connected to %s", device.address)
        notice(f"Connected to {device.address}.")
        await client.start_notify(CHAR_UUID, on_notify)
        await asyncio.sleep(0.4)

        if settle_s > 0:
            notice(f"Settling for {settle_s:.1f}s before first probe...")
            await asyncio.sleep(settle_s)

        for i, (cmd, payload, label, notes) in enumerate(_PROBE_COMMANDS, start=1):
            if not client.is_connected:
                print(f"\n\033[1;91m[disconnected before probe {i}; "
                      f"likely killed by previous probe]\033[0m", flush=True)
                logger.warning("probe %d aborted: client disconnected", i)
                break
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            frame = Frame.request(cmd=cmd, payload=payload,
                                  req_fmt=FrameFormat.LONG,
                                  resp_fmt=FrameFormat.LONG)
            raw = encode(frame)
            print(f"\n\033[1;96m[{ts}] probe {i}/{len(_PROBE_COMMANDS)}: "
                  f"cmd=0x{cmd:02x} ({label})\033[0m  \033[2m({notes})\033[0m",
                  flush=True)
            print(f"\033[2m  tx bytes: {raw.hex()}\033[0m", flush=True)
            logger.info("probe %d: %s — tx %s", i, label, raw.hex())
            try:
                await client.write_gatt_char(CHAR_UUID, raw, True)
            except Exception as e:
                logger.warning("probe %d write failed: %s", i, e)
                print(f"  \033[1;91merror sending: {e}\033[0m")
                if not client.is_connected:
                    print(f"  \033[1;91m→ device disconnected after this probe\033[0m")
                    break
            await asyncio.sleep(per_probe_delay_s)

        notice(f"\nProbe sweep done. Got {len(rx_log)} notification(s) total.")
        notice("Tell me which probe (if any) made the GLM beep.")


def _print_settings(s: DeviceSettings) -> None:
    print(f"Units:        {UNIT_NAMES.get(s.measurement_unit, s.measurement_unit)}")
    print(f"Angle:        {ANGLE_UNIT_NAMES.get(s.angle_unit, s.angle_unit)}")
    print(f"Laser:        {'on' if s.laser_pointer else 'off'}")
    print(f"Beep:         {'on' if s.speaker else 'off'}")
    print(f"Backlight:    {BACKLIGHT_NAMES.get(s.backlight, s.backlight)}")
    print(f"Spirit level: {'on' if s.spirit_level else 'off'}")
    print(f"Rotate disp:  {'on' if s.disp_rotation else 'off'}")
    print(f"Stored items: {s.last_used_list_index}")


def settings_main() -> None:
    """Get or set device settings."""
    parser = argparse.ArgumentParser(
        description="Read or write Bosch GLM device settings.",
        epilog="Run with no flags to read current settings."
    )
    _add_version(parser)
    parser.add_argument("--units", choices=sorted(_UNIT_CHOICES))
    parser.add_argument("--beep", choices=sorted(_ONOFF_CHOICES))
    parser.add_argument("--laser", choices=sorted(_ONOFF_CHOICES))
    parser.add_argument("--backlight", choices=sorted(_BACKLIGHT_CHOICES))
    parser.add_argument("--spirit-level", choices=sorted(_ONOFF_CHOICES))
    parser.add_argument("--rotate", choices=sorted(_ONOFF_CHOICES),
                        help="display rotation")
    parser.add_argument("--test-beep", type=int, nargs="?", const=2, metavar="N",
                        help="rapidly toggle the speaker setting N times (default 2) "
                             "to probe whether the device beeps on settings writes")
    parser.add_argument("--toggle-delay-ms", type=int, default=250,
                        help="delay between toggles for --test-beep (default 250)")
    parser.add_argument("--settle-s", type=float, default=5.0,
                        help="settle time after connect before first toggle (default 5.0)")
    parser.add_argument("--probe-cmds", action="store_true",
                        help="fire a sweep of unknown command bytes with delays "
                             "and log everything; listen for beeps and report")
    parser.add_argument("--probe-delay-s", type=float, default=3.0,
                        help="seconds between probes (default 3.0)")
    parser.add_argument("--sweep", metavar="START-END",
                        help="brute-force sweep cmd bytes in hex range, e.g. "
                             "--sweep 0x15-0x6F. Empty payload, fast cadence, "
                             "summarized by status code at end.")
    parser.add_argument("--sweep-delay-s", type=float, default=0.4,
                        help="delay between sweep probes (default 0.4s)")
    parser.add_argument("--single-beep", metavar="MS_LIST",
                        help="fire 0x45→wait→0x46 for each comma-separated duration "
                             "in ms (e.g. 30,50,80,120,200). No laser, no measurement.")
    parser.add_argument("--pattern", metavar="PATTERNS",
                        help="play one or more beep patterns. Each pattern is "
                             "alternating on/off durations in ms (odd-length list, "
                             "ends on an ON). Multiple patterns separated by ';'. "
                             "Example: '30 ; 30,80,30 ; 30,80,30,80,30' = single, "
                             "double, triple")
    parser.add_argument("--pattern-gap-s", type=float, default=2.0,
                        help="gap between patterns (default 2s)")
    parser.add_argument("--stealth-beep", type=int, nargs="?", const=3, metavar="N",
                        help="fire EDCDoRemote(measure) immediately followed by "
                             "LaserOff, N times, with --stealth-gap between pairs "
                             "(default 3 pairs)")
    parser.add_argument("--stealth-gap-s", type=float, default=0.5,
                        help="gap between stealth-beep pairs (default 0.5s)")
    parser.add_argument("--log-file", metavar="PATH",
                        help="write DEBUG-level diagnostic log to PATH")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    console_level = logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose >= 1 else logging.CRITICAL
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if args.log_file else console_level)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(fmt)
    root.addHandler(console)
    if args.log_file:
        fh = logging.FileHandler(args.log_file, mode="w")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if args.verbose < 2 and not args.log_file:
        logging.getLogger("bleak").setLevel(logging.WARNING)

    has_writes = any(getattr(args, n) is not None
                     for n in ("units", "beep", "laser", "backlight", "spirit_level", "rotate"))

    async def go():
        if args.pattern:
            try:
                patterns = [
                    [int(x.strip()) for x in p.split(",") if x.strip()]
                    for p in args.pattern.split(";")
                ]
            except ValueError:
                raise SystemExit(f"--pattern parse error: {args.pattern!r}")
            await _play_patterns(patterns=patterns,
                                  settle_s=args.settle_s,
                                  gap_between_patterns_s=args.pattern_gap_s)
            return
        if args.single_beep:
            try:
                durations = [int(x.strip()) for x in args.single_beep.split(",")]
            except ValueError:
                raise SystemExit(f"--single-beep expects comma-separated ints, got {args.single_beep!r}")
            await _single_beep(durations_ms=durations, settle_s=args.settle_s)
            return
        if args.sweep:
            try:
                lo, hi = args.sweep.split("-")
                start, end = int(lo, 16), int(hi, 16)
            except (ValueError, TypeError):
                raise SystemExit(f"--sweep expects HEX-HEX, got {args.sweep!r}")
            await _sweep_commands(
                start=start, end=end,
                skip={0x2E, 0x35},  # known to crash the BT connection
                per_probe_delay_s=args.sweep_delay_s,
                settle_s=args.settle_s,
            )
            return
        if args.stealth_beep is not None:
            await _test_stealth_beep(
                settle_s=args.settle_s,
                repeats=args.stealth_beep,
                gap_s=args.stealth_gap_s,
            )
            return
        if args.probe_cmds:
            await _probe_commands(
                per_probe_delay_s=args.probe_delay_s,
                settle_s=args.settle_s,
            )
            return
        if args.test_beep is not None:
            await _test_beep(
                toggles=args.test_beep,
                delay_s=args.toggle_delay_ms / 1000.0,
                settle_s=args.settle_s,
            )
            return
        notice("Looking for your GLM...")
        current = await _read_settings()
        if not has_writes:
            print()
            _print_settings(current)
            return
        new = DeviceSettings(
            spirit_level=current.spirit_level if args.spirit_level is None else _ONOFF_CHOICES[args.spirit_level],
            disp_rotation=current.disp_rotation if args.rotate is None else _ONOFF_CHOICES[args.rotate],
            speaker=current.speaker if args.beep is None else _ONOFF_CHOICES[args.beep],
            laser_pointer=current.laser_pointer if args.laser is None else _ONOFF_CHOICES[args.laser],
            backlight=current.backlight if args.backlight is None else _BACKLIGHT_CHOICES[args.backlight],
            angle_unit=current.angle_unit,
            measurement_unit=current.measurement_unit if args.units is None else _UNIT_CHOICES[args.units],
            dev_configuration=current.dev_configuration,
            last_used_list_index=current.last_used_list_index,
        )
        notice("Writing settings...")
        updated = await _write_settings(new)
        print()
        _print_settings(updated)

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        print(f"\033[1;91merror: {msg}\033[0m")
        raise SystemExit(1)


def _add_version(parser: argparse.ArgumentParser) -> None:
    from . import __version__
    parser.add_argument("--version", "-V", action="version",
                        version=f"%(prog)s {__version__}")


def tui() -> None:
    """Launch the Textual TUI."""
    parser = argparse.ArgumentParser(description="Bosch GLM TUI.")
    _add_version(parser)
    parser.add_argument("--offset", type=float, default=0.0, metavar="INCHES",
                        help="static offset in decimal inches added to every measurement")
    parser.add_argument("--catchup", action="store_true",
                        help="on connect, recover measurements taken while disconnected")
    parser.add_argument("--no-location", action="store_true",
                        help="don't query macOS Location Services for geotagging")
    parser.add_argument("--sites", metavar="PATH",
                        help="JSON file of named sites for nearest-site matching")
    parser.add_argument("--station-idle-s", type=float, default=60.0,
                        help="idle window for station auto-close (default 60s)")
    parser.add_argument("--no-gestures", action="store_true",
                        help="disable error-error soft-delete gesture detection")
    args = parser.parse_args()
    sites_path = Path(args.sites).expanduser() if args.sites else None
    from .tui.app import run_tui
    run_tui(offset_in=args.offset, catchup=args.catchup,
            use_location=not args.no_location, sites_path=sites_path,
            station_idle_s=args.station_idle_s,
            gestures=not args.no_gestures)


def headless() -> None:
    parser = argparse.ArgumentParser(description="Stream measurements from a Bosch GLM rangefinder.")
    _add_version(parser)
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose output: -v for info/warnings, -vv for full debug")
    parser.add_argument("-c", "--clipboard", nargs="?", const="in", default=None,
                        type=str.lower, choices=["in", "arch", "m", "mm"],
                        help="copy each measurement to the clipboard via pbcopy. "
                             "in=decimal inches (default), arch=feet-inches string, m=meters, mm=millimeters")
    parser.add_argument("--offset", type=float, default=0.0, metavar="INCHES",
                        help="static offset in decimal inches added to every measurement")
    parser.add_argument("--no-store", action="store_true",
                        help="don't persist measurements to the local SQLite store")
    parser.add_argument("--catchup", action="store_true",
                        help="on connect, recover measurements taken while disconnected "
                             "by sequentially probing the device's stored history")
    parser.add_argument("--no-location", action="store_true",
                        help="don't query macOS Location Services for geotagging")
    parser.add_argument("--sites", metavar="PATH",
                        help="JSON file of named sites for nearest-site matching "
                             "(default: ~/Library/Application Support/bosch-glm/sites.json)")
    parser.add_argument("--station-idle-s", type=float, default=60.0,
                        help="idle window for station auto-close (default 60s)")
    parser.add_argument("--no-gestures", action="store_true",
                        help="disable error-error soft-delete gesture detection")
    parser.add_argument("--log-file", metavar="PATH",
                        help="write DEBUG-level diagnostic log to PATH (overrides -v level for the file)")
    args = parser.parse_args()

    console_level = logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose >= 1 else logging.CRITICAL
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if args.log_file else console_level)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(fmt)
    root.addHandler(console)
    if args.log_file:
        fh = logging.FileHandler(args.log_file, mode="w")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if args.verbose < 2 and not args.log_file:
        logging.getLogger("bleak").setLevel(logging.WARNING)

    store = None if args.no_store else Store()
    sites_path = Path(args.sites).expanduser() if args.sites else None
    try:
        asyncio.run(_run_headless(
            args.clipboard, args.offset, store, args.catchup,
            use_location=not args.no_location, sites_path=sites_path,
            station_idle_s=args.station_idle_s,
            gestures=not args.no_gestures,
        ))
    except KeyboardInterrupt:
        pass
    finally:
        if store is not None:
            store.close()
