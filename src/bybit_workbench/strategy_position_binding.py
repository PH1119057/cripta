from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection


@dataclass(frozen=True, slots=True)
class UniversalEntryLineage:
    execution_request_id: str
    signal_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    exit_plan_fingerprint: str
    strategy_attempt_id: str
    entry_decision_id: str
    strategy_activation_id: str
    capital_reservation_id: str
    account_ref: str
    capital_reservation_state: str

    def __post_init__(self) -> None:
        for field in (
            "execution_request_id",
            "signal_id",
            "strategy_id",
            "strategy_version",
            "strategy_config_fingerprint",
            "entry_plan_fingerprint",
            "exit_plan_fingerprint",
            "strategy_attempt_id",
            "entry_decision_id",
            "strategy_activation_id",
            "capital_reservation_id",
            "account_ref",
            "capital_reservation_state",
        ):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"Universal Entry lineage requires {field}")
        if self.capital_reservation_state not in {
            "DISPATCHED",
            "PENDING_EXCHANGE_REFLECTION",
            "RECONCILIATION_REQUIRED",
            "CONSUMED",
        }:
            raise ValueError(
                "Universal Entry fill has invalid capital reservation state "
                f"{self.capital_reservation_state}"
            )

    def validate_command_payload(self, payload: Mapping[str, object]) -> None:
        expected = {
            "source": "universal_entry",
            "execution_request_id": self.execution_request_id,
            "signal_id": self.signal_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_fingerprint": self.strategy_config_fingerprint,
            "entry_plan_fingerprint": self.entry_plan_fingerprint,
            "exit_plan_fingerprint": self.exit_plan_fingerprint,
            "strategy_attempt_id": self.strategy_attempt_id,
            "entry_decision_id": self.entry_decision_id,
            "strategy_activation_id": self.strategy_activation_id,
            "capital_reservation_id": self.capital_reservation_id,
            "bot_instance_id": "universal-entry",
        }
        for field, value in expected.items():
            if str(payload.get(field) or "") != value:
                raise RuntimeError(f"Universal Entry command/{field} lineage mismatch")


class CursorLike(Protocol):
    rowcount: int

    def fetchone(self) -> Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


def exchange_position_key(symbol: str, position_idx: int) -> str:
    if position_idx < 0:
        raise ValueError("position_idx cannot be negative")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return f"BYBIT:UNIFIED:LINEAR:USDT:{normalized}:{position_idx}"


def load_universal_entry_lineage(
    connection: ConnectionLike,
    *,
    command_id: str,
    command_payload: Mapping[str, object],
) -> UniversalEntryLineage | None:
    row = connection.execute(
        """SELECT d.execution_request_id,r.signal_id,r.strategy_id,r.strategy_version,
                  r.strategy_config_fingerprint,r.entry_plan_fingerprint,
                  r.exit_plan_fingerprint,r.strategy_attempt_id,r.entry_decision_id,
                  t.strategy_activation_id,r.capital_reservation_id,c.account_ref,c.state
             FROM strategy_entry.execution_dispatches d
             JOIN strategy_entry.execution_requests r
               ON r.execution_request_id=d.execution_request_id
             JOIN strategy_entry.strategy_attempts t
               ON t.strategy_attempt_id=r.strategy_attempt_id
              AND t.signal_id=r.signal_id
             JOIN runtime.capital_reservations c
               ON c.reservation_id=r.capital_reservation_id
              AND c.strategy_attempt_id=r.strategy_attempt_id
            WHERE d.command_id=%s
              AND d.state='DISPATCHED'
              AND d.capital_reservation_id=r.capital_reservation_id""",
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    lineage = UniversalEntryLineage(
        execution_request_id=str(row[0]),
        signal_id=str(row[1]),
        strategy_id=str(row[2]),
        strategy_version=str(row[3]),
        strategy_config_fingerprint=str(row[4]),
        entry_plan_fingerprint=str(row[5]),
        exit_plan_fingerprint=str(row[6]),
        strategy_attempt_id=str(row[7]),
        entry_decision_id=str(row[8]),
        strategy_activation_id=str(row[9]),
        capital_reservation_id=str(row[10]),
        account_ref=str(row[11]),
        capital_reservation_state=str(row[12]),
    )
    lineage.validate_command_payload(command_payload)
    return lineage


def persist_universal_strategy_position(
    connection: ConnectionLike,
    *,
    lineage: UniversalEntryLineage,
    position_id: str,
    trade_id: str,
    entry_command_id: str,
    symbol: str,
    side: str,
    actual_avg_fill: Decimal,
    actual_qty: Decimal,
    fill_at: datetime,
    exchange_order_ids: Sequence[str],
    client_order_ids: Sequence[str],
    execution_ids: Sequence[str],
    position_idx: int,
) -> StrategyPosition:
    if side not in {"Buy", "Sell"}:
        raise ValueError(f"unsupported Bybit position side: {side}")
    if actual_avg_fill <= 0 or actual_qty <= 0:
        raise ValueError("actual fill and quantity must be positive")
    key = exchange_position_key(symbol, position_idx)
    connection.execute(
        """INSERT INTO runtime.position_ownership(
               position_id,trade_id,bot_instance_id,strategy_id,strategy_version,
               signal_id,entry_command_id,geometry_handoff_id,symbol,side,
               actual_avg_fill,actual_qty,fill_at,exchange_order_ids,
               client_order_ids,execution_ids,exchange_position_key,position_idx,
               account_ref,strategy_config_fingerprint,strategy_activation_id,
               strategy_attempt_id,entry_decision_id,entry_execution_request_id,
               entry_plan_fingerprint,exit_plan_fingerprint)
           VALUES(%s,%s,'universal-entry',%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,
                  %s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(entry_command_id) DO UPDATE SET
               actual_avg_fill=excluded.actual_avg_fill,
               actual_qty=excluded.actual_qty,
               exchange_order_ids=excluded.exchange_order_ids,
               client_order_ids=excluded.client_order_ids,
               execution_ids=excluded.execution_ids,
               exchange_position_key=excluded.exchange_position_key,
               position_idx=excluded.position_idx
           RETURNING position_id,strategy_id,strategy_version,
                     strategy_config_fingerprint,strategy_activation_id,
                     signal_id,strategy_attempt_id,entry_decision_id,
                     entry_execution_request_id,entry_plan_fingerprint,
                     exit_plan_fingerprint,account_ref,exchange_position_key,
                     position_idx,symbol,side,actual_avg_fill,actual_qty,fill_at,
                     entry_command_id""",
        (
            position_id,
            trade_id,
            lineage.strategy_id,
            lineage.strategy_version,
            lineage.signal_id,
            entry_command_id,
            symbol,
            side,
            actual_avg_fill,
            actual_qty,
            fill_at,
            json.dumps(sorted(set(exchange_order_ids))),
            json.dumps(sorted(set(client_order_ids))),
            json.dumps(tuple(execution_ids)),
            key,
            position_idx,
            lineage.account_ref,
            lineage.strategy_config_fingerprint,
            lineage.strategy_activation_id,
            lineage.strategy_attempt_id,
            lineage.entry_decision_id,
            lineage.execution_request_id,
            lineage.entry_plan_fingerprint,
            lineage.exit_plan_fingerprint,
        ),
    )
    stored = connection.execute(
        """SELECT position_id,strategy_id,strategy_version,
                  strategy_config_fingerprint,strategy_activation_id,
                  signal_id,strategy_attempt_id,entry_decision_id,
                  entry_execution_request_id,entry_plan_fingerprint,
                  exit_plan_fingerprint,account_ref,exchange_position_key,
                  position_idx,symbol,side,actual_avg_fill,actual_qty,fill_at,
                  entry_command_id
             FROM runtime.position_ownership
            WHERE entry_command_id=%s""",
        (entry_command_id,),
    ).fetchone()
    if stored is None:
        raise RuntimeError("StrategyPosition persistence disappeared")
    actual_lineage = tuple(str(stored[i]) for i in range(1, 12))
    expected_lineage = (
        lineage.strategy_id,
        lineage.strategy_version,
        lineage.strategy_config_fingerprint,
        lineage.strategy_activation_id,
        lineage.signal_id,
        lineage.strategy_attempt_id,
        lineage.entry_decision_id,
        lineage.execution_request_id,
        lineage.entry_plan_fingerprint,
        lineage.exit_plan_fingerprint,
        lineage.account_ref,
    )
    if actual_lineage != expected_lineage:
        raise RuntimeError("persisted StrategyPosition immutable lineage mismatch")
    stored_position_id = str(stored[0])
    if stored_position_id != position_id:
        raise RuntimeError("entry command is already bound to another StrategyPosition")

    reservation = connection.execute(
        """SELECT state,strategy_position_id,exchange_commitment_ref
             FROM runtime.capital_reservations
            WHERE reservation_id=%s
            FOR UPDATE""",
        (lineage.capital_reservation_id,),
    ).fetchone()
    if reservation is None:
        raise RuntimeError("StrategyPosition lost capital reservation")
    reservation_state = str(reservation[0])
    existing_position = None if reservation[1] is None else str(reservation[1])
    existing_ref = None if reservation[2] is None else str(reservation[2])
    if reservation_state == "CONSUMED":
        if existing_position != position_id or existing_ref != position_id:
            raise RuntimeError("consumed capital reservation is bound to another position")
    else:
        if reservation_state not in {
            "DISPATCHED",
            "PENDING_EXCHANGE_REFLECTION",
            "RECONCILIATION_REQUIRED",
        }:
            raise RuntimeError(
                f"cannot bind StrategyPosition to reservation state {reservation_state}"
            )
        updated = connection.execute(
            """UPDATE runtime.capital_reservations
                  SET state='CONSUMED',
                      strategy_position_id=%s,
                      exchange_commitment_ref=%s,
                      exchange_commitment_at=%s
                WHERE reservation_id=%s
                  AND state IN (
                      'DISPATCHED','PENDING_EXCHANGE_REFLECTION',
                      'RECONCILIATION_REQUIRED'
                  )""",
            (
                position_id,
                position_id,
                fill_at,
                lineage.capital_reservation_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("capital reservation consumption race")

    direction = TradeDirection.LONG if side == "Buy" else TradeDirection.SHORT
    return StrategyPosition(
        strategy_position_id=position_id,
        account_ref=lineage.account_ref,
        exchange_position_key=key,
        position_idx=position_idx,
        strategy_id=lineage.strategy_id,
        strategy_version=lineage.strategy_version,
        strategy_config_fingerprint=lineage.strategy_config_fingerprint,
        strategy_activation_id=lineage.strategy_activation_id,
        signal_id=lineage.signal_id,
        strategy_attempt_id=lineage.strategy_attempt_id,
        entry_decision_id=lineage.entry_decision_id,
        entry_execution_request_id=lineage.execution_request_id,
        entry_plan_fingerprint=lineage.entry_plan_fingerprint,
        exit_plan_fingerprint=lineage.exit_plan_fingerprint,
        entry_command_id=entry_command_id,
        symbol=symbol,
        direction=direction,
        actual_avg_fill=actual_avg_fill,
        actual_qty=actual_qty,
        fill_at=fill_at,
    )


def release_position_capital_reservation(
    connection: ConnectionLike,
    *,
    position_id: str,
    entry_execution_request_id: str | None,
) -> None:
    if not entry_execution_request_id:
        return
    row = connection.execute(
        """SELECT c.reservation_id,c.state,c.strategy_position_id
             FROM strategy_entry.execution_requests r
             JOIN runtime.capital_reservations c
               ON c.reservation_id=r.capital_reservation_id
            WHERE r.execution_request_id=%s
            FOR UPDATE OF c""",
        (entry_execution_request_id,),
    ).fetchone()
    if row is None:
        return
    reservation_id, state, bound_position = (
        str(row[0]),
        str(row[1]),
        None if row[2] is None else str(row[2]),
    )
    if bound_position != position_id:
        raise RuntimeError("capital reservation/StrategyPosition close binding mismatch")
    if state == "RELEASED":
        return
    if state != "CONSUMED":
        raise RuntimeError(f"cannot release capital reservation from state {state}")
    updated = connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='RELEASED'
            WHERE reservation_id=%s
              AND state='CONSUMED'
              AND strategy_position_id=%s""",
        (reservation_id, position_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError("capital reservation release race")
