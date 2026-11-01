"""Durable control state for generic REAL readiness preparation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

ACTIVE_STATES = frozenset(
    {"REQUESTED", "CHECKING", "STARTING_SERVICES", "RECONCILING", "VALIDATING"}
)
TERMINAL_STATES = frozenset({"READY_FOR_OWNER_ARM", "BLOCKED"})
STALE_AFTER = timedelta(minutes=5)


class RealReadinessControlError(ValueError):
    """Stable request/state failure."""


def _fingerprint(scope: Mapping[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_dict(row: object) -> dict[str, object]:
    if row is None:
        raise RealReadinessControlError("READINESS_OPERATION_NOT_FOUND")
    keys = (
        "request_id",
        "requested_by",
        "release_commit",
        "scope_key",
        "scope_json",
        "state",
        "phase",
        "progress_json",
        "blocker_code",
        "blocker_detail",
        "retry_eligible",
        "created_at",
        "updated_at",
        "completed_at",
    )
    return dict(zip(keys, row, strict=True))


def begin_operation(
    connection: Any,
    *,
    request_id: str,
    operator: str,
    release_commit: str,
    scope_key: str,
    scope: Mapping[str, object],
    now: datetime,
) -> dict[str, object]:
    current = now.astimezone(UTC)
    fingerprint = _fingerprint(scope)
    existing = connection.execute(
        """SELECT request_id,requested_by,release_commit,scope_key,scope_json,
                  state,phase,progress_json,blocker_code,blocker_detail,
                  retry_eligible,created_at,updated_at,completed_at
             FROM control.real_readiness_operations
            WHERE request_id=%s
            FOR UPDATE""",
        (request_id,),
    ).fetchone()
    if existing is not None:
        result = _row_dict(existing)
        if (
            result["requested_by"] != operator
            or result["release_commit"] != release_commit
            or result["scope_key"] != scope_key
            or _fingerprint(result["scope_json"]) != fingerprint
        ):
            raise RealReadinessControlError("READINESS_REQUEST_ID_CONFLICT")
        return result

    active = connection.execute(
        """SELECT request_id,updated_at
             FROM control.real_readiness_operations
            WHERE scope_key=%s
              AND state=ANY(%s)
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE""",
        (scope_key, list(ACTIVE_STATES)),
    ).fetchone()
    if active is not None:
        active_id, updated_at = active
        if (
            not isinstance(updated_at, datetime)
            or current - updated_at.astimezone(UTC) <= STALE_AFTER
        ):
            raise RealReadinessControlError(
                "READINESS_OPERATION_ALREADY_ACTIVE:" + str(active_id)
            )
        connection.execute(
            """UPDATE control.real_readiness_operations
                  SET state='BLOCKED',
                      phase='interrupted',
                      blocker_code='COORDINATOR_INTERRUPTED',
                      blocker_detail='stale nonterminal readiness operation',
                      retry_eligible=true,
                      updated_at=%s,
                      completed_at=%s
                WHERE request_id=%s""",
            (current, current, active_id),
        )

    connection.execute(
        """INSERT INTO control.real_readiness_operations(
              request_id,requested_by,release_commit,scope_key,scope_json,
              scope_fingerprint,state,phase,progress_json,retry_eligible,
              created_at,updated_at)
           VALUES(%s,%s,%s,%s,%s::jsonb,%s,'REQUESTED','requested',
                  '{}'::jsonb,false,%s,%s)""",
        (
            request_id,
            operator,
            release_commit,
            scope_key,
            json.dumps(scope, sort_keys=True),
            fingerprint,
            current,
            current,
        ),
    )
    return get_operation(connection, request_id=request_id)


def set_state(
    connection: Any,
    *,
    request_id: str,
    state: str,
    phase: str,
    now: datetime,
    progress: Mapping[str, object] | None = None,
) -> None:
    if state not in ACTIVE_STATES:
        raise RealReadinessControlError("READINESS_NONTERMINAL_STATE_INVALID")
    current = now.astimezone(UTC)
    result = connection.execute(
        """UPDATE control.real_readiness_operations
              SET state=%s,phase=%s,progress_json=%s::jsonb,
                  blocker_code=NULL,blocker_detail=NULL,retry_eligible=false,
                  completed_at=NULL,updated_at=%s
            WHERE request_id=%s
              AND state=ANY(%s)""",
        (
            state,
            phase,
            json.dumps(progress or {}, sort_keys=True),
            current,
            request_id,
            list(ACTIVE_STATES),
        ),
    )
    if result.rowcount != 1:
        raise RealReadinessControlError("READINESS_STATE_TRANSITION_INVALID")


def mark_ready(
    connection: Any,
    *,
    request_id: str,
    now: datetime,
    progress: Mapping[str, object] | None = None,
) -> None:
    current = now.astimezone(UTC)
    result = connection.execute(
        """UPDATE control.real_readiness_operations
              SET state='READY_FOR_OWNER_ARM',phase='ready',
                  progress_json=%s::jsonb,blocker_code=NULL,
                  blocker_detail=NULL,retry_eligible=false,
                  updated_at=%s,completed_at=%s
            WHERE request_id=%s AND state=ANY(%s)""",
        (
            json.dumps(progress or {}, sort_keys=True),
            current,
            current,
            request_id,
            list(ACTIVE_STATES),
        ),
    )
    if result.rowcount != 1:
        raise RealReadinessControlError("READINESS_READY_TRANSITION_INVALID")


def mark_blocked(
    connection: Any,
    *,
    request_id: str,
    code: str,
    detail: str,
    retry_eligible: bool,
    now: datetime,
    progress: Mapping[str, object] | None = None,
) -> None:
    current = now.astimezone(UTC)
    result = connection.execute(
        """UPDATE control.real_readiness_operations
              SET state='BLOCKED',phase='blocked',
                  progress_json=%s::jsonb,blocker_code=%s,
                  blocker_detail=%s,retry_eligible=%s,
                  updated_at=%s,completed_at=%s
            WHERE request_id=%s AND state=ANY(%s)""",
        (
            json.dumps(progress or {}, sort_keys=True),
            code,
            detail[:1000],
            retry_eligible,
            current,
            current,
            request_id,
            list(ACTIVE_STATES),
        ),
    )
    if result.rowcount != 1:
        raise RealReadinessControlError("READINESS_BLOCK_TRANSITION_INVALID")


def get_operation(connection: Any, *, request_id: str) -> dict[str, object]:
    row = connection.execute(
        """SELECT request_id,requested_by,release_commit,scope_key,scope_json,
                  state,phase,progress_json,blocker_code,blocker_detail,
                  retry_eligible,created_at,updated_at,completed_at
             FROM control.real_readiness_operations
            WHERE request_id=%s""",
        (request_id,),
    ).fetchone()
    return _row_dict(row)


def latest_operation(connection: Any, *, scope_key: str) -> dict[str, object] | None:
    row = connection.execute(
        """SELECT request_id,requested_by,release_commit,scope_key,scope_json,
                  state,phase,progress_json,blocker_code,blocker_detail,
                  retry_eligible,created_at,updated_at,completed_at
             FROM control.real_readiness_operations
            WHERE scope_key=%s
            ORDER BY created_at DESC,request_id DESC
            LIMIT 1""",
        (scope_key,),
    ).fetchone()
    return None if row is None else _row_dict(row)
