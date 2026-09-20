from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.live_arm_readiness import (
    REQUIRED_LIVE_ARM_CHECKS,
    LiveArmContext,
    active_live_arm_session,
    evaluate_live_arm,
    scope_for_check,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
RELEASE = "a" * 40


def _context(prefix: str) -> LiveArmContext:
    return LiveArmContext(
        strategy_id=f"{prefix}-strategy",
        strategy_version="1",
        strategy_config_fingerprint=f"{prefix}-fp",
        strategy_activation_id=f"{prefix}-activation",
        symbol="UNIUSDT",
        release_commit=RELEASE,
    )


def _seed_evidence(
    connection: psycopg.Connection,
    context: LiveArmContext,
    *,
    include_owner: bool = True,
    release_commit: str = RELEASE,
    valid_until: datetime | None = None,
) -> None:
    for index, code in enumerate(REQUIRED_LIVE_ARM_CHECKS):
        if not include_owner and code == "MAINNET_GATE_EXPLICIT_OWNER_APPROVAL":
            continue
        scope_type, scope_key = scope_for_check(code, context)
        connection.execute(
            """INSERT INTO control.live_arm_evidence(
                   evidence_id,check_code,scope_type,scope_key,status,checked_at,
                   valid_until,release_commit,source,evidence
               ) VALUES(%s,%s,%s,%s,'PASS',%s,%s,%s,'test','{}'::jsonb)""",
            (
                f"{context.strategy_id}-{index}-{uuid4().hex[:8]}",
                code,
                scope_type,
                scope_key,
                NOW,
                valid_until,
                release_commit,
            ),
        )


def _seed_session(connection: psycopg.Connection, context: LiveArmContext) -> str:
    session_id = f"{context.strategy_id}-session-{uuid4().hex[:8]}"
    connection.execute(
        """INSERT INTO control.live_arm_sessions(
               live_arm_session_id,strategy_id,strategy_version,
               strategy_config_fingerprint,strategy_activation_id,symbol,
               release_commit,state,owner_approved_at,activated_at,
               deactivated_at,source
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,'test')""",
        (
            session_id,
            context.strategy_id,
            context.strategy_version,
            context.strategy_config_fingerprint,
            context.strategy_activation_id,
            context.symbol,
            context.release_commit,
            NOW,
            NOW,
        ),
    )
    return session_id


def test_missing_live_arm_evidence_is_fail_closed() -> None:
    assert DSN is not None
    context = _context("missing-" + uuid4().hex[:8])
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        decision = evaluate_live_arm(connection, context=context, now=NOW)
    assert decision.ready is False
    assert set(decision.failed_codes) == set(REQUIRED_LIVE_ARM_CHECKS)
    assert all(
        check.status in {"NOT_CHECKED_HERE", "STALE"}
        for check in decision.checks
    )


def test_exact_release_evidence_and_active_owner_session_are_accepted() -> None:
    assert DSN is not None
    context = _context("ready-" + uuid4().hex[:8])
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed_evidence(
            connection,
            context,
            valid_until=NOW + timedelta(minutes=10),
        )
        session_id = _seed_session(connection, context)
        decision = evaluate_live_arm(connection, context=context, now=NOW)
        active = active_live_arm_session(connection, context=context)
    assert decision.ready is True
    assert decision.failed_codes == ()
    assert active == session_id


def test_wrong_release_or_expired_evidence_becomes_stale() -> None:
    assert DSN is not None
    wrong_release = "b" * 40
    context = _context("stale-" + uuid4().hex[:8])
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed_evidence(
            connection,
            context,
            release_commit=wrong_release,
            valid_until=NOW + timedelta(minutes=10),
        )
        decision = evaluate_live_arm(connection, context=context, now=NOW)
    assert decision.ready is False
    assert set(decision.failed_codes) == set(REQUIRED_LIVE_ARM_CHECKS)
    assert all(check.status == "STALE" for check in decision.checks)


def test_runtime_role_cannot_rewrite_live_arm_evidence_history() -> None:
    assert DSN is not None
    context = _context("append-only-" + uuid4().hex[:8])
    with psycopg.connect(DSN) as connection:
        _seed_evidence(connection, context, include_owner=False)
        connection.commit()
        connection.execute("SET ROLE cripta")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(
                """UPDATE control.live_arm_evidence
                          SET status='FAIL'
                        WHERE check_code='CANON_CURRENT'
                          AND scope_type='GLOBAL'
                          AND scope_key='GLOBAL'"""
            )
