import csv
import io
import json
import pathlib
import tempfile
import time

import pytest

from glm.export import _row_to_dict, to_attribs, to_mleader
from glm.protocol.messages import EDCMeasurement
from glm.store import Store


def _m(meas_id: int, result: float, dev_mode: int = 1) -> EDCMeasurement:
    return EDCMeasurement(
        ref_edge=0, dev_mode=dev_mode, laser_on=False, temp_warning=False,
        batt_warning=False, config_units=0, device_status=0,
        meas_id=meas_id, result=result, comp1=0.0, comp2=0.0,
    )


@pytest.fixture
def setup_store():
    """Store with one confirmed setup of 4 labeled measurements."""
    with tempfile.TemporaryDirectory() as td:
        s = Store(pathlib.Path(td) / "test.sqlite")
        sid = 1000
        # Insert in unsorted order; export should sort by Z
        s.insert("AA", _m(1, 2.500), setup_id=sid); time.sleep(0.001)
        s.insert("AA", _m(2, 2.200), setup_id=sid); time.sleep(0.001)
        s.insert("AA", _m(3, 2.700), setup_id=sid); time.sleep(0.001)
        s.insert("AA", _m(4, 2.650), setup_id=sid)
        s.set_setup_label("AA", 2, "bottom-of-beam")        # 2.200 = lowest
        s.set_setup_label("AA", 1, "bottom-of-purlin")
        s.set_setup_label("AA", 4, 'bottom-of-pipe(4")')
        s.set_setup_label("AA", 3, "bottom-of-deck")        # 2.700 = highest
        s.confirm_setup(sid)
        yield s, sid
        s.close()


def test_mleader_orders_members_top_to_bottom(setup_store):
    """MLEADER output lists highest Z first to match how vertical sections
    are drawn in CAD."""
    s, sid = setup_store
    rows = [_row_to_dict(r) for r in s.query()]
    out = io.StringIO()
    to_mleader(rows, out)
    text = out.getvalue()
    # Deck should appear before beam since deck is top
    deck_pos = text.find("bottom-of-deck")
    beam_pos = text.find("bottom-of-beam")
    assert deck_pos != -1 and beam_pos != -1
    assert deck_pos < beam_pos


def test_mleader_includes_setup_header(setup_store):
    s, sid = setup_store
    rows = [_row_to_dict(r) for r in s.query()]
    out = io.StringIO()
    to_mleader(rows, out)
    assert "Setup " in out.getvalue()


def test_mleader_skips_when_no_confirmed_setups(setup_store):
    s, _ = setup_store
    # Query with include_drafts=False AND no confirmed setups
    s.draft_setup(1000)
    rows = [_row_to_dict(r) for r in s.query(include_drafts=False)]
    out = io.StringIO()
    to_mleader(rows, out)
    assert "no setups" in out.getvalue()


def test_attribs_emits_one_row_per_setup(setup_store):
    s, sid = setup_store
    rows = [_row_to_dict(r) for r in s.query()]
    out = io.StringIO()
    to_attribs(rows, out)
    out.seek(0)
    csv_rows = list(csv.DictReader(out))
    assert len(csv_rows) == 1
    row = csv_rows[0]
    assert int(row["setup_id"]) == sid
    assert row["BOT_BEAM"] != ""        # filled
    assert row["BOT_PURLIN"] != ""
    assert row["BOT_DECK"] != ""
    assert row["BOT_PIPE"] != ""
    assert row["BOT_PIPE_SIZE"] == '4"'
    assert row["BOT_SUBPURLIN"] == ""   # not labeled
    assert row["BOT_FOIL"] == ""        # not labeled


def test_attribs_round_trips_custom_labels(setup_store):
    s, _ = setup_store
    # Add a setup with a custom label
    custom_sid = 2000
    s.insert("AA", _m(10, 1.5), setup_id=custom_sid)
    s.set_setup_label("AA", 10, "weird-detail")
    s.confirm_setup(custom_sid)

    rows = [_row_to_dict(r) for r in s.query(setup_id=custom_sid)]
    out = io.StringIO()
    to_attribs(rows, out)
    out.seek(0)
    csv_rows = list(csv.DictReader(out))
    assert len(csv_rows) == 1
    customs = json.loads(csv_rows[0]["custom_labels_json"])
    assert "weird-detail" in customs


def test_attribs_skips_loose_rows(setup_store):
    s, _ = setup_store
    s.insert("AA", _m(50, 9.9))  # no setup_id
    rows = [_row_to_dict(r) for r in s.query()]
    out = io.StringIO()
    to_attribs(rows, out)
    out.seek(0)
    csv_rows = list(csv.DictReader(out))
    # Only the original setup, not the loose row
    assert len(csv_rows) == 1
