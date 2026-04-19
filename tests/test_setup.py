from glm.setup import (
    DEFAULT_Z_ORDER, PIPE_SIZES, PIPE_SIZE_DEFAULT, PRESET_LABELS,
    MemberAdded, SetupClosed, SetupOpened, SetupTracker,
    format_pipe_label, suggest_labels,
)


def test_preset_labels_first_is_lowest_z():
    assert PRESET_LABELS[0] == "bottom-of-beam"
    assert "bottom-of-pipe" in PRESET_LABELS


def test_default_z_order_excludes_pipe():
    assert "bottom-of-pipe" not in DEFAULT_Z_ORDER
    assert DEFAULT_Z_ORDER[0] == "bottom-of-beam"
    assert DEFAULT_Z_ORDER[-1] == "bottom-of-deck"


def test_pipe_default_is_2_5_inch():
    assert PIPE_SIZE_DEFAULT == '2-1/2"'
    assert PIPE_SIZE_DEFAULT in PIPE_SIZES


def test_format_pipe_label():
    assert format_pipe_label('4"') == 'bottom-of-pipe(4")'
    assert format_pipe_label('2-1/2"') == 'bottom-of-pipe(2-1/2")'


def test_suggest_labels_for_n_members():
    assert suggest_labels(1) == ["bottom-of-beam"]
    assert suggest_labels(3) == ["bottom-of-beam", "bottom-of-purlin", "bottom-of-subpurlin"]
    assert suggest_labels(5) == DEFAULT_Z_ORDER


def test_tracker_groups_within_window():
    t = SetupTracker(idle_window_ms=60_000)
    e1 = t.feed(meas_id=1, ts_ms=0)
    assert any(isinstance(e, SetupOpened) for e in e1)
    assert any(isinstance(e, MemberAdded) for e in e1)
    e2 = t.feed(meas_id=2, ts_ms=10_000)
    assert not any(isinstance(e, SetupClosed) for e in e2)
    assert not any(isinstance(e, SetupOpened) for e in e2)


def test_tracker_closes_on_idle_gap():
    t = SetupTracker(idle_window_ms=5_000)
    t.feed(1, 0)
    t.feed(2, 1_000)
    events = t.feed(3, 10_000)  # 9s gap > 5s window
    closed = [e for e in events if isinstance(e, SetupClosed)]
    opened = [e for e in events if isinstance(e, SetupOpened)]
    assert len(closed) == 1
    assert closed[0].member_meas_ids == [1, 2]
    assert len(opened) == 1
    assert opened[0].setup_id == 10_000


def test_tracker_force_close_emits_event():
    t = SetupTracker()
    t.feed(1, 0)
    t.feed(2, 100)
    ev = t.force_close()
    assert ev is not None
    assert ev.member_meas_ids == [1, 2]
    assert t.is_open is False


def test_tracker_force_close_no_op_when_empty():
    t = SetupTracker()
    assert t.force_close() is None


def test_setup_id_is_first_member_timestamp():
    t = SetupTracker()
    events = t.feed(1, ts_ms=12345)
    opened = [e for e in events if isinstance(e, SetupOpened)][0]
    assert opened.setup_id == 12345


def test_tracker_default_idle_window_is_20s():
    t = SetupTracker()
    assert t.idle_window_ms == 20_000
