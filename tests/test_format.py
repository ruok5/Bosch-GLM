"""Display-precision tests. The TUI and exports pick a precision from user
prefs and dispatch through format_imperial_at — make sure each precision
rounds consistently, including the sub-foot edge case (always show 0')."""
from glm.format import (
    format_imperial, format_imperial_at, format_imperial_eighth,
    format_imperial_inch, format_imperial_quarter,
)


METER_PER_INCH = 0.0254


def _m(inches: float) -> float:
    return inches * METER_PER_INCH


def test_inch_precision_rounds_to_nearest_inch():
    assert format_imperial_inch(_m(43.4)) == "3'-7\""
    assert format_imperial_inch(_m(43.6)) == "3'-8\""
    assert format_imperial_inch(_m(0.4)) == "0'-0\""


def test_half_precision_matches_original_helper():
    # format_imperial is the historical 1/2" helper; dispatcher should match.
    for val in (0.25, 1.0, 2.628, 10.0):
        assert format_imperial_at(val, "1/2") == format_imperial(val)


def test_quarter_precision_matches_original_helper():
    for val in (0.25, 1.0, 2.628, 10.0):
        assert format_imperial_at(val, "1/4") == format_imperial_quarter(val)


def test_eighth_precision_rounds_to_nearest_eighth():
    # 1/8" = 0.125"; test each fraction bucket is reachable
    assert format_imperial_eighth(_m(0)) == "0'-0\""
    assert format_imperial_eighth(_m(0.125)) == "0'-0 1/8\""
    assert format_imperial_eighth(_m(0.25)) == "0'-0 1/4\""
    assert format_imperial_eighth(_m(0.375)) == "0'-0 3/8\""
    assert format_imperial_eighth(_m(0.5)) == "0'-0 1/2\""
    assert format_imperial_eighth(_m(0.625)) == "0'-0 5/8\""
    assert format_imperial_eighth(_m(0.75)) == "0'-0 3/4\""
    assert format_imperial_eighth(_m(0.875)) == "0'-0 7/8\""
    assert format_imperial_eighth(_m(1.0)) == "0'-1\""


def test_dispatcher_falls_back_to_half_on_unknown_precision():
    # 1/2 is the historical default; be forgiving of stale prefs files.
    val = 2.628
    assert format_imperial_at(val, "bogus") == format_imperial(val)


def test_dispatcher_covers_all_four_precisions():
    val = _m(43.8)  # finicky fraction under all precisions
    got_1    = format_imperial_at(val, "1")
    got_half = format_imperial_at(val, "1/2")
    got_qtr  = format_imperial_at(val, "1/4")
    got_8th  = format_imperial_at(val, "1/8")
    # All four should render something feet-inches shaped.
    for s in (got_1, got_half, got_qtr, got_8th):
        assert s.startswith("3'-") and s.endswith("\"")
    # Each finer precision either matches the previous or includes a new
    # fraction marker — never loses information vs. the coarser one.
    # (This is a consistency guarantee, not an exactness guarantee.)
    assert len(got_8th) >= len(got_qtr) >= len(got_half) >= len(got_1) - 1
