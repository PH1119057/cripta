from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)


def _insert_generation(
    connection: psycopg.Connection,
    *,
    generation_id: str,
    account_ref: str,
    state: str,
    started_at: datetime,
    completed_at: datetime | None,
) -> None:
    if state == "COMPLETE":
        connection.execute(
            """INSERT INTO runtime.account_state_generations(
                   generation_id,account_ref,reason,state,started_at,completed_at,
                   account_type,total_equity,wallet_balance,available_balance,
                   positions_count,active_orders_count,position_mode_refs,wallet_payload,error)
               VALUES(%s,%s,'test','COMPLETE',%s,%s,'UNIFIED',100,100,80,0,0,
                      '{}'::jsonb,'{}'::jsonb,'')""",
            (generation_id, account_ref, started_at, completed_at),
        )
    elif state == "COLLECTING":
        connection.execute(
            """INSERT INTO runtime.account_state_generations(
                   generation_id,account_ref,reason,state,started_at,position_mode_refs,error)
               VALUES(%s,%s,'test','COLLECTING',%s,'{}'::jsonb,'')""",
            (generation_id, account_ref, started_at),
        )
    elif state == "FAILED":
        connection.execute(
            """INSERT INTO runtime.account_state_generations(
                   generation_id,account_ref,reason,state,started_at,completed_at,
                   position_mode_refs,error)
               VALUES(%s,%s,'test','FAILED',%s,%s,'{}'::jsonb,'test failure')""",
            (generation_id, account_ref, started_at, completed_at),
        )
    else:
        raise AssertionError(state)


def test_collecting_generation_does_not_invalidate_previous_complete() -> None:
    assert DSN is not None
    token = uuid4().hex
    account_ref = f"test-account-{token}"
    now = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        _insert_generation(
            connection,
            generation_id=f"complete-{token}",
            account_ref=account_ref,
            state="COMPLETE",
            started_at=now,
            completed_at=now + timedelta(seconds=1),
        )
        _insert_generation(
            connection,
            generation_id=f"collecting-{token}",
            account_ref=account_ref,
            state="COLLECTING",
            started_at=now + timedelta(seconds=2),
            completed_at=None,
        )
        generation = current_complete_account_state_generation(
            connection,
            account_ref=account_ref,
        )
    assert generation.generation_id == f"complete-{token}"
    assert generation.available_balance == 80


def test_latest_failed_generation_blocks_until_next_complete() -> None:
    assert DSN is not None
    token = uuid4().hex
    account_ref = f"test-account-{token}"
    now = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        _insert_generation(
            connection,
            generation_id=f"complete-{token}",
            account_ref=account_ref,
            state="COMPLETE",
            started_at=now,
            completed_at=now + timedelta(seconds=1),
        )
        _insert_generation(
            connection,
            generation_id=f"failed-{token}",
            account_ref=account_ref,
            state="FAILED",
            started_at=now + timedelta(seconds=2),
            completed_at=now + timedelta(seconds=3),
        )
        with pytest.raises(AccountStateGenerationUnavailable, match="FAILED"):
            current_complete_account_state_generation(
                connection,
                account_ref=account_ref,
            )