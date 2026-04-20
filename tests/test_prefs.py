"""Preference load/save tests. Pref file is a user-facing artifact — the
round-trip and the failure modes (missing, malformed, unknown keys) are
what actually matters here."""
import json
import pathlib
import tempfile

import pytest

from glm import prefs


def test_defaults_when_file_missing(tmp_path):
    p = prefs.load(tmp_path / "nonexistent.json")
    assert p.setup_idle_s == 20.0
    assert p.display_precision == "1/2"
    assert p.right_panel_collapsed is None


def test_round_trip(tmp_path):
    path = tmp_path / "prefs.json"
    original = prefs.Preferences(setup_idle_s=45.0, display_precision="1/8",
                                   right_panel_collapsed=True)
    prefs.save(original, path)
    loaded = prefs.load(path)
    assert loaded.setup_idle_s == 45.0
    assert loaded.display_precision == "1/8"
    assert loaded.right_panel_collapsed is True


def test_unknown_keys_are_dropped(tmp_path):
    """Forward/backward compat: a future pref field in the file should load
    cleanly on an older client, and a removed field should load cleanly on
    a newer one."""
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "setup_idle_s": 10.0,
        "display_precision": "1/4",
        "future_thing": "whatever",
    }))
    loaded = prefs.load(path)
    assert loaded.setup_idle_s == 10.0
    assert loaded.display_precision == "1/4"


def test_malformed_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text("{ this is not json")
    loaded = prefs.load(path)
    assert loaded.setup_idle_s == 20.0


def test_cycle_precision_wraps():
    p = prefs.Preferences(display_precision="1/2")
    nxt = p.cycle_precision()
    assert nxt == "1/4"
    assert p.cycle_precision() == "1/8"
    assert p.cycle_precision() == "1"
    assert p.cycle_precision() == "1/2"  # full cycle


def test_cycle_precision_recovers_from_bogus_value():
    p = prefs.Preferences(display_precision="bogus")
    # Any unknown value cycles back onto the canonical sequence without crashing.
    nxt = p.cycle_precision()
    assert nxt in prefs.PRECISION_VALUES
