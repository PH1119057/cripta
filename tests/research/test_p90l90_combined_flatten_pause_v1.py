"""Research-only causal tests: hybrid Hard Stop flatten + Entry embargo."""
from decimal import Decimal as D

from scripts.research import p90l90_combined_flatten_pause_v1 as study
from scripts.research import p90l90_portfolio_pause_v1 as legacy

STATE = (D("80"), D("120"), D("1"))


def candidate(sym, t):
    return ({
        "symbol": sym, "price": "100", "side": "LONG", "ts": repr(t),
        "event_id": f"{sym}:{t}", "trade_id": f"{sym}:trade",
        "PREV7D": True,
    },)


def test_all_nine_independent_scenarios_exist():
    books = study.make_books()
    assert len(books) == len(study.SCENARIOS) == 9
    assert list(books).count("BASELINE") == 1
    assert sum(s.startswith("FLATTEN_") for s in books) == 4
    assert sum(s.startswith("ENTRY_ONLY_") for s in books) == 4


def test_first_flatten_preserves_legacy_tick_chronology():
    new = study.Hybrid(1, 24)
    old = legacy.Portfolio(24)
    for book in (new, old):
        book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
        book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
        book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
        assert "LTCUSDT" in book.pending
        book.handle("DOTUSDT", 102, 1, D("100"), candidate("DOTUSDT", 102), STATE)
        assert book.pos["DOTUSDT"] is None
        book.handle("LTCUSDT", 103, 2, D("99"), (), STATE)
        assert book.trades[-1]["exit_reason"] == "PORTFOLIO_FLATTEN"
        book.handle("DOTUSDT", 101 + 24 * 3600, 2, D("100"),
                    candidate("DOTUSDT", 101 + 24 * 3600), STATE)
    assert new.trades == old.trades
    a, b = new.finish(), old.finish()
    assert a["money"] == b["money"]
    assert a["counts"] == b["counts"]


def test_second_flatten_requires_two_actual_stops_in_48h():
    book = study.Hybrid(2, 48)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("DOTUSDT", 100, 1, D("100"), candidate("DOTUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    assert book.count["global_triggers"] == 0
    assert not book.pending
    assert book.pos["LTCUSDT"] is not None
    assert book.pos["DOTUSDT"] is not None
    book.handle("LTCUSDT", 102, 2, D("89"), (), STATE)
    assert book.count["global_triggers"] == 1
    assert book.pending == {"DOTUSDT"}
    book.handle("ARBUSDT", 103, 1, D("100"), candidate("ARBUSDT", 103), STATE)
    assert book.pos["ARBUSDT"] is None
    book.handle("DOTUSDT", 104, 2, D("99"), (), STATE)
    assert book.count["close_PORTFOLIO_FLATTEN"] == 1
    assert book.trades[-1]["exit_reason"] == "PORTFOLIO_FLATTEN"
    assert book.finish()["counts"]["blocked_global_pause"] == 1


def test_second_threshold_does_not_use_old_or_future_stops():
    book = study.Hybrid(2, 24)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    book.handle("LTCUSDT", 101 + 48 * 3600 + 1, 2, D("89"), (), STATE)
    assert book.count["close_HARD_STOP"] == 2
    assert book.count["global_triggers"] == 0
    assert not book.pending
    assert book.finish()["counts"]["censored_open"] == 0


def test_hybrid_pause_extends_only_on_new_qualifying_actual_hardstop():
    book = study.Hybrid(2, 24)
    for sym in ("APTUSDT", "LTCUSDT", "DOTUSDT"):
        book.handle(sym, 100, 1, D("100"), candidate(sym, 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    book.handle("LTCUSDT", 102, 2, D("89"), (), STATE)
    first_pause_end = book.pause_until
    # Third candidate is blocked, and the remaining DOT position must
    # be flattened on its next native trade rather than at a fake price.
    book.handle("ARBUSDT", 103, 1, D("100"), candidate("ARBUSDT", 103), STATE)
    book.handle("DOTUSDT", 104, 2, D("99"), (), STATE)
    assert book.pause_until == first_pause_end
    assert book.count["close_PORTFOLIO_FLATTEN"] == 1
    assert book.finish()["counts"]["global_triggers"] == 1
