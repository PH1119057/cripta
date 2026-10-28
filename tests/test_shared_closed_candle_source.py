"""Shared immutable source check: no candle gap, source guess or duplication."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey, verify_closed_window,
)

KEY = MarketCandleKey(
    "ms-bybit-mainnet-public-v1", "venue-bybit",
    "inst-bybit-linear-ltcusdt", "LTCUSDT", "LINEAR", "5",
)


def bar(i: int, *, interval: int = 5) -> Candle:
    close = datetime(2026, 10, 28, 12, 0, tzinfo=UTC) + timedelta(minutes=interval*i)
    return Candle(
        "LTCUSDT", str(interval), close-timedelta(minutes=interval), close,
        Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"),
        Decimal("10"),
    )


def test_h9_108_fives_l53_36_fives() -> None:
    bars = tuple(bar(i) for i in range(108))
    assert len(verify_closed_window(KEY, bars, minimum=108)) == 108
    assert len(verify_closed_window(KEY, bars[-36:], minimum=36)) == 36


def test_missing_or_duplicate_candle_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="GAP_OR_DUPLICATE"):
        verify_closed_window(KEY, (bar(0), bar(2)))
    with pytest.raises(RuntimeError, match="GAP_OR_DUPLICATE"):
        verify_closed_window(KEY, (bar(0), bar(0)))
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        verify_closed_window(KEY, (bar(0),), minimum=108)


def test_wrong_timeframe_and_end_are_not_silent() -> None:
    with pytest.raises(RuntimeError, match="UNVERIFIED_CLOSED"):
        verify_closed_window(KEY, (bar(0, interval=15),))
    with pytest.raises(RuntimeError, match="END_NOT_PROVEN"):
        verify_closed_window(KEY, (bar(0),), through=bar(1).closed_at)


def test_one_common_sql_owner_with_protective_grants() -> None:
    sql = (Path(__file__).resolve().parents[1] /
        "operations/sql/20261028_shared_closed_candle_source.sql"
    ).read_text()
    assert "PRIMARY KEY (market_source_id, instrument_id, timeframe, closed_at)" in sql
    assert "GRANT SELECT, INSERT ON analytics.closed_candles TO cripta;" in sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON analytics.closed_candles FROM cripta;" in sql
    assert "strategy_id" not in sql
    assert "trading_account_id" not in sql
