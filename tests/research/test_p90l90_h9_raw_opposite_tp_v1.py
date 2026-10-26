"""Causal H9 RAW TP experiment. Research-only invariants for Issue #218."""

from decimal import Decimal as D

from scripts.research import (
    p90l90_h9_raw_opposite_tp_v1 as study,
    p90l90_portfolio_pause_v1 as original,
)


L53 = (D("98"), D("102"), D("1"))


def entry(ts=100, sym="APTUSDT", side="LONG"):
    return {
        "symbol": sym, "side": side, "price": "100",
        "ts": repr(ts), "event_id": f"{sym}:{ts}",
        "trade_id": f"{sym}:t{ts}", "PREV7D": True,
    }


def test_exact_baseline_invariance_under_h9_state():
    reference = original.Portfolio(None)
    candidate = study.TPBook("L53_DYNAMIC_BASELINE")
    ticks = [
        (100, 1, D("100"), (entry(),), (D("90"), D("110"))),
        (101, 2, D("100.5"), (), (D("89"), D("112"))),
        (102, 3, D("102"), (), (D("89"), D("112"))),
    ]
    for ts, seq, px, events, h9 in ticks:
        reference.handle("APTUSDT", ts, seq, px, events, L53)
        candidate.handle_tp("APTUSDT", ts, seq, px, events, L53, h9)
    assert candidate.trades == reference.trades
    assert candidate.finish()["money"] == reference.finish()["money"]
    assert candidate.trades[0]["exit_reason"] == "RESTING_TP"


def test_dynamic_h9_does_not_exit_at_local_l53_tp():
    dynamic = study.TPBook("H9_RAW_DYNAMIC")
    baseline = study.TPBook("L53_DYNAMIC_BASELINE")
    for book in (dynamic, baseline):
        book.handle_tp("APTUSDT", 100, 1, D("100"), (entry(),), L53,
                       (D("90"), D("110")))
        book.handle_tp("APTUSDT", 101, 2, D("102"), (), L53,
                       (D("90"), D("110")))
    assert baseline.pos["APTUSDT"] is None
    assert baseline.trades[0]["exit_reason"] == "RESTING_TP"
    assert dynamic.pos["APTUSDT"] is not None
    assert dynamic.pos["APTUSDT"].tp == D("110")


def test_dynamic_h9_reprices_only_after_completed_bucket():
    dynamic = study.TPBook("H9_RAW_DYNAMIC")
    frozen = study.TPBook("H9_RAW_FIXED_ENTRY")
    for book in (dynamic, frozen):
        book.handle_tp("APTUSDT", 300, 1, D("100"), (entry(300),), L53,
                       (D("90"), D("110")))
        book.handle_tp("APTUSDT", 301, 2, D("101"), (), L53,
                       (D("90"), D("110")))
        book.handle_tp("APTUSDT", 600, 3, D("101.1"), (), L53,
                       (D("91"), D("102")))
    assert dynamic.pos["APTUSDT"].tp == D("102")
    assert frozen.pos["APTUSDT"].tp == D("110")
    for book in (dynamic, frozen):
        book.handle_tp("APTUSDT", 601, 4, D("102"), (), L53,
                       (D("91"), D("102")))
    assert dynamic.trades[0]["exit_reason"] == "RESTING_TP"
    assert frozen.pos["APTUSDT"] is not None


def test_h9_requires_ready_geometry_at_accepted_entry():
    book = study.TPBook("H9_RAW_DYNAMIC")
    try:
        book.handle_tp("APTUSDT", 100, 1, D("100"), (entry(),), L53, None)
    except ValueError as error:
        assert "causal H9" in str(error)
    else:
        raise AssertionError("missing H9 readiness did not fail closed")


def test_fixed_favorable_threshold_keeps_original_eligibility():
    book = study.TPBook("H9_RAW_FIXED_ENTRY")
    book.handle_tp("APTUSDT", 100, 1, D("100"), (entry(),), L53,
                   (D("90"), D("100.2")))
    assert book.pos["APTUSDT"].tp is None
    book.handle_tp("APTUSDT", 301, 2, D("100.2"), (), L53,
                   (D("91"), D("120")))
    assert book.pos["APTUSDT"].tp is None


def test_conservative_taker_taker_is_not_less_costly_than_resting_maker():
    row = {
        "gross_usdt": "0.2", "entry_fee_usdt": "0.0055",
        "exit_price": "102", "entry_price": "100", "net_usdt": "0.19246",
    }
    assert study.conservative_net(row) < D(row["net_usdt"])


def test_three_scenarios_baseline_and_two_h9_sensitivities():
    assert study.SCENARIOS == (
        "L53_DYNAMIC_BASELINE", "H9_RAW_DYNAMIC", "H9_RAW_FIXED_ENTRY"
    )
    assert study.ROOTS["90d"] != study.ROOTS["29d"]
