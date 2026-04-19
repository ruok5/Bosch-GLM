"""Textual TUI for live GLM streaming, history view, and settings panel."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import (
    DataTable, Footer, Header, Input, Static,
)

from ..ble import CHAR_UUID, stream_frames
from .. import feedback
from ..format import (
    IN_PER_M, copy_to_clipboard, displayed_inches, format_imperial,
    fractional_inches, render_big,
)
from ..gestures import ErrorErrorTracker
from ..protocol.constants import FrameType
from ..protocol.frame import encode
from ..protocol.messages import (
    ANGLE_UNIT_NAMES, BACKLIGHT_NAMES, CMD_EDC,
    DeviceSettings, EDCMeasurement, UNIT_NAMES,
    edc_request_history_item, get_settings_request,
)
from ..sites import load_sites, nearest_site
from ..station import StationClosed, StationTracker
from ..store import LocationFix, Store
from .screens import StationReviewScreen

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 50
ERROR_DISPLAY_S = 4.0
CATCHUP_STARTUP_DELAY_S = 1.5
CATCHUP_RESPONSE_TIMEOUT_S = 1.5
MAX_LIST_INDEX = 63


class StatusBar(Static):
    pass


class ReadingPanel(Static):
    """Big-text current reading display."""


class SettingsPanel(Static):
    """Read-only display of the device's current settings."""


class CatchupBanner(Static):
    pass


class GlmApp(App):
    CSS = """
    Screen { layout: vertical; }

    StatusBar {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    StatusBar.connected { background: $success-darken-2; }
    StatusBar.warning   { background: $warning-darken-2; }

    CatchupBanner {
        height: 1;
        padding: 0 1;
        background: $accent-darken-2;
        color: $text;
    }
    CatchupBanner.hidden { display: none; }

    ReadingPanel {
        height: 9;
        padding: 1 2;
        content-align: center middle;
    }

    .lower {
        height: 1fr;
    }

    DataTable {
        width: 3fr;
    }

    SettingsPanel {
        width: 1fr;
        padding: 1 2;
        border-left: solid $boost;
    }

    Input.offset {
        dock: bottom;
        height: 3;
    }

    .copy-menu {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $accent-darken-2;
        color: $text;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("c", "copy_last", "Copy last"),
        Binding("o", "set_offset", "Offset"),
        Binding("r", "refresh_history", "Refresh"),
        Binding("s", "fetch_settings", "Sync settings"),
        Binding("l", "review_station", "Review station"),
        Binding("D", "toggle_deleted", "Show/hide deleted"),
        Binding("U", "undelete_last", "Undelete"),
    ]

    connected: reactive[bool] = reactive(False)
    device_name: reactive[str] = reactive("...")
    last_measurement: reactive[EDCMeasurement | None] = reactive(None, layout=False)
    last_deleted_meas_id: reactive[int | None] = reactive(None, layout=False)
    offset_in: reactive[float] = reactive(0.0)
    settings: reactive[DeviceSettings | None] = reactive(None, layout=False)
    has_warnings: reactive[bool] = reactive(False)
    catchup_status: reactive[str] = reactive("")
    station_status: reactive[str] = reactive("")
    show_deleted: reactive[bool] = reactive(True)

    def __init__(self, store: Store, offset_in: float = 0.0,
                 catchup: bool = False, use_location: bool = True,
                 sites_path=None, station_idle_s: float = 60.0,
                 gestures: bool = True) -> None:
        super().__init__()
        self.store = store
        self.set_reactive(GlmApp.offset_in, offset_in)
        self.catchup_enabled = catchup
        self.use_location = use_location
        self.sites = load_sites(sites_path) if use_location else []
        self.device_address: str | None = None
        self.client = None
        self.location: LocationFix | None = None
        self.site_name: str | None = None
        self._error_clear_timer = None
        self.station_idle_s = station_idle_s
        self.station = StationTracker(idle_window_ms=int(station_idle_s * 1000))
        self.err_tracker = ErrorErrorTracker(window_ms=3000) if gestures else None
        self._last_closed_station: int | None = None
        self._station_close_timer = None
        self._station_open_at_ts_ms: int | None = None
        self._station_countdown_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar("○ Looking for your GLM…", id="status")
        yield CatchupBanner("", id="catchup", classes="hidden")
        yield CatchupBanner("", id="station-banner", classes="hidden")
        yield ReadingPanel("", id="reading")
        with Horizontal(classes="lower"):
            yield DataTable(id="history", zebra_stripes=True, cursor_type="row")
            yield SettingsPanel("[dim]settings not yet read[/dim]", id="settings")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Bosch GLM"
        self.sub_title = f"offset {self.offset_in:+g}\""
        table = self.query_one("#history", DataTable)
        table.add_columns("Time", "Result", "Imperial", "Mode", "ID", "Label")
        self._reload_history()
        self.run_worker(self._ble_loop(), exclusive=True, name="ble")

    # -- reactive watchers ---------------------------------------------------

    def watch_connected(self, _value: bool) -> None:
        self._refresh_status()

    def watch_device_name(self, _value: str) -> None:
        self._refresh_status()

    def watch_has_warnings(self, _value: bool) -> None:
        self._refresh_status()

    def watch_catchup_status(self, value: str) -> None:
        banner = self.query_one("#catchup", CatchupBanner)
        if value:
            banner.update(value)
            banner.remove_class("hidden")
        else:
            banner.add_class("hidden")

    def watch_station_status(self, value: str) -> None:
        banner = self.query_one("#station-banner", CatchupBanner)
        if value:
            banner.update(value)
            banner.remove_class("hidden")
        else:
            banner.add_class("hidden")

    def watch_offset_in(self, value: float) -> None:
        self.sub_title = f"offset {value:+g}\""
        # Re-render the panel with the new offset applied.
        if self.last_measurement is not None:
            self._render_measurement(self.last_measurement)

    def watch_last_measurement(self, m: EDCMeasurement | None) -> None:
        if m is None:
            return
        self._render_measurement(m)

    def watch_settings(self, s: DeviceSettings | None) -> None:
        panel = self.query_one("#settings", SettingsPanel)
        if s is None:
            panel.update("[dim]settings not yet read[/dim]")
            return
        rows = [
            "[bold]Device settings[/bold]",
            "",
            f"Units:        {UNIT_NAMES.get(s.measurement_unit, str(s.measurement_unit))}",
            f"Angle:        {ANGLE_UNIT_NAMES.get(s.angle_unit, str(s.angle_unit))}",
            f"Laser:        {'on' if s.laser_pointer else 'off'}",
            f"Beep:         {'on' if s.speaker else 'off'}",
            f"Backlight:    {BACKLIGHT_NAMES.get(s.backlight, str(s.backlight))}",
            f"Spirit level: {'on' if s.spirit_level else 'off'}",
            f"Rotate disp:  {'on' if s.disp_rotation else 'off'}",
            f"Stored items: {s.last_used_list_index}",
        ]
        panel.update("\n".join(rows))

    # -- helpers -------------------------------------------------------------

    def _refresh_status(self) -> None:
        bar = self.query_one("#status", StatusBar)
        bar.remove_class("connected")
        bar.remove_class("warning")
        if not self.connected:
            bar.update("○ Looking for your GLM…")
            return
        marker = "●"
        text = f"{marker} Connected to {self.device_name}"
        if self.has_warnings and self.last_measurement is not None:
            flags = []
            if self.last_measurement.batt_warning:
                flags.append("LOW BATT")
            if self.last_measurement.temp_warning:
                flags.append("TEMP")
            text += "  ·  " + " ".join(flags)
            bar.add_class("warning")
        else:
            bar.add_class("connected")
        bar.update(text)

    def _render_measurement(self, m: EDCMeasurement, color: str = "cyan") -> None:
        adj_m = m.result + self.offset_in / IN_PER_M
        imperial = format_imperial(adj_m)
        big = render_big(imperial)
        ts = datetime.now().strftime("%H:%M:%S")
        if self.offset_in:
            small = (f"[{ts}]  {m.result:.4f} m  ·  raw {m.result*IN_PER_M:.2f}\""
                     f"  ·  adj {adj_m*IN_PER_M:.2f}\"  ·  measID {m.meas_id}")
            color = "yellow"
        else:
            small = f"[{ts}]  {m.result:.4f} m  ·  {adj_m * IN_PER_M:.2f}\"  ·  measID {m.meas_id}"
        panel = self.query_one("#reading", ReadingPanel)
        panel.update(f"[bold {color}]{big}[/bold {color}]\n[dim]{small}[/dim]")

    def _show_error(self, code: int) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        big = render_big("ERR")
        panel = self.query_one("#reading", ReadingPanel)
        panel.update(
            f"[bold red]{big}[/bold red]\n"
            f"[red][{ts}]  measurement error (code {code})[/red]"
        )
        # Auto-restore the last good measurement after a few seconds.
        if self._error_clear_timer is not None:
            self._error_clear_timer.stop()
        self._error_clear_timer = self.set_timer(ERROR_DISPLAY_S, self._restore_after_error)

    def _restore_after_error(self) -> None:
        if self.last_measurement is not None:
            self._render_measurement(self.last_measurement)
        else:
            self.query_one("#reading", ReadingPanel).update("")

    def _reload_history(self) -> None:
        table = self.query_one("#history", DataTable)
        table.clear()
        sql = ("SELECT meas_id, dev_mode, result_m, captured_at, deleted_at, station_label "
               "FROM measurements")
        params: list = []
        if not self.show_deleted:
            sql += " WHERE deleted_at IS NULL"
        sql += " ORDER BY captured_at DESC LIMIT ?"
        params.append(HISTORY_LIMIT)
        rows = self.store.conn.execute(sql, params).fetchall()
        for r in rows:
            ts = datetime.fromtimestamp(r["captured_at"] / 1000).strftime("%H:%M:%S")
            res_str = f"{r['result_m']:.4f} m"
            imp_str = format_imperial(r["result_m"])
            label = r["station_label"] or ""
            if r["deleted_at"]:
                res_str = f"[strike dim]{res_str}[/strike dim]"
                imp_str = f"[strike dim]{imp_str}[/strike dim]"
            table.add_row(ts, res_str, imp_str, str(r["dev_mode"]),
                          str(r["meas_id"]), label)

    # Textual DataTable has no insert-at-top API. For ~50 rows the cheapest
    # correct way to keep newest-first is to clear and re-query the store on
    # each new measurement; callers just call `_reload_history()` directly.

    # -- BLE loop ------------------------------------------------------------

    async def _ble_loop(self) -> None:
        catchup_queue: asyncio.Queue[EDCMeasurement] | None = None
        catchup_task: asyncio.Task | None = None

        def on_connect(client) -> None:
            nonlocal catchup_queue, catchup_task
            self.client = client
            self.device_address = client.address
            self.connected = True
            short = (client.address or "GLM")
            short = short.split("-")[0] if "-" in short else short
            self.device_name = short
            # Kick off settings request shortly after connection settles.
            asyncio.create_task(self._request_settings_after_delay(client, 1.0))
            # Geolocation lookup
            if self.use_location:
                asyncio.create_task(self._refresh_location())
            # Catchup, if enabled
            if self.catchup_enabled:
                if catchup_task and not catchup_task.done():
                    catchup_task.cancel()
                catchup_queue = asyncio.Queue()
                catchup_task = asyncio.create_task(self._catchup(client, catchup_queue))

        async for frame in stream_frames(on_connect=on_connect):
            # Responses are dispatched by payload size — settings is 11 bytes,
            # EDC measurement is 16. The wire protocol drops the cmd byte for
            # responses, so size is the only discriminator we have.
            if frame.type == FrameType.RESPONSE:
                n = len(frame.payload)
                if n == 11:
                    try:
                        self.settings = DeviceSettings.from_payload(frame.payload)
                    except Exception as e:
                        logger.debug("settings parse failed: %s", e)
                elif n >= 16:
                    m = EDCMeasurement.from_payload(frame.payload)
                    if catchup_queue is not None:
                        catchup_queue.put_nowait(m)
                continue
            # Live EDC notifications (REQUEST type, cmd 0x55)
            if (frame.type != FrameType.REQUEST
                    or frame.cmd != CMD_EDC
                    or len(frame.payload) < 16):
                continue
            m = EDCMeasurement.from_payload(frame.payload)
            now_ms = int(datetime.now().timestamp() * 1000)
            if m.is_error:
                self._show_error(int(m.result))
                # Error-error gesture: 2 errors within 3s → soft delete last good
                if self.err_tracker is not None:
                    trigger = self.err_tracker.on_error(now_ms)
                    if trigger and self.device_address and trigger.device_address:
                        if self.store.soft_delete(trigger.device_address, trigger.meas_id):
                            self.last_deleted_meas_id = trigger.meas_id
                            self.notify(
                                f"Soft-deleted measurement #{trigger.meas_id}",
                                severity="warning",
                            )
                            if self.client is not None:
                                asyncio.create_task(feedback.double_beep(self.client))
                            self._reload_history()
                continue
            if not m.is_meaningful:
                # Update warning flags from heartbeats
                self.has_warnings = m.batt_warning or m.temp_warning
                continue
            # Station tracking
            events = self.station.feed(m.meas_id, now_ms)
            for ev in events:
                if isinstance(ev, StationClosed) and len(ev.member_meas_ids) > 1:
                    self._handle_station_closed(ev)
            self._station_open_at_ts_ms = now_ms
            self._reschedule_station_close()
            sid = self.station._open_id
            if self.device_address:
                self.store.insert(
                    self.device_address, m,
                    offset_in=self.offset_in,
                    location=self.location,
                    site_name=self.site_name,
                    station_id=sid,
                )
            if self.err_tracker is not None and self.device_address:
                self.err_tracker.on_good(m.meas_id, self.device_address, now_ms)
            self.has_warnings = m.batt_warning or m.temp_warning
            self.last_measurement = m
            self._reload_history()

    # -- station close timer + countdown ------------------------------------

    def _handle_station_closed(self, ev: StationClosed) -> None:
        self._last_closed_station = ev.station_id
        self.station_status = (
            f"Station of {len(ev.member_meas_ids)} ready to review (l)"
        )
        if self.client is not None:
            asyncio.create_task(feedback.beep(self.client))
        self._stop_station_countdown()

    def _reschedule_station_close(self) -> None:
        # Cancel any pending close timer; arm a new one for the full idle window.
        if self._station_close_timer is not None:
            self._station_close_timer.stop()
        self._station_close_timer = self.set_timer(
            self.station_idle_s, self._on_station_idle_expired
        )
        self._start_station_countdown()

    def _on_station_idle_expired(self) -> None:
        if not self.station.is_open:
            return
        ev = self.station.force_close()
        if ev is not None and len(ev.member_meas_ids) > 1:
            self._handle_station_closed(ev)
        elif ev is not None and len(ev.member_meas_ids) == 1:
            # Single-shot station — silently close, no review prompt
            self._stop_station_countdown()

    def _start_station_countdown(self) -> None:
        if self._station_countdown_timer is not None:
            self._station_countdown_timer.stop()
        self._station_countdown_timer = self.set_interval(
            1.0, self._tick_station_countdown
        )
        self._tick_station_countdown()

    def _tick_station_countdown(self) -> None:
        if not self.station.is_open or self._station_open_at_ts_ms is None:
            self._stop_station_countdown()
            return
        elapsed_s = (int(datetime.now().timestamp() * 1000) - self._station_open_at_ts_ms) / 1000.0
        remaining = max(0, int(self.station_idle_s - elapsed_s))
        n = self.station.open_count
        self.station_status = (
            f"Station: {n} member{'s' if n != 1 else ''} · closes in {remaining}s"
        )

    def _stop_station_countdown(self) -> None:
        if self._station_countdown_timer is not None:
            self._station_countdown_timer.stop()
            self._station_countdown_timer = None

    async def _request_settings_after_delay(self, client, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        await self._send_get_settings(client)

    async def _refresh_location(self) -> None:
        from ..location import get_fix
        fix = await get_fix(timeout_s=4.0)
        if fix is None:
            self.notify("Location unavailable.", severity="information")
            return
        self.location = fix
        if self.sites:
            match = nearest_site((fix.latitude, fix.longitude), self.sites)
            if match:
                self.site_name = match[0].name
                self.notify(f"Site: {match[0].name} ({match[1]:.0f}m)")
            else:
                self.notify(
                    f"Located {fix.latitude:.5f},{fix.longitude:.5f} (no site match)"
                )
        else:
            self.notify(f"Located {fix.latitude:.5f},{fix.longitude:.5f}")

    async def _send_get_settings(self, client) -> None:
        try:
            await client.write_gatt_char(CHAR_UUID, encode(get_settings_request()), True)
        except Exception as e:
            logger.debug("settings request failed: %s", e)

    async def _catchup(self, client, queue: asyncio.Queue[EDCMeasurement]) -> None:
        await asyncio.sleep(CATCHUP_STARTUP_DELAY_S)
        self.catchup_status = "Catchup: probing device history…"
        scanned = 0
        recovered = 0
        for list_idx in range(1, MAX_LIST_INDEX + 1):
            # Drain any stale frames
            while not queue.empty():
                queue.get_nowait()
            try:
                await client.write_gatt_char(
                    CHAR_UUID, encode(edc_request_history_item(list_idx)), True)
            except Exception as e:
                logger.warning("catchup write failed: %s", e)
                break
            try:
                m = await asyncio.wait_for(queue.get(), timeout=CATCHUP_RESPONSE_TIMEOUT_S)
            except asyncio.TimeoutError:
                break
            scanned += 1
            if not m.is_meaningful:
                break
            if self.device_address and self.store.insert_history(
                    self.device_address, m,
                    offset_in=self.offset_in,
                    location=self.location,
                    site_name=self.site_name):
                recovered += 1
                # Visually replay in magenta and adopt as last_measurement so
                # `c` copies what's currently shown on screen.
                self.last_measurement = m
                self._render_measurement(m, color="magenta")
                self._reload_history()
            self.catchup_status = (
                f"Catchup: scanned {scanned}/{MAX_LIST_INDEX}, recovered {recovered}…"
            )
        self.catchup_status = (
            f"Catchup done: scanned {scanned}, recovered {recovered}."
        )
        # Hide the banner after a moment
        self.set_timer(3.0, lambda: setattr(self, "catchup_status", ""))

    # -- key actions ---------------------------------------------------------

    # Clipboard format menu --------------------------------------------------
    # Maps single-key shortcut → (label, formatter taking adjusted meters).
    # 'c' is reserved for "repeat last choice" so mashing c-c uses the prior
    # format. Defaults to inches on first run.
    COPY_FORMATS: ClassVar[dict[str, tuple[str, callable]]] = {
        "i": ("inches",                lambda m: f"{displayed_inches(m):g}"),
        "f": ("fractional inches",     lambda m: fractional_inches(m)),
        "a": ("ft-in (arch)",          lambda m: format_imperial(m)),
        "d": ("decimal feet",          lambda m: f"{m * IN_PER_M / 12:.3f}"),
        "m": ("meters (4dp)",          lambda m: f"{m:.4f}"),
        "r": ("raw meters",            lambda m: f"{m:.6f}"),
        "n": ("millimeters",           lambda m: str(round(m * 1000))),
        "y": ("yards (3dp)",           lambda m: f"{m * 1.0936133:.3f}"),
    }
    _last_copy_key: str = "i"

    def action_copy_last(self) -> None:
        if self.last_measurement is None:
            self.notify("Nothing to copy yet.", severity="warning")
            return
        # Show a one-line picker prompt; the actual copy happens in on_key.
        # If the picker is already open, do nothing (avoid duplication).
        existing = self.query("#copy-prompt")
        if existing:
            existing.last().focus()
            return
        last_label = self.COPY_FORMATS[self._last_copy_key][0]
        choices = "  ".join(f"[bold]{k}[/bold]={label.split()[0]}"
                            for k, (label, _) in self.COPY_FORMATS.items())
        prompt = Static(
            f"[bold]Copy[/bold]:  [bold]c[/bold]=again({last_label})  "
            f"{choices}   [dim](Esc to cancel)[/dim]",
            id="copy-prompt",
            classes="copy-menu",
        )
        self.mount(prompt)
        self._copy_menu_open = True

    async def on_key(self, event) -> None:
        if not getattr(self, "_copy_menu_open", False):
            return
        key = event.key
        if key == "escape":
            self._dismiss_copy_menu()
            event.stop()
            return
        # 'c' = repeat the last chosen format. First-run default is 'i'.
        if key == "c":
            key = self._last_copy_key
        if key in self.COPY_FORMATS and self.last_measurement is not None:
            label, fn = self.COPY_FORMATS[key]
            adj_m = self.last_measurement.result + self.offset_in / IN_PER_M
            text = fn(adj_m)
            copy_to_clipboard(text)
            self._last_copy_key = key
            self.notify(f"Copied [bold]{text}[/bold]  ({label})")
            self._dismiss_copy_menu()
            event.stop()

    def _dismiss_copy_menu(self) -> None:
        self._copy_menu_open = False
        for w in self.query("#copy-prompt"):
            w.remove()

    def action_set_offset(self) -> None:
        # Mount a small input prompt
        existing = self.query("Input.offset")
        if existing:
            existing.last().focus()
            return
        prompt = Input(
            placeholder=f"offset in inches (current: {self.offset_in:+g})",
            classes="offset",
            id="offset-input",
        )
        self.mount(prompt)
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "offset-input":
            return
        try:
            self.offset_in = float(event.value) if event.value.strip() else 0.0
            self.notify(f"Offset set to {self.offset_in:+g}\"")
        except ValueError:
            self.notify("Invalid number; offset unchanged.", severity="error")
        event.input.remove()

    def action_refresh_history(self) -> None:
        self._reload_history()
        self.notify("History reloaded from store.")

    def action_fetch_settings(self) -> None:
        if self.client is None:
            self.notify("Not connected.", severity="warning")
            return
        asyncio.create_task(self._send_get_settings(self.client))

    # -- station + soft-delete actions --------------------------------------

    def action_review_station(self) -> None:
        sid = self._last_closed_station
        if sid is None:
            # Maybe an open station — close it manually for review
            close_ev = self.station.force_close()
            if close_ev is None:
                self.notify("No station to review yet.", severity="warning")
                return
            sid = close_ev.station_id
        members = self.store.station_members(sid)
        if not members:
            self.notify(f"No members for station {sid}.", severity="warning")
            return

        def apply_labels(labels: dict[int, str | None], confirmed: bool) -> None:
            if self.device_address is None:
                return
            for meas_id, label in labels.items():
                self.store.set_station_label(self.device_address, meas_id, label)
            if confirmed:
                self.store.confirm_station(sid)
                self.notify(f"Station {sid} confirmed ({len(labels)} labeled).")
                if self.client is not None:
                    asyncio.create_task(feedback.beep(self.client))
            else:
                self.notify(f"Station {sid} saved as draft.")
            self._reload_history()
            self.station_status = ""

        self.push_screen(StationReviewScreen(sid, members, apply_labels))

    def action_toggle_deleted(self) -> None:
        self.show_deleted = not self.show_deleted
        self._reload_history()
        self.notify(f"Show deleted: {'on' if self.show_deleted else 'off'}")

    def action_undelete_last(self) -> None:
        if self.last_deleted_meas_id is None or self.device_address is None:
            self.notify("Nothing to undelete.", severity="warning")
            return
        if self.store.undelete(self.device_address, self.last_deleted_meas_id):
            self.notify(f"Undeleted measurement #{self.last_deleted_meas_id}.")
            self.last_deleted_meas_id = None
            self._reload_history()


def run_tui(offset_in: float = 0.0, catchup: bool = False,
            use_location: bool = True, sites_path=None,
            station_idle_s: float = 60.0, gestures: bool = True) -> None:
    store = Store()
    try:
        GlmApp(store=store, offset_in=offset_in, catchup=catchup,
               use_location=use_location, sites_path=sites_path,
               station_idle_s=station_idle_s, gestures=gestures).run()
    finally:
        store.close()
