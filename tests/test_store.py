import pathlib
import tempfile

import pytest

from glm.protocol.messages import EDCMeasurement
from glm.store import Store


def make_measurement(meas_id: int = 1, dev_mode: int = 1, ref_edge: int = 0,
                     result: float = 1.234, comp1: float = 0.0, comp2: float = 0.0) -> EDCMeasurement:
    return EDCMeasurement(
        ref_edge=ref_edge, dev_mode=dev_mode, laser_on=False, temp_warning=False,
        batt_warning=False, config_units=0, device_status=0,
        meas_id=meas_id, result=result, comp1=comp1, comp2=comp2,
    )


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        s = Store(pathlib.Path(td) / "test.sqlite")
        yield s
        s.close()


def test_insert_returns_true_for_new_row(store):
    assert store.insert("AA:BB", make_measurement(meas_id=1)) is True


def test_insert_dedups_on_same_meas_id(store):
    addr = "AA:BB"
    m = make_measurement(meas_id=1)
    assert store.insert(addr, m) is True
    assert store.insert(addr, m) is False  # same meas_id → dedup
    # Different value at same meas_id is also a dup (PK on meas_id wins)
    assert store.insert(addr, make_measurement(meas_id=1, result=99.0)) is False


def test_insert_separate_devices_no_collision(store):
    m = make_measurement(meas_id=1)
    assert store.insert("AA:BB", m) is True
    assert store.insert("CC:DD", m) is True  # different device, same meas_id


def test_max_meas_id_returns_none_when_empty(store):
    assert store.max_meas_id("AA:BB") is None


def test_max_meas_id_tracks_highest(store):
    addr = "AA:BB"
    store.insert(addr, make_measurement(meas_id=5))
    store.insert(addr, make_measurement(meas_id=10))
    store.insert(addr, make_measurement(meas_id=7))
    assert store.max_meas_id(addr) == 10


def test_insert_history_dedups_by_value_tuple(store):
    addr = "AA:BB"
    # Live capture stores meas_id=100 at result=2.628
    assert store.insert(addr, make_measurement(meas_id=100, result=2.628)) is True
    # Same physical measurement comes back via catchup with a fresh meas_id
    history = make_measurement(meas_id=580, result=2.628)
    assert store.insert_history(addr, history) is False  # dup by value tuple


def test_insert_history_recovers_genuine_new_value(store):
    addr = "AA:BB"
    store.insert(addr, make_measurement(meas_id=100, result=2.628))
    new = make_measurement(meas_id=200, result=3.142)
    assert store.insert_history(addr, new) is True
    # Re-recovering the same historical value returns False
    assert store.insert_history(addr, make_measurement(meas_id=999, result=3.142)) is False


def test_insert_history_treats_different_dev_mode_as_distinct(store):
    addr = "AA:BB"
    # Same result but different mode is a different measurement
    store.insert(addr, make_measurement(meas_id=100, dev_mode=1, result=2.628))
    other_mode = make_measurement(meas_id=200, dev_mode=4, result=2.628)
    assert store.insert_history(addr, other_mode) is True


def test_insert_history_treats_different_ref_edge_as_distinct(store):
    addr = "AA:BB"
    store.insert(addr, make_measurement(meas_id=100, ref_edge=0, result=2.628))
    other_edge = make_measurement(meas_id=200, ref_edge=2, result=2.628)
    assert store.insert_history(addr, other_edge) is True


def test_break_setup_reverts_members_to_singletons(store):
    """break_setup (#9 escape hatch) must strip setup_id, setup_label, and
    setup_status from every member so each row becomes a clean singleton."""
    addr = "AA:BB"
    for mid in (1, 2, 3):
        store.insert(addr, make_measurement(meas_id=mid, result=1.0 + mid),
                     setup_id=42)
    store.conn.execute(
        "UPDATE measurements SET setup_label = 'bottom-of-beam', "
        "setup_status = 'confirmed' WHERE setup_id = 42"
    )
    store.conn.commit()

    affected = store.break_setup(42)
    assert affected == 3

    rows = store.conn.execute(
        "SELECT meas_id, setup_id, setup_label, setup_status "
        "FROM measurements WHERE device_address = ? ORDER BY meas_id",
        (addr,),
    ).fetchall()
    assert len(rows) == 3
    for r in rows:
        assert r["setup_id"] is None
        assert r["setup_label"] is None
        assert r["setup_status"] is None


def test_break_setup_leaves_other_setups_alone(store):
    addr = "AA:BB"
    store.insert(addr, make_measurement(meas_id=1, result=1.0), setup_id=10)
    store.insert(addr, make_measurement(meas_id=2, result=2.0), setup_id=20)
    store.break_setup(10)
    other = store.conn.execute(
        "SELECT setup_id FROM measurements WHERE meas_id = 2"
    ).fetchone()
    assert other["setup_id"] == 20
