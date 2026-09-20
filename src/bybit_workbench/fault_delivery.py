from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class CursorLike(Protocol):
    rowcount: int

    def fetchone(self) -> Mapping[str, object] | Sequence[object] | None: ...
    def fetchall(self) -> Sequence[Mapping[str, object] | Sequence[object]]: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


@dataclass(frozen=True, slots=True)
class FaultDelivery:
    delivery_id: str
    fault_id: str
    payload: Mapping[str, object]
    attempts: int


@dataclass(frozen=True, slots=True)
class DeliveryProcessResult:
    attempted: int
    delivered: int
    escalated: int
    pending: int


def _value(
    row: Mapping[str, object] | Sequence[object],
    key: str,
    index: int,
) -> object:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def due_deliveries(
    connection: ConnectionLike,
    *,
    now: datetime,
    limit: int = 20,
) -> tuple[FaultDelivery, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows = connection.execute(
        """SELECT d.delivery_id,d.fault_id,d.payload,d.attempts
             FROM runtime.lifecycle_fault_deliveries d
             JOIN runtime.lifecycle_faults f ON f.fault_id=d.fault_id
            WHERE d.state='PENDING'
              AND d.next_attempt_at <= %s
              AND f.state='OPEN'
              AND f.severity='CRITICAL'
            ORDER BY d.next_attempt_at,d.delivery_id
            LIMIT %s
            FOR UPDATE OF d SKIP LOCKED""",
        (now.astimezone(UTC), limit),
    ).fetchall()
    result: list[FaultDelivery] = []
    for row in rows:
        payload = _value(row, "payload", 2)
        if not isinstance(payload, Mapping):
            raise RuntimeError("fault delivery payload must be json object")
        result.append(
            FaultDelivery(
                delivery_id=str(_value(row, "delivery_id", 0)),
                fault_id=str(_value(row, "fault_id", 1)),
                payload=payload,
                attempts=int(str(_value(row, "attempts", 3))),
            )
        )
    return tuple(result)


def mark_delivery_success(
    connection: ConnectionLike,
    *,
    delivery_id: str,
    now: datetime,
) -> None:
    updated = connection.execute(
        """UPDATE runtime.lifecycle_fault_deliveries
              SET state='DELIVERED',
                  attempts=attempts+1,
                  last_attempt_at=%s,
                  delivered_at=coalesce(delivered_at,%s),
                  last_error=NULL,
                  updated_at=clock_timestamp()
            WHERE delivery_id=%s AND state='PENDING'""",
        (now.astimezone(UTC), now.astimezone(UTC), delivery_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError("fault delivery success transition race")


def mark_delivery_failure(
    connection: ConnectionLike,
    *,
    delivery_id: str,
    now: datetime,
    error: str,
    max_attempts: int,
    retry_seconds: int,
) -> str:
    if max_attempts <= 0 or retry_seconds <= 0:
        raise ValueError("delivery retry policy must be positive")
    row = connection.execute(
        """SELECT attempts FROM runtime.lifecycle_fault_deliveries
            WHERE delivery_id=%s AND state='PENDING'
            FOR UPDATE""",
        (delivery_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("fault delivery pending row disappeared")
    attempts = int(str(_value(row, "attempts", 0))) + 1
    if attempts >= max_attempts:
        state = "ESCALATION_REQUIRED"
        next_attempt_at = now.astimezone(UTC)
        escalation_at = now.astimezone(UTC)
    else:
        state = "PENDING"
        next_attempt_at = now.astimezone(UTC) + timedelta(seconds=retry_seconds)
        escalation_at = None
    updated = connection.execute(
        """UPDATE runtime.lifecycle_fault_deliveries
              SET state=%s,attempts=%s,last_attempt_at=%s,next_attempt_at=%s,
                  escalation_at=%s,last_error=%s,updated_at=clock_timestamp()
            WHERE delivery_id=%s AND state='PENDING'""",
        (
            state,
            attempts,
            now.astimezone(UTC),
            next_attempt_at,
            escalation_at,
            error[:1000],
            delivery_id,
        ),
    )
    if updated.rowcount != 1:
        raise RuntimeError("fault delivery failure transition race")
    return state


def escalate_unacknowledged(
    connection: ConnectionLike,
    *,
    now: datetime,
    acknowledgement_timeout_seconds: int,
) -> int:
    if acknowledgement_timeout_seconds <= 0:
        raise ValueError("acknowledgement timeout must be positive")
    updated = connection.execute(
        """UPDATE runtime.lifecycle_fault_deliveries d
              SET state='ESCALATION_REQUIRED',
                  escalation_at=%s,
                  last_error=coalesce(last_error,'OWNER_ACK_TIMEOUT'),
                  updated_at=clock_timestamp()
             FROM runtime.lifecycle_faults f
            WHERE f.fault_id=d.fault_id
              AND f.state='OPEN'
              AND f.severity='CRITICAL'
              AND d.state='DELIVERED'
              AND d.acknowledged_at IS NULL
              AND d.delivered_at <= %s""",
        (
            now.astimezone(UTC),
            now.astimezone(UTC) - timedelta(seconds=acknowledgement_timeout_seconds),
        ),
    )
    return updated.rowcount


def acknowledge_delivery(
    connection: ConnectionLike,
    *,
    delivery_id: str,
    acknowledged_at: datetime,
) -> bool:
    updated = connection.execute(
        """UPDATE runtime.lifecycle_fault_deliveries
              SET state='ACKNOWLEDGED',acknowledged_at=%s,updated_at=clock_timestamp()
            WHERE delivery_id=%s
              AND state IN ('DELIVERED','ESCALATION_REQUIRED')""",
        (acknowledged_at.astimezone(UTC), delivery_id),
    )
    return updated.rowcount == 1


def process_due_deliveries(
    connection: ConnectionLike,
    *,
    now: datetime,
    sender: Callable[[Mapping[str, object]], None] | None,
    max_attempts: int,
    retry_seconds: int,
    acknowledgement_timeout_seconds: int,
) -> DeliveryProcessResult:
    escalated = escalate_unacknowledged(
        connection,
        now=now,
        acknowledgement_timeout_seconds=acknowledgement_timeout_seconds,
    )
    attempted = 0
    delivered = 0
    for item in due_deliveries(connection, now=now):
        attempted += 1
        if sender is None:
            mark_delivery_failure(
                connection,
                delivery_id=item.delivery_id,
                now=now,
                error="OWNER_WEBHOOK_NOT_CONFIGURED",
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
            )
            continue
        try:
            sender(item.payload)
        except Exception as exc:
            mark_delivery_failure(
                connection,
                delivery_id=item.delivery_id,
                now=now,
                error=f"{type(exc).__name__}:{exc}",
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
            )
        else:
            mark_delivery_success(connection, delivery_id=item.delivery_id, now=now)
            delivered += 1

    row = connection.execute(
        """SELECT count(*)
             FROM runtime.lifecycle_fault_deliveries d
             JOIN runtime.lifecycle_faults f ON f.fault_id=d.fault_id
            WHERE f.state='OPEN' AND f.severity='CRITICAL'
              AND d.state IN ('PENDING','DELIVERED','ESCALATION_REQUIRED')"""
    ).fetchone()
    if row is None:
        pending = 0
    elif isinstance(row, Mapping):
        pending = int(next(iter(row.values())))
    else:
        pending = int(str(row[0]))
    return DeliveryProcessResult(
        attempted=attempted,
        delivered=delivered,
        escalated=escalated,
        pending=pending,
    )
