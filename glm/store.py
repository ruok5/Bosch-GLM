"""SQLite persistence for measurements with dedup, location, and notes."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from .protocol.messages import EDCMeasurement

# Base schema (initial version). Migrations below add columns idempotently.
SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    device_address TEXT NOT NULL,
    meas_id        INTEGER NOT NULL,
    dev_mode       INTEGER NOT NULL,
    ref_edge       INTEGER NOT NULL,
    result_m       REAL    NOT NULL,
    comp1_m        REAL,
    comp2_m        REAL,
    captured_at    INTEGER NOT NULL,
    PRIMARY KEY (device_address, meas_id)
);
CREATE INDEX IF NOT EXISTS idx_measurements_recent
    ON measurements (device_address, captured_at DESC);
"""

# Each entry: (column_name, "<type with default/constraint>"). Applied in order
# if the column doesn't already exist. SQLite's ALTER TABLE ADD COLUMN is fine
# for nullable columns and columns with constant defaults.
MIGRATIONS = [
    ("offset_in",        "REAL NOT NULL DEFAULT 0"),
    ("latitude",         "REAL"),
    ("longitude",        "REAL"),
    ("loc_accuracy_m",   "REAL"),
    ("site_name",        "TEXT"),
    ("notes",            "TEXT"),
    ("station_id",       "INTEGER"),       # groups rows in one observation station
    ("station_label",    "TEXT"),          # e.g. "bottom-of-beam", or "bottom-of-pipe(4\")"
    ("station_status",   "TEXT"),          # NULL → "draft" → "confirmed"
    ("deleted_at",       "INTEGER"),       # unix ms; NULL = live row
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_measurements_station "
    "ON measurements (station_id) WHERE station_id IS NOT NULL",
]


@dataclass
class LocationFix:
    """A point-in-time geolocation reading."""
    latitude: float
    longitude: float
    accuracy_m: float | None = None


def default_db_path() -> Path:
    base = user_data_path("bosch-glm", appauthor=False, ensure_exists=True)
    return base / "measurements.sqlite"


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(measurements)")}
        for col, decl in MIGRATIONS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE measurements ADD COLUMN {col} {decl}")
        for stmt in INDEX_STATEMENTS:
            self.conn.execute(stmt)

    def insert(self, device_address: str, m: EDCMeasurement,
               offset_in: float = 0.0,
               location: LocationFix | None = None,
               site_name: str | None = None,
               station_id: int | None = None) -> bool:
        """Insert a live measurement; dedup by (device_address, meas_id) which
        is stable for autosync notifications. Returns True if new."""
        lat = lon = acc = None
        if location is not None:
            lat, lon, acc = location.latitude, location.longitude, location.accuracy_m
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO measurements "
            "(device_address, meas_id, dev_mode, ref_edge, result_m, comp1_m, comp2_m, "
            " captured_at, offset_in, latitude, longitude, loc_accuracy_m, site_name, station_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_address, m.meas_id, m.dev_mode, m.ref_edge,
             m.result, m.comp1, m.comp2, int(time.time() * 1000),
             offset_in, lat, lon, acc, site_name, station_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def insert_history(self, device_address: str, m: EDCMeasurement,
                       offset_in: float = 0.0,
                       location: LocationFix | None = None,
                       site_name: str | None = None,
                       station_id: int | None = None) -> bool:
        """Insert a history-fetch measurement; dedup by value tuple because the
        device assigns a fresh meas_id to every history response. Bit-exact
        float matching is sufficient — two real physical measurements producing
        identical floats is vanishingly rare for noisy distance readings."""
        existing = self.conn.execute(
            "SELECT 1 FROM measurements WHERE device_address=? AND dev_mode=? "
            "AND ref_edge=? AND result_m=? AND comp1_m IS ? AND comp2_m IS ?",
            (device_address, m.dev_mode, m.ref_edge, m.result, m.comp1, m.comp2),
        ).fetchone()
        if existing:
            return False
        lat = lon = acc = None
        if location is not None:
            lat, lon, acc = location.latitude, location.longitude, location.accuracy_m
        self.conn.execute(
            "INSERT OR IGNORE INTO measurements "
            "(device_address, meas_id, dev_mode, ref_edge, result_m, comp1_m, comp2_m, "
            " captured_at, offset_in, latitude, longitude, loc_accuracy_m, site_name, station_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_address, m.meas_id, m.dev_mode, m.ref_edge,
             m.result, m.comp1, m.comp2, int(time.time() * 1000),
             offset_in, lat, lon, acc, site_name, station_id),
        )
        self.conn.commit()
        return True

    # --- Soft delete -------------------------------------------------------

    def soft_delete(self, device_address: str, meas_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE measurements SET deleted_at = ? "
            "WHERE device_address = ? AND meas_id = ? AND deleted_at IS NULL",
            (int(time.time() * 1000), device_address, meas_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def undelete(self, device_address: str, meas_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE measurements SET deleted_at = NULL "
            "WHERE device_address = ? AND meas_id = ? AND deleted_at IS NOT NULL",
            (device_address, meas_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- Stations ----------------------------------------------------------

    def set_station_label(self, device_address: str, meas_id: int,
                          label: str | None) -> bool:
        cur = self.conn.execute(
            "UPDATE measurements SET station_label = ? "
            "WHERE device_address = ? AND meas_id = ?",
            (label, device_address, meas_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def confirm_station(self, station_id: int) -> int:
        cur = self.conn.execute(
            "UPDATE measurements SET station_status = 'confirmed' "
            "WHERE station_id = ?",
            (station_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def draft_station(self, station_id: int) -> int:
        """Mark all members of a station as draft (default state for new entries
        but useful for explicitly resetting a confirmed station)."""
        cur = self.conn.execute(
            "UPDATE measurements SET station_status = 'draft' "
            "WHERE station_id = ?",
            (station_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def station_members(self, station_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM measurements WHERE station_id = ? "
            "AND deleted_at IS NULL ORDER BY result_m ASC",
            (station_id,),
        ))

    def recent_stations(self, limit: int = 50) -> list[sqlite3.Row]:
        """Aggregate per-station summary, newest first."""
        return list(self.conn.execute(
            "SELECT station_id, "
            "       MIN(captured_at) AS first_at, "
            "       COUNT(*) AS member_count, "
            "       COALESCE(MAX(station_status), 'draft') AS status, "
            "       MIN(site_name) AS site_name "
            "FROM measurements "
            "WHERE station_id IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY station_id "
            "ORDER BY first_at DESC LIMIT ?",
            (limit,),
        ))

    def max_meas_id(self, device_address: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(meas_id) FROM measurements WHERE device_address = ?",
            (device_address,),
        ).fetchone()
        return row[0] if row else None

    def set_note(self, device_address: str, meas_id: int, note: str) -> bool:
        """Attach or replace the note on a measurement. Returns True if a row
        was updated."""
        cur = self.conn.execute(
            "UPDATE measurements SET notes = ? WHERE device_address = ? AND meas_id = ?",
            (note, device_address, meas_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def query(self, *, since_ms: int | None = None, until_ms: int | None = None,
              site: str | None = None, device_address: str | None = None,
              station_id: int | None = None,
              include_deleted: bool = False,
              include_drafts: bool = True,
              limit: int | None = None) -> list[sqlite3.Row]:
        """Generic filtered fetch, newest first. Excludes soft-deleted rows
        unless include_deleted=True. Excludes draft stations only if
        include_drafts=False (i.e. confirmed-only export mode)."""
        clauses, params = [], []
        if since_ms is not None:
            clauses.append("captured_at >= ?"); params.append(since_ms)
        if until_ms is not None:
            clauses.append("captured_at <= ?"); params.append(until_ms)
        if site is not None:
            clauses.append("site_name = ?"); params.append(site)
        if device_address is not None:
            clauses.append("device_address = ?"); params.append(device_address)
        if station_id is not None:
            clauses.append("station_id = ?"); params.append(station_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if not include_drafts:
            # Either no station OR a confirmed station
            clauses.append("(station_id IS NULL OR station_status = 'confirmed')")
        sql = "SELECT * FROM measurements"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY captured_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return list(self.conn.execute(sql, params))

    def close(self) -> None:
        self.conn.close()
