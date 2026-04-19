from glm.gestures import ErrorErrorTracker, SoftDeleteTrigger


def test_two_errors_in_window_after_good_fires():
    t = ErrorErrorTracker(window_ms=3000)
    t.on_good(meas_id=10, device_address="AA", ts_ms=0)
    assert t.on_error(ts_ms=1000) is None       # first error: arms
    trigger = t.on_error(ts_ms=2500)            # second error: fires
    assert trigger == SoftDeleteTrigger(meas_id=10, device_address="AA")


def test_errors_outside_window_dont_fire():
    t = ErrorErrorTracker(window_ms=3000)
    t.on_good(10, "AA", 0)
    assert t.on_error(1000) is None
    # Second error at 5000 — beyond 3s window from first error at 1000
    assert t.on_error(5000) is None
    # But we're now armed at 5000, so a 3rd error within window fires
    assert t.on_error(7000) == SoftDeleteTrigger(meas_id=10, device_address="AA")


def test_good_event_resets_partial_sequence():
    t = ErrorErrorTracker(window_ms=3000)
    t.on_good(10, "AA", 0)
    t.on_error(500)              # arm
    t.on_good(11, "AA", 1000)   # reset; new last_good is 11
    # Next two errors should target meas 11, not 10
    t.on_error(2000)             # arm
    trigger = t.on_error(2500)
    assert trigger == SoftDeleteTrigger(meas_id=11, device_address="AA")


def test_no_trigger_without_prior_good():
    t = ErrorErrorTracker(window_ms=3000)
    assert t.on_error(0) is None
    assert t.on_error(500) is None  # no prior good = no target


def test_reset_clears_state():
    t = ErrorErrorTracker(window_ms=3000)
    t.on_good(10, "AA", 0)
    t.on_error(500)
    t.reset()
    # After reset, an error doesn't fire (no last_good)
    assert t.on_error(1000) is None


def test_after_firing_state_is_consumed():
    t = ErrorErrorTracker(window_ms=3000)
    t.on_good(10, "AA", 0)
    t.on_error(500)
    assert t.on_error(1000) is not None
    # Next error pair without a fresh good shouldn't fire — last_good was consumed
    assert t.on_error(2000) is None
    assert t.on_error(2500) is None
