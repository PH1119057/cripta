from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)
from bybit_workbench.entry_admission import EntryAdmissionRequest, PostgresEntryAdmissionPort
from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=r1-reverse-worker",
)
POLL_SECONDS = float(os.environ.get("CRIPTA_R1_REVERSE_POLL_SECONDS", "0.25"))
CONSUMER_ARM = os.environ.get("CRIPTA_R1_REVERSE_MAINNET", "DISABLED").strip().upper()
ACCOUNT_REF = "BYBIT:UNIFIED"
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return value


def _execution_gate_enabled(connection: psycopg.Connection[Any]) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    return bool(row and row["enabled"])


def _execution_permission_enabled(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
) -> bool:
    found = connection.execute(
        """SELECT 1
             FROM strategy_entry.execution_permissions
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s AND enabled=true""",
        (
            row["strategy_id"],
            row["strategy_version"],
            row["strategy_config_fingerprint"],
        ),
    ).fetchone()
    return found is not None


def _next_transition(connection: psycopg.Connection[Any]) -> Mapping[str, object] | None:
    return connection.execute(
        """SELECT *
             FROM strategy_entry.reverse_transitions
            WHERE state IN (
              'REQUESTED','CLOSE_DISPATCHED','FLAT_CONFIRMED',
              'OPEN_REQUESTED','OPEN_DISPATCHED'
            )
            ORDER BY requested_at,reverse_transition_id
            LIMIT 1
            FOR UPDATE SKIP LOCKED"""
    ).fetchone()


def _card(connection: psycopg.Connection[Any], row: Mapping[str, object]) -> Mapping[str, object]:
    card = connection.execute(
        """SELECT card_json
             FROM strategy_entry.strategy_cards
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s""",
        (
            row["strategy_id"],
            row["strategy_version"],
            row["strategy_config_fingerprint"],
        ),
    ).fetchone()
    if card is None:
        raise RuntimeError("R1 reverse exact StrategyCard is missing")
    return _mapping(card["card_json"], "R1 StrategyCard")


def _active_exact(connection: psycopg.Connection[Any], row: Mapping[str, object]) -> bool:
    found = connection.execute(
        """SELECT 1
             FROM strategy_entry.strategy_activations
            WHERE activation_id=%s AND strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s AND enabled=true""",
        (
            row["strategy_activation_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["strategy_config_fingerprint"],
        ),
    ).fetchone()
    return found is not None


def _queue_close(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    now: datetime,
) -> str:
    position = connection.execute(
        """SELECT position_id,state,side,symbol
             FROM runtime.position_ownership
            WHERE position_id=%s FOR UPDATE""",
        (row["strategy_position_id"],),
    ).fetchone()
    if position is None:
        raise RuntimeError("R1 reverse source StrategyPosition is missing")
    if position["state"] != "OPEN":
        raise RuntimeError(f"R1 reverse source position state={position['state']}")
    expected_side = "Buy" if row["from_direction"] == "LONG" else "Sell"
    if str(position["side"]) != expected_side or str(position["symbol"]) != str(row["symbol"]):
        raise RuntimeError("R1 reverse source position identity mismatch")
    command_id = "r1-reverse-close-" + fingerprint(
        {"reverse_transition_id": row["reverse_transition_id"]}
    )[:19]
    payload = {
        "source": "universal_entry_reverse",
        "position_id": row["strategy_position_id"],
        "strategy_id": row["strategy_id"],
        "strategy_version": row["strategy_version"],
        "strategy_config_fingerprint": row["strategy_config_fingerprint"],
        "reason": "OPPOSITE_ENTRY_FORCED_FLIP",
        "reverse_transition_id": row["reverse_transition_id"],
    }
    connection.execute(
        """INSERT INTO runtime.trade_commands(
               command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms
           ) VALUES(%s,'close',%s,%s,'queued',%s)
           ON CONFLICT(command_id) DO NOTHING""",
        (
            command_id,
            row["symbol"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            int(now.timestamp() * 1000),
        ),
    )
    connection.execute(
        """UPDATE strategy_entry.reverse_transitions
              SET state='CLOSE_DISPATCHED',close_command_id=%s,updated_at=%s
            WHERE reverse_transition_id=%s AND state='REQUESTED'""",
        (command_id, now, row["reverse_transition_id"]),
    )
    return command_id


def _confirm_flat(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    now: datetime,
) -> str:
    position = connection.execute(
        """SELECT state,close_link_status
             FROM runtime.position_ownership
            WHERE position_id=%s""",
        (row["strategy_position_id"],),
    ).fetchone()
    if position is None:
        raise RuntimeError("R1 reverse source StrategyPosition disappeared")
    state = str(position["state"])
    link = str(position["close_link_status"])
    if state == "CLOSED" and link == "EXACT":
        connection.execute(
            """UPDATE strategy_entry.reverse_transitions
                  SET state='FLAT_CONFIRMED',updated_at=%s
                WHERE reverse_transition_id=%s AND state='CLOSE_DISPATCHED'""",
            (now, row["reverse_transition_id"]),
        )
        return "FLAT_CONFIRMED"
    if state == "RECONCILIATION_REQUIRED" or link == "UNRESOLVED_EXACT_LINK":
        connection.execute(
            """UPDATE strategy_entry.reverse_transitions
                  SET state='RECONCILIATION_REQUIRED',updated_at=%s
                WHERE reverse_transition_id=%s""",
            (now, row["reverse_transition_id"]),
        )
        return "RECONCILIATION_REQUIRED"
    return "WAITING_FOR_FLAT"


def _capacity(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
    capital: Mapping[str, object],
) -> tuple[str, datetime, Decimal]:
    del now
    try:
        generation = current_complete_account_state_generation(
            connection,
            account_ref=ACCOUNT_REF,
            acquire_account_lock=True,
        )
    except AccountStateGenerationUnavailable as exc:
        raise RuntimeError(f"R1 reverse account state generation unavailable: {exc}") from exc
    if str(capital.get("capacity_min_quality") or "") != "HIGH":
        raise RuntimeError("R1 reverse requires HIGH generation-backed capital quality")
    return (
        generation.generation_id,
        generation.completed_at,
        generation.available_balance,
    )


def _create_open_request(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    now: datetime,
) -> str:
    if not _active_exact(connection, row):
        raise RuntimeError("R1 reverse exact StrategyActivation is not active")
    card = _card(connection, row)
    lifecycle = _mapping(card.get("lifecycle_policy"), "R1 lifecycle_policy")
    reverse = _mapping(
        lifecycle.get("reverse_on_opposite_signal"),
        "R1 reverse_on_opposite_signal",
    )
    if (
        not bool(reverse.get("enabled", False))
        or str(reverse.get("close_execution") or "").upper() != "MARKET"
        or str(reverse.get("open_execution") or "").upper() != "MARKET"
        or str(reverse.get("position_mode") or "") != "ONE_WAY"
        or int(str(reverse.get("position_idx") or -1)) != 0
    ):
        raise RuntimeError("R1 reverse lifecycle policy mismatch")

    source_payload = _mapping(row["payload"], "reverse transition payload")
    request_payload = dict(
        _mapping(source_payload.get("entry_request_payload"), "reverse entry request payload")
    )
    if str(request_payload.get("execution_override") or "") != "OPPOSITE_FLIP_TAKER":
        raise RuntimeError("R1 reverse common intent execution_override mismatch")
    if str(source_payload.get("execution_override") or "") != "OPPOSITE_FLIP_TAKER":
        raise RuntimeError("R1 reverse transition execution_override mismatch")

    capital = _mapping(card.get("capital_policy"), "R1 capital_policy")
    requested_amount = Decimal(str(capital.get("requested_amount") or 0))
    if requested_amount <= 0 or str(capital.get("amount_currency") or "").upper() != "USDT":
        raise RuntimeError("R1 reverse capital policy invalid")
    capacity_id, capacity_at, capacity_available = _capacity(
        connection, now=now, capital=capital
    )

    attempt_id = "attempt-reverse-" + fingerprint(
        {"reverse_transition_id": row["reverse_transition_id"], "phase": "open"}
    )[:24]
    attempt_payload = {
        "strategy_attempt_id": attempt_id,
        "signal_id": row["signal_id"],
        "strategy_id": row["strategy_id"],
        "strategy_version": row["strategy_version"],
        "strategy_config_fingerprint": row["strategy_config_fingerprint"],
        "entry_plan_fingerprint": row["entry_plan_fingerprint"],
        "strategy_activation_id": row["strategy_activation_id"],
        "created_at": now.isoformat(),
        "source": "r1_reverse_worker",
        "reverse_transition_id": row["reverse_transition_id"],
    }
    connection.execute(
        """INSERT INTO strategy_entry.strategy_attempts(
               strategy_attempt_id,signal_id,strategy_id,strategy_version,
               strategy_config_fingerprint,entry_plan_fingerprint,
               strategy_activation_id,created_at,payload
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
           ON CONFLICT(strategy_attempt_id) DO NOTHING""",
        (
            attempt_id,
            row["signal_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["strategy_config_fingerprint"],
            row["entry_plan_fingerprint"],
            row["strategy_activation_id"],
            now,
            canonical_json(attempt_payload),
        ),
    )

    entry_policy = _mapping(card.get("entry_policy"), "R1 entry_policy")
    execution = _mapping(entry_policy.get("execution_policy"), "R1 execution_policy")
    max_age = int(str(execution.get("max_request_age_seconds") or 0))
    if max_age <= 0:
        raise RuntimeError("R1 reverse execution max_request_age_seconds invalid")

    admission = PostgresEntryAdmissionPort(connection).admit(
        EntryAdmissionRequest(
            account_ref=ACCOUNT_REF,
            exchange_position_key=(
                f"BYBIT:UNIFIED:LINEAR:USDT:{row['symbol']}:0"
            ),
            symbol=str(row["symbol"]),
            direction=str(row["to_direction"]),
            strategy_id=str(row["strategy_id"]),
            strategy_version=str(row["strategy_version"]),
            strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
            entry_plan_fingerprint=str(row["entry_plan_fingerprint"]),
            signal_id=str(row["signal_id"]),
            strategy_attempt_id=attempt_id,
            requested_amount=requested_amount,
            amount_currency="USDT",
            capacity_snapshot_id=capacity_id,
            capacity_observed_at=capacity_at,
            capacity_available=capacity_available,
            requested_at=now,
            pre_dispatch_expires_at=now + timedelta(seconds=max_age),
            account_state_generation_id=capacity_id,
        )
    )

    decision_id = "decision-" + fingerprint(
        {
            "reverse_transition_id": row["reverse_transition_id"],
            "attempt_id": attempt_id,
            "code": "ACCEPTED",
        }
    )[:32]
    decision_payload = {
        "entry_decision_id": decision_id,
        "strategy_attempt_id": attempt_id,
        "signal_id": row["signal_id"],
        "code": "ACCEPTED",
        "reason": "R1 confirmed flat; opposite Entry admitted atomically",
        "decided_at": now.isoformat(),
        "capacity_snapshot_id": capacity_id,
        "capital_reservation_id": admission.capital_reservation.reservation_id,
        "exchange_position_slot_claim_id": admission.exchange_position_slot_claim_id,
        "position_mode_state_ref": admission.position_mode_state_ref,
        "reverse_transition_id": row["reverse_transition_id"],
    }
    connection.execute(
        """INSERT INTO strategy_entry.entry_decisions(
               entry_decision_id,strategy_attempt_id,signal_id,decision_code,
               reason,decided_at,capacity_snapshot_id,capital_reservation_id,
               exchange_position_slot_claim_id,position_mode_state_ref,payload
           ) VALUES(%s,%s,%s,'ACCEPTED',%s,%s,%s,%s,%s,%s,%s::jsonb)
           ON CONFLICT(entry_decision_id) DO NOTHING""",
        (
            decision_id,
            attempt_id,
            row["signal_id"],
            decision_payload["reason"],
            now,
            capacity_id,
            admission.capital_reservation.reservation_id,
            admission.exchange_position_slot_claim_id,
            admission.position_mode_state_ref,
            canonical_json(decision_payload),
        ),
    )

    request_id = "request-" + fingerprint(
        {"reverse_transition_id": row["reverse_transition_id"], "decision": decision_id}
    )[:32]
    connection.execute(
        """INSERT INTO strategy_entry.execution_requests(
               execution_request_id,strategy_attempt_id,entry_decision_id,
               signal_id,strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,symbol,direction,requested_at,payload,
               exit_plan_fingerprint,capital_reservation_id,
               exchange_position_slot_claim_id,position_mode_state_ref
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
           ON CONFLICT(execution_request_id) DO NOTHING""",
        (
            request_id,
            attempt_id,
            decision_id,
            row["signal_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["strategy_config_fingerprint"],
            row["entry_plan_fingerprint"],
            row["symbol"],
            row["to_direction"],
            now,
            canonical_json(request_payload),
            row["exit_plan_fingerprint"],
            admission.capital_reservation.reservation_id,
            admission.exchange_position_slot_claim_id,
            admission.position_mode_state_ref,
        ),
    )
    state_id = "reqstate-" + request_id.removeprefix("request-")
    connection.execute(
        """INSERT INTO strategy_entry.execution_request_state_events(
               request_state_event_id,execution_request_id,state,occurred_at,reason,payload
           ) VALUES(%s,%s,'REQUEST_PENDING',%s,%s,%s::jsonb)
           ON CONFLICT(request_state_event_id) DO NOTHING""",
        (
            state_id,
            request_id,
            now,
            "R1 reverse flat confirmed; opposite open request created",
            canonical_json({"reverse_transition_id": row["reverse_transition_id"]}),
        ),
    )
    connection.execute(
        """UPDATE strategy_entry.reverse_transitions
              SET state='OPEN_REQUESTED',open_strategy_attempt_id=%s,
                  open_entry_decision_id=%s,open_execution_request_id=%s,
                  updated_at=%s
            WHERE reverse_transition_id=%s AND state='FLAT_CONFIRMED'""",
        (
            attempt_id,
            decision_id,
            request_id,
            now,
            row["reverse_transition_id"],
        ),
    )
    return request_id


def _advance_open(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    now: datetime,
) -> str:
    request_id = str(row.get("open_execution_request_id") or "")
    if not request_id:
        raise RuntimeError("R1 reverse open request id missing")
    dispatch = connection.execute(
        """SELECT state,command_id,reason
             FROM strategy_entry.execution_dispatches
            WHERE execution_request_id=%s""",
        (request_id,),
    ).fetchone()
    if str(row["state"]) == "OPEN_REQUESTED":
        if dispatch is None:
            return "WAITING_FOR_OPEN_DISPATCH"
        if str(dispatch["state"]) == "BLOCKED":
            connection.execute(
                """UPDATE strategy_entry.reverse_transitions
                      SET state='FAILED',updated_at=%s
                    WHERE reverse_transition_id=%s""",
                (now, row["reverse_transition_id"]),
            )
            return "OPEN_FAILED"
        if str(dispatch["state"]) != "DISPATCHED":
            raise RuntimeError(
                f"R1 reverse unsupported dispatch state={dispatch['state']}"
            )
        connection.execute(
            """UPDATE strategy_entry.reverse_transitions
                  SET state='OPEN_DISPATCHED',updated_at=%s
                WHERE reverse_transition_id=%s AND state='OPEN_REQUESTED'""",
            (now, row["reverse_transition_id"]),
        )
        return "OPEN_DISPATCHED"

    owned = connection.execute(
        """SELECT position_id,state
             FROM runtime.position_ownership
            WHERE entry_execution_request_id=%s
            ORDER BY fill_at DESC LIMIT 1""",
        (request_id,),
    ).fetchone()
    if owned is not None and str(owned["state"]) == "OPEN":
        connection.execute(
            """UPDATE strategy_entry.reverse_transitions
                  SET state='COMPLETED',completed_at=%s,updated_at=%s
                WHERE reverse_transition_id=%s AND state='OPEN_DISPATCHED'""",
            (now, now, row["reverse_transition_id"]),
        )
        return "COMPLETED"
    request_state = connection.execute(
        """SELECT state,reason
             FROM strategy_entry.execution_request_state_events
            WHERE execution_request_id=%s
            ORDER BY occurred_at DESC,created_at DESC LIMIT 1""",
        (request_id,),
    ).fetchone()
    if request_state is not None and str(request_state["state"]) in {
        "REQUEST_CANCELLED",
        "REQUEST_RECONCILIATION_REQUIRED",
        "REQUEST_EXPIRED",
    }:
        terminal = (
            "RECONCILIATION_REQUIRED"
            if str(request_state["state"]) == "REQUEST_RECONCILIATION_REQUIRED"
            else "FAILED"
        )
        connection.execute(
            """UPDATE strategy_entry.reverse_transitions
                  SET state=%s,updated_at=%s
                WHERE reverse_transition_id=%s""",
            (terminal, now, row["reverse_transition_id"]),
        )
        return terminal
    return "WAITING_FOR_OPEN_FILL"


def run_once(connection: psycopg.Connection[Any], *, now: datetime | None = None) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    with connection.transaction():
        row = _next_transition(connection)
        if row is None:
            return "NO_TRANSITION"
        state = str(row["state"])
        # The close half belongs to the already-owned position and must remain
        # executable even when new Entry is disarmed. Re-opening the opposite
        # direction is a new Entry and therefore requires gate + exact permission.
        if state == "REQUESTED":
            command_id = _queue_close(connection, row, current)
            return f"CLOSE_DISPATCHED:{command_id}"
        if state == "CLOSE_DISPATCHED":
            return _confirm_flat(connection, row, current)
        if state == "FLAT_CONFIRMED":
            if not _execution_gate_enabled(connection) or not _execution_permission_enabled(
                connection, row
            ):
                connection.execute(
                    """UPDATE strategy_entry.reverse_transitions
                          SET state='COMPLETED',completed_at=%s,updated_at=%s,
                              payload=payload || %s::jsonb
                        WHERE reverse_transition_id=%s AND state='FLAT_CONFIRMED'""",
                    (
                        current,
                        current,
                        json.dumps(
                            {
                                "reopen_skipped": True,
                                "reopen_skip_reason": "NEW_ENTRY_DISARMED",
                            },
                            ensure_ascii=False,
                        ),
                        row["reverse_transition_id"],
                    ),
                )
                return "COMPLETED_FLAT_NO_REOPEN"
            request_id = _create_open_request(connection, row, current)
            return f"OPEN_REQUESTED:{request_id}"
        if state in {"OPEN_REQUESTED", "OPEN_DISPATCHED"}:
            return _advance_open(connection, row, current)
        raise RuntimeError(f"unsupported R1 reverse state: {state}")


def main() -> int:
    if POLL_SECONDS <= 0:
        raise SystemExit("CRIPTA_R1_REVERSE_POLL_SECONDS must be positive")
    if CONSUMER_ARM != "ENABLED":
        raise SystemExit("R1 reverse mainnet worker is explicitly disabled")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with psycopg.connect(DB_DSN, row_factory=dict_row) as connection:
        while running:
            try:
                result = run_once(connection)
            except Exception as exc:
                connection.rollback()
                print(
                    json.dumps(
                        {"state": "ERROR", "error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                time.sleep(max(POLL_SECONDS, 1.0))
                continue
            if result in {"NO_TRANSITION", "WAITING_FOR_FLAT"}:
                time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())