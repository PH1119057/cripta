import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from bybit_workbench.domain.intents import (
    CancelEntryIntent,
    EnterIntent,
    ExitIntent,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.models import Order, OrderRequest, Position
from bybit_workbench.domain.types import OrderRole, OrderSide, OrderStatus, OrderType, PositionSide
from bybit_workbench.execution import (
    ExecutionCommandKind,
    ExecutionCommandRecord,
    ExecutionCommandStatus,
)
from bybit_workbench.historical.gate import (
    HistoricalEligibilityQuery,
    HistoricalEligibilityRecord,
    eligibility_binding_fingerprint,
)
from bybit_workbench.replay.models import ReplayFill
from bybit_workbench.risk.models import RiskDecision

from .database import open_database

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "signature",
    "authorization",
    "x_bapi_sign",
    "x-bapi-sign",
)


@dataclass(frozen=True, slots=True)
class LocalProjection:
    symbol: str
    position: Position
    active_orders: tuple[Order, ...]
    replay_snapshot: dict[str, Any] | None


class TradingJournal:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection = open_database(self.path)

    def close(self) -> None:
        self._connection.close()

    def create_execution_command(
        self,
        command_id: str,
        kind: ExecutionCommandKind,
        idempotency_key: str,
        symbol: str,
        request: Mapping[str, Any],
        *,
        intent_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ExecutionCommandRecord:
        if not command_id or not idempotency_key or not symbol:
            raise ValueError("execution command identifiers and symbol are required")
        payload = canonical_json(request)
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        timestamp = created_at or datetime.now(UTC)
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO execution_commands
                    (command_id, kind, idempotency_key, symbol, intent_id,
                     request_json, request_checksum, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    kind.value,
                    idempotency_key,
                    symbol,
                    intent_id,
                    payload,
                    checksum,
                    ExecutionCommandStatus.PLANNED.value,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM execution_commands WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("execution command insert could not be read back")
            if cursor.rowcount == 0:
                identity = (row["kind"], row["symbol"], row["intent_id"], row["request_checksum"])
                expected = (kind.value, symbol, intent_id, checksum)
                if identity != expected:
                    raise ValueError(
                        "execution idempotency key was reused with different command data"
                    )
        return _execution_command_from_row(row)

    def save_historical_validation_report(
        self,
        report_id: str,
        report: Any,
    ) -> None:
        if not report_id:
            raise ValueError("historical validation report_id is required")
        rules_fingerprint = report.instrument_rules_fingerprint
        maker = report.maker_fee_rate
        taker = report.taker_fee_rate
        if rules_fingerprint is None or maker is None or taker is None:
            binding_fingerprint = None
        else:
            query = HistoricalEligibilityQuery(
                report.symbol,
                report.timeframe,
                report.code_version,
                maker,
                taker,
                report.slippage_percent,
                report.execution_mode,
                report.price_trigger,
                rules_fingerprint,
            )
            binding_fingerprint = eligibility_binding_fingerprint(
                strategy_id=report.strategy_id,
                strategy_version=report.strategy_version,
                parameters_fingerprint=report.parameters_fingerprint,
                query=query,
                dataset_fingerprint=report.dataset_fingerprint,
            )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO historical_validation_reports
                    (report_id, strategy_id, strategy_version, parameters_fingerprint,
                     dataset_fingerprint, eligible_for_testnet, report_json, generated_at,
                     symbol, timeframe, code_version, maker_fee_rate, taker_fee_rate,
                     slippage_percent, execution_mode, price_trigger,
                     instrument_rules_fingerprint, production_equivalent, binding_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    report.strategy_id,
                    report.strategy_version,
                    report.parameters_fingerprint,
                    report.dataset_fingerprint,
                    int(report.eligible_for_micro_live),
                    canonical_json(report),
                    report.generated_at.isoformat(),
                    report.symbol,
                    report.timeframe,
                    report.code_version,
                    None if maker is None else str(maker),
                    None if taker is None else str(taker),
                    str(report.slippage_percent),
                    report.execution_mode,
                    report.price_trigger,
                    rules_fingerprint,
                    int(report.production_equivalent),
                    binding_fingerprint,
                ),
            )

    def latest_historical_eligibility(
        self,
        strategy_id: str,
        strategy_version: str,
        parameters_fingerprint: str,
        query: HistoricalEligibilityQuery,
    ) -> HistoricalEligibilityRecord | None:
        row = self._connection.execute(
            """
            SELECT report_id, eligible_for_testnet, dataset_fingerprint,
                   binding_fingerprint, production_equivalent
            FROM historical_validation_reports
            WHERE strategy_id=? AND strategy_version=? AND parameters_fingerprint=?
              AND symbol=? AND timeframe=? AND code_version=?
              AND maker_fee_rate=? AND taker_fee_rate=? AND slippage_percent=?
              AND execution_mode=? AND price_trigger=?
              AND instrument_rules_fingerprint=?
            ORDER BY generated_at DESC, report_id DESC
            LIMIT 1
            """,
            (
                strategy_id,
                strategy_version,
                parameters_fingerprint,
                query.symbol,
                query.timeframe,
                query.code_version,
                str(query.maker_fee_rate),
                str(query.taker_fee_rate),
                str(query.slippage_percent),
                query.execution_mode,
                query.price_trigger,
                query.instrument_rules_fingerprint,
            ),
        ).fetchone()
        if row is None:
            return None
        binding = row["binding_fingerprint"]
        if not binding:
            return None
        return HistoricalEligibilityRecord(
            row["report_id"],
            bool(row["eligible_for_testnet"]),
            row["dataset_fingerprint"],
            binding,
            bool(row["production_equivalent"]),
        )

    def update_execution_command(
        self,
        command_id: str,
        status: ExecutionCommandStatus,
        *,
        exchange_order_id: str | None = None,
        response: Mapping[str, Any] | None = None,
        error: str | None = None,
        updated_at: datetime | None = None,
    ) -> ExecutionCommandRecord:
        timestamp = updated_at or datetime.now(UTC)
        with self._connection:
            row = self._connection.execute(
                "SELECT * FROM execution_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown execution command: {command_id}")
            current = ExecutionCommandStatus(row["status"])
            if status is not current and status not in _COMMAND_TRANSITIONS[current]:
                raise ValueError(f"invalid execution command transition {current} -> {status}")
            selected_order_id = exchange_order_id or row["exchange_order_id"]
            if (
                exchange_order_id
                and row["exchange_order_id"]
                and exchange_order_id != row["exchange_order_id"]
            ):
                raise ValueError("execution command exchange order id cannot change")
            self._connection.execute(
                """
                UPDATE execution_commands
                SET status=?, exchange_order_id=?,
                    response_json=COALESCE(?, response_json), error=?, updated_at=?
                WHERE command_id=?
                """,
                (
                    status.value,
                    selected_order_id,
                    None if response is None else canonical_json(response),
                    error,
                    timestamp.isoformat(),
                    command_id,
                ),
            )
            updated = self._connection.execute(
                "SELECT * FROM execution_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
        return _execution_command_from_row(updated)

    def execution_command(
        self,
        *,
        command_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionCommandRecord | None:
        if (command_id is None) == (idempotency_key is None):
            raise ValueError("provide exactly one execution command identifier")
        column = "command_id" if command_id is not None else "idempotency_key"
        value = command_id if command_id is not None else idempotency_key
        row = self._connection.execute(
            f"SELECT * FROM execution_commands WHERE {column}=?",
            (value,),
        ).fetchone()
        return None if row is None else _execution_command_from_row(row)

    def recent_execution_commands(
        self,
        limit: int = 100,
    ) -> tuple[ExecutionCommandRecord, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            "SELECT * FROM execution_commands ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_execution_command_from_row(row) for row in rows)

    def save_settings_version(
        self,
        profile: str,
        settings: Mapping[str, Any],
        *,
        created_at: datetime | None = None,
    ) -> int:
        payload = canonical_json(settings)
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        occurred_at = (created_at or datetime.now(UTC)).isoformat()
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO settings_versions
                    (profile, settings_json, checksum, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (profile, payload, checksum, occurred_at),
            )
        row = self._connection.execute(
            "SELECT id FROM settings_versions WHERE profile=? AND checksum=?",
            (profile, checksum),
        ).fetchone()
        return int(row["id"])

    def start_strategy_run(
        self,
        run_id: str,
        *,
        strategy_id: str,
        strategy_version: str,
        mode: str,
        symbol: str,
        parameters: Mapping[str, Any],
        code_version: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO strategy_runs
                    (run_id, strategy_id, strategy_version, code_version, mode, symbol,
                     parameters_json, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?)
                """,
                (
                    run_id,
                    strategy_id,
                    strategy_version,
                    code_version,
                    mode,
                    symbol,
                    canonical_json(parameters),
                    (started_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def finish_strategy_run(
        self,
        run_id: str,
        status: str,
        *,
        ended_at: datetime | None = None,
    ) -> None:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE strategy_runs SET status=?, ended_at=? WHERE run_id=?",
                (status, (ended_at or datetime.now(UTC)).isoformat(), run_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"unknown strategy run: {run_id}")

    def record_strategy_decision(
        self,
        decision_id: str,
        run_id: str,
        *,
        inputs: Mapping[str, Any],
        decision: Mapping[str, Any],
        candle_at: datetime | None = None,
        created_at: datetime | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO strategy_decisions
                    (decision_id, run_id, candle_at, inputs_json, decision_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    run_id,
                    None if candle_at is None else candle_at.isoformat(),
                    canonical_json(inputs),
                    canonical_json(decision),
                    (created_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def record_trade_intent(
        self,
        intent: (
            EnterIntent | ExitIntent | UpdateProtectionIntent | CancelEntryIntent | NoOpIntent
        ),
        run_id: str,
        *,
        decision_id: str | None,
        created_at: datetime | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO trade_intents
                    (intent_id, run_id, decision_id, intent_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    run_id,
                    decision_id,
                    type(intent).__name__,
                    canonical_json(intent),
                    (created_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def record_risk_decision(
        self,
        risk_decision_id: str,
        intent_id: str,
        decision: RiskDecision,
        *,
        created_at: datetime | None = None,
    ) -> None:
        order_json = (
            None if decision.normalized_order is None else canonical_json(decision.normalized_order)
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO risk_decisions
                    (risk_decision_id, intent_id, approved, checks_json,
                     normalized_order_json, normalized_stop, risk_budget,
                     estimated_loss_at_stop, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    risk_decision_id,
                    intent_id,
                    int(decision.approved),
                    canonical_json(decision.checks),
                    order_json,
                    _decimal_or_none(decision.normalized_stop),
                    _decimal_or_none(decision.risk_budget),
                    _decimal_or_none(decision.estimated_loss_at_stop),
                    (created_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def upsert_order(
        self,
        order: Order,
        *,
        intent_id: str | None = None,
        event_id: str | None = None,
        raw_payload: Mapping[str, Any] | None = None,
    ) -> None:
        request = order.request
        with self._connection:
            existing_order = self._connection.execute(
                """
                SELECT client_order_id, symbol, side, role, reduce_only
                FROM orders WHERE order_id=?
                """,
                (order.order_id,),
            ).fetchone()
            immutable_values = (
                request.client_order_id,
                request.symbol,
                request.side.value,
                request.role.value,
                int(request.reduce_only),
            )
            if existing_order is not None and tuple(existing_order) != immutable_values:
                raise ValueError("order id was reused with different immutable identity")
            self._connection.execute(
                """
                INSERT INTO orders
                    (order_id, client_order_id, intent_id, symbol, side, order_type, role,
                     reduce_only, quantity, price, status, filled_quantity, average_price,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    intent_id=COALESCE(orders.intent_id, excluded.intent_id),
                    order_type=excluded.order_type,
                    quantity=excluded.quantity,
                    price=excluded.price,
                    status=excluded.status,
                    filled_quantity=excluded.filled_quantity,
                    average_price=excluded.average_price,
                    updated_at=excluded.updated_at
                """,
                (
                    order.order_id,
                    request.client_order_id,
                    intent_id,
                    request.symbol,
                    request.side.value,
                    request.order_type.value,
                    request.role.value,
                    int(request.reduce_only),
                    str(request.quantity),
                    _decimal_or_none(request.price),
                    order.status.value,
                    str(order.filled_quantity),
                    _decimal_or_none(order.average_price),
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )
            if event_id is not None:
                history_cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO order_state_history
                        (order_id, event_id, status, cumulative_filled_quantity,
                         average_price, occurred_at, raw_payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.order_id,
                        event_id,
                        order.status.value,
                        str(order.filled_quantity),
                        _decimal_or_none(order.average_price),
                        order.updated_at.isoformat(),
                        canonical_json(raw_payload or {}),
                    ),
                )
                if history_cursor.rowcount == 0:
                    existing = self._connection.execute(
                        """
                        SELECT order_id, status, cumulative_filled_quantity, average_price
                        FROM order_state_history WHERE event_id=?
                        """,
                        (event_id,),
                    ).fetchone()
                    expected = (
                        order.order_id,
                        order.status.value,
                        str(order.filled_quantity),
                        _decimal_or_none(order.average_price),
                    )
                    actual = tuple(existing) if existing is not None else None
                    if actual != expected:
                        raise ValueError("order event id was reused with different data")

    def record_replay_fill(
        self,
        fill: ReplayFill,
        *,
        order_id: str,
        symbol: str,
        raw_payload: Mapping[str, Any] | None = None,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO executions
                    (execution_id, order_id, client_order_id, symbol, side, quantity,
                     price, fee, reason, executed_at, raw_payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.execution_id,
                    order_id,
                    fill.client_order_id,
                    symbol,
                    fill.side.value,
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.fee),
                    fill.reason.value,
                    fill.occurred_at.isoformat(),
                    canonical_json(raw_payload or {}),
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                existing = self._connection.execute(
                    """
                    SELECT order_id, client_order_id, symbol, side, quantity, price,
                           fee, reason, executed_at
                    FROM executions WHERE execution_id=?
                    """,
                    (fill.execution_id,),
                ).fetchone()
                expected = (
                    order_id,
                    fill.client_order_id,
                    symbol,
                    fill.side.value,
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.fee),
                    fill.reason.value,
                    fill.occurred_at.isoformat(),
                )
                actual = tuple(existing) if existing is not None else None
                if actual != expected:
                    raise ValueError("execution id was reused with different data")
        return inserted

    def record_position_snapshot(
        self,
        position: Position,
        *,
        source: str,
        run_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO position_snapshots
                    (symbol, side, quantity, average_price, source, observed_at, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.symbol,
                    position.side.value,
                    str(position.quantity),
                    _decimal_or_none(position.average_price),
                    source,
                    (observed_at or datetime.now(UTC)).isoformat(),
                    run_id,
                ),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a position snapshot id")
        return cursor.lastrowid

    def record_stop_update(
        self,
        *,
        run_id: str | None,
        intent_id: str | None = None,
        order_id: str | None = None,
        symbol: str,
        status: str,
        price: Decimal,
        protected_quantity: Decimal,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO stop_updates
                    (run_id, symbol, status, price, protected_quantity, reason, occurred_at,
                     intent_id, order_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    symbol,
                    status,
                    str(price),
                    str(protected_quantity),
                    reason,
                    (occurred_at or datetime.now(UTC)).isoformat(),
                    intent_id,
                    order_id,
                ),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a stop update id")
        return cursor.lastrowid

    def save_engine_snapshot(
        self,
        run_id: str,
        snapshot_type: str,
        snapshot: Mapping[str, Any],
        *,
        created_at: datetime | None = None,
    ) -> int:
        with self._connection:
            self._connection.execute(
                """
                UPDATE engine_snapshots SET is_current=0
                WHERE run_id=? AND snapshot_type=? AND is_current=1
                """,
                (run_id, snapshot_type),
            )
            cursor = self._connection.execute(
                """
                INSERT INTO engine_snapshots
                    (run_id, snapshot_type, snapshot_json, is_current, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (
                    run_id,
                    snapshot_type,
                    canonical_json(snapshot),
                    (created_at or datetime.now(UTC)).isoformat(),
                ),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an engine snapshot id")
        return cursor.lastrowid

    def load_current_engine_snapshot(
        self,
        run_id: str,
        snapshot_type: str,
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT snapshot_json FROM engine_snapshots
            WHERE run_id=? AND snapshot_type=? AND is_current=1
            ORDER BY id DESC LIMIT 1
            """,
            (run_id, snapshot_type),
        ).fetchone()
        return None if row is None else json.loads(row["snapshot_json"])

    def start_reconciliation(
        self,
        reconciliation_id: str,
        symbol: str,
        *,
        started_at: datetime | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO reconciliation_runs
                    (reconciliation_id, symbol, status, discrepancies_json, started_at)
                VALUES (?, ?, 'RUNNING', '[]', ?)
                """,
                (
                    reconciliation_id,
                    symbol,
                    (started_at or datetime.now(UTC)).isoformat(),
                ),
            )

    def finish_reconciliation(
        self,
        reconciliation_id: str,
        *,
        synchronized: bool,
        discrepancies: Any,
        finished_at: datetime | None = None,
    ) -> None:
        status = "SYNCHRONIZED" if synchronized else "MISMATCH"
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE reconciliation_runs
                SET status=?, discrepancies_json=?, finished_at=?
                WHERE reconciliation_id=?
                """,
                (
                    status,
                    canonical_json(discrepancies),
                    (finished_at or datetime.now(UTC)).isoformat(),
                    reconciliation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"unknown reconciliation: {reconciliation_id}")

    def load_projection(self, symbol: str, *, run_id: str | None = None) -> LocalProjection:
        position_row = self._connection.execute(
            """
            SELECT symbol, side, quantity, average_price
            FROM position_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        position = (
            Position(symbol, PositionSide.FLAT, Decimal("0"), None)
            if position_row is None
            else Position(
                position_row["symbol"],
                PositionSide(position_row["side"]),
                Decimal(position_row["quantity"]),
                _parse_optional_decimal(position_row["average_price"]),
            )
        )
        order_rows = self._connection.execute(
            """
            SELECT * FROM orders
            WHERE symbol=? AND status IN (?, ?)
            ORDER BY created_at, order_id
            """,
            (symbol, OrderStatus.ACCEPTED.value, OrderStatus.PARTIALLY_FILLED.value),
        ).fetchall()
        active_orders = tuple(_order_from_row(row) for row in order_rows)
        snapshot = None if run_id is None else self.load_current_engine_snapshot(run_id, "replay")
        return LocalProjection(symbol, position, active_orders, snapshot)

    def trace_for_intent(self, intent_id: str) -> dict[str, Any]:
        intent_row = self._connection.execute(
            "SELECT * FROM trade_intents WHERE intent_id=?", (intent_id,)
        ).fetchone()
        if intent_row is None:
            raise LookupError(f"unknown trade intent: {intent_id}")
        run_row = self._connection.execute(
            "SELECT * FROM strategy_runs WHERE run_id=?", (intent_row["run_id"],)
        ).fetchone()
        decision_row = None
        if intent_row["decision_id"] is not None:
            decision_row = self._connection.execute(
                "SELECT * FROM strategy_decisions WHERE decision_id=?",
                (intent_row["decision_id"],),
            ).fetchone()
        risk_rows = self._connection.execute(
            "SELECT * FROM risk_decisions WHERE intent_id=? ORDER BY created_at",
            (intent_id,),
        ).fetchall()
        order_rows = self._connection.execute(
            "SELECT * FROM orders WHERE intent_id=? ORDER BY created_at", (intent_id,)
        ).fetchall()
        order_ids = [row["order_id"] for row in order_rows]
        executions: list[sqlite3.Row] = []
        if order_ids:
            placeholders = ",".join("?" for _ in order_ids)
            executions = self._connection.execute(
                f"SELECT * FROM executions WHERE order_id IN ({placeholders}) ORDER BY executed_at",
                order_ids,
            ).fetchall()
        stop_rows = self._connection.execute(
            """
            SELECT * FROM stop_updates
            WHERE intent_id=? OR (intent_id IS NULL AND run_id=?)
            ORDER BY id
            """,
            (intent_id, intent_row["run_id"]),
        ).fetchall()
        position_rows = self._connection.execute(
            "SELECT * FROM position_snapshots WHERE run_id=? ORDER BY id",
            (intent_row["run_id"],),
        ).fetchall()
        return {
            "run": _row_to_dict(run_row),
            "decision": _row_to_dict(decision_row),
            "intent": _row_to_dict(intent_row),
            "risk_decisions": [_row_to_dict(row) for row in risk_rows],
            "orders": [_row_to_dict(row) for row in order_rows],
            "executions": [_row_to_dict(row) for row in executions],
            "position_snapshots": [_row_to_dict(row) for row in position_rows],
            "stop_updates": [_row_to_dict(row) for row in stop_rows],
        }

    def table_count(self, table: str) -> int:
        allowed = {
            "settings_versions",
            "strategy_runs",
            "strategy_decisions",
            "trade_intents",
            "risk_decisions",
            "orders",
            "order_state_history",
            "executions",
            "position_snapshots",
            "stop_updates",
            "reconciliation_runs",
            "engine_snapshots",
            "system_events",
            "execution_commands",
            "historical_validation_reports",
        }
        if table not in allowed:
            raise ValueError("unknown table")
        return int(self._connection.execute(f"SELECT COUNT(1) FROM {table}").fetchone()[0])


def sanitize_for_storage(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)  # type: ignore[arg-type]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if any(part.replace("-", "_") in normalized for part in _SENSITIVE_KEY_PARTS):
                result[key_text] = "***REDACTED***"
            else:
                result[key_text] = sanitize_for_storage(nested)
        return result
    if isinstance(value, (set, frozenset)):
        sanitized = [sanitize_for_storage(item) for item in value]
        return sorted(
            sanitized,
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, (list, tuple)):
        return [sanitize_for_storage(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        sanitize_for_storage(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _parse_optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _order_from_row(row: sqlite3.Row) -> Order:
    request = OrderRequest(
        client_order_id=row["client_order_id"],
        symbol=row["symbol"],
        side=OrderSide(row["side"]),
        order_type=OrderType(row["order_type"]),
        quantity=Decimal(row["quantity"]),
        price=_parse_optional_decimal(row["price"]),
        reduce_only=bool(row["reduce_only"]),
        role=OrderRole(row["role"]),
    )
    return Order(
        order_id=row["order_id"],
        request=request,
        status=OrderStatus(row["status"]),
        filled_quantity=Decimal(row["filled_quantity"]),
        average_price=_parse_optional_decimal(row["average_price"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key, value in tuple(result.items()):
        if key.endswith("_json") and value is not None:
            result[key] = json.loads(value)
    return result


_COMMAND_TRANSITIONS: dict[ExecutionCommandStatus, frozenset[ExecutionCommandStatus]] = {
    ExecutionCommandStatus.PLANNED: frozenset(
        {ExecutionCommandStatus.REQUESTED, ExecutionCommandStatus.FAILED}
    ),
    ExecutionCommandStatus.REQUESTED: frozenset(
        {
            ExecutionCommandStatus.ACKNOWLEDGED,
            ExecutionCommandStatus.FAILED,
            ExecutionCommandStatus.AMBIGUOUS,
        }
    ),
    ExecutionCommandStatus.AMBIGUOUS: frozenset(
        {ExecutionCommandStatus.ACKNOWLEDGED, ExecutionCommandStatus.FAILED}
    ),
    ExecutionCommandStatus.ACKNOWLEDGED: frozenset(
        {
            ExecutionCommandStatus.CONFIRMED,
            ExecutionCommandStatus.FAILED,
            ExecutionCommandStatus.AMBIGUOUS,
        }
    ),
    ExecutionCommandStatus.CONFIRMED: frozenset(),
    ExecutionCommandStatus.FAILED: frozenset(),
}


def _execution_command_from_row(row: sqlite3.Row) -> ExecutionCommandRecord:
    return ExecutionCommandRecord(
        command_id=row["command_id"],
        kind=ExecutionCommandKind(row["kind"]),
        idempotency_key=row["idempotency_key"],
        symbol=row["symbol"],
        request=json.loads(row["request_json"]),
        status=ExecutionCommandStatus(row["status"]),
        intent_id=row["intent_id"],
        exchange_order_id=row["exchange_order_id"],
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
