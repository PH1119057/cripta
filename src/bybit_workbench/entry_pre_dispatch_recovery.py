"""Safe recovery of expired REAL Entry admission before any Exchange dispatch."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint


def release_expired_pre_dispatch(
    connection: Any,
    *,
    now: datetime,
    trading_account_id: str | None = None,
) -> int:
    """Release expired RESERVED/CLAIMED admission with no dispatch.

    This path is Exchange-inert. It acts only when the exact Entry request has
    never acquired an execution_dispatch row, so no trade command/order can
    have been published by this request.
    """
    current = now.astimezone(UTC)
    account_sql = ""
    params: list[object] = [current]
    if trading_account_id:
        account_sql = " AND r.trading_account_id=%s"
        params.append(trading_account_id)
    rows = connection.execute(
        f"""SELECT r.execution_request_id,r.strategy_attempt_id,
                   r.entry_decision_id,r.signal_id,r.strategy_id,
                   r.strategy_version,r.strategy_config_fingerprint,
                   r.entry_plan_fingerprint,r.exit_plan_fingerprint,
                   r.capital_reservation_id,r.exchange_position_slot_claim_id,
                   r.position_mode_state_ref,r.trading_account_id,
                   c.pre_dispatch_expires_at
              FROM strategy_entry.execution_requests r
              JOIN runtime.capital_reservations c
                ON c.reservation_id=r.capital_reservation_id
               AND c.strategy_attempt_id=r.strategy_attempt_id
               AND c.trading_account_id=r.trading_account_id
               AND c.identity_status='PROVEN'
              JOIN runtime.exchange_position_slot_claims s
                ON s.exchange_position_slot_claim_id=r.exchange_position_slot_claim_id
               AND s.strategy_attempt_id=r.strategy_attempt_id
               AND s.trading_account_id=r.trading_account_id
               AND s.identity_status='PROVEN'
             WHERE r.identity_status='PROVEN'
               AND c.state='RESERVED'
               AND s.claim_state='CLAIMED'
               AND c.pre_dispatch_expires_at IS NOT NULL
               AND c.pre_dispatch_expires_at <= %s
               AND NOT EXISTS (
                   SELECT 1 FROM strategy_entry.execution_dispatches d
                    WHERE d.execution_request_id=r.execution_request_id
               ){account_sql}
             ORDER BY c.pre_dispatch_expires_at,r.execution_request_id
             FOR UPDATE OF c,s SKIP LOCKED""",
        tuple(params),
    ).fetchall()
    released = 0
    for row in rows:
        item = row if isinstance(row, Mapping) else {
            "execution_request_id": row[0],
            "strategy_attempt_id": row[1],
            "entry_decision_id": row[2],
            "signal_id": row[3],
            "strategy_id": row[4],
            "strategy_version": row[5],
            "strategy_config_fingerprint": row[6],
            "entry_plan_fingerprint": row[7],
            "exit_plan_fingerprint": row[8],
            "capital_reservation_id": row[9],
            "exchange_position_slot_claim_id": row[10],
            "position_mode_state_ref": row[11],
            "trading_account_id": row[12],
            "pre_dispatch_expires_at": row[13],
        }
        request_id = str(item["execution_request_id"])
        reservation_id = str(item["capital_reservation_id"])
        slot_id = str(item["exchange_position_slot_claim_id"])
        reservation = connection.execute(
            """UPDATE runtime.capital_reservations
                  SET state='RELEASED',
                      state_reason='REQUEST_EXPIRED_PRE_DISPATCH',
                      updated_at=%s
                WHERE reservation_id=%s AND state='RESERVED'""",
            (current, reservation_id),
        )
        claim = connection.execute(
            """UPDATE runtime.exchange_position_slot_claims
                  SET claim_state='RELEASED',released_at=%s,updated_at=%s,
                      release_reason='REQUEST_EXPIRED_PRE_DISPATCH'
                WHERE exchange_position_slot_claim_id=%s
                  AND claim_state='CLAIMED'""",
            (current, current, slot_id),
        )
        if reservation.rowcount != 1 or claim.rowcount != 1:
            raise RuntimeError("expired pre-dispatch admission release race")
        payload = {
            "block_code": "REQUEST_EXPIRED",
            "pre_dispatch_expires_at": str(item["pre_dispatch_expires_at"]),
            "recovery_owner": "LIFECYCLE_OR_CONSUMER",
        }
        dispatch_id = "dispatch-" + fingerprint({
            "execution_request_id": request_id,
            "state": "BLOCKED",
        })[:32]
        connection.execute(
            """INSERT INTO strategy_entry.execution_dispatches(
                   dispatch_id,execution_request_id,command_id,state,reason,
                   strategy_attempt_id,entry_decision_id,signal_id,
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   entry_plan_fingerprint,exit_plan_fingerprint,payload,
                   capital_reservation_id,exchange_position_slot_claim_id,
                   position_mode_state_ref
               ) VALUES(%s,%s,NULL,'BLOCKED','REQUEST_EXPIRED_PRE_DISPATCH',
                        %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
               ON CONFLICT(execution_request_id) DO NOTHING""",
            (
                dispatch_id,
                request_id,
                str(item["strategy_attempt_id"]),
                str(item["entry_decision_id"]),
                str(item["signal_id"]),
                str(item["strategy_id"]),
                str(item["strategy_version"]),
                str(item["strategy_config_fingerprint"]),
                str(item["entry_plan_fingerprint"]),
                str(item["exit_plan_fingerprint"]),
                canonical_json(payload),
                reservation_id,
                slot_id,
                str(item["position_mode_state_ref"]),
            ),
        )
        event_id = "reqstate-" + fingerprint({
            "execution_request_id": request_id,
            "state": "REQUEST_EXPIRED",
            "reason": "REQUEST_EXPIRED_PRE_DISPATCH",
        })[:32]
        connection.execute(
            """INSERT INTO strategy_entry.execution_request_state_events(
                   request_state_event_id,execution_request_id,state,
                   occurred_at,reason,payload
               ) VALUES(%s,%s,'REQUEST_EXPIRED',%s,
                        'REQUEST_EXPIRED_PRE_DISPATCH','{}'::jsonb)
               ON CONFLICT(request_state_event_id) DO NOTHING""",
            (event_id, request_id, current),
        )
        released += 1
    return released
