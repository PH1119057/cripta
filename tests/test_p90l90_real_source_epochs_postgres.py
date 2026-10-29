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
