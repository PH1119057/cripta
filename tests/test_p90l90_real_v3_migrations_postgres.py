"""Disposable PostgreSQL 16 proof for the P90L90 REAL v3 release migrations.

The workflow applies the exact release SQL before this test. No Strategy is
activated, no LIVE arm is created, and no Exchange operation is possible here.
"""

from __future__ import annotations

import os

import psycopg
import pytest


DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="requires explicitly disposable PG16 CI database"
)


def test_real_v3_release_sql_compiles_and_keeps_runtime_least_privilege() -> None:
    assert DSN is not None
    with psycopg.connect(DSN) as conn:
        objects = conn.execute(
            """SELECT
                 to_regprocedure(
                   'strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)'
                 ) IS NOT NULL,
                 to_regprocedure(
                   'strategy_entry.prearm_p90l90_real_v3(timestamptz,text,text)'
                 ) IS NOT NULL,
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
        assert objects == (True, True, True, True, True)

        binding = conn.execute(
            """SELECT
                 pg_has_role(
                   'cripta','cripta_p90l90_real_v3_binding','MEMBER'
                 ),
                 has_table_privilege(
                   'cripta',
                   'strategy_entry.strategy_execution_bindings',
                   'INSERT'
                 ),
                 has_function_privilege(
                   'cripta',
                   'strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)',
                   'EXECUTE'
                 )"""
        ).fetchone()
        assert binding == (False, False, True)

        epochs = conn.execute(
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
        assert epochs == (True, False, True, False, False)

        active = conn.execute(
            """SELECT
                 count(*) FILTER (WHERE enabled=true),
                 (SELECT count(*) FROM strategy_entry.execution_permissions
                   WHERE enabled=true),
                 (SELECT count(*) FROM control.live_arm_sessions
                   WHERE state='ACTIVE'),
                 (SELECT count(*) FROM control.account_live_arm_sessions
                   WHERE state='ACTIVE')
               FROM strategy_entry.strategy_activations"""
        ).fetchone()
        assert active == (0, 0, 0, 0)
