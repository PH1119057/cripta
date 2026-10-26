"""Causal chronology and no-flatten invariants for P90L90 Entry-only embargo."""
from decimal import Decimal as D

from scripts.research.p90l90_entry_only_hardstop_embargo_v1 import EntryEmbargo, SCENARIOS

STATE = (D("80"), D("120"), D("1"))


def candidate(symbol, t, side="LONG"):
    return ({
        "symbol": symbol, "price": "100", "side": side, "ts": repr(t),
        "event_id": f"{symbol}:{t}", "trade_id": f"{symbol}:trade",
        "PREV7D": True,
    },)


def test_first_stop_blocks_new_entry_but_does_not_flatten_any_position():
    book = EntryEmbargo(1, 6)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    assert book.count["close_HARD_STOP"] == 1
    assert book.count["pause_triggers"] == 1
    assert book.pos["LTCUSDT"] is not None
    assert book.pending == set()
    book.handle("LTCUSDT", 102, 2, D("99"), (), STATE)
    assert book.pos["LTCUSDT"] is not None
    book.handle("DOTUSDT", 102, 1, D("100"), candidate("DOTUSDT", 102), STATE)
    assert book.pos["DOTUSDT"] is None
    assert book.count["blocked_global_pause"] == 1
    later = 101 + 6 * 3600
    book.handle("DOTUSDT", later, 2, D("100"), candidate("DOTUSDT", later), STATE)
    assert book.pos["DOTUSDT"] is not None
    report = book.finish()
    assert report["counts"].get("close_PORTFOLIO_FLATTEN", 0) == 0
    assert report["counts"]["accepted"] == 3


def test_second_stop_triggers_only_after_two_observed_stops_in_48h():
    book = EntryEmbargo(2, 12)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    assert book.count["pause_triggers"] == 0
    book.handle("DOTUSDT", 102, 1, D("100"), candidate("DOTUSDT", 102), STATE)
    assert book.pos["DOTUSDT"] is not None
    book.handle("LTCUSDT", 103, 2, D("89"), (), STATE)
    assert book.count["close_HARD_STOP"] == 2
    assert book.count["pause_triggers"] == 1
    assert book.pos["DOTUSDT"] is not None
    book.handle("ARBUSDT", 104, 1, D("100"), candidate("ARBUSDT", 104), STATE)
    assert book.pos["ARBUSDT"] is None
    assert book.count["blocked_global_pause"] == 1
    assert book.stops[0]["prior_stops_in_48h_including_current"] == 2
    book.finish()


def test_second_stop_outside_48h_does_not_trigger():
    book = EntryEmbargo(2, 24)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    later = 101 + 48 * 3600 + 1
    book.handle("LTCUSDT", later, 2, D("89"), (), STATE)
    assert book.count["pause_triggers"] == 0
    book.handle("DOTUSDT", later + 1, 1, D("100"),
                candidate("DOTUSDT", later + 1), STATE)
    assert book.pos["DOTUSDT"] is not None
    book.finish()


def test_baseline_and_scenario_matrix():
    assert len(SCENARIOS) == 9
    baseline = EntryEmbargo(0, None)
    baseline.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    baseline.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    baseline.handle("DOTUSDT", 102, 1, D("100"), candidate("DOTUSDT", 102), STATE)
    assert baseline.pos["DOTUSDT"] is not None
    assert baseline.count["pause_triggers"] == 0
    assert baseline.finish()["counts"]["close_HARD_STOP"] == 1


def test_same_tick_exit_never_reenters():
    book = EntryEmbargo(1, 6)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), candidate("APTUSDT", 101), STATE)
    assert book.count["accepted"] == 1
    assert book.count["busy_or_same_tick"] == 1
    assert book.count["close_HARD_STOP"] == 1
    book.finish()
