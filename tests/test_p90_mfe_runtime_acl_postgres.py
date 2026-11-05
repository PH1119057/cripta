"""Run actual MFE ledger with production-equivalent append-only runtime ACLs."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from test_p90l90_real_mfe import SHA, _cursor, _position

from bybit_workbench.universal_exit.p90l90_real_mfe_ledger import P90RealMfeLedger

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL required")


class RuntimeDB:
    def __init__(self, connection, schema):
        self.connection = connection
        self.schema = schema

    @property
    def info(self):
        return self.connection.info

    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace("strategy_exit.", self.schema + "."), params)


@pytest.fixture
def runtime_db():
    import psycopg
    from psycopg.rows import dict_row

    schema = "mfe_acl_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        assert admin.info.dbname != "cripta", "production database forbidden"
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            admin.execute(
                f"CREATE TABLE {schema}.exit_observations("
                "observation_id text PRIMARY KEY,strategy_position_id text,"
                "exit_plan_fingerprint text,symbol text,event_kind text,"
                "event_at timestamptz,observed_at timestamptz,received_at timestamptz,"
                "attributes jsonb,source_refs jsonb)"
            )
            admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta")
            admin.execute(f"GRANT SELECT,INSERT ON {schema}.exit_observations TO cripta")
            admin.execute(f"REVOKE UPDATE,DELETE ON {schema}.exit_observations FROM cripta")

            def connect():
                c = psycopg.connect(DSN, row_factory=dict_row)
                c.execute("SET ROLE cripta")
                assert c.execute("SELECT current_user AS actor").fetchone()["actor"] == "cripta"
                c.commit()
                return RuntimeDB(c, schema)

            yield connect
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def args():
    p = _position()
    cursor = _cursor(p)
    return dict(
        position=p,
        source_commit=SHA,
        fact_id="actual-native-fact",
        cursor=cursor,
        price=Decimal("100.2"),
        received_at=cursor.traded_at + timedelta(milliseconds=1),
        source_refs=("native:exact",),
        first_fill_forward_coverage_proven=True,
        native_continuity_proven=True,
    )


def test_runtime_first_tick_and_closed_bar_read_need_no_update_grant(runtime_db):
    import psycopg

    db = runtime_db()
    try:
        with db.connection.transaction():
            # Demonstrate the exact production failure even on an EMPTY table.
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connection.transaction():
                db.execute("SELECT * FROM strategy_exit.exit_observations LIMIT 1 FOR UPDATE")
            ledger = P90RealMfeLedger(db)
            state = ledger.on_trade(**args())
            assert ledger.load_latest(position=args()["position"], source_commit=SHA)[0] == state
            assert ledger.on_trade(**args()) == state
            for mutation in (
                "UPDATE strategy_exit.exit_observations SET symbol='BAD'",
                "DELETE FROM strategy_exit.exit_observations",
            ):
                with (
                    pytest.raises(psycopg.errors.InsufficientPrivilege),
                    db.connection.transaction(),
                ):
                    db.execute(mutation)
        assert (
            db.execute("SELECT count(*) AS n FROM strategy_exit.exit_observations").fetchone()["n"]
            == 1
        )
    finally:
        db.connection.close()


def test_per_position_advisory_lock_serializes_concurrent_runtime_writers(runtime_db):
    first = runtime_db()
    second = runtime_db()
    entered = Event()
    finished = Event()

    def repeat():
        entered.set()
        with second.connection.transaction():
            state = P90RealMfeLedger(second).on_trade(**args())
        finished.set()
        return state

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with first.connection.transaction():
                expected = P90RealMfeLedger(first).on_trade(**args())
                # A broken serialization primitive must fail bounded, not hang CI.
                second.execute("SET statement_timeout='5s'")
                second.connection.commit()
                future = pool.submit(repeat)
                assert entered.wait(2)
                deadline = monotonic() + 3
                waiting = False
                while monotonic() < deadline and not finished.is_set():
                    waiting = first.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                        "AND locktype='advisory' AND NOT granted) AS waiting",
                        (second.info.backend_pid,),
                    ).fetchone()["waiting"]
                    if waiting:
                        break
                    sleep(0.01)
                assert waiting, "second runtime writer must wait on the exact advisory lock"
                assert not finished.is_set()
            assert future.result(timeout=8) == expected
        assert (
            first.execute("SELECT count(*) AS n FROM strategy_exit.exit_observations").fetchone()[
                "n"
            ]
            == 1
        )
    finally:
        first.connection.close()
        second.connection.close()
