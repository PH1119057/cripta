"""Tests for exact weekly modeled cash attribution, independent of market replay."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as ch
from scripts.research import p90l90_bull2025_weekly_symbol_cashflow_v1 as cash


def ts(year: int, month: int, day: int, hour: int = 0) -> float:
    return dt.datetime(year, month, day, hour, tzinfo=dt.UTC).timestamp()


def empty_book():
    return {
        "trades": [],
        "pos": dict.fromkeys(ch.old.SYMBOLS),
        "open_fee": {},
        "wallet": ch.old.START_WALLET,
    }


def test_entry_fee_exit_cash_in_different_utc_weeks_and_open_fee():
    book = empty_book()
    book["trades"].append({
        "symbol": "LTCUSDT",
        "entry_ts": str(ts(2025, 4, 6, 23)),
        "exit_ts": str(ts(2025, 4, 7, 1)),
        "gross_usdt": "5",
        "entry_fee_usdt": "0.2",
        "exit_fee_usdt": "0.3",
    })
    book["pos"]["DOTUSDT"] = SimpleNamespace(
        entry_ts=ts(2025, 4, 7, 2),
    )
    book["open_fee"]["DOTUSDT"] = D("0.4")
    flows = cash.portfolio_cash_events(book)
    assert flows["2025-W14"]["LTCUSDT"] == D("-0.2")
    assert flows["2025-W15"]["LTCUSDT"] == D("4.7")
    assert flows["2025-W15"]["DOTUSDT"] == D("-0.4")
    assert len(flows) == 2


def test_original_183day_week_boundaries():
    w = cash.group_weeks()
    assert len(w) == 27
    assert w[0][:4] == ("2025-W14", "2025-04-01", "2025-04-06", 6)
    assert w[-1][:4] == ("2025-W40", "2025-09-29", "2025-09-30", 2)


def test_weekly_16_books_exactly_equal_wallet_checkpoints():
    end = {
        index: {
            ch.key(s, m): {"wallet": ch.old.START_WALLET}
            for s in ch.old.SCENARIOS for m in ch.old.CASH_MODES
        }
        for _, _, _, _, index in cash.group_weeks()
    }
    source = {
        "books": {
            ch.key(s, m): empty_book()
            for s in ch.old.SCENARIOS for m in ch.old.CASH_MODES
        }
    }
    result = cash.assemble(source, end)
    assert len(result) == 16
    assert len(result[ch.key("NO_PAUSE", "fixed")]) == 27
    assert all(
        row["week_cash_delta_usdt"] == "0"
        for row in result[ch.key("NO_PAUSE", "fixed")]
    )
    last = cash.group_weeks()[-1][-1]
    end[last][ch.key("NO_PAUSE", "fixed")]["wallet"] = D("999")
    with pytest.raises(ValueError, match="weekly cash-by-coin"):
        cash.assemble(source, end)


def test_short_and_long_require_independent_full_portfolio_qc():
    assert cash.SIDE_NAMES == {"LONG_ONLY", "SHORT_ONLY"}
    assert (
        cash.STATUS_NAMES["SHORT_ONLY"]
        == "INDEPENDENT_QC_PASS_SAVED_2025_SHORT_16_CASES"
    )
    assert cash.ch.old.START_WALLET == D("1000")
