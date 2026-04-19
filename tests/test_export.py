import csv
import io
import json
import pathlib
import tempfile
import time

import pytest

from glm.export import _row_to_dict, to_csv, to_json, to_markdown
from glm.protocol.messages import EDCMeasurement
from glm.store import LocationFix, Store


@pytest.fixture
def populated_store():
    """Store with a few measurements at known timestamps + sites + locations."""
    with tempfile.TemporaryDirectory() as td:
        s = Store(pathlib.Path(td) / "test.sqlite")
        loc = LocationFix(latitude=37.5, longitude=-122.5, accuracy_m=10.0)
        for i, (val, site) in enumerate([
            (1.234, "Home"), (2.345, "Home"), (3.456, "Office"),
        ]):
            m = EDCMeasurement(
                ref_edge=0, dev_mode=1, laser_on=False, temp_warning=False,
                batt_warning=False, config_units=0, device_status=0,
                meas_id=i + 1, result=val, comp1=0.0, comp2=0.0,
            )
            s.insert("AA:BB", m, offset_in=0.5, location=loc, site_name=site)
            time.sleep(0.001)  # ensure distinct captured_at
        yield s
        s.close()


def test_export_csv_includes_all_rows(populated_store):
    rows = [_row_to_dict(r) for r in populated_store.query()]
    out = io.StringIO()
    to_csv(rows, out)
    out.seek(0)
    reader = list(csv.DictReader(out))
    assert len(reader) == 3
    # CSV preserves the field set from EXPORT_FIELDS
    assert "site_name" in reader[0]
    assert "offset_in" in reader[0]
    assert "latitude" in reader[0]


def test_export_json_round_trip(populated_store):
    rows = [_row_to_dict(r) for r in populated_store.query()]
    out = io.StringIO()
    to_json(rows, out)
    decoded = json.loads(out.getvalue())
    assert len(decoded) == 3
    assert decoded[0]["site_name"] in ("Home", "Office")
    assert decoded[0]["latitude"] == 37.5


def test_export_markdown_renders_table(populated_store):
    rows = [_row_to_dict(r) for r in populated_store.query()]
    out = io.StringIO()
    to_markdown(rows, out)
    text = out.getvalue()
    assert text.startswith("| Time |")
    assert "| Home |" in text or "| Office |" in text
    assert text.count("\n") >= 4  # header + separator + 3 rows


def test_export_markdown_handles_empty(populated_store):
    out = io.StringIO()
    to_markdown([], out)
    assert "(no rows)" in out.getvalue()


def test_query_filters_by_site(populated_store):
    rows = populated_store.query(site="Home")
    assert all(r["site_name"] == "Home" for r in rows)
    assert len(rows) == 2


def test_query_orders_newest_first(populated_store):
    rows = populated_store.query()
    assert rows[0]["captured_at"] >= rows[-1]["captured_at"]


def test_query_limit(populated_store):
    rows = populated_store.query(limit=1)
    assert len(rows) == 1
