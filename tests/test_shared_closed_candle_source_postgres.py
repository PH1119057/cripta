"""Disposable PG16 proof of one canonical candle owner and no overwrite.

The exact production additive migration runs in the trade-lifecycle workflow
before this test. All inserted fixtures roll back. Never uses production DSN.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 only")
T = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
KEY = MarketCandleKey(
    "ms-bybit-mainnet-public-v1",
    "venue-bybit", "inst-bybit-linear-ltcusdt",
    "LTCUSDT", "LINEAR", "5",
)


def _candle(i: int, close: str = "100.5") -> Candle:
    end = T + timedelta(minutes=5*i)
    return Candle(
        "LTCUSDT", "5", end-timedelta(minutes=5), end,
        Decimal("100"), Decimal("101"), Decimal("99"),
        Decimal(close), Decimal("8"),
    )


def test_pg16_shared_candle_one_row_per_source_not_per_strategy() -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_SHARED_CANDLE"),
        conn.transaction(),
    ):
        assert conn.execute(
            "SELECT to_regclass('analytics.closed_candles')::text"
        ).fetchone() == ("analytics.closed_candles",)
        assert conn.execute(
            "SELECT to_regclass('strategy_entry.p90l90_candle_gap_recovery_audit')::text"
        ).fetchone() == ("strategy_entry.p90l90_candle_gap_recovery_audit",)
        conn.execute(
            """INSERT INTO identity.instruments(
                    instrument_id,venue_id,product_category,external_symbol)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT (instrument_id) DO NOTHING""",
            (KEY.instrument_id, KEY.venue_id,
             KEY.product_category, KEY.external_symbol),
        )
        conn.execute("SET LOCAL ROLE cripta")
        assert conn.execute(
            """SELECT has_table_privilege('cripta',
                       'analytics.closed_candles','SELECT,INSERT'),
                      has_table_privilege('cripta',
                       'analytics.closed_candles','UPDATE'),
                      has_table_privilege('cripta',
                       'analytics.closed_candles','DELETE'),
                      has_table_privilege('cripta',
                       'strategy_entry.p90l90_candle_gap_recovery_audit',
                       'SELECT,INSERT'),
                      has_table_privilege('cripta',
                       'strategy_entry.p90l90_candle_gap_recovery_audit',
                       'UPDATE')"""
        ).fetchone() == (True, False, False, True, False)
        one = SharedClosedCandleStore(conn, KEY)
        two = SharedClosedCandleStore(conn, KEY)
        candles = (_candle(0), _candle(1))
        metadata = dict(
            producer_release_commit="a"*40,
            transport="BYBIT_REST_KLINE", observed_at=T+timedelta(minutes=5),
        )
        assert one.ingest(candles, **metadata) == 2
        assert two.ingest(candles, **metadata) == 0
        assert two.history(through=candles[-1].closed_at, required=2) == candles
        assert conn.execute(
            """SELECT count(*) FROM analytics.closed_candles
                WHERE market_source_id=%s AND instrument_id=%s
                  AND timeframe='5'""",
            (KEY.market_source_id, KEY.instrument_id),
        ).fetchone() == (2,)
        with pytest.raises(RuntimeError, match="IMMUTABLE_SOURCE_CONFLICT"):
            two.ingest((_candle(1, "100.6"),), **metadata)
        # Failed equality check never silently changes original source.
        assert two.history(through=candles[-1].closed_at, required=2) == candles
        raise RuntimeError("ROLLBACK_SHARED_CANDLE")
