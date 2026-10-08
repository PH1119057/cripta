"""Disposable PostgreSQL integration: never targets production."""
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from bybit_workbench.release_arm_safety import check_release_arm_invariant


@pytest.mark.skipif(
    not os.getenv("TEST_RELEASE_ARM_PG_DSN"),
    reason="disposable PostgreSQL DSN not configured",
)
def test_release_arm_migration_and_gate_invariant_with_open_exchange_inventory():
    admin_dsn = os.environ["TEST_RELEASE_ARM_PG_DSN"]
    with psycopg.connect(admin_dsn) as admin:
        admin.execute("CREATE ROLE cripta LOGIN PASSWORD 'synthetic_test_password'")
        admin.execute("CREATE SCHEMA control")
        admin.execute("CREATE SCHEMA strategy_entry")
        admin.execute("CREATE SCHEMA runtime")
        admin.execute(
            """CREATE TABLE control.execution_gates(
                mode text primary key,enabled boolean not null,reason text,
                updated_at_epoch_ms bigint)"""
        )
        admin.execute(
            """CREATE TABLE control.execution_gate_events(
                id bigint generated always as identity primary key,
                at_epoch_ms bigint not null,mode text not null,
                previous_enabled boolean not null,requested_enabled boolean not null,
                resulting_enabled boolean not null,reason text not null,
                source text not null,origin text not null,request_id text not null,
                settings_version text, created_at timestamptz default now())"""
        )
        admin.execute(
            """CREATE TABLE control.live_arm_sessions(
                strategy_id text,strategy_version text,
                symbol text,release_commit text,state text)"""
        )
        admin.execute(
            """CREATE TABLE strategy_entry.execution_permissions(
                strategy_id text,strategy_version text,enabled boolean)"""
        )
        admin.execute("CREATE TABLE runtime.hot_positions(symbol text)")
        admin.execute("CREATE TABLE runtime.hot_orders(symbol text)")
        admin.execute(
            "INSERT INTO runtime.hot_positions VALUES('LTCUSDT')"
        )
        admin.execute("INSERT INTO runtime.hot_orders VALUES('LTCUSDT')")
        admin.execute(
            """INSERT INTO control.execution_gates VALUES(
                'mainnet',true,'owner approved',0)"""
        )
        admin.execute(
            """INSERT INTO control.live_arm_sessions VALUES(
                'r1_injusdt','1.1-micro-live','INJUSDT',%s,'ACTIVE')""",
            ("a" * 40,),
        )
        admin.execute(
            """INSERT INTO strategy_entry.execution_permissions VALUES(
                'r1_injusdt','1.1-micro-live',true)"""
        )
        sql = Path("operations/sql/20261009_release_arm_incidents.sql").read_text()
        admin.execute(sql)
        admin.execute(sql)
        admin.execute("GRANT USAGE ON SCHEMA control,strategy_entry TO cripta")
        admin.execute(
            "GRANT SELECT,UPDATE ON control.execution_gates TO cripta"
        )
        admin.execute(
            "GRANT INSERT ON control.execution_gate_events TO cripta"
        )
        admin.execute(
            "GRANT SELECT ON control.live_arm_sessions TO cripta"
        )
        admin.execute(
            "GRANT SELECT ON strategy_entry.execution_permissions TO cripta"
        )
    with psycopg.connect(
        "host=localhost port=5432 dbname=postgres user=cripta "
        "password=synthetic_test_password"
    ) as runtime:
        assert check_release_arm_invariant(
            runtime,
            loaded_commit="b" * 40,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        ) is False
        # A second poll deduplicates the same incident; no repeated gate toggle.
        assert check_release_arm_invariant(
            runtime,
            loaded_commit="b" * 40,
            now=datetime(2026, 10, 9, tzinfo=UTC),
        ) is False

    with psycopg.connect(admin_dsn) as admin:
        assert admin.execute(
            "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone() == (False,)
        assert admin.execute(
            "SELECT count(*) FROM control.execution_gate_events"
        ).fetchone() == (1,)
        assert admin.execute(
            "SELECT count(*) FROM control.release_arm_incidents WHERE state='OPEN'"
        ).fetchone() == (1,)
        assert admin.execute(
            "SELECT count(*) FROM runtime.hot_positions"
        ).fetchone() == (1,)
        assert admin.execute(
            "SELECT count(*) FROM runtime.hot_orders"
        ).fetchone() == (1,)
