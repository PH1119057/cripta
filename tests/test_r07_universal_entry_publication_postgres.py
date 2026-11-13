"""R07 exact SQL/ACL publication admission on disposable PostgreSQL 16 only."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.r07_publication_provenance import (
    R07PublicationProvenanceUnavailable,
    require_r07_dispatch_epoch_guard,
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
        source_session_id text PRIMARY KEY, market_source_id text, state text,
        producer_release_commit text
      )""")
    admin.execute("""
      CREATE TABLE analytics.market_publications (
        publication_id bigint PRIMARY KEY, source_session_id text,
        market_source_id text, producer_release_commit text,
        published_at timestamptz, as_of_second timestamptz,
        publication_status text, native_watermarks jsonb
      )""")
    admin.execute("""
      CREATE TABLE analytics.native_public_trade_source_events (
        source_session_id text, frame_number bigint, frame_index int,
        market_source_id text, instrument_id text,
        external_symbol text, exec_id text, exchange_sequence bigint,
        event_at timestamptz, accepted_at timestamptz,
        PRIMARY KEY(source_session_id, frame_number, frame_index)
      )""")
    admin.execute("""
      CREATE TABLE strategy_entry.p90_scoped_native_consumer_checkpoints (
        strategy_id text, strategy_version text, source_session_id text,
        entry_plan_fingerprint text, exit_plan_fingerprint text,
        producer_release_commit text,
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
        "INSERT INTO analytics.native_public_trade_source_sessions "
        "(source_session_id,market_source_id,state,producer_release_commit) "
        "VALUES (%s,%s,'ACTIVE',%s)",
        (SESSION, SOURCE, PRODUCER),
    )
    admin.execute(
        "INSERT INTO analytics.market_publications "
        "(publication_id,source_session_id,market_source_id,producer_release_commit,"
        "published_at,as_of_second,publication_status,native_watermarks) "
        "VALUES (45,%s,%s,%s,%s,%s,%s,%s::jsonb)",
        (SESSION, SOURCE, PRODUCER, EVENT + timedelta(seconds=1),
         EVENT, "WATERMARKS_ONLY", json.dumps({
             "LTCUSDT": {
                 "instrument_id": "inst-bybit-linear-ltcusdt",
                 "frame_number": 100,
                 "frame_index": 2,
                 "exchange_sequence": 123456,
                 "exec_id": "native-100",
             },
         })),
    )
    admin.execute(
        "INSERT INTO analytics.native_public_trade_source_events VALUES "
        "(%s,100,2,%s,'inst-bybit-linear-ltcusdt','LTCUSDT','native-100',123456,%s,%s)",
        (SESSION, SOURCE, EVENT, EVENT + timedelta(milliseconds=100)),
    )
    admin.execute(
        "INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints "
        "VALUES (%s,%s,%s,%s,%s,%s,101,0,40,46,%s)",
        (MAIN_ID, VERSION, SESSION, "e" * 64, "f" * 64, PRODUCER, CONSUMER),
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
        "entry_plan_fingerprint": "e" * 64,
        "exit_plan_fingerprint": "f" * 64,
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
    ("strategy_entry.p90_scoped_native_consumer_checkpoints",
     "entry_plan_fingerprint", "0" * 64),
    ("strategy_entry.p90_scoped_native_consumer_checkpoints",
     "exit_plan_fingerprint", "1" * 64),
    ("strategy_entry.p90_scoped_native_consumer_checkpoints",
     "producer_release_commit", "a" * 40),
    ("analytics.native_public_trade_source_sessions",
     "producer_release_commit", "a" * 40),
])
def test_pg16_original_source_invalidation_blocks_before_order(db,table,column,value):
    admin, runtime = db
    # Only disposable ADMIN edits a fixture. The actual runtime actor stays
    # read-only with no permission to create or modify source evidence.
    admin.execute(f"UPDATE {table} SET {column}=%s", (value,))
    with pytest.raises(R07PublicationProvenanceUnavailable):
        require_r07_publication_provenance(runtime, request("ta-sub-2"))


@pytest.mark.parametrize(("field", "forged"), [
    ("entry_plan_fingerprint", "0" * 64),
    ("exit_plan_fingerprint", "1" * 64),
])
def test_pg16_original_cannot_authorize_changed_financial_plan(db, field, forged):
    _admin, runtime = db
    changed = request("ta-sub-2")
    changed[field] = forged
    with pytest.raises(R07PublicationProvenanceUnavailable, match="INVARIANT_BROKEN"):
        require_r07_publication_provenance(runtime, changed)


def test_pg16_select_only_final_proof_rejects_gap_between_preflight_and_dispatch(db):
    admin, runtime = db
    # The Consumer's source role has SELECT, never UPDATE. PostgreSQL
    # FOR SHARE would require UPDATE on these source-owned tables.
    assert admin.execute(
        "SELECT has_table_privilege('cripta', %s, 'UPDATE')",
        ("analytics.native_public_trade_source_sessions",),
    ).fetchone()[0] is False
    assert require_r07_publication_provenance(runtime, request("ta-sub-9")) == 45
    # Independent source writer revokes ACTIVE between Consumer reads.
    # No production database or shared test service is used here.
    admin.execute(
        "UPDATE analytics.native_public_trade_source_sessions "
        "SET state='GAP' WHERE source_session_id=%s",
        (SESSION,),
    )
    with pytest.raises(
        R07PublicationProvenanceUnavailable, match="R07_SOURCE_PROOF_INVARIANT_BROKEN",
    ):
        require_r07_publication_provenance(runtime, request("ta-sub-9"))


def test_pg16_second_active_session_same_market_source_denies_financial_entry(db):
    admin, runtime = db
    assert require_r07_publication_provenance(runtime, request("ta-main")) == 45
    # R06 _one_pin rejects TWO active epochs; R07 must not accept a request
    # from the older session simply because that session remains ACTIVE.
    admin.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions "
        "(source_session_id,market_source_id,state,producer_release_commit) "
        "VALUES (%s,%s,'ACTIVE',%s)",
        ("a" * 32, SOURCE, PRODUCER),
    )
    with pytest.raises(R07PublicationProvenanceUnavailable, match="MISSING_OR_AMBIGUOUS"):
        require_r07_publication_provenance(runtime, request("ta-sub-3"))
    # A GAP-only historical session must not make the real ACTIVE owner ambiguous.
    admin.execute(
        "UPDATE analytics.native_public_trade_source_sessions "
        "SET state='GAP' WHERE source_session_id=%s",
        ("a" * 32,),
    )
    assert require_r07_publication_provenance(runtime, request("ta-sub-3")) == 45


def test_pg16_active_source_session_producer_must_match_publication(db):
    admin, runtime = db
    assert require_r07_publication_provenance(runtime, request("ta-main")) == 45
    admin.execute(
        "UPDATE analytics.native_public_trade_source_sessions "
        "SET producer_release_commit=%s WHERE source_session_id=%s",
        ("a" * 40, SESSION),
    )
    with pytest.raises(R07PublicationProvenanceUnavailable, match="INVARIANT_BROKEN"):
        require_r07_publication_provenance(runtime, request("ta-sub-2"))


@pytest.mark.parametrize(("watermarks", "reason"), [
    ({}, "MISSING"),
    ({"LTCUSDT": {
        "instrument_id": "inst-bybit-linear-ltcusdt",
        "frame_number": 99, "frame_index": 5,
        "exchange_sequence": 123455, "exec_id": "native-99",
    }}, "INVALID"),
    ({"LTCUSDT": {
        "instrument_id": "inst-bybit-linear-ltcusdt",
        "frame_number": 100, "frame_index": 2,
        "exchange_sequence": 123456, "exec_id": "mismatched",
    }}, "INVALID"),
])
def test_pg16_original_native_event_not_covered_by_publication_denies_entry(
    db, watermarks, reason,
):
    admin, runtime = db
    # Only disposable fixture admin may mutate an immutable source row here.
    admin.execute(
        "UPDATE analytics.market_publications SET native_watermarks=%s::jsonb "
        "WHERE publication_id=45",
        (json.dumps(watermarks),),
    )
    with pytest.raises(R07PublicationProvenanceUnavailable, match="WATERMARK_" + reason):
        require_r07_publication_provenance(runtime, request("ta-sub-4"))


def test_pg16_source_read_only_role_cannot_replace_publication_watermarks(db):
    _admin, runtime = db
    assert require_r07_publication_provenance(runtime, request("ta-main")) == 45
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        runtime.execute(
            "UPDATE analytics.market_publications SET native_watermarks='{}'::jsonb"
        )
    runtime.rollback()


def test_pg16_known_unfenced_gap_after_final_proof_before_dispatch_commit(db):
    """Document the remaining cross-owner race without permitting a real order.

    Synthetic dispatch is intentionally a local disposable table, NOT
    runtime.trade_commands or strategy_entry.execution_dispatches. The
    existing SELECT-only final proof cannot lock the independent source
    writer. Producer/Consumer atomic fencing requires an owner-approved
    cross-component coordination contract, not a silent UPDATE grant.
    """
    admin, _readonly = db
    assert admin.execute(
        "SELECT has_table_privilege('cripta', %s, 'UPDATE')",
        ("analytics.native_public_trade_source_sessions",),
    ).fetchone()[0] is False
    admin.execute(
        "CREATE TABLE strategy_entry.r07_unfenced_dispatch_probe "
        "(signal_id text PRIMARY KEY, source_session_id text NOT NULL)"
    )
    admin.execute(
        "GRANT SELECT, INSERT ON strategy_entry.r07_unfenced_dispatch_probe TO cripta"
    )

    # A separate connection stands in for the account-scoped Consumer. It may
    # write ONLY to the synthetic probe, never to original source facts.
    with psycopg.connect(DSN, row_factory=dict_row) as consumer:
        consumer.execute("SET ROLE cripta")
        consumer.commit()
        with consumer.transaction():
            consumer.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            assert require_r07_publication_provenance(
                consumer, request("ta-sub-2"),
            ) == 45
            # A separate source-owned connection can commit GAP while the
            # Consumer transaction remains alive, after final proof.
            admin.execute("SET lock_timeout = '500ms'")
            changed = admin.execute(
                "UPDATE analytics.native_public_trade_source_sessions "
                "SET state='GAP' WHERE source_session_id=%s",
                (SESSION,),
            )
            assert changed.rowcount == 1

            # This harmless stand-in models command insertion *after* the
            # source has become GAP. A further SELECT would reject, but the
            # current R07 command path has no source-row fence after its proof.
            consumer.execute(
                "INSERT INTO strategy_entry.r07_unfenced_dispatch_probe "
                "(signal_id,source_session_id) VALUES (%s,%s)",
                ("signal-100", SESSION),
            )
            with pytest.raises(
                R07PublicationProvenanceUnavailable,
                match="R07_SOURCE_PROOF_INVARIANT_BROKEN",
            ):
                require_r07_publication_provenance(
                    consumer, request("ta-sub-2"),
                )

    assert admin.execute(
        "SELECT count(*) FROM strategy_entry.r07_unfenced_dispatch_probe"
    ).fetchone()[0] == 1
    assert admin.execute(
        "SELECT state FROM analytics.native_public_trade_source_sessions "
        "WHERE source_session_id=%s",
        (SESSION,),
    ).fetchone()[0] == "GAP"


# R07 RACE SPIKE (EVIDENCE ONLY).  This trigger and probe table exist ONLY
# inside the disposable PostgreSQL16 fixture; neither is a production
# migration, a runtime fence or permission to enable financial Entry.

def _install_epoch_fence_spike(admin):
    """Install the checked-in production SQL only in disposable PG16.

    Fixture SELECT-only tests remain independent. This isolated test changes
    no real source ACL: it models the existing canonical SQL's already-granted
    INSERT/UPDATE rights for the shared runtime role before executing the
    postgres-owned DDL. The migration itself grants no source privileges.
    """
    admin.execute(
        "GRANT INSERT,UPDATE ON analytics.native_public_trade_source_sessions TO cripta"
    )
    admin.execute(
        "GRANT UPDATE ON strategy_entry.p90_scoped_native_consumer_checkpoints TO cripta"
    )
    migration = (
        Path(__file__).resolve().parents[1]
        / "operations/sql/20261114_r07_source_epoch_transaction_guard.sql"
    )
    admin.execute(migration.read_text(encoding="utf-8"))
    admin.execute("""
        CREATE TABLE strategy_entry.r07_epoch_dispatch_probe (
            signal_id text PRIMARY KEY, source_session_id text NOT NULL
        )
    """)
    admin.execute(
        "GRANT INSERT, SELECT ON strategy_entry.r07_epoch_dispatch_probe TO cripta"
    )
    assert admin.execute(
        "SELECT tgenabled,tgtype FROM pg_trigger "
        "WHERE tgrelid='analytics.native_public_trade_source_sessions'::regclass "
        "AND tgname='r07_source_epoch_guard_v1'"
    ).fetchone() == ("O", 31)


def _spike_consumer():
    consumer = psycopg.connect(DSN, row_factory=dict_row)
    consumer.execute("SET ROLE cripta")
    consumer.commit()
    return consumer


def _spike_lock_read(connection):
    # Real R07 Consumer source guard (ABI check + shared transaction lock).
    require_r07_dispatch_epoch_guard(connection, request("ta-spike"))


def test_epoch_barrier_spike_source_gap_cannot_commit_before_consumer_dispatch(db):
    admin, _readonly = db
    _install_epoch_fence_spike(admin)
    assert admin.execute(
        "SELECT has_table_privilege('cripta', %s, 'UPDATE')",
        ("analytics.native_public_trade_source_sessions",),
    ).fetchone()[0] is True

    # BEFORE: the unfenced race is preserved by the separate regression test.
    # AFTER prototype: the Consumer holds a shared transaction advisory lock
    # for the actual source proof + synthetic command insertion transaction.
    with _spike_consumer() as consumer, consumer.transaction():
        _spike_lock_read(consumer)
        assert require_r07_publication_provenance(
            consumer, request("ta-sub-2"),
        ) == 45
        admin.execute("SET lock_timeout = '250ms'")
        # Independent source owner cannot commit ACTIVE -> GAP after proof
        # but before synthetic dispatch. The timeout is an expected guard.
        with pytest.raises(psycopg.errors.LockNotAvailable):
            admin.execute(
                "UPDATE analytics.native_public_trade_source_sessions "
                "SET state='GAP' WHERE source_session_id=%s",
                (SESSION,),
            )
        assert admin.execute(
            "SELECT state FROM analytics.native_public_trade_source_sessions "
            "WHERE source_session_id=%s",
            (SESSION,),
        ).fetchone()[0] == "ACTIVE"
        consumer.execute(
            "INSERT INTO strategy_entry.r07_epoch_dispatch_probe "
            "(signal_id,source_session_id) VALUES (%s,%s)",
            ("signal-100", SESSION),
        )

    # The real source owner may revoke as soon as Consumer commits.
    assert admin.execute(
        "UPDATE analytics.native_public_trade_source_sessions "
        "SET state='GAP' WHERE source_session_id=%s",
        (SESSION,),
    ).rowcount == 1
    assert admin.execute(
        "SELECT count(*) FROM strategy_entry.r07_epoch_dispatch_probe"
    ).fetchone()[0] == 1
    with pytest.raises(R07PublicationProvenanceUnavailable):
        require_r07_publication_provenance(_readonly, request("ta-sub-2"))


def test_epoch_barrier_spike_consumer_cannot_prove_while_source_revokes(db):
    admin, _readonly = db
    _install_epoch_fence_spike(admin)
    with _spike_consumer() as consumer:
        with admin.transaction():
            assert admin.execute(
                "UPDATE analytics.native_public_trade_source_sessions "
                "SET state='GAP' WHERE source_session_id=%s",
                (SESSION,),
            ).rowcount == 1
            # Producer holds exclusive epoch lock until its transaction commits.
            with pytest.raises(R07PublicationProvenanceUnavailable, match="LOCK_UNAVAILABLE"), consumer.transaction():
                consumer.execute("SET LOCAL lock_timeout = '250ms'")
                _spike_lock_read(consumer)
            assert admin.execute(
                "SELECT count(*) FROM strategy_entry.r07_epoch_dispatch_probe"
            ).fetchone()[0] == 0
        with consumer.transaction():
            _spike_lock_read(consumer)
            with pytest.raises(
                R07PublicationProvenanceUnavailable, match="INVARIANT_BROKEN",
            ):
                require_r07_publication_provenance(
                    consumer, request("ta-sub-2"),
                )
    assert admin.execute(
        "SELECT state FROM analytics.native_public_trade_source_sessions "
        "WHERE source_session_id=%s",
        (SESSION,),
    ).fetchone()[0] == "GAP"


def test_epoch_barrier_spike_does_not_stop_normal_frame_or_other_market(db):
    admin, _readonly = db
    _install_epoch_fence_spike(admin)
    admin.execute(
        "ALTER TABLE analytics.native_public_trade_source_sessions "
        "ADD COLUMN last_accepted_at timestamptz"
    )
    with _spike_consumer() as consumer, consumer.transaction():
        _spike_lock_read(consumer)
        assert require_r07_publication_provenance(
            consumer, request("ta-sub-1"),
        ) == 45
        admin.execute("SET lock_timeout = '250ms'")
        # Normal journaling updates no source epoch/ABI and cannot be stalled.
        assert admin.execute(
            "UPDATE analytics.native_public_trade_source_sessions "
            "SET last_accepted_at=%s WHERE source_session_id=%s",
            (NOW, SESSION),
        ).rowcount == 1
        # Advisory locks are MarketSource-scoped, not a global ENTRY veto.
        assert admin.execute(
            "INSERT INTO analytics.native_public_trade_source_sessions "
            "(source_session_id,market_source_id,state,producer_release_commit) "
            "VALUES (%s,%s,'ACTIVE',%s)",
            ("a" * 32, "a-different-market-source", PRODUCER),
        ).rowcount == 1
        assert require_r07_publication_provenance(
            consumer, request("ta-sub-1"),
        ) == 45



def test_pg16_prod_source_guard_missing_or_disabled_denies_before_dispatch(db):
    admin, _runtime = db
    with (
        _spike_consumer() as consumer,
        consumer.transaction(),
        pytest.raises(R07PublicationProvenanceUnavailable, match="GUARD_"),
    ):
        require_r07_dispatch_epoch_guard(consumer, request("ta-sub-1"))

    _install_epoch_fence_spike(admin)
    admin.execute(
        "ALTER TABLE analytics.native_public_trade_source_sessions "
        "DISABLE TRIGGER r07_source_epoch_guard_v1"
    )
    with (
        _spike_consumer() as consumer,
        consumer.transaction(),
        pytest.raises(
            R07PublicationProvenanceUnavailable, match="GUARD_MISSING_OR_DISABLED",
        ),
    ):
        require_r07_dispatch_epoch_guard(consumer, request("ta-sub-2"))


def test_pg16_prod_source_guard_prevents_disable_trigger_ddl_after_lock(db):
    admin, _runtime = db
    _install_epoch_fence_spike(admin)
    with _spike_consumer() as consumer, consumer.transaction():
        _spike_lock_read(consumer)
        assert require_r07_publication_provenance(
            consumer, request("ta-main"), lock_checkpoint_for_dispatch=True,
        ) == 45
        admin.execute("SET lock_timeout='250ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            admin.execute(
                "ALTER TABLE analytics.native_public_trade_source_sessions "
                "DISABLE TRIGGER r07_source_epoch_guard_v1"
            )
        assert admin.execute(
            "SELECT tgenabled FROM pg_trigger "
            "WHERE tgrelid='analytics.native_public_trade_source_sessions'::regclass "
            "AND tgname='r07_source_epoch_guard_v1'"
        ).fetchone()[0] == "O"


def test_pg16_mutable_r06_checkpoint_cannot_change_under_final_command_proof(db):
    admin, _runtime = db
    _install_epoch_fence_spike(admin)
    with _spike_consumer() as consumer, consumer.transaction():
        _spike_lock_read(consumer)
        assert require_r07_publication_provenance(
            consumer, request("ta-main"), lock_checkpoint_for_dispatch=True,
        ) == 45
        admin.execute("SET lock_timeout='250ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            admin.execute(
                "UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
                "SET entry_plan_fingerprint=%s "
                "WHERE strategy_id=%s AND source_session_id=%s",
                ("0" * 64, MAIN_ID, SESSION),
            )
        consumer.execute(
            "INSERT INTO strategy_entry.r07_epoch_dispatch_probe "
            "(signal_id,source_session_id) VALUES (%s,%s)",
            ("protected-checkpoint", SESSION),
        )
    admin.execute(
        "UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
        "SET entry_plan_fingerprint=%s "
        "WHERE strategy_id=%s AND source_session_id=%s",
        ("0" * 64, MAIN_ID, SESSION),
    )
    with pytest.raises(R07PublicationProvenanceUnavailable, match="INVARIANT_BROKEN"):
        require_r07_publication_provenance(_runtime, request("ta-main"))


def test_pg16_many_accounts_can_hold_shared_source_and_checkpoint_guards(db):
    admin, _runtime = db
    _install_epoch_fence_spike(admin)
    with (
        _spike_consumer() as main,
        _spike_consumer() as sub,
        main.transaction(),
        sub.transaction(),
    ):
        _spike_lock_read(main)
        _spike_lock_read(sub)
        assert require_r07_publication_provenance(
            main, request("ta-main"), lock_checkpoint_for_dispatch=True,
        ) == 45
        assert require_r07_publication_provenance(
            sub, request("ta-sub-10"), lock_checkpoint_for_dispatch=True,
        ) == 45
        admin.execute("SET lock_timeout='250ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            admin.execute(
                "UPDATE analytics.native_public_trade_source_sessions "
                "SET state='GAP' WHERE source_session_id=%s",
                (SESSION,),
            )
        main.execute(
            "INSERT INTO strategy_entry.r07_epoch_dispatch_probe "
            "(signal_id,source_session_id) VALUES (%s,%s)",
            ("main-guard", SESSION),
        )
        sub.execute(
            "INSERT INTO strategy_entry.r07_epoch_dispatch_probe "
            "(signal_id,source_session_id) VALUES (%s,%s)",
            ("sub-guard", SESSION),
        )
    assert admin.execute(
        "SELECT count(*) FROM strategy_entry.r07_epoch_dispatch_probe"
    ).fetchone()[0] == 2
