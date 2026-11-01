from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.real_readiness_control import (
    RealReadinessControlError,
    begin_operation,
    get_operation,
    latest_operation,
    mark_blocked,
    mark_ready,
    set_state,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)


def _begin(
    connection: psycopg.Connection,
    *,
    request_id: str,
    scope_key: str,
    now: datetime,
    release: str = "a" * 40,
    operator: str = "owner",
) -> dict[str, object]:
    return begin_operation(
        connection,
        request_id=request_id,
        operator=operator,
        release_commit=release,
        scope_key=scope_key,
        scope={
            "kind": "TEST_REAL_READINESS",
            "scope_key": scope_key,
            "accounts": ["ta-" + "1" * 32],
        },
        now=now,
    )


def test_real_readiness_same_request_is_idempotent_and_conflict_is_rejected() -> None:
    assert DSN is not None
    token = uuid4().hex
    request_id = "ready-" + token
    scope_key = "scope-" + token
    now = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.execute("SET ROLE cripta")
        first = _begin(
            connection,
            request_id=request_id,
            scope_key=scope_key,
            now=now,
        )
        repeated = _begin(
            connection,
            request_id=request_id,
            scope_key=scope_key,
            now=now + timedelta(seconds=1),
        )
        assert first["_existing"] is False
        assert repeated["_existing"] is True
        assert repeated["state"] == "REQUESTED"
        count = connection.execute(
            "SELECT count(*) FROM control.real_readiness_operations "
            "WHERE request_id=%s",
            (request_id,),
        ).fetchone()
        assert count == (1,)
        with pytest.raises(
            RealReadinessControlError,
            match="READINESS_REQUEST_ID_CONFLICT",
        ):
            _begin(
                connection,
                request_id=request_id,
                scope_key=scope_key,
                now=now + timedelta(seconds=2),
                release="b" * 40,
            )


def test_real_readiness_overlapping_scope_blocks_second_fresh_operation() -> None:
    assert DSN is not None
    token = uuid4().hex
    scope_key = "scope-" + token
    now = datetime(2026, 11, 2, 0, 10, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.execute("SET ROLE cripta")
        first_id = "ready-a-" + token
        _begin(connection, request_id=first_id, scope_key=scope_key, now=now)
        with pytest.raises(
            RealReadinessControlError,
            match="READINESS_OPERATION_ALREADY_ACTIVE:" + first_id,
        ):
            _begin(
                connection,
                request_id="ready-b-" + token,
                scope_key=scope_key,
                now=now + timedelta(seconds=5),
            )


def test_stale_operation_is_durably_blocked_before_replacement_request() -> None:
    assert DSN is not None
    token = uuid4().hex
    scope_key = "scope-" + token
    old_id = "ready-old-" + token
    new_id = "ready-new-" + token
    now = datetime(2026, 11, 2, 0, 20, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.execute("SET ROLE cripta")
        _begin(connection, request_id=old_id, scope_key=scope_key, now=now)
        connection.execute(
            "UPDATE control.real_readiness_operations "
            "SET updated_at=%s WHERE request_id=%s",
            (now - timedelta(minutes=6), old_id),
        )
        new = _begin(
            connection,
            request_id=new_id,
            scope_key=scope_key,
            now=now + timedelta(seconds=1),
        )
        old = get_operation(connection, request_id=old_id)
        assert old["state"] == "BLOCKED"
        assert old["blocker_code"] == "COORDINATOR_INTERRUPTED"
        assert old["retry_eligible"] is True
        assert old["completed_at"] is not None
        assert new["state"] == "REQUESTED"
        assert new["_existing"] is False


def test_readiness_progress_ready_and_blocked_are_durable() -> None:
    assert DSN is not None
    token = uuid4().hex
    now = datetime(2026, 11, 2, 0, 30, tzinfo=UTC)
    with psycopg.connect(DSN) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.execute("SET ROLE cripta")

        ready_scope = "ready-scope-" + token
        ready_id = "ready-ok-" + token
        _begin(connection, request_id=ready_id, scope_key=ready_scope, now=now)
        set_state(
            connection,
            request_id=ready_id,
            state="STARTING_SERVICES",
            phase="starting_services",
            now=now + timedelta(seconds=1),
            progress={"starting": ["cripta-example.service"]},
        )
        mark_ready(
            connection,
            request_id=ready_id,
            now=now + timedelta(seconds=2),
            progress={"services_active": 1},
        )
        ready = latest_operation(connection, scope_key=ready_scope)
        assert ready is not None
        assert ready["state"] == "READY_FOR_OWNER_ARM"
        assert ready["phase"] == "ready"
        assert ready["progress_json"] == {"services_active": 1}
        assert ready["completed_at"] is not None

        blocked_scope = "blocked-scope-" + token
        blocked_id = "ready-blocked-" + token
        _begin(
            connection,
            request_id=blocked_id,
            scope_key=blocked_scope,
            now=now,
        )
        mark_blocked(
            connection,
            request_id=blocked_id,
            code="SERVICE_START_FAILED",
            detail="test failure",
            retry_eligible=True,
            now=now + timedelta(seconds=1),
            progress={"unit": "cripta-example.service"},
        )
        blocked = latest_operation(connection, scope_key=blocked_scope)
        assert blocked is not None
        assert blocked["state"] == "BLOCKED"
        assert blocked["blocker_code"] == "SERVICE_START_FAILED"
        assert blocked["retry_eligible"] is True
        assert blocked["completed_at"] is not None
