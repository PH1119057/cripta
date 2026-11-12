"""#638 tests: one shared LONG/SHORT book and executable 2025 accounting parity."""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path
import sys

# scripts/ is a source-tree namespace (not an installed wheel package).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from scripts.research import p90l90_bull2025_portfolio_v1 as original
from scripts.research import p90l90_unified_depth_three_period_v1 as run


def candidate(symbol: str, side: str, ts: float, seq: int, depth="0") -> dict:
    return {
        "symbol": symbol, "side": side,
        "PREV7D": True, "depth_pct": depth,
        "width_pct": "0.50", "price": "100",
        "event_id": f"{symbol}:{seq}:{side}",
        "ts": repr(ts), "trade_id": f"raw:{symbol}:{seq}",
    }


def test_exact_two_depths_and_one_portfolio_for_all_periods():
    assert set(run.WINDOW) == {"29d", "90d", "183d"}
    assert [run.WINDOW[p]["days"] for p in ("29d", "90d", "183d")] == [29, 90, 183]
    assert len(run.BOOK_NAMES) == 8
    assert len(set(run.BOOK_NAMES)) == 8
    assert run.CASH == ("fixed", "compound_capped")
    assert run.PAUSE == ("NO_PAUSE", "SECOND48")
    assert run.SYMBOLS == ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
    assert run.FIXED_MARGIN.compare(D("200")) == 0
    assert run.START_WALLET.compare(D("1000")) == 0


def test_depth_canonical_inclusive_and_causal_prev7d():
    e = candidate("LTCUSDT", "LONG", 100.0, 1)
    assert run.filter_event(e, "ZERO")
    assert run.filter_event(e, "ORIGINAL_0TO15")
    assert not run.filter_event({**e, "depth_pct": "0.000000001"}, "ZERO")
    assert run.filter_event({**e, "depth_pct": "15"}, "ORIGINAL_0TO15")
    with pytest.raises(ValueError, match="outside frozen"):
        run.filter_event({**e, "depth_pct": "15.00000001"}, "ORIGINAL_0TO15")
    with pytest.raises(ValueError, match="outside frozen"):
        run.filter_event({**e, "depth_pct": "-0.000001"}, "ZERO")
    assert not run.filter_event({**e, "PREV7D": False}, "ZERO")


def test_original_2025_book_same_side_exact_behavior_and_cash():
    uni = run.UnifiedBook("ORIGINAL_0TO15", "fixed", "SECOND48")
    old = original.Book("LONG_ONLY", "SECOND48", "fixed", [])
    state = (D("80"), D("120"), D("40"))
    points = [
        ("LTCUSDT", 1000.0, 1, D("100"), (candidate("LTCUSDT", "LONG", 1000.0, 1),)),
        ("LTCUSDT", 1010.0, 2, D("90"), ()),
        ("DOTUSDT", 1020.0, 1, D("100"), (candidate("DOTUSDT", "LONG", 1020.0, 1),)),
        ("DOTUSDT", 1030.0, 2, D("90"), ()),
    ]
    for symbol, ts, seq, px, events in points:
        uni.handle_unified(symbol, ts, seq, px, events, state, 0)
        old.handle(symbol, ts, seq, px, events, state, 0)
        assert uni.pos[symbol] == old.pos[symbol]
        assert uni.wallet == old.wallet
        assert uni.reserved == old.reserved
        assert uni.counts == old.counts
        assert uni.trades == old.trades
    assert uni.counts["exit_HARD_STOP"] == 2
    assert uni.counts["second48_pause_triggers"] >= 1


def test_one_slot_per_coin_blocks_opposite_position_and_shared_second48():
    book = run.UnifiedBook("ZERO", "fixed", "SECOND48")
    state = (D("80"), D("120"), D("40"))
    long = candidate("LTCUSDT", "LONG", 2000.0, 1)
    short = candidate("LTCUSDT", "SHORT", 2010.0, 2)
    book.handle_unified("LTCUSDT", 2000.0, 1, D("100"), (long,), state, 0)
    book.handle_unified("LTCUSDT", 2010.0, 2, D("100"), (short,), state, 0)
    assert book.counts["accepted"] == 1
    assert book.counts["busy_or_same_tick"] == 1
    assert book.pos["LTCUSDT"].side == "LONG"
    book.handle_unified("LTCUSDT", 2020.0, 3, D("90"), (), state, 0)
    book.handle_unified("DOTUSDT", 2030.0, 1, D("100"),
                        (candidate("DOTUSDT", "SHORT", 2030.0, 1),), state, 1)
    assert book.pos["DOTUSDT"].side == "SHORT"
    book.handle_unified("DOTUSDT", 2040.0, 2, D("110"), (), state, 1)
    assert book.counts["exit_HARD_STOP"] == 2
    assert book.counts["second48_pause_triggers"] == 1
    # Pause is global to BOTH directions. No new APT LONG during embargo.
    book.handle_unified("APTUSDT", 2050.0, 1, D("100"),
                        (candidate("APTUSDT", "LONG", 2050.0, 1),), state, 2)
    assert book.pos["APTUSDT"] is None
    assert book.counts["paused_candidate"] == 1
    assert book.max_slots <= 5


def test_true_free_cash_gate_prevents_fifth_fixed_200_usdt_slot():
    b = run.UnifiedBook("ZERO", "fixed", "NO_PAUSE")
    st = (D("80"), D("120"), D("40"))
    for i, symbol in enumerate(run.SYMBOLS):
        ev = candidate(symbol, "LONG", 5000.0 + i, 1)
        b.handle_unified(symbol, 5000.0 + i, 1, D("100"), (ev,), st, i)
    assert b.counts["accepted"] == 4
    assert b.counts["capital_blocked_candidate"] == 1
    assert b.max_slots == 4
    assert b.reserved.compare(D("800")) == 0
    assert b.wallet < D("1000")
    assert b.wallet - b.reserved < D("200.55")


def test_compound_capped_is_own_path_dependent_reentry():
    b = run.UnifiedBook("ZERO", "compound_capped", "NO_PAUSE")
    st = (D("80"), D("120"), D("40"))
    for i, symbol in enumerate(run.SYMBOLS):
        ev = candidate(symbol, "SHORT", 5100.0 + i, 1)
        b.handle_unified(symbol, 5100.0 + i, 1, D("100"), (ev,), st, i)
    assert b.max_slots <= 5
    assert b.counts["accepted"] == 5
    assert b.margin[run.SYMBOLS[-1]] < D("200")
    assert b.reserved <= b.wallet


def test_checkpoint_roundtrip_preserves_global_positions_and_wallet():
    b = run.UnifiedBook("ZERO", "compound_capped", "SECOND48")
    e = candidate("LTCUSDT", "SHORT", 5120.0, 1)
    b.handle_unified("LTCUSDT", 5120.0, 1, D("100"), (e,),
                     (D("80"), D("120"), D("40")), 0)
    b.last_stops.extend([3900.0, 5000.0])
    b.pause_until = 5000.0 + 48 * 3600
    frozen = run.freeze(b)
    copy = run.UnifiedBook("ZERO", "compound_capped", "SECOND48")
    run.restore(copy, frozen)
    assert run.freeze(copy) == frozen
    assert copy.pos["LTCUSDT"].side == "SHORT"
    assert copy.wallet == b.wallet
    assert copy.margin == b.margin


def test_results_are_scoped_new_data_disk_children_only():
    assert all(
        run.output(period).as_posix().startswith("/data/cripta/research/runs/")
        for period in run.WINDOW
    )
    assert all(run.output(period).name == run.LEAF for period in run.WINDOW)
