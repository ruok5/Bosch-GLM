"""Station tracking: group consecutive vertical-elevation shots into one
named X-Y datum + label catalog + station CLI.

A station is a batch of measurements taken within a short idle window at
the same location. The user shoots 3–6 vertical elevations, the tracker
auto-groups them, and a review modal lets them assign labels from the
preset palette in Z-order.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime

from .format import format_imperial
from .store import Store

# Preset label palette. Ordered LOWEST Z → HIGHEST Z based on user's
# clarification. Pipe is special — variable Z so it floats outside.
PRESET_LABELS = [
    "bottom-of-beam",
    "bottom-of-purlin",
    "bottom-of-subpurlin",
    "bottom-of-foil",
    "bottom-of-deck",
    "bottom-of-pipe",       # gets a size suffix when applied
]

DEFAULT_Z_ORDER = [lbl for lbl in PRESET_LABELS if lbl != "bottom-of-pipe"]

# Picker list for pipe sizes; cursor lands on 2-1/2" by default since
# smaller is uncommon in the user's work.
PIPE_SIZES = ['1"', '1-1/4"', '1-1/2"', '2"', '2-1/2"',
              '3"', '3-1/2"', '4"', '5"', '6"', '8"']
PIPE_SIZE_DEFAULT = '2-1/2"'


def format_pipe_label(size: str) -> str:
    return f"bottom-of-pipe({size})"


def suggest_labels(member_count: int) -> list[str]:
    """Default Z-order assignment for an N-member station — applies the
    first N labels from DEFAULT_Z_ORDER. Pipe is left for the user to
    swap in manually since it floats."""
    return DEFAULT_Z_ORDER[:member_count]


# --- Tracker ---------------------------------------------------------------


@dataclass
class StationOpened:
    station_id: int  # captured_at_ms of first member


@dataclass
class MemberAdded:
    station_id: int
    meas_id: int


@dataclass
class StationClosed:
    station_id: int
    member_meas_ids: list[int] = field(default_factory=list)


StationEvent = StationOpened | MemberAdded | StationClosed


class StationTracker:
    """Time-window grouping of measurements into stations.

    Each call to feed() with a (meas_id, captured_at_ms) potentially
    emits one or two events: closing the previous station (if the new
    measurement falls outside the idle window) AND opening/extending
    one for the new measurement.
    """

    def __init__(self, idle_window_ms: int = 60_000) -> None:
        self.idle_window_ms = idle_window_ms
        self._open_id: int | None = None
        self._members: list[int] = []
        self._last_ts_ms: int | None = None

    def feed(self, meas_id: int, ts_ms: int) -> list[StationEvent]:
        events: list[StationEvent] = []
        if self._open_id is not None and self._last_ts_ms is not None:
            if ts_ms - self._last_ts_ms > self.idle_window_ms:
                events.append(StationClosed(self._open_id, list(self._members)))
                self._open_id = None
                self._members = []
        if self._open_id is None:
            self._open_id = ts_ms
            events.append(StationOpened(self._open_id))
        self._members.append(meas_id)
        events.append(MemberAdded(self._open_id, meas_id))
        self._last_ts_ms = ts_ms
        return events

    def force_close(self) -> StationClosed | None:
        """Close any open station explicitly (e.g. on app exit)."""
        if self._open_id is None:
            return None
        ev = StationClosed(self._open_id, list(self._members))
        self._open_id = None
        self._members = []
        self._last_ts_ms = None
        return ev

    @property
    def is_open(self) -> bool:
        return self._open_id is not None

    @property
    def open_count(self) -> int:
        return len(self._members)


# --- CLI -------------------------------------------------------------------


def _list_stations(store: Store, limit: int = 50) -> None:
    rows = store.recent_stations(limit=limit)
    if not rows:
        print("(no stations yet)")
        return
    print(f"{'station_id':>14}  {'when':<20}  {'count':>5}  {'status':<10}  site")
    for r in rows:
        ts = datetime.fromtimestamp(r["first_at"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
        site = r["site_name"] or ""
        print(f"{r['station_id']:>14}  {ts:<20}  {r['member_count']:>5}  "
              f"{r['status'] or 'draft':<10}  {site}")


def _show_station(store: Store, station_id: int) -> None:
    members = store.station_members(station_id)
    if not members:
        print(f"(no members for station {station_id})")
        return
    print(f"Station {station_id}  —  {len(members)} member(s)\n")
    print(f"{'meas_id':>7}  {'result_m':>10}  {'imperial':<14}  label")
    for m in members:
        label = m["station_label"] or ""
        print(f"{m['meas_id']:>7}  {m['result_m']:>10.4f}  "
              f"{format_imperial(m['result_m']):<14}  {label}")


def _confirm_station(store: Store, station_id: int) -> None:
    n = store.confirm_station(station_id)
    print(f"confirmed {n} member(s) of station {station_id}")


def station_main() -> None:
    from . import __version__
    parser = argparse.ArgumentParser(description="Inspect or confirm stations.")
    parser.add_argument("--version", "-V", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list recent stations")
    p_show = sub.add_parser("show", help="show members of a station")
    p_show.add_argument("station_id", type=int)
    p_confirm = sub.add_parser("confirm", help="mark a station as confirmed")
    p_confirm.add_argument("station_id", type=int)
    args = parser.parse_args()

    store = Store()
    try:
        if args.cmd == "list":
            _list_stations(store)
        elif args.cmd == "show":
            _show_station(store, args.station_id)
        elif args.cmd == "confirm":
            _confirm_station(store, args.station_id)
    finally:
        store.close()


if __name__ == "__main__":
    station_main()
