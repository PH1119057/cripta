"""R07 exact SQL/ACL publication admission on disposable PostgreSQL 16 only."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.r07_publication_provenance import (
    R07PublicationProvenanceUnavailable,
    require_r07_publication_provenance,
)

DSN = os.environ.get("CRIPTA_R07_TEST_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN or "/r07_consumer_ci" not in DSN,
    reason="R07 disposable PostgreSQL16 (r07_consumer_ci) explicitly required",
)
SOURCE, SESSION, PRODUCER, CONSUMER = (
    BYBIT_PUBLIC_MARKET_SOURCE_ID, "b" * 32, "c" * 40, "d" * 40
)
NOW = datetime(2026, 11, 12, 12, 0, 5, tzinfo=UTC)
EVENT = NOW - timedelta(seconds=3)


@pytest.fixture
def db():
    admin = psycopg.connect(DSN, autocommit=True)
    assert admin.info.dbname == "r07_consumer_ci"
    for schema in ("strategy_entry", "analytics"):
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.execute(f"CREATE SCHEMA {schema}")
    admin.execute(
        "DO $$ BEGIN CREATE ROLE cripta; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    admin.execute("GRANT USAGE ON SCHEMA analytics,strategy_entry TO cripta")
    admin.execute("""
      CREATE TABLE analytics.native_public_trade_source_sessions (
        source_session_id text PRIMARY KEY, market_source_id text, state text
      )""")
    admin.execute("""
      CREATE TABLE analytics.market_publications (
        publication_id bigint PRIMARY KEY, source_session_id text,
        market_source_id text, producer_release_commit text,
        published_at timestamptz, as_of_second timestamptz,
        publication_status text
      )""")
    admin.execute("""
      CREATE TABLE analytics.native_public_trade_source_events (
        source_session_id text, frame_number bigint, frame_index int,
        market_source_id text, instrument_id text,
        external_symbol text, exec_id text,
        event_at timestamptz, accepted_at timestamptz,
        PRIMARY KEY(source_session_id, frame_number, frame_index)
      )""")
    admin.execute("""
      CREATE TABLE strategy_entry.p90_scoped_native_consumer_checkpoints (
        strategy_id text, strategy_version text, source_session_id text,
        frame_number bigint, frame_index int,
        bootstrap_publication_id bigint, last_publication_id bigint,
        consumer_release_commit text,
        PRIMARY KEY(strategy_id,strategy_version,source_session_id)
      )""")
    admin.execute("""
      CREATE TABLE strategy_entry.p90_r06_publication_observations (
        strategy_id text, strategy_version text, signal_id text,
        source_session_id text, frame_number bigint, frame_index int,
        publication_id bigint, symbol text, direction text,
        native_exec_id text, event_at timestamptz
      )""")
    for table in (
        "analytics.native_public_trade_source_sessions",
        "analytics.market_publications",
        "analytics.native_public_trade_source_events",
        "strategy_entry.p90_scoped_native_consumer_checkpoints",
        "strategy_entry.p90_r06_publication_observations",
    ):
        admin.execute("GRANT SELECT ON " + table + " TO cripta")

    admin.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions VALUES (%s,%s,'ACTIVE')",
        (SESSION, SOURCE),
    )
    admin.execute(
        "INSERT INTO analytics.market_publications VALUES (45,%s,%s,%s,%s,%s,%s)",
        (SESSION, SOURCE, PRODUCER, EVENT + timedelta(seconds=1),
         EVENT, "WATERMARKS_ONLY"),
    )
    admin.execute(
        "INSERT INTO analytics.native_public_trade_source_events VALUES "
        "(%s,100,2,%s,'inst-bybit-linear-ltcusdt','LTCUSDT','native-100',%s,%s)",
        (SESSION, SOURCE, EVENT, EVENT + timedelta(milliseconds=100)),
    )
    admin.execute(
        "INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints "
        "VALUES (%s,%s,%s,101,0,40,46,%s)",
        (MAIN_ID, VERSION, SESSION, CONSUMER),
    )
    admin.execute(
        "INSERT INTO strategy_entry.p90_r06_publication_observations "
        "VALUES (%s,%s,'signal-100',%s,100,2,45,'LTCUSDT','LONG','native-100',%s)",
        (MAIN_ID, VERSION, SESSION, EVENT),
    )

    runtime = psycopg.connect(DSN, row_factory=dict_row)
    runtime.execute("SET ROLE cripta")
    runtime.execute("SET default_transaction_read_only = on")
    runtime.commit()
    try:
        yield admin, runtime
    finally:
        runtime.close()
        admin.close()


def request(account):
    return {
        "strategy_id": MAIN_ID, "strategy_version": VERSION,
        "signal_id": "signal-100", "symbol": "LTCUSDT",
        "direction": "LONG", "requested_at": NOW,
        "trading_account_id": account,
    }


def test_three_accounts_share_proven_original_without_cross_account_order(db):
    _admin, runtime = db
    assert runtime.execute("SHOW transaction_read_only").fetchone()[
        "transaction_read_only"
    ] == "on"
    for account in ("ta-main", "ta-sub-1", "ta-sub-2", "ta-sub-10"):
        assert require_r07_publication_provenance(runtime, request(account)) == 45
    assert runtime.execute(
        "SELECT count(*) AS n FROM strategy_entry.p90_r06_publication_observations"
    ).fetchone()["n"] == 1
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        runtime.execute(
            "UPDATE strategy_entry.p90_r06_publication_observations SET signal_id='forged'"
        )
    runtime.rollback()


@pytest.mark.parametrize(("table","column","value"), [
    ("analytics.native_public_trade_source_events","exec_id","forged"),
    ("analytics.native_public_trade_source_sessions","state","GAP"),
    ("strategy_entry.p90_scoped_native_consumer_checkpoints",
     "last_publication_id", "44"),
    ("analytics.market_publications","producer_release_commit","invalid"),
])
def test_pg16_original_source_invalidation_blocks_before_order(db,table,column,value):
    admin, runtime = db
    # Only disposable ADMIN edits a fixture. The actual runtime actor stays
    # read-only with no permission to create or modify source evidence.
    admin.execute(f"UPDATE {table} SET {column}=%s", (value,))
    with pytest.raises(R07PublicationProvenanceUnavailable):
        require_r07_publication_provenance(runtime, request("ta-sub-2"))


def test_pg16_dispatch_row_locks_prevent_source_epoch_revocation_mid_commit(db):
    admin, runtime = db
    # Disposable PostgreSQL only. Consumer obtains this lock in its command
    # transaction, so a concurrent source GAP cannot slip between final
    # provenance read and durable queue insertion.
    with psycopg.connect(DSN, autocommit=True) as mutator:
        mutator.execute("SET lock_timeout = '200ms'")
        with admin.transaction():
            assert require_r07_publication_provenance(
                admin, request("ta-sub-9"), lock_source_rows=True,
            ) == 45
            with pytest.raises(psycopg.errors.LockNotAvailable):
                mutator.execute(
                    "UPDATE analytics.native_public_trade_source_sessions "
                    "SET state='GAP' WHERE source_session_id=%s",
                    (SESSION,),
                )
        mutator.execute("SET lock_timeout = '0'")
        mutator.execute(
            "UPDATE analytics.native_public_trade_source_sessions "
            "SET state='GAP' WHERE source_session_id=%s",
            (SESSION,),
        )
    with pytest.raises(
        R07PublicationProvenanceUnavailable, match="R07_SOURCE_PROOF_INVARIANT_BROKEN",
    ):
        require_r07_publication_provenance(runtime, request("ta-sub-9"))
