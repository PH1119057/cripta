"""Actual migration and effective-source regression; explicitly disposable PG only."""
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

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
        schema = "gateway_fix_" + uuid4().hex
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta")
        c.execute(f"CREATE TABLE {schema}.closed_candles "
                  "(LIKE analytics.closed_candles INCLUDING ALL)")
        c.execute(f"GRANT SELECT,INSERT ON {schema}.closed_candles TO cripta")
        c.execute(f"CREATE TABLE {schema}.execution_gates(mode text,enabled integer)")
        c.execute(f"INSERT INTO {schema}.execution_gates VALUES ('mainnet',0)")
        c.execute(f"CREATE TABLE {schema}.position_ownership(state text)")
        c.execute(f"CREATE TABLE {schema}.trade_commands(state text)")
        migration = SQL.replace("analytics.", schema + ".").replace(
            "control.execution_gates", schema + ".execution_gates",
        ).replace("runtime.position_ownership", schema + ".position_ownership").replace(
            "runtime.trade_commands", schema + ".trade_commands",
        )
        where = "market_source_id=%s AND instrument_id=%s AND timeframe=%s AND closed_at=%s"
        c.execute(
            f"INSERT INTO {schema}.closed_candles"
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
                f"SELECT to_jsonb(c) FROM {schema}.closed_candles c WHERE " + where, KEY,
            ).fetchone()[0]
            # Gate ON prevents a first correction, never silently changes history.
            c.execute(f"UPDATE {schema}.execution_gates SET enabled=1 WHERE mode='mainnet'")
            with pytest.raises(psycopg.errors.RaiseException, match="DISARMED_FLAT"):
                c.execute(migration)
            c.execute("ROLLBACK")
            c.execute(f"UPDATE {schema}.execution_gates SET enabled=0 WHERE mode='mainnet'")
            c.execute(f"INSERT INTO {schema}.position_ownership VALUES ('OPEN')")
            with pytest.raises(psycopg.errors.RaiseException, match="DISARMED_FLAT"):
                c.execute(migration)
            c.execute("ROLLBACK")
            c.execute(f"DELETE FROM {schema}.position_ownership")
            c.execute(f"UPDATE {schema}.closed_candles SET close=.6392")
            with pytest.raises(psycopg.errors.RaiseException, match="PRECONDITION_DRIFT"):
                c.execute(migration)
            c.execute("ROLLBACK")
            c.execute(f"UPDATE {schema}.closed_candles SET close=.6393")
            c.execute(migration)
            c.execute(migration)  # installer rerun is an exact no-op
            assert c.execute(
                f"SELECT to_jsonb(c) FROM {schema}.closed_candles c WHERE " + where, KEY,
            ).fetchone()[0] == original
            assert c.execute(
                f"SELECT original_record FROM {schema}.closed_candle_corrections WHERE "
                + where, KEY,
            ).fetchone()[0] == original
            c.execute("SET ROLE cripta")
            key = MarketCandleKey(KEY[0], "venue-bybit", KEY[1], "APTUSDT", "LINEAR", "5")
            corrected = Candle("APTUSDT", "5", KEY[3]-timedelta(minutes=5), KEY[3],
                               Decimal(".6391"), Decimal(".6395"), Decimal(".638"),
                               Decimal(".6394"), Decimal("17006.02"))
            class IsolatedSource:
                def execute(self, query, args=()):
                    return c.execute(query.replace("analytics.", schema + "."), args)
                def transaction(self): return c.transaction()
                def cursor(self): return c.cursor()

            store = SharedClosedCandleStore(IsolatedSource(), key)
            assert store.history(through=KEY[3], required=1) == (corrected,)
            assert store.ingest(
                (corrected,), producer_release_commit="a"*40,
                transport="BYBIT_REST_KLINE", observed_at=datetime.now(UTC),
            ) == 0
            assert c.execute(
                "SELECT has_table_privilege(current_user,"
                f"'{schema}.closed_candle_corrections','INSERT'),"
                f"has_table_privilege(current_user,'{schema}.closed_candles','UPDATE')"
            ).fetchone() == (False, False)
        finally:
            c.execute("ROLLBACK")
            c.execute("RESET ROLE")
            c.execute(f"DROP SCHEMA {schema} CASCADE")


def test_migration_has_no_original_history_or_financial_mutation():
    assert "UPDATE analytics.closed_candles" not in SQL
    assert "DELETE FROM analytics.closed_candles" not in SQL
    assert "UPDATE control." not in SQL
    assert "UPDATE strategy_entry." not in SQL
    assert "UPDATE runtime." not in SQL
