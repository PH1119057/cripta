"""Actual PG16 ACL and all-or-nothing R06 release adoption."""
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from operations.monitoring import p90l90_real_source_bootstrap as private
from operations.monitoring import r06_entry_resume as mod

DSN = os.environ.get("CRIPTA_R06_RECOVERY_TEST_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN or "r06_recovery_ci" not in DSN, reason="explicit disposable R06 PG16 only",
)
TARGET, PRODUCER, SESSION = "b"*40, "c"*40, "d"*32
IDS = (mod.MAIN_ID, mod.SUB_ID)
STATE = {"version": 1, "by_symbol": {"LTCUSDT": {"geometry_at": "2026-11-12T05:30:00+00:00"}}}


@pytest.fixture
def db(monkeypatch):
    admin = psycopg.connect(DSN, autocommit=True)
    assert admin.info.dbname == "r06_recovery_ci"
    for schema in ("strategy_entry", "analytics"):
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.execute(f"CREATE SCHEMA {schema}")
    admin.execute("DO $$ BEGIN CREATE ROLE cripta; "
                  "EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    admin.execute("GRANT USAGE ON SCHEMA analytics,strategy_entry TO cripta")
    admin.execute("CREATE TABLE analytics.native_public_trade_source_sessions "
                  "(source_session_id text PRIMARY KEY,state text,producer_release_commit text)")
    admin.execute("CREATE TABLE analytics.market_publications "
                  "(publication_id bigint PRIMARY KEY,source_session_id text,"
                  "producer_release_commit text)")
    admin.execute("CREATE TABLE strategy_entry.p90_scoped_native_consumer_checkpoints ("
                  "strategy_id text,strategy_version text,entry_plan_fingerprint text,"
                  "exit_plan_fingerprint text,source_session_id text,producer_release_commit text,"
                  "consumer_release_commit text,frame_number bigint,frame_index int,"
                  "last_exec_id text,"
                  "bootstrap_publication_id bigint,last_publication_id bigint,"
                  "bootstrap_source_at timestamptz,causal_state jsonb,"
                  "PRIMARY KEY(strategy_id,strategy_version,source_session_id))")
    admin.execute("GRANT SELECT,UPDATE ON analytics.native_public_trade_source_sessions TO cripta")
    admin.execute("GRANT SELECT ON analytics.market_publications TO cripta")
    admin.execute("GRANT SELECT,INSERT,UPDATE ON "
                  "strategy_entry.p90_scoped_native_consumer_checkpoints TO cripta")
    migration = Path(__file__).resolve().parents[1] / (
        "operations/sql/20261112_r06_checkpoint_release_bridge.sql"
    )
    admin.execute(migration.read_text(encoding="utf-8"))
    admin.execute("INSERT INTO analytics.native_public_trade_source_sessions VALUES (%s,%s,%s)",
                  (SESSION, "ACTIVE", PRODUCER))
    admin.execute("INSERT INTO analytics.market_publications VALUES (10,%s,%s),(20,%s,%s)",
                  (SESSION, PRODUCER, SESSION, PRODUCER))
    for name in IDS:
        admin.execute("INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,100,2,%s,10,20,%s,%s::jsonb)",
                      (name, "v3", "e"*64, "f"*64, SESSION, PRODUCER, mod.APPROVED_BASELINE,
                       "exec-100-2", datetime(2026, 11, 12, tzinfo=UTC), json.dumps(STATE)))
    runtime = psycopg.connect(DSN, autocommit=True)
    runtime.execute("SET ROLE cripta")
    assert runtime.execute("SELECT current_user").fetchone() == ("cripta",)
    signed = []
    monkeypatch.setattr(private, "preflight_p90_real_private_flat_source",
                        lambda *_a, **_k: signed.append("SIGNED"))
    monkeypatch.setattr(mod, "verify_semantic_compatibility", lambda *_a: None)
    monkeypatch.setattr(mod, "_require_no_exposure_or_requests", lambda _db: None)
    try:
        yield admin, runtime, signed
    finally:
        runtime.close()
        admin.close()


def adopt(connection):
    bundles = tuple(SimpleNamespace(
        card=SimpleNamespace(strategy_id=name, strategy_version="v3"),
        entry_plan=SimpleNamespace(entry_plan_fingerprint="e"*64),
        exit_plan=SimpleNamespace(exit_plan_fingerprint="f"*64),
    ) for name in IDS)
    mod.bridge_checkpoint_release(
        connection, bundles=bundles, session=SESSION, producer=PRODUCER,
        target=TARGET, source_checkout=Path("/unused"),
    )


def snapshot(connection):
    return connection.execute(
        "SELECT strategy_id,strategy_version,entry_plan_fingerprint,exit_plan_fingerprint,"
        "source_session_id,producer_release_commit,frame_number,frame_index,last_exec_id,"
        "bootstrap_publication_id,last_publication_id,bootstrap_source_at,causal_state "
        "FROM strategy_entry.p90_scoped_native_consumer_checkpoints ORDER BY strategy_id"
    ).fetchall()


def releases(connection):
    return connection.execute("SELECT DISTINCT consumer_release_commit FROM "
                              "strategy_entry.p90_scoped_native_consumer_checkpoints").fetchall()


def audit_count(connection):
    return connection.execute(
        "SELECT count(*) FROM strategy_entry.p90_r06_checkpoint_release_bridges"
    ).fetchone()


def test_runtime_role_preserves_every_causal_field_and_audits_both(db):
    _admin, runtime, signed = db
    before = snapshot(runtime)
    adopt(runtime)
    assert snapshot(runtime) == before
    assert signed == ["SIGNED"]
    assert releases(runtime) == [(TARGET,)]
    assert audit_count(runtime) == (2,)
    for table in ("analytics.market_publications",
                  "strategy_entry.p90_r06_checkpoint_release_bridges"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(f"DELETE FROM {table}")


def test_second_audit_failure_rolls_back_first_adoption_and_audit(db):
    admin, runtime, _ = db
    before = snapshot(runtime)
    admin.execute("ALTER TABLE strategy_entry.p90_r06_checkpoint_release_bridges "
                  "ADD CHECK(strategy_id <> 'p90l90_legacy_sub_dot_arb_apt')")
    with pytest.raises(psycopg.errors.CheckViolation):
        adopt(runtime)
    assert snapshot(runtime) == before
    assert releases(runtime) == [(mod.APPROVED_BASELINE,)]
    assert audit_count(runtime) == (0,)


@pytest.mark.parametrize("defect", ["plan", "release", "epoch", "publication"])
def test_identity_drift_never_changes_a_cursor(db, defect):
    admin, runtime, _ = db
    if defect == "plan":
        admin.execute("UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
                      "SET entry_plan_fingerprint='wrong'")
    elif defect == "release":
        admin.execute("UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
                      "SET consumer_release_commit=%s", ("a"*40,))
    elif defect == "epoch":
        admin.execute("UPDATE analytics.native_public_trade_source_sessions SET state='ENDED'")
    else:
        admin.execute("DELETE FROM analytics.market_publications WHERE publication_id=20")
    before = snapshot(runtime)
    with pytest.raises(mod.R06ResumeBlocked):
        adopt(runtime)
    assert snapshot(runtime) == before
    assert audit_count(runtime) == (0,)

# Fresh epoch is an explicit no-order bootstrap, not cursor transplantation.
NEW_SESSION = '9'*32


def prepare_epoch(admin):
    admin.execute('ALTER TABLE analytics.native_public_trade_source_sessions '
                  'ADD ended_at timestamptz')
    admin.execute("UPDATE analytics.native_public_trade_source_sessions "
                  "SET state='GAP',ended_at=%s",
                  (datetime(2026, 11, 12, 1, tzinfo=UTC),))
    admin.execute('INSERT INTO analytics.native_public_trade_source_sessions '
                  'VALUES (%s,%s,%s,NULL)',
                  (NEW_SESSION, 'ACTIVE', PRODUCER))
    admin.execute('INSERT INTO analytics.market_publications VALUES (30,%s,%s)',
                  (NEW_SESSION, PRODUCER))
    admin.execute((Path(__file__).resolve().parents[1] /
                   'operations/sql/20261112_r06_fresh_epoch_transition.sql').read_text())
    return SimpleNamespace(
        new_epoch_only=True, legacy_detached_at=datetime(2026, 11, 12, 2, tzinfo=UTC),
        closed_5m_at=datetime(2026, 11, 12, 2, 5, tzinfo=UTC),
        pin=SimpleNamespace(source_session_id=NEW_SESSION, publication_id=30),
    )


def fresh_epoch(runtime, proof):
    from operations.monitoring.r06_entry_epoch import record_fresh_epoch

    with runtime.transaction():
        record_fresh_epoch(runtime, proof=proof, consumer_release=TARGET,
                           strategies=tuple((n, 'v3', 'e'*64, 'f'*64) for n in IDS))
        # Same transaction boundary as production's two future cursor INSERTs.
        runtime.execute(
            'INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints '
            'SELECT strategy_id,strategy_version,entry_plan_fingerprint,exit_plan_fingerprint,'
            '%s,producer_release_commit,%s,0,0,NULL,30,30,%s,%s::jsonb '
            'FROM strategy_entry.p90_scoped_native_consumer_checkpoints WHERE source_session_id=%s',
            (NEW_SESSION, TARGET, proof.closed_5m_at, json.dumps({'version': 1}), SESSION),
        )


def epoch_audits(runtime):
    return runtime.execute(
        'SELECT count(*) FROM strategy_entry.p90_r06_fresh_epoch_transitions',
    ).fetchone()


def test_fresh_epoch_preserves_old_history_and_append_only_audit(db):
    admin, runtime, _ = db
    proof = prepare_epoch(admin)
    before = snapshot(runtime)
    fresh_epoch(runtime, proof)
    old = [r for r in snapshot(runtime) if r[4] == SESSION]
    assert old == before
    assert epoch_audits(runtime) == (2,)
    assert len(snapshot(runtime)) == 4
    for verb in ('DELETE FROM', "UPDATE"):
        sql = (f'{verb} strategy_entry.p90_r06_fresh_epoch_transitions' +
               (" SET consumer_release_commit='wrong'" if verb == 'UPDATE' else ''))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(sql)


def test_second_epoch_audit_failure_is_atomic(db):
    admin, runtime, _ = db
    proof = prepare_epoch(admin)
    admin.execute('ALTER TABLE strategy_entry.p90_r06_fresh_epoch_transitions '
                  "ADD CHECK(strategy_id <> 'p90l90_legacy_sub_dot_arb_apt')")
    before = snapshot(runtime)
    with pytest.raises(psycopg.errors.CheckViolation):
        fresh_epoch(runtime, proof)
    assert snapshot(runtime) == before
    assert epoch_audits(runtime) == (0,)


@pytest.mark.parametrize('defect', ['active', 'mixed', 'fence', 'plan', 'ended'])
def test_new_epoch_rejects_invalid_lineage_without_mutation(db, defect):
    from operations.monitoring.r06_entry_epoch import R06EpochTransitionBlocked

    admin, runtime, _ = db
    proof = prepare_epoch(admin)
    if defect == 'active':
        admin.execute("UPDATE analytics.native_public_trade_source_sessions SET state='ACTIVE' "
                      'WHERE source_session_id=%s', (SESSION,))
    elif defect == 'mixed':
        admin.execute('UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints '
                      'SET source_session_id=%s WHERE strategy_id=%s', (NEW_SESSION, IDS[0]))
    elif defect == 'fence':
        proof.closed_5m_at = proof.legacy_detached_at
    elif defect == 'plan':
        admin.execute("UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
                      "SET entry_plan_fingerprint='wrong'")
    else:
        admin.execute('UPDATE analytics.native_public_trade_source_sessions SET ended_at=%s '
                      'WHERE source_session_id=%s', (proof.closed_5m_at, SESSION))
    before = snapshot(runtime)
    with pytest.raises(R06EpochTransitionBlocked):
        fresh_epoch(runtime, proof)
    assert snapshot(runtime) == before
    assert epoch_audits(runtime) == (0,)
