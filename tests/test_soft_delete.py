import pathlib
import tempfile

import pytest

from glm.protocol.messages import EDCMeasurement
from glm.store import Store


def _m(meas_id: int, result: float = 1.0) -> EDCMeasurement:
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


def test_soft_delete_returns_true_when_row_exists(store):
    store.insert("AA", _m(1))
    assert store.soft_delete("AA", 1) is True


def test_soft_delete_returns_false_for_missing(store):
    assert store.soft_delete("AA", 999) is False


def test_soft_delete_idempotent(store):
    store.insert("AA", _m(1))
    assert store.soft_delete("AA", 1) is True
    # Already deleted; UPDATE matches no rows due to deleted_at IS NULL guard
    assert store.soft_delete("AA", 1) is False


def test_query_excludes_deleted_by_default(store):
    store.insert("AA", _m(1))
    store.insert("AA", _m(2))
    store.insert("AA", _m(3))
    store.soft_delete("AA", 2)
    rows = store.query()
    ids = sorted(r["meas_id"] for r in rows)
    assert ids == [1, 3]


def test_query_includes_deleted_when_requested(store):
    store.insert("AA", _m(1))
    store.insert("AA", _m(2))
    store.soft_delete("AA", 1)
    rows = store.query(include_deleted=True)
    ids = sorted(r["meas_id"] for r in rows)
    assert ids == [1, 2]


def test_undelete_restores_visibility(store):
    store.insert("AA", _m(1))
    store.soft_delete("AA", 1)
    assert store.query() == []
    assert store.undelete("AA", 1) is True
    rows = store.query()
    assert len(rows) == 1


def test_undelete_no_op_when_not_deleted(store):
    store.insert("AA", _m(1))
    assert store.undelete("AA", 1) is False


def test_set_station_label(store):
    store.insert("AA", _m(1))
    assert store.set_station_label("AA", 1, "bottom-of-beam") is True
    rows = store.query()
    assert rows[0]["station_label"] == "bottom-of-beam"


def test_confirm_station_marks_all_members(store):
    sid = 1000
    store.insert("AA", _m(1), station_id=sid)
    store.insert("AA", _m(2), station_id=sid)
    store.insert("AA", _m(3), station_id=sid)
    assert store.confirm_station(sid) == 3
    rows = store.query(station_id=sid)
    assert all(r["station_status"] == "confirmed" for r in rows)


def test_query_excludes_drafts_when_asked(store):
    sid = 1000
    store.insert("AA", _m(1), station_id=sid)
    store.insert("AA", _m(2), station_id=sid)
    store.insert("AA", _m(3))  # not in any station
    # Default include_drafts=True returns all 3
    assert len(store.query()) == 3
    # include_drafts=False keeps the standalone row but excludes the draft station
    rows = store.query(include_drafts=False)
    ids = sorted(r["meas_id"] for r in rows)
    assert ids == [3]
    # After confirming, the station rows show up too
    store.confirm_station(sid)
    assert len(store.query(include_drafts=False)) == 3


def test_recent_stations_aggregates(store):
    import time
    store.insert("AA", _m(1, result=1.1), station_id=1000); time.sleep(0.005)
    store.insert("AA", _m(2, result=1.2), station_id=1000); time.sleep(0.005)
    store.insert("AA", _m(3, result=1.3), station_id=2000)
    rows = store.recent_stations()
    assert len(rows) == 2
    # newest-first by first_at
    assert rows[0]["station_id"] == 2000
    assert rows[1]["station_id"] == 1000
    assert rows[0]["member_count"] == 1
    assert rows[1]["member_count"] == 2


def test_station_members_sorted_by_z(store):
    sid = 1000
    store.insert("AA", _m(1, result=2.5), station_id=sid)
    store.insert("AA", _m(2, result=1.0), station_id=sid)
    store.insert("AA", _m(3, result=3.0), station_id=sid)
    members = store.station_members(sid)
    assert [m["result_m"] for m in members] == [1.0, 2.5, 3.0]
