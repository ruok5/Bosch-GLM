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
#
# Note on terminology: "setup" = the time-bounded batch of consecutive shots
# at one place (surveyor sense — a single instrument setup yields a setup's
# worth of observations). "Station" is reserved for the X-Y datum (future).
MIGRATIONS = [
    ("offset_in",        "REAL NOT NULL DEFAULT 0"),
    ("latitude",         "REAL"),
    ("longitude",        "REAL"),
    ("loc_accuracy_m",   "REAL"),
    ("site_name",        "TEXT"),
    ("notes",            "TEXT"),
    ("setup_id",         "INTEGER"),       # groups rows in one observation setup
    ("setup_label",      "TEXT"),          # e.g. "bottom-of-beam", or "bottom-of-pipe(4\")"
    ("setup_status",     "TEXT"),          # NULL → "draft" → "confirmed"
    ("deleted_at",       "INTEGER"),       # unix ms; NULL = live row
]

# Old column names from the prior "station" terminology — renamed in place
# during migration so existing data is preserved.
LEGACY_RENAMES = [
    ("station_id",     "setup_id"),
    ("station_label",  "setup_label"),
    ("station_status", "setup_status"),
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_measurements_setup "
    "ON measurements (setup_id) WHERE setup_id IS NOT NULL",
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
        # Apply legacy renames first so the column-add pass below can use the
        # new names consistently.
        for old, new in LEGACY_RENAMES:
            if old in existing and new not in existing:
                self.conn.execute(
                    f"ALTER TABLE measurements RENAME COLUMN {old} TO {new}"
                )
                existing.discard(old)
                existing.add(new)
        # Drop any stale index that referenced the old column name.
        self.conn.execute("DROP INDEX IF EXISTS idx_measurements_station")
        for col, decl in MIGRATIONS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE measurements ADD COLUMN {col} {decl}")
        for stmt in INDEX_STATEMENTS:
            self.conn.execute(stmt)

    def insert(self, device_address: str, m: EDCMeasurement,
               offset_in: float = 0.0,
               location: LocationFix | None = None,
               site_name: str | None = None,
               setup_id: int | None = None) -> bool:
        """Insert a live measurement; dedup by (device_address, meas_id) which
        is stable for autosync notifications. Returns True if new."""
        lat = lon = acc = None
        if location is not None:
            lat, lon, acc = location.latitude, location.longitude, location.accuracy_m
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO measurements "
            "(device_address, meas_id, dev_mode, ref_edge, result_m, comp1_m, comp2_m, "
            " captured_at, offset_in, latitude, longitude, loc_accuracy_m, site_name, setup_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_address, m.meas_id, m.dev_mode, m.ref_edge,
             m.result, m.comp1, m.comp2, int(time.time() * 1000),
             offset_in, lat, lon, acc, site_name, setup_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def insert_history(self, device_address: str, m: EDCMeasurement,
                       offset_in: float = 0.0,
                       location: LocationFix | None = None,
                       site_name: str | None = None,
                       setup_id: int | None = None) -> bool:
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
            " captured_at, offset_in, latitude, longitude, loc_accuracy_m, site_name, setup_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_address, m.meas_id, m.dev_mode, m.ref_edge,
             m.result, m.comp1, m.comp2, int(time.time() * 1000),
             offset_in, lat, lon, acc, site_name, setup_id),
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

    # --- Setups (groupings of consecutive shots) ---------------------------

    def set_setup_label(self, device_address: str, meas_id: int,
                        label: str | None) -> bool:
        cur = self.conn.execute(
            "UPDATE measurements SET setup_label = ? "
            "WHERE device_address = ? AND meas_id = ?",
            (label, device_address, meas_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def confirm_setup(self, setup_id: int) -> int:
        cur = self.conn.execute(
            "UPDATE measurements SET setup_status = 'confirmed' "
            "WHERE setup_id = ?",
            (setup_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def draft_setup(self, setup_id: int) -> int:
        """Mark all members of a setup as draft (default state for new entries
        but useful for explicitly resetting a confirmed setup)."""
        cur = self.conn.execute(
            "UPDATE measurements SET setup_status = 'draft' "
            "WHERE setup_id = ?",
            (setup_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def clear_setup(self, setup_id: int) -> int:
        """Strip setup_id and setup_status from all rows in the given setup.
        Used when a setup turns out to be a singleton (one shot, no real
        grouping) — the row reverts to a plain measurement."""
        cur = self.conn.execute(
            "UPDATE measurements SET setup_id = NULL, setup_status = NULL "
            "WHERE setup_id = ?",
            (setup_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def setup_members(self, setup_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM measurements WHERE setup_id = ? "
            "AND deleted_at IS NULL ORDER BY result_m ASC",
            (setup_id,),
        ))

    def recent_setups(self, limit: int = 50) -> list[sqlite3.Row]:
        """Aggregate per-setup summary, newest first."""
        return list(self.conn.execute(
            "SELECT setup_id, "
            "       MIN(captured_at) AS first_at, "
            "       COUNT(*) AS member_count, "
            "       COALESCE(MAX(setup_status), 'draft') AS status, "
            "       MIN(site_name) AS site_name "
            "FROM measurements "
            "WHERE setup_id IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY setup_id "
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
              setup_id: int | None = None,
              include_deleted: bool = False,
              include_drafts: bool = True,
              limit: int | None = None) -> list[sqlite3.Row]:
        """Generic filtered fetch, newest first. Excludes soft-deleted rows
        unless include_deleted=True. Excludes draft setups only if
        include_drafts=False (i.e. confirmed-only export mode). Singleton
        rows (setup_id IS NULL) always pass — drafts are a setup concept."""
        clauses, params = [], []
        if since_ms is not None:
            clauses.append("captured_at >= ?"); params.append(since_ms)
        if until_ms is not None:
            clauses.append("captured_at <= ?"); params.append(until_ms)
        if site is not None:
            clauses.append("site_name = ?"); params.append(site)
        if device_address is not None:
            clauses.append("device_address = ?"); params.append(device_address)
        if setup_id is not None:
            clauses.append("setup_id = ?"); params.append(setup_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if not include_drafts:
            # Either no setup OR a confirmed setup
            clauses.append("(setup_id IS NULL OR setup_status = 'confirmed')")
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
