import pathlib
import tempfile

import pytest

from glm.protocol.messages import EDCMeasurement
from glm.store import Store


def _m(meas_id: int, result: float = 1.234) -> EDCMeasurement:
    return EDCMeasurement(
        ref_edge=0, dev_mode=1, laser_on=False, temp_warning=False,
        batt_warning=False, config_units=0, device_status=0,
        meas_id=meas_id, result=result, comp1=0.0, comp2=0.0,
    )


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        s = Store(pathlib.Path(td) / "test.sqlite")
        yield s
        s.close()


def test_set_note_returns_true_when_row_exists(store):
    store.insert("AA", _m(1))
    assert store.set_note("AA", 1, "front porch height") is True


def test_set_note_returns_false_when_row_missing(store):
    assert store.set_note("AA", 999, "anything") is False


def test_set_note_persists_text(store):
    store.insert("AA", _m(1))
    store.set_note("AA", 1, "test note")
    row = store.conn.execute(
        "SELECT notes FROM measurements WHERE device_address='AA' AND meas_id=1"
    ).fetchone()
    assert row["notes"] == "test note"


def test_set_note_replaces_previous(store):
    store.insert("AA", _m(1))
    store.set_note("AA", 1, "first")
    store.set_note("AA", 1, "second")
    row = store.conn.execute(
        "SELECT notes FROM measurements WHERE device_address='AA' AND meas_id=1"
    ).fetchone()
    assert row["notes"] == "second"
