from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from bybit_workbench.persistence.database import DatabaseConnection, open_database

from .models import (
    ClaimedPositionHandoff,
    Direction,
    EntrySignalEvent,
    PositionHandoff,
)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Decimal)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _payload(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, sort_keys=True)


class PositionHandoffStore:
    """Durable Entry -> Exit boundary.

    Entry publishes only after a real fill and confirmed initial protection.
    Exit claims OPEN rows and becomes the position owner. The live scanner never
    infers ownership by watching its own signals.
    """

    def __init__(self, database_path: Path | str) -> None:
        self._connection: DatabaseConnection = open_database(database_path)

    def close(self) -> None:
        self._connection.close()

    def record_signal(self, signal: EntrySignalEvent) -> bool:
        created_at = datetime.now(UTC).isoformat()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO entry_bot_signals(
                    signal_id, strategy_id, strategy_version, symbol, direction,
                    candidate_bar_at, touch_at, entry_price, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.strategy_id,
                    signal.strategy_version,
                    signal.symbol,
                    signal.direction,
                    signal.candidate_bar_at.isoformat(),
                    signal.touch_at.isoformat(),
                    str(signal.entry_price),
                    _payload(asdict(signal)),
                    created_at,
                ),
            )
        return cursor.rowcount == 1

    def publish_position(self, handoff: PositionHandoff) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO position_handoffs(
                    handoff_id, source_signal_id, strategy_id, strategy_version,
                    symbol, side, quantity, average_entry, initial_stop,
                    entry_order_id, client_order_id, protection_order_id, filled_at,
                    state, claimed_by, claimed_at, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', NULL, NULL, ?, ?, ?)
                """,
                (
                    handoff.handoff_id,
                    handoff.source_signal_id,
                    handoff.strategy_id,
                    handoff.strategy_version,
                    handoff.symbol,
                    handoff.side,
                    str(handoff.quantity),
                    str(handoff.average_entry),
                    str(handoff.initial_stop),
                    handoff.entry_order_id,
                    handoff.client_order_id,
                    handoff.protection_order_id,
                    handoff.filled_at.isoformat(),
                    handoff.payload_json,
                    now,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def claim_next(self, consumer_id: str) -> ClaimedPositionHandoff | None:
        selected_consumer = consumer_id.strip()
        if not selected_consumer:
            raise ValueError("consumer_id is required")
        now = datetime.now(UTC)
        with self._connection:
            row = self._connection.execute(
                """
                SELECT * FROM position_handoffs
                WHERE state='OPEN'
                ORDER BY created_at, handoff_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            cursor = self._connection.execute(
                """
                UPDATE position_handoffs
                SET state='CLAIMED', claimed_by=?, claimed_at=?, updated_at=?
                WHERE handoff_id=? AND state='OPEN'
                """,
                (selected_consumer, now.isoformat(), now.isoformat(), row["handoff_id"]),
            )
            if cursor.rowcount != 1:
                return None
        handoff = _handoff_from_row(row)
        return ClaimedPositionHandoff(handoff, selected_consumer, now)

    def close_handoff(self, handoff_id: str, *, consumer_id: str) -> bool:
        selected = handoff_id.strip()
        consumer = consumer_id.strip()
        if not selected or not consumer:
            raise ValueError("handoff_id and consumer_id are required")
        now = datetime.now(UTC).isoformat()
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE position_handoffs
                SET state='CLOSED', updated_at=?
                WHERE handoff_id=? AND state='CLAIMED' AND claimed_by=?
                """,
                (now, selected, consumer),
            )
        return cursor.rowcount == 1

    def open_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM position_handoffs WHERE state='OPEN'"
        ).fetchone()
        return 0 if row is None else int(row[0])


def _handoff_from_row(row: Any) -> PositionHandoff:
    return PositionHandoff(
        handoff_id=str(row["handoff_id"]),
        source_signal_id=str(row["source_signal_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        symbol=str(row["symbol"]),
        side=cast(Direction, str(row["side"])),
        quantity=Decimal(str(row["quantity"])),
        average_entry=Decimal(str(row["average_entry"])),
        initial_stop=Decimal(str(row["initial_stop"])),
        entry_order_id=str(row["entry_order_id"]),
        client_order_id=str(row["client_order_id"]),
        protection_order_id=(
            None if row["protection_order_id"] is None else str(row["protection_order_id"])
        ),
        filled_at=datetime.fromisoformat(str(row["filled_at"])).astimezone(UTC),
        payload_json=str(row["payload_json"]),
    )
