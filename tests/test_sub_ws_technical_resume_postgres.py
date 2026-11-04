"""Disposable PostgreSQL contract for an atomic SUB WS technical clear.

Requires CRIPTA_TRADE_LIFECYCLE_TEST_DSN and exactly cripta_integration.
Never touches production, live Exchange, gate authorization or owner arms.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL only")
SUB = "ta-853ac5fcd58185697396d33cd57459fa"
SUB_CONNECTOR = "ac-ec57819f875f1bfd929a382d6dc8a125"
MAIN = "ta-2122c81ecff14f3b85b102db605212f7"
MAIN_CONNECTOR = "ac-ed73de20410548d6841aeafa5f196f70"
RELEASE = "a" * 40


def _db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("disposable PostgreSQL required")
    conn = psycopg.connect(DSN, autocommit=True)
    dbname = conn.execute("SELECT current_database()").fetchone()[0]
    if dbname != "cripta_integration":
        conn.close()
        pytest.skip("never use a non-disposable database")
    return conn


def _seed(conn: psycopg.Connection, *, flat: bool = True) -> tuple[datetime, int, str]:
    uid = "123456789"
    for account, kind, parent in ((MAIN, "MAIN", None), (SUB, "SUB", MAIN)):
        conn.execute(
            """INSERT INTO identity.trading_accounts(
                 trading_account_id,venue_id,account_kind,account_type,
                 environment,status,external_account_ref,parent_trading_account_id)
               VALUES(%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s,%s)
               ON CONFLICT(trading_account_id) DO NOTHING""",
            (account, kind, "123456788" if kind == "MAIN" else uid, parent),
        )
    conn.execute(
        """INSERT INTO identity.account_connectors(
             account_connector_id,trading_account_id,connector_kind,
             adapter_id,credential_ref,status)
           VALUES(%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                  'DISPOSABLE_CI_ONLY','ACTIVE')
           ON CONFLICT(account_connector_id) DO NOTHING""",
        (SUB_CONNECTOR, SUB),
    )
    conn.execute(
        """INSERT INTO runtime.account_entry_safety_blocks(
             trading_account_id,account_connector_id,reason)
           VALUES(%s,%s,'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN')
           ON CONFLICT DO NOTHING""",
        (SUB, SUB_CONNECTOR),
    )
    blocked_at = conn.execute(
        """SELECT blocked_at FROM runtime.account_entry_safety_blocks
           WHERE trading_account_id=%s AND account_connector_id=%s""",
        (SUB, SUB_CONNECTOR),
    ).fetchone()[0]
    now = datetime.now(UTC)
    conn.execute(
        """INSERT INTO runtime.entry_consumer_health(
             trading_account_id,account_connector_id,release_commit,
             state,detail,observed_at)
           VALUES(%s,%s,%s,'BLOCKED','ACCOUNT_ENTRY_SAFETY_BLOCKED',%s)
           ON CONFLICT(trading_account_id) DO UPDATE
           SET account_connector_id=excluded.account_connector_id,
               release_commit=excluded.release_commit,
               state=excluded.state,
               detail=excluded.detail,
               observed_at=excluded.observed_at""",
        (SUB, SUB_CONNECTOR, RELEASE, now),
    )
    generation = "generation-" + uuid4().hex[:15]
    conn.execute(
        """INSERT INTO runtime.account_state_generations(
             generation_id,account_ref,reason,state,started_at,completed_at,
             position_mode_refs,error,trading_account_id,
             account_connector_id,identity_status,positions_count,
             active_orders_count,account_type,total_equity,
             wallet_balance,available_balance)
           VALUES(%s,'BYBIT:UNIFIED','reconnect','COMPLETE',%s,%s,
                  '{}'::jsonb,'',%s,%s,'PROVEN',%s,0,
                  'UNIFIED',100,100,100)""",
        (
            generation, now - timedelta(milliseconds=50), now,
            SUB, SUB_CONNECTOR, 0 if flat else 1,
        ),
    )
    conn.execute(
        """INSERT INTO runtime.connection_events(
             at_epoch_ms,channel,event,details_json,trading_account_id,
             account_connector_id,identity_status)
           VALUES(%s,'private','subscriptions_confirmed','{}',%s,%s,'PROVEN')""",
        (int(now.timestamp() * 1000), SUB, SUB_CONNECTOR),
    )
    event = conn.execute(
        """INSERT INTO runtime.connection_events(
             at_epoch_ms,channel,event,details_json,trading_account_id,
             account_connector_id,identity_status)
           VALUES(%s,'private','reconnect_verified','{}',%s,%s,'PROVEN')
           RETURNING id""",
        (int(now.timestamp() * 1000) + 1, SUB, SUB_CONNECTOR),
    ).fetchone()[0]
    return blocked_at, event, generation


@pytest.fixture
def disposable():
    conn = _db()
    conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
    try:
        yield conn
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def test_repeat_transient_latch_refreshes_epoch_and_hard_fault_cannot_downgrade(
    disposable: psycopg.Connection,
) -> None:
    blocked, _, _ = _seed(disposable)
    time.sleep(0.02)
    insert = """INSERT INTO runtime.account_entry_safety_blocks(
                trading_account_id,account_connector_id,reason)
              VALUES(%s,%s,%s) ON CONFLICT DO NOTHING"""
    disposable.execute(
        insert, (SUB, SUB_CONNECTOR, "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"),
    )
    newer, reason = disposable.execute(
        """SELECT blocked_at,reason FROM runtime.account_entry_safety_blocks
           WHERE trading_account_id=%s""", (SUB,),
    ).fetchone()
    assert newer > blocked and reason == "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"
    disposable.execute(
        insert, (SUB, SUB_CONNECTOR, "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED"),
    )
    disposable.execute(
        insert, (SUB, SUB_CONNECTOR, "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"),
    )
    assert disposable.execute(
        "SELECT reason FROM runtime.account_entry_safety_blocks WHERE trading_account_id=%s",
        (SUB,),
    ).fetchone()[0] == "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED"


def test_runtime_role_does_not_gain_update_delete_or_owner_arm_permissions(
    disposable: psycopg.Connection,
) -> None:
    assert disposable.execute(
        """SELECT has_column_privilege('cripta',
                       'runtime.account_entry_safety_blocks','reason','INSERT'),
                  has_table_privilege('cripta',
                       'runtime.account_entry_safety_blocks','UPDATE'),
                  has_table_privilege('cripta',
                       'runtime.account_entry_safety_blocks','DELETE')"""
    ).fetchone() == (True, False, False)
    migration = Path(
        "operations/sql/20261104_sub_ws_technical_resume.sql"
    ).read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in migration
    assert "FOR UPDATE" in migration
    assert "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED" not in (
        migration.split("CREATE OR REPLACE FUNCTION control.clear_sub_ws_transient_latch", 1)[1]
    )
    assert "UPDATE control.execution_gates" not in migration
    assert "UPDATE control.live_arm_sessions" not in migration
    assert "UPDATE control.account_live_arm_sessions" not in migration
    assert "GRANT UPDATE ON runtime.account_entry_safety_blocks TO cripta" not in migration


def test_stale_epoch_rejected_before_delete_with_serializable_cripta_actor(
    disposable: psycopg.Connection,
) -> None:
    blocked, event_id, generation = _seed(disposable)
    disposable.execute(
        """INSERT INTO runtime.account_entry_safety_blocks(
             trading_account_id,account_connector_id,reason)
           VALUES(%s,%s,'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN')
           ON CONFLICT DO NOTHING""",
        (SUB, SUB_CONNECTOR),
    )
    proof_time = datetime.now(UTC)
    disposable.execute("SET SESSION AUTHORIZATION cripta")
    with pytest.raises(psycopg.Error):
        disposable.execute(
            """SELECT control.clear_sub_ws_transient_latch(
                 %s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                SUB, SUB_CONNECTOR, RELEASE, generation, "123456789",
                event_id, blocked, proof_time,
            ),
        )


def test_complete_flat_reconnect_can_clear_only_technical_block(
    disposable: psycopg.Connection,
) -> None:
    blocked, event_id, generation = _seed(disposable)
    proof_time = datetime.now(UTC)
    before = disposable.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()[0]
    if before != 0:
        pytest.skip("CI gate is not in safely disarmed fixture state")
    disposable.execute("SET SESSION AUTHORIZATION cripta")
    result = disposable.execute(
        "SELECT control.clear_sub_ws_transient_latch(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            SUB, SUB_CONNECTOR, RELEASE, generation, "123456789",
            event_id, blocked, proof_time,
        ),
    ).fetchone()
    assert result == (SUB,)
    disposable.execute("RESET SESSION AUTHORIZATION")
    assert disposable.execute(
        "SELECT count(*) FROM runtime.account_entry_safety_blocks WHERE trading_account_id=%s",
        (SUB,),
    ).fetchone()[0] == 0
    assert disposable.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()[0] == before
    assert disposable.execute(
        """SELECT count(*) FROM runtime.account_ws_technical_resume_events
           WHERE trading_account_id=%s""",
        (SUB,),
    ).fetchone()[0] == 1
