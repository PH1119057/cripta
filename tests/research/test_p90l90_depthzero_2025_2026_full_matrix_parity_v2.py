"""2025 frozen Book vs 2026 native EntryEmbargo behavioral equivalence.

This probes the actual original 2025 Book.handle/on_trade sequence, not merely
trade-ledger rescoring. Both scenarios receive the same synthetic native ticks,
including two hardstops inside 48h and a blocked new Entry.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.research import p90l90_bull2025_portfolio_v1 as old
from scripts.research import p90l90_depthzero_prev7d_full_matrix_v2 as new


@pytest.mark.parametrize("second48", [False, True])
def test_original_2025_entry_exit_stop_pause_and_100usd_fees(
    monkeypatch: pytest.MonkeyPatch, second48: bool
):
    monkeypatch.setattr(old, "START_WALLET", D("1000000000000"))
    monkeypatch.setattr(old, "FIXED_MARGIN", D("20"))
    monkeypatch.setattr(old, "LEVERAGE", D("5"))
    code = "SECOND48" if second48 else "NO_PAUSE"
    book_2025 = old.Book("LONG_ONLY", code, "fixed", [])
    book_2026 = new.pause.EntryEmbargo(2, 48) if second48 else (
        new.pause.EntryEmbargo(0, None)
    )
    state = (D("80"), D("120"), D("40"))
    events = (
        ("LTCUSDT", 100000.0, 1, D("100"), True),
        ("LTCUSDT", 100060.0, 2, D("90"), False),
        ("DOTUSDT", 100070.0, 1, D("100"), True),
        ("DOTUSDT", 100130.0, 2, D("90"), False),
        ("APTUSDT", 100200.0, 1, D("100"), True),
        ("ARBUSDT", 100130.0 + 48 * 3600 + 1, 1, D("100"), True),
    )
    for symbol, ts, seq, price, opening in events:
        candidate = ({
            "symbol": symbol, "side": "LONG", "PREV7D": True,
            "price": str(price), "ts": repr(ts),
            "event_id": f"{symbol}:{seq}:{ts}",
            "trade_id": f"src-{symbol}-{seq}",
        },) if opening else ()
        book_2025.handle(symbol, ts, seq, price, candidate, state, 0)
        book_2026.handle(symbol, ts, seq, price, candidate, state)
        assert book_2025.pos[symbol] == book_2026.pos[symbol]

    assert book_2025.counts["accepted"] == book_2026.count["accepted"]
    assert book_2025.counts["closed"] == book_2026.count["closed"] == 2
    assert book_2025.counts["exit_HARD_STOP"] == book_2026.count["close_HARD_STOP"] == 2
    assert [(r["candidate_event_id"], r["exit_reason"])
            for r in book_2025.trades] == [
                (r["candidate_event_id"], r["exit_reason"])
                for r in book_2026.trades
            ]
    assert book_2025.pause_until == book_2026.pause_until
    if second48:
        assert book_2025.counts["paused_candidate"] == 1
        assert book_2026.count["blocked_global_pause"] == 1
        assert book_2025.counts["accepted"] == 3
    else:
        assert book_2025.counts["accepted"] == 4
    for row_old, row_native_10 in zip(book_2025.trades, book_2026.trades):
        row_100 = new.comparable_100_trade(row_native_10)
        for field in ("entry_price", "exit_price", "entry_ts", "exit_ts",
                      "exit_reason", "candidate_event_id", "maker_exit",
                      "gross_usdt", "entry_fee_usdt", "exit_fee_usdt", "net_usdt"):
            assert row_old[field] == row_100[field]
