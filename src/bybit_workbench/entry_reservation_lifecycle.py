from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol


class CursorLike(Protocol):
    rowcount: int

    def fetchone(self) -> Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


def _mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _reservation_for_command(
    connection: ConnectionLike,
    command_id: str,
) -> tuple[str, str] | None:
    row = connection.execute(
        """SELECT c.reservation_id,c.state
             FROM runtime.trade_commands tc
             JOIN strategy_entry.execution_dispatches d
               ON d.command_id=tc.command_id
              AND d.state='DISPATCHED'
             JOIN strategy_entry.execution_requests r
               ON r.execution_request_id=d.execution_request_id
             JOIN runtime.capital_reservations c
               ON c.reservation_id=r.capital_reservation_id
              AND c.reservation_id=d.capital_reservation_id
            WHERE tc.command_id=%s
              AND tc.command_type='entry'""",
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    payload_row = connection.execute(
        "SELECT payload_json FROM runtime.trade_commands WHERE command_id=%s",
        (command_id,),
    ).fetchone()
    if payload_row is None:
        return None
    payload = _mapping(payload_row[0])
    if payload is None or str(payload.get("source") or "") != "universal_entry":
        return None
    reservation_id = str(payload.get("capital_reservation_id") or "")
    if not reservation_id or reservation_id != str(row[0]):
        raise RuntimeError("Universal Entry command/reservation lineage mismatch")
    return reservation_id, str(row[1])


def mark_entry_order_acknowledged(
    connection: ConnectionLike,
    *,
    command_id: str,
    exchange_order_id: str,
    acknowledged_at: datetime,
) -> str | None:
    if not exchange_order_id.strip():
        raise ValueError("exchange_order_id is required")
    binding = _reservation_for_command(connection, command_id)
    if binding is None:
        return None
    reservation_id, state = binding
    if state == "PENDING_EXCHANGE_REFLECTION":
        return state
    if state in {"CONSUMED", "RELEASED", "RECONCILIATION_REQUIRED"}:
        return state
    if state != "DISPATCHED":
        raise RuntimeError(f"order acknowledgement from unexpected reservation state {state}")
    updated = connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='PENDING_EXCHANGE_REFLECTION',
                  state_reason='ENTRY_ORDER_ACKNOWLEDGED',
                  exchange_commitment_ref=%s,
                  exchange_commitment_at=%s
            WHERE reservation_id=%s AND state='DISPATCHED'""",
        (exchange_order_id, acknowledged_at.astimezone(UTC), reservation_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError("entry order acknowledgement reservation race")
    return "PENDING_EXCHANGE_REFLECTION"


def finalize_failed_entry_command_reservation(
    connection: ConnectionLike,
    *,
    command_id: str,
    reason: str,
    mutation_ambiguous: bool,
) -> str | None:
    binding = _reservation_for_command(connection, command_id)
    if binding is None:
        return None
    reservation_id, state = binding
    if state in {"CONSUMED", "RELEASED"}:
        return state

    if mutation_ambiguous or state in {
        "PENDING_EXCHANGE_REFLECTION",
        "RECONCILIATION_REQUIRED",
    }:
        if state != "RECONCILIATION_REQUIRED":
            updated = connection.execute(
                """UPDATE runtime.capital_reservations
                      SET state='RECONCILIATION_REQUIRED',state_reason=%s
                    WHERE reservation_id=%s
                      AND state IN ('DISPATCHED','PENDING_EXCHANGE_REFLECTION')""",
                (f"AMBIGUOUS_ENTRY_OUTCOME:{reason}"[:500], reservation_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("entry reconciliation reservation race")
        return "RECONCILIATION_REQUIRED"

    if state == "DISPATCHED":
        updated = connection.execute(
            """UPDATE runtime.capital_reservations
                  SET state='RELEASED',state_reason=%s
                WHERE reservation_id=%s AND state='DISPATCHED'""",
            (f"ENTRY_NO_MUTATION:{reason}"[:500], reservation_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError("deterministic entry rejection reservation race")
        return "RELEASED"

    raise RuntimeError(f"failed Entry command has unexpected reservation state {state}")


def resolve_cancelled_entry_reservation_after_reconcile(
    connection: ConnectionLike,
    *,
    command_id: str,
    exchange_order_id: str,
) -> str | None:
    binding = _reservation_for_command(connection, command_id)
    if binding is None:
        return None
    reservation_id, state = binding
    if state in {"CONSUMED", "RELEASED"}:
        return state

    position = connection.execute(
        """SELECT position_id
             FROM runtime.position_ownership
            WHERE entry_command_id=%s
            ORDER BY fill_at DESC
            LIMIT 1""",
        (command_id,),
    ).fetchone()
    if position is not None:
        return finalize_failed_entry_command_reservation(
            connection,
            command_id=command_id,
            reason="CANCEL_AFTER_POSITION_BINDING",
            mutation_ambiguous=True,
        )

    history = connection.execute(
        """SELECT order_status,payload_json
             FROM runtime.exchange_order_history
            WHERE order_id=%s AND order_link_id=%s""",
        (exchange_order_id, command_id[:36]),
    ).fetchone()
    if history is None:
        return finalize_failed_entry_command_reservation(
            connection,
            command_id=command_id,
            reason="CANCEL_HISTORY_MISSING",
            mutation_ambiguous=True,
        )

    payload = _mapping(history[1])
    if payload is None:
        return finalize_failed_entry_command_reservation(
            connection,
            command_id=command_id,
            reason="CANCEL_HISTORY_PAYLOAD_INVALID",
            mutation_ambiguous=True,
        )
    try:
        cumulative_fill = Decimal(str(payload.get("cumExecQty") or "0"))
    except InvalidOperation:
        return finalize_failed_entry_command_reservation(
            connection,
            command_id=command_id,
            reason="CANCEL_HISTORY_FILL_INVALID",
            mutation_ambiguous=True,
        )

    execution = connection.execute(
        """SELECT 1
             FROM runtime.executions
            WHERE order_id=%s OR order_link_id=%s
            LIMIT 1""",
        (exchange_order_id, command_id[:36]),
    ).fetchone()
    order_status = str(history[0])
    if order_status == "Cancelled" and cumulative_fill == 0 and execution is None:
        if state not in {"DISPATCHED", "PENDING_EXCHANGE_REFLECTION"}:
            return finalize_failed_entry_command_reservation(
                connection,
                command_id=command_id,
                reason=f"ZERO_FILL_CANCEL_FROM_{state}",
                mutation_ambiguous=True,
            )
        updated = connection.execute(
            """UPDATE runtime.capital_reservations
                  SET state='RELEASED',
                      state_reason='LIMIT_TTL_CANCEL_CONFIRMED_ZERO_FILL'
                WHERE reservation_id=%s
                  AND state IN ('DISPATCHED','PENDING_EXCHANGE_REFLECTION')""",
            (reservation_id,),
        )
        if updated.rowcount != 1:
            raise RuntimeError("zero-fill cancel reservation release race")
        return "RELEASED"

    return finalize_failed_entry_command_reservation(
        connection,
        command_id=command_id,
        reason=(
            f"CANCEL_NOT_PROVEN_ZERO_FILL:status={order_status}:"
            f"cumExecQty={cumulative_fill}"
        ),
        mutation_ambiguous=True,
    )
