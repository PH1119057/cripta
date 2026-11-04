"""Actual migration and effective-source regression; explicitly disposable PG only."""
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL required")
SQL = Path("operations/sql/20261105_gateway_closed_candle_corrections.sql").read_text()
KEY = ("ms-bybit-mainnet-public-v1", "inst-bybit-linear-aptusdt", "5",
       datetime(2026, 11, 4, 22, 5, tzinfo=UTC))


def test_guarded_correction_retains_original_is_idempotent_and_shared():
    import psycopg

    from bybit_workbench.domain.models import Candle
    from bybit_workbench.market_data.shared_closed_candles import (
        MarketCandleKey,
        SharedClosedCandleStore,
    )

    with psycopg.connect(DSN, autocommit=True) as c:
        assert c.info.dbname != "cripta", "production DB forbidden"
        c.execute(
            "INSERT INTO identity.instruments"
            "(instrument_id,venue_id,product_category,external_symbol) "
            "VALUES (%s,'venue-bybit','LINEAR','APTUSDT') ON CONFLICT DO NOTHING",
            (KEY[1],),
        )
        where = "market_source_id=%s AND instrument_id=%s AND timeframe=%s AND closed_at=%s"
        c.execute(
            "INSERT INTO analytics.closed_candles"
            "(market_source_id,venue_id,instrument_id,external_symbol,product_category,"
            "timeframe,opened_at,closed_at,open,high,low,close,volume,source_transport,"
            "producer_release_commit,observed_at) VALUES "
            "(%s,'venue-bybit',%s,'APTUSDT','LINEAR',%s,%s,%s,"
            ".6391,.6395,.638,.6393,16987.93,'BYBIT_REST_KLINE',%s,%s)",
            (*KEY[:3], KEY[3]-timedelta(minutes=5), KEY[3],
             "768b2423e89f161b2195526f34e5320dc0687a84",
             datetime(2026, 11, 4, 22, 5, 0, 172217, tzinfo=UTC)),
        )
        try:
            original = c.execute(
                "SELECT to_jsonb(c) FROM analytics.closed_candles c WHERE " + where, KEY,
            ).fetchone()[0]
            # Gate ON prevents a first correction, never silently changes history.
            c.execute("UPDATE control.execution_gates SET enabled=1 WHERE mode='mainnet'")
            with pytest.raises(psycopg.errors.RaiseException, match="DISARMED_FLAT"):
                c.execute(SQL)
            c.execute("ROLLBACK")
            c.execute("UPDATE control.execution_gates SET enabled=0 WHERE mode='mainnet'")
            c.execute(SQL)
            c.execute(SQL)  # installer rerun is an exact no-op
            assert c.execute(
                "SELECT to_jsonb(c) FROM analytics.closed_candles c WHERE " + where, KEY,
            ).fetchone()[0] == original
            assert c.execute(
                "SELECT original_record FROM analytics.closed_candle_corrections WHERE "
                + where, KEY,
            ).fetchone()[0] == original
            c.execute("SET ROLE cripta")
            key = MarketCandleKey(KEY[0], "venue-bybit", KEY[1], "APTUSDT", "LINEAR", "5")
            corrected = Candle("APTUSDT", "5", KEY[3]-timedelta(minutes=5), KEY[3],
                               Decimal(".6391"), Decimal(".6395"), Decimal(".638"),
                               Decimal(".6394"), Decimal("17006.02"))
            store = SharedClosedCandleStore(c, key)
            assert store.history(through=KEY[3], required=1) == (corrected,)
            assert store.ingest(
                (corrected,), producer_release_commit="a"*40,
                transport="BYBIT_REST_KLINE", observed_at=datetime.now(UTC),
            ) == 0
            assert c.execute(
                "SELECT has_table_privilege(current_user,"
                "'analytics.closed_candle_corrections','INSERT'),"
                "has_table_privilege(current_user,'analytics.closed_candles','UPDATE')"
            ).fetchone() == (False, False)
        finally:
            c.execute("ROLLBACK")
            c.execute("RESET ROLE")
            c.execute("UPDATE control.execution_gates SET enabled=0 WHERE mode='mainnet'")
            c.execute("DELETE FROM analytics.closed_candle_corrections WHERE " + where, KEY)
            c.execute("DELETE FROM analytics.closed_candles WHERE " + where, KEY)


def test_migration_has_no_original_history_or_financial_mutation():
    assert "UPDATE analytics.closed_candles" not in SQL
    assert "DELETE FROM analytics.closed_candles" not in SQL
    assert "UPDATE control." not in SQL
    assert "UPDATE strategy_entry." not in SQL
    assert "UPDATE runtime." not in SQL
