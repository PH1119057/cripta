"""P90 raw Exit memory has no right to mutate legacy PAPER orders."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


class NeverSql:
    def execute(self, query, params=()):
        raise AssertionError("P90 raw Exit geometry must not mutate ordinary PAPER SQL")


def _bar(close: datetime) -> Candle:
    return Candle(
        symbol="LTCUSDT", timeframe="5",
        opened_at=close-timedelta(minutes=5),
        closed_at=close, open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100"),
        volume=Decimal("2"), is_closed=True,
    )


def test_p90_raw_history_restart_never_reconciles_an_r1_order():
    paper = PaperTradeRuntime(NeverSql())
    old = tuple(_bar(B-timedelta(minutes=5*i)) for i in reversed(range(36)))
    paper.bootstrap_p90_l53_history("LTCUSDT", old)
    assert len(paper._l53_candles["LTCUSDT"]) == 36
    assert paper._l53_candles["LTCUSDT"][-1].closed_at == B
    paper.on_p90_closed_candle(old[-1])
    assert len(paper._l53_candles["LTCUSDT"]) == 36
    paper.on_p90_closed_candle(_bar(B+timedelta(minutes=5)))
    assert paper._l53_candles["LTCUSDT"][-1].closed_at == B+timedelta(minutes=5)


def test_p90_exit_gap_conflict_wrong_timeframe_and_incomplete_seed_fail():
    paper = PaperTradeRuntime(NeverSql())
    old = tuple(_bar(B-timedelta(minutes=5*i)) for i in reversed(range(36)))
    with pytest.raises(RuntimeError, match="needs 36"):
        paper.bootstrap_p90_l53_history("LTCUSDT", old[:35])
    with pytest.raises(RuntimeError, match="not fully restored"):
        paper.on_p90_closed_candle(_bar(B+timedelta(minutes=5)))
    paper.bootstrap_p90_l53_history("LTCUSDT", old)
    with pytest.raises(RuntimeError, match="missing/late"):
        paper.on_p90_closed_candle(_bar(B+timedelta(minutes=10)))
    with pytest.raises(RuntimeError, match="immutable"):
        paper.on_p90_closed_candle(replace(old[-1], close=Decimal("99.5")))
    with pytest.raises(ValueError, match="completed approved 5m"):
        paper.on_p90_closed_candle(replace(old[-1], timeframe="15"))
    with pytest.raises(RuntimeError, match="continuity"):
        paper.bootstrap_p90_l53_history("LTCUSDT", old[:-1]+(old[-2],))
