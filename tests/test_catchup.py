"""Catchup-flow tests. Exercises glm.cli._request_history_packet and _catchup
against a stubbed BleakClient + queue. The regression that prompted these
tests (#5 Offline measurement recovery regression) was that live autosync
frames leaking into the catchup queue were consumed as if they were
responses to the current listIndex probe, causing real history entries to
be dropped on the floor."""
import asyncio
import pathlib
import tempfile

import pytest

from glm import cli
from glm.protocol.messages import EDCMeasurement
from glm.store import Store


def _mk(meas_id=1, dev_mode=1, result=1.234, ref_edge=0):
    return EDCMeasurement(
        ref_edge=ref_edge, dev_mode=dev_mode, laser_on=False, temp_warning=False,
        batt_warning=False, config_units=0, device_status=0,
        meas_id=meas_id, result=result, comp1=0.0, comp2=0.0,
    )


class FakeClient:
    """Stub BleakClient-ish. On write_gatt_char, pushes the next scripted
    response onto the queue (simulating the device replying). Collects the
    write bytes for assertion."""
    def __init__(self, queue: asyncio.Queue, scripted_responses):
        self.queue = queue
        self.scripted = list(scripted_responses)
        self.writes = []
        self.address = "AA:BB:CC:DD:EE:FF"

    async def write_gatt_char(self, _uuid, data, _response):
        self.writes.append(bytes(data))
        if self.scripted:
            nxt = self.scripted.pop(0)
            if nxt is not None:
                self.queue.put_nowait(nxt)


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        s = Store(pathlib.Path(td) / "test.sqlite")
        yield s
        s.close()


def test_request_history_packet_drains_stale_entries():
    """Stale frames queued before the request must not be returned as the
    response. _drain is the defensive seam; if someone re-adds a bad tee,
    drain at least keeps each probe's correlation correct."""
    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        queue.put_nowait(_mk(meas_id=999, result=99.99))  # stale live leak
        real_response = _mk(meas_id=10, result=2.628)
        client = FakeClient(queue, [real_response])
        got = await cli._request_history_packet(client, queue,
                                                 list_idx=1, indicator=0)
        assert got is real_response
        assert got.meas_id == 10
        assert queue.empty()
    asyncio.run(scenario())


def test_catchup_recovers_offline_measurement(store, monkeypatch):
    """The #5 scenario: user shoots before BT connects, measurement lands in
    device history at listIndex 1. Catchup should fetch it and call
    insert_history, recovering it into the local store."""
    monkeypatch.setattr(cli, "CATCHUP_STARTUP_DELAY_S", 0.0)

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        offline = _mk(meas_id=42, result=3.1415)
        end = _mk(meas_id=0, dev_mode=0, result=0.0)  # dev_mode=0 stops scan
        client = FakeClient(queue, [offline, end])
        await cli._catchup(client, store, client.address, queue,
                            offset_in=0.0, state=None)
    asyncio.run(scenario())

    rows = store.conn.execute(
        "SELECT meas_id, result_m FROM measurements "
        "WHERE device_address=?", ("AA:BB:CC:DD:EE:FF",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["meas_id"] == 42
    assert rows[0]["result_m"] == pytest.approx(3.1415)


def test_catchup_dedups_when_already_present(store, monkeypatch):
    """If the offline measurement already got stored via live autosync
    replay, catchup should see the value-tuple match and skip re-inserting."""
    monkeypatch.setattr(cli, "CATCHUP_STARTUP_DELAY_S", 0.0)
    addr = "AA:BB:CC:DD:EE:FF"
    store.insert(addr, _mk(meas_id=42, result=3.1415))

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        same = _mk(meas_id=99, result=3.1415)  # fresh meas_id, same value
        end = _mk(meas_id=0, dev_mode=0, result=0.0)
        client = FakeClient(queue, [same, end])
        await cli._catchup(client, store, addr, queue,
                            offset_in=0.0, state=None)
    asyncio.run(scenario())

    n = store.conn.execute(
        "SELECT COUNT(*) AS n FROM measurements WHERE device_address=?",
        (addr,),
    ).fetchone()["n"]
    assert n == 1  # live row held; catchup tuple-dedup skipped the second insert


def test_catchup_stops_on_timeout(store, monkeypatch):
    """No response → catchup stops walking listIndex and returns cleanly."""
    monkeypatch.setattr(cli, "CATCHUP_STARTUP_DELAY_S", 0.0)
    monkeypatch.setattr(cli, "CATCHUP_RESPONSE_TIMEOUT_S", 0.05)

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        client = FakeClient(queue, [])
        await cli._catchup(client, store, "AA:BB:CC:DD:EE:FF", queue,
                            offset_in=0.0, state=None)
    asyncio.run(scenario())

    n = store.conn.execute("SELECT COUNT(*) AS n FROM measurements").fetchone()["n"]
    assert n == 0
