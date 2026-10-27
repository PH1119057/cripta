"""Disposable-PostgreSQL regression for Issue #235 lifecycle fault isolation.

Requires CRIPTA_TRADE_LIFECYCLE_TEST_DSN (never use production).
All inserted rows and accidental changes are rolled back unconditionally.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.lifecycle_supervisor import LifecycleSupervisor

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)


def test_account_local_resolution_never_closes_other_account_or_global_pg() -> None:
    assert DSN is not None
    token = uuid4().hex
    account_a = "test-ta-a-" + token
    account_b = "test-ta-b-" + token
    names = {
        "a": "lifecycle-isolation-a-" + token,
        "b": "lifecycle-isolation-b-" + token,
        "global": "lifecycle-isolation-global-" + token,
        "unknown": "lifecycle-isolation-unknown-" + token,
    }
    observed = datetime.now(UTC)
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        try:
            for label, (code, scope) in {
                "a": ("POSITION_WITHOUT_EXIT_OWNER", {
                    "fault_scope_kind": "ACCOUNT", "trading_account_id": account_a,
                }),
                "b": ("POSITION_WITHOUT_EXIT_OWNER", {
                    "fault_scope_kind": "ACCOUNT", "trading_account_id": account_b,
                }),
                "global": ("PLAN_PAIR_INCOMPLETE", {"fault_scope_kind": "GLOBAL"}),
                "unknown": ("POSITION_WITHOUT_EXIT_OWNER", {}),
            }.items():
                connection.execute(
                    """INSERT INTO runtime.lifecycle_faults(
                           fault_id,fault_code,severity,state,strategy_position_id,
                           detected_at,resolved_at,exact_ids,payload
                       ) VALUES(%s,%s,'CRITICAL','OPEN',NULL,%s,NULL,%s::jsonb,'{}'::jsonb)""",
                    (names[label], code, observed, json.dumps(scope)),
                )
            sub = LifecycleSupervisor(
                connection, trading_account_id=account_b, owns_global_faults=False
            )
            sub._resolve_cleared_faults((), observed)
            rows = connection.execute(
                """SELECT fault_id,state FROM runtime.lifecycle_faults
                    WHERE fault_id = ANY(%s)""",
                (list(names.values()),),
            ).fetchall()
            state = {str(row["fault_id"]): str(row["state"]) for row in rows}
            assert state == {
                names["a"]: "OPEN",
                names["b"]: "RESOLVED",
                names["global"]: "OPEN",
                names["unknown"]: "OPEN",
            }
        finally:
            connection.rollback()
