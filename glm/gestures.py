"""Hands-free gesture detection from the GLM measurement event stream.

Currently implements one gesture: **error-error soft-delete**. Two GLM
measurement errors arriving within `window_s` of each other (with no
good measurement in between) signals "delete the most recent good
measurement". The user triggers it in the field by aiming at empty
space twice in a row — fastest fix-a-misfire workflow possible.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SoftDeleteTrigger:
    """Emitted when the error-error gesture fires; identifies which row
    to soft-delete."""
    meas_id: int
    device_address: str | None = None


class ErrorErrorTracker:
    """Watches the EDC stream for the error-error gesture.

    Usage:
        t = ErrorErrorTracker()
        # On each EDC frame:
        if measurement.is_error:
            trigger = t.on_error(now_ms)
        else:
            t.on_good(measurement.meas_id, measurement.device_address, now_ms)

    The tracker holds the most recent good (meas_id, device_address) pair
    so it knows what to point the trigger at when two errors fire.
    Resets on every good event so a sequence like error → good → error
    will NOT fire.
    """

    def __init__(self, window_ms: int = 3000) -> None:
        self.window_ms = window_ms
        self._last_good_meas_id: int | None = None
        self._last_good_device: str | None = None
        self._first_error_ts_ms: int | None = None

    def on_good(self, meas_id: int, device_address: str, ts_ms: int) -> None:
        """Record a good measurement; resets any partial error sequence."""
        self._last_good_meas_id = meas_id
        self._last_good_device = device_address
        self._first_error_ts_ms = None

    def on_error(self, ts_ms: int) -> SoftDeleteTrigger | None:
        """Record an error event. Returns a trigger if this is the second
        error within the window after a good measurement."""
        if self._last_good_meas_id is None:
            # No good measurement to delete; ignore.
            return None
        if self._first_error_ts_ms is None:
            # First error in a possible pair; arm.
            self._first_error_ts_ms = ts_ms
            return None
        # Second error — check window
        elapsed = ts_ms - self._first_error_ts_ms
        if elapsed > self.window_ms:
            # Too slow; treat as a fresh first-error.
            self._first_error_ts_ms = ts_ms
            return None
        # Fire!
        trigger = SoftDeleteTrigger(
            meas_id=self._last_good_meas_id,
            device_address=self._last_good_device,
        )
        # Consume: the good measurement is now (about to be) deleted, so
        # reset state. Future deletes need a fresh good event.
        self._last_good_meas_id = None
        self._last_good_device = None
        self._first_error_ts_ms = None
        return trigger

    def reset(self) -> None:
        """Hard reset (e.g. on disconnect)."""
        self._last_good_meas_id = None
        self._last_good_device = None
        self._first_error_ts_ms = None
