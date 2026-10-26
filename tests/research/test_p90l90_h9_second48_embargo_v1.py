"""Research #224: combined H9 RAW TP and actual second Hard Stop pause."""
from decimal import Decimal as D

import pytest

from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as oldpause
from scripts.research import p90l90_h9_raw_opposite_tp_v1 as oldh9
from scripts.research import p90l90_h9_second48_embargo_v1 as study

L53 = (D("98"), D("102"), D("1"))
H9 = (D("90"), D("110"))


def entry(symbol, ts, side="LONG"):
    return ({
        "symbol": symbol, "side": side, "price": "100",
        "ts": repr(ts), "event_id": f"{symbol}:{ts}",
        "trade_id": f"{symbol}:t{ts}", "PREV7D": True,
    },)


def test_exact_l53_pause_books_match_previous_on_realized_second_stop():
    control = oldpause.EntryEmbargo(2, 48)
    candidate = study.CombinationBook("L53", 2, 48)
    ticks = (
        ("APTUSDT", 100, 1, D("100"), entry("APTUSDT", 100)),
        ("LTCUSDT", 100, 1, D("100"), entry("LTCUSDT", 100)),
        ("DOTUSDT", 100, 1, D("100"), entry("DOTUSDT", 100)),
        ("APTUSDT", 101, 2, D("89"), ()),
        ("LTCUSDT", 102, 2, D("89"), ()),
        ("ARBUSDT", 103, 1, D("100"), entry("ARBUSDT", 103)),
        ("DOTUSDT", 104, 2, D("100.5"), ()),
    )
    for sym, ts, seq, px, ev in ticks:
        control.handle(sym, ts, seq, px, ev, L53)
        candidate.handle_combined(sym, ts, seq, px, ev, L53, H9)
    assert candidate.trades == control.trades
    assert candidate.count == control.count
    assert candidate.pos["DOTUSDT"] is not None
    assert candidate.pos["ARBUSDT"] is None
    assert candidate.count["pause_triggers"] == 1
    assert candidate.count["close_PORTFOLIO_FLATTEN"] == 0
    assert candidate.finish()["money"] == control.finish()["money"]


def test_exact_h9_no_pause_matches_prior_h9_replay():
    control = oldh9.TPBook("H9_RAW_DYNAMIC")
    candidate = study.CombinationBook("H9", 0, None)
    ticks = (
        (100, 1, D("100"), entry("APTUSDT", 100), H9),
        (101, 2, D("102"), (), H9),
        (102, 3, D("110"), (), H9),
    )
    for ts, seq, px, ev, level in ticks:
        control.handle_tp("APTUSDT", ts, seq, px, ev, L53, level)
        candidate.handle_combined("APTUSDT", ts, seq, px, ev, L53, level)
    assert candidate.trades == control.trades
    assert candidate.finish()["money"] == control.finish()["money"]


def test_h9_with_two_actual_stops_pauses_entries_but_keeps_other_position():
    combo = study.CombinationBook("H9", 2, 48)
    for sym in ("APTUSDT", "LTCUSDT", "DOTUSDT"):
        combo.handle_combined(sym, 100, 1, D("100"), entry(sym, 100), L53, H9)
    combo.handle_combined("APTUSDT", 101, 2, D("89"), (), L53, H9)
    assert combo.count["pause_triggers"] == 0
    combo.handle_combined("LTCUSDT", 102, 2, D("89"), (), L53, H9)
    assert combo.count["pause_triggers"] == 1
    assert combo.pos["DOTUSDT"] is not None
    combo.handle_combined("ARBUSDT", 103, 1, D("100"), entry("ARBUSDT", 103), L53, H9)
    assert combo.pos["ARBUSDT"] is None
    assert combo.count["blocked_global_pause"] == 1
    assert not combo.pending
    assert combo.finish()["counts"]["close_PORTFOLIO_FLATTEN"] == 0


def test_h9_no_geometry_fails_closed():
    combo = study.CombinationBook("H9", 2, 48)
    with pytest.raises(ValueError, match="causally closed H9"):
        combo.handle_combined(
            "APTUSDT", 100, 1, D("100"), entry("APTUSDT", 100), L53, None
        )


def test_second_stop_outside_48_hours_does_not_block():
    combo = study.CombinationBook("H9", 2, 48)
    combo.handle_combined("APTUSDT", 100, 1, D("100"), entry("APTUSDT", 100), L53, H9)
    combo.handle_combined("LTCUSDT", 100, 1, D("100"), entry("LTCUSDT", 100), L53, H9)
    combo.handle_combined("APTUSDT", 101, 2, D("89"), (), L53, H9)
    t = 101 + 48 * 3600 + 1
    combo.handle_combined("LTCUSDT", t, 2, D("89"), (), L53, H9)
    assert combo.count["pause_triggers"] == 0
    combo.handle_combined("DOTUSDT", t + 1, 1, D("100"),
                          entry("DOTUSDT", t + 1), L53, H9)
    assert combo.pos["DOTUSDT"] is not None
    combo.finish()


def test_exact_scenario_matrix_and_references():
    assert len(study.SCENARIOS) == 4
    assert study.SCENARIOS["H9_DYNAMIC_SECOND48"] == ("H9", 2, 48)
    assert study.REFERENCES["L53_SECOND48"] == ("entry_only", "SECOND_48H")
    assert study.REFERENCES["H9_DYNAMIC_NO_PAUSE"] == ("h9_tp", "H9_RAW_DYNAMIC")
