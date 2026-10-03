from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

ALERT_CLASSES = frozenset(
    {
        "REGIME_CHANGE",
        "MARKET_WIDE_STRESS",
        "SYNCHRONIZATION_SPIKE",
        "LIQUIDATION_CASCADE",
        "DATA_QUALITY_DEGRADATION",
        "SOURCE_OUTAGE",
    }
)


class CursorLike(Protocol):
    rowcount: int

    def fetchone(self) -> Mapping[str, object] | Sequence[object] | None: ...
    def fetchall(self) -> Sequence[Mapping[str, object] | Sequence[object]]: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


@dataclass(frozen=True, slots=True)
class MarketObservationAlert:
    alert_id: str
    alert_class: str
    observed_at: datetime
    source_component: str
    scope_key: str
    owner_notifiable: bool
    payload: Mapping[str, object]
    provenance: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MarketObservationAlertDelivery:
    delivery_id: str
    alert_id: str
    payload: Mapping[str, object]
    attempts: int


@dataclass(frozen=True, slots=True)
class AlertDeliveryProcessResult:
    attempted: int
    delivered: int
    escalated: int
    pending: int


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _value(
    row: Mapping[str, object] | Sequence[object],
    key: str,
    index: int,
) -> object:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def create_market_observation_alert(
    connection: ConnectionLike,
    *,
    alert_class: str,
    observed_at: datetime,
    source_component: str,
    scope_key: str,
    payload: Mapping[str, object],
    provenance: Mapping[str, object],
    owner_notifiable: bool = False,
) -> MarketObservationAlert:
    if alert_class not in ALERT_CLASSES:
        raise ValueError(f"unsupported market observation alert class: {alert_class}")
    if not source_component.strip():
        raise ValueError("source_component must be non-empty")
    if not scope_key.strip():
        raise ValueError("scope_key must be non-empty")
    trading_command = provenance.get("trading_command")
    if trading_command is not False:
        raise ValueError("provenance.trading_command must be false")

    observed_utc = observed_at.astimezone(UTC)
    content = {
        "alert_class": alert_class,
        "observed_at": observed_utc.isoformat(),
        "source_component": source_component,
        "scope_key": scope_key,
        "owner_notifiable": owner_notifiable,
        "payload": dict(payload),
        "provenance": dict(provenance),
    }
    content_hash = _fingerprint(content)
    alert_id = f"market-alert-{content_hash[:32]}"

    connection.execute(
        """INSERT INTO mayak_v2.market_observation_alerts(
               alert_id,alert_class,observed_at,source_component,scope_key,
               owner_notifiable,payload,provenance,content_hash
           ) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
           ON CONFLICT(alert_id) DO NOTHING""",
        (
            alert_id,
            alert_class,
            observed_utc,
            source_component,
            scope_key,
            owner_notifiable,
            canonical_json(dict(payload)),
            canonical_json(dict(provenance)),
            content_hash,
        ),
    )

    if owner_notifiable:
        delivery_id = f"market-alert-delivery-{_fingerprint({'alert_id': alert_id})[:32]}"
        delivery_payload = {
            "delivery_id": delivery_id,
            "alert_id": alert_id,
            "alert_class": alert_class,
            "observed_at": observed_utc.isoformat(),
            "source_component": source_component,
            "scope_key": scope_key,
            "payload": dict(payload),
            "provenance": dict(provenance),
        }
        connection.execute(
            """INSERT INTO mayak_v2.market_observation_alert_deliveries(
                   delivery_id,alert_id,channel,state,attempts,next_attempt_at,
                   last_attempt_at,delivered_at,acknowledged_at,escalation_at,
                   last_error,payload
               ) VALUES(%s,%s,'OWNER_WEBHOOK','PENDING',0,%s,
                        NULL,NULL,NULL,NULL,NULL,%s::jsonb)
               ON CONFLICT(alert_id) DO NOTHING""",
            (delivery_id, alert_id, observed_utc, canonical_json(delivery_payload)),
        )

    return MarketObservationAlert(
        alert_id=alert_id,
        alert_class=alert_class,
        observed_at=observed_utc,
        source_component=source_component,
        scope_key=scope_key,
        owner_notifiable=owner_notifiable,
        payload=dict(payload),
        provenance=dict(provenance),
    )


def due_alert_deliveries(
    connection: ConnectionLike,
    *,
    now: datetime,
    limit: int = 20,
) -> tuple[MarketObservationAlertDelivery, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows = connection.execute(
        """SELECT d.delivery_id,d.alert_id,d.payload,d.attempts
             FROM mayak_v2.market_observation_alert_deliveries d
             JOIN mayak_v2.market_observation_alerts a ON a.alert_id=d.alert_id
            WHERE d.state='PENDING'
              AND d.next_attempt_at <= %s
              AND a.owner_notifiable IS TRUE
            ORDER BY d.next_attempt_at,d.delivery_id
            LIMIT %s
            FOR UPDATE OF d SKIP LOCKED""",
        (now.astimezone(UTC), limit),
    ).fetchall()
    result: list[MarketObservationAlertDelivery] = []
    for row in rows:
        payload = _value(row, "payload", 2)
        if not isinstance(payload, Mapping):
            raise RuntimeError("market observation alert delivery payload must be json object")
        result.append(
            MarketObservationAlertDelivery(
                delivery_id=str(_value(row, "delivery_id", 0)),
                alert_id=str(_value(row, "alert_id", 1)),
                payload=payload,
                attempts=int(str(_value(row, "attempts", 3))),
            )
        )
    return tuple(result)


def mark_alert_delivery_success(
    connection: ConnectionLike,
    *,
    delivery_id: str,
    now: datetime,
) -> None:
    updated = connection.execute(
        """UPDATE mayak_v2.market_observation_alert_deliveries
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
        raise RuntimeError("market observation alert delivery success transition race")


def mark_alert_delivery_failure(
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
        """SELECT attempts FROM mayak_v2.market_observation_alert_deliveries
            WHERE delivery_id=%s AND state='PENDING'
            FOR UPDATE""",
        (delivery_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("market observation alert delivery pending row disappeared")
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
        """UPDATE mayak_v2.market_observation_alert_deliveries
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
        raise RuntimeError("market observation alert delivery failure transition race")
    return state


def escalate_unacknowledged_alerts(
    connection: ConnectionLike,
    *,
    now: datetime,
    acknowledgement_timeout_seconds: int,
) -> int:
    if acknowledgement_timeout_seconds <= 0:
        raise ValueError("acknowledgement timeout must be positive")
    updated = connection.execute(
        """UPDATE mayak_v2.market_observation_alert_deliveries d
              SET state='ESCALATION_REQUIRED',
                  escalation_at=%s,
                  last_error=coalesce(last_error,'OWNER_ACK_TIMEOUT'),
                  updated_at=clock_timestamp()
             FROM mayak_v2.market_observation_alerts a
            WHERE a.alert_id=d.alert_id
              AND a.owner_notifiable IS TRUE
              AND d.state='DELIVERED'
              AND d.acknowledged_at IS NULL
              AND d.delivered_at <= %s""",
        (
            now.astimezone(UTC),
            now.astimezone(UTC) - timedelta(seconds=acknowledgement_timeout_seconds),
        ),
    )
    return updated.rowcount


def acknowledge_alert_delivery(
    connection: ConnectionLike,
    *,
    delivery_id: str,
    acknowledged_at: datetime,
) -> bool:
    updated = connection.execute(
        """UPDATE mayak_v2.market_observation_alert_deliveries
              SET state='ACKNOWLEDGED',acknowledged_at=%s,updated_at=clock_timestamp()
            WHERE delivery_id=%s
              AND state IN ('DELIVERED','ESCALATION_REQUIRED')""",
        (acknowledged_at.astimezone(UTC), delivery_id),
    )
    return updated.rowcount == 1


def process_due_alert_deliveries(
    connection: ConnectionLike,
    *,
    now: datetime,
    sender: Callable[[Mapping[str, object]], None] | None,
    max_attempts: int,
    retry_seconds: int,
    acknowledgement_timeout_seconds: int,
) -> AlertDeliveryProcessResult:
    escalated = escalate_unacknowledged_alerts(
        connection,
        now=now,
        acknowledgement_timeout_seconds=acknowledgement_timeout_seconds,
    )
    attempted = 0
    delivered = 0
    for item in due_alert_deliveries(connection, now=now):
        attempted += 1
        if sender is None:
            mark_alert_delivery_failure(
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
            mark_alert_delivery_failure(
                connection,
                delivery_id=item.delivery_id,
                now=now,
                error=f"{type(exc).__name__}:{exc}",
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
            )
        else:
            mark_alert_delivery_success(
                connection,
                delivery_id=item.delivery_id,
                now=now,
            )
            delivered += 1

    row = connection.execute(
        """SELECT count(*)
             FROM mayak_v2.market_observation_alert_deliveries d
             JOIN mayak_v2.market_observation_alerts a ON a.alert_id=d.alert_id
            WHERE a.owner_notifiable IS TRUE
              AND d.state IN ('PENDING','DELIVERED','ESCALATION_REQUIRED')"""
    ).fetchone()
    if row is None:
        pending = 0
    elif isinstance(row, Mapping):
        pending = int(str(next(iter(row.values()))))
    else:
        pending = int(str(row[0]))
    return AlertDeliveryProcessResult(
        attempted=attempted,
        delivered=delivered,
        escalated=escalated,
        pending=pending,
    )
