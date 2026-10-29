"""Disposable PostgreSQL 16 proof for P90L90 REAL source-epoch schema.

The workflow applies the exact release SQL before this test. This test creates
no source epoch row, LIVE arm, execution permission, or Exchange operation.
"""

from __future__ import annotations

import os

import psycopg
import pytest


DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="requires explicitly disposable PG16 CI database"
)


def test_real_source_epoch_migration_compiles_and_is_least_privilege() -> None:
    assert DSN is not None
    with psycopg.connect(DSN) as conn:
        objects = conn.execute(
            """SELECT
                 to_regclass(
                   'strategy_entry.p90l90_real_source_epochs'
                 ) IS NOT NULL,
                 to_regclass(
                   'strategy_entry.p90l90_real_causal_checkpoints'
                 ) IS NOT NULL,
                 to_regclass(
                   'strategy_entry.p90l90_real_restart_bridges'
                 ) IS NOT NULL"""
        ).fetchone()
        assert objects == (True, True, True)

        bridge_columns = conn.execute(
            """SELECT column_name
                 FROM information_schema.columns
                WHERE table_schema='strategy_entry'
                  AND table_name='p90l90_real_restart_bridges'
                ORDER BY ordinal_position"""
        ).fetchall()
        assert [row[0] for row in bridge_columns] == [
            "restart_id",
            "epoch_id",
            "source_commit",
            "mirror_serial",
            "mirror_started_at",
            "source_ws_ready_at",
            "completed_at",
            "symbol_cursors",
            "position_cursors",
            "account_generations",
            "created_at",
        ]

        bridge_checks = " ".join(
            row[0]
            for row in conn.execute(
                """SELECT pg_get_constraintdef(c.oid)
                     FROM pg_constraint c
                     JOIN pg_class r ON r.oid=c.conrelid
                     JOIN pg_namespace n ON n.oid=r.relnamespace
                    WHERE n.nspname='strategy_entry'
                      AND r.relname='p90l90_real_restart_bridges'
                      AND c.contype='c'
                    ORDER BY c.conname"""
            ).fetchall()
        )
        assert "p90-real-restart-[0-9a-f]{40}" in bridge_checks
        assert "source_commit" in bridge_checks
        assert "mirror_serial > 0" in bridge_checks
        assert "jsonb_typeof(symbol_cursors)" in bridge_checks
        assert "jsonb_typeof(position_cursors)" in bridge_checks
        assert "jsonb_typeof(account_generations)" in bridge_checks

        bridge_index = conn.execute(
            """SELECT pg_get_indexdef(indexrelid)
                 FROM pg_index i
                 JOIN pg_class r ON r.oid=i.indrelid
                 JOIN pg_namespace n ON n.oid=r.relnamespace
                WHERE n.nspname='strategy_entry'
                  AND r.relname='p90l90_real_restart_bridges'
                  AND i.indisunique
                  AND pg_get_indexdef(indexrelid) LIKE
                      '%p90_real_restart_bridge_source_session%'"""
        ).fetchone()
        assert bridge_index is not None
        assert "epoch_id, source_commit, mirror_started_at" in bridge_index[0]

        privileges = conn.execute(
            """SELECT
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_real_source_epochs',
                   'SELECT,INSERT,UPDATE'
                 ),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_real_source_epochs','DELETE'
                 ),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_real_restart_bridges',
                   'SELECT,INSERT'
                 ),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_real_restart_bridges','UPDATE'
                 ),
                 has_table_privilege(
                   'cripta','strategy_entry.p90l90_real_restart_bridges','DELETE'
                 )"""
        ).fetchone()
        assert privileges == (True, False, True, False, False)

        assert conn.execute(
            "SELECT count(*) FROM strategy_entry.p90l90_real_source_epochs"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM strategy_entry.p90l90_real_restart_bridges"
        ).fetchone() == (0,)
