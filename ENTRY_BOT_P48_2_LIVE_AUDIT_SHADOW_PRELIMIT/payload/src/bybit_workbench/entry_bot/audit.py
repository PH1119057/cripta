from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from bybit_workbench.persistence.database import DatabaseConnection, open_database

from .models import EntryBotAuditEvent


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _payload(event: EntryBotAuditEvent) -> str:
    if event.payload_json and event.payload_json != "{}":
        return event.payload_json
    return json.dumps(
        asdict(event), default=_json_default, ensure_ascii=False, sort_keys=True
    )


class EntryBotAuditStore:
    """Append-only live Entry audit trail.

    The store is observational only. It never creates, changes, or cancels orders.
    Candidate state, shadow pre-limit intent, touch decisions, Core signals and
    post-touch diagnostic outcomes are persisted for later live-vs-research review.
    """

    def __init__(self, database_path: Path | str) -> None:
        self._connection: DatabaseConnection = open_database(database_path)
        row = self._connection.execute(
            "SELECT COUNT(*) FROM entry_bot_candidate_events"
        ).fetchone()
        self._count = 0 if row is None else int(row[0])

    def close(self) -> None:
        self._connection.close()

    @property
    def count(self) -> int:
        return self._count

    def record_events(self, events: tuple[EntryBotAuditEvent, ...]) -> int:
        if not events:
            return 0
        created_at = datetime.now(UTC).isoformat()
        inserted = 0
        with self._connection:
            for event in events:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO entry_bot_candidate_events(
                        event_id, occurred_at, symbol, event_type, status,
                        candidate_id, direction, candidate_bar_at, entry_price,
                        last_price, distance_pct, flow_state, oi_state, reason,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.occurred_at.isoformat(),
                        event.symbol,
                        event.event_type,
                        event.status,
                        event.candidate_id,
                        event.direction,
                        None
                        if event.candidate_bar_at is None
                        else event.candidate_bar_at.isoformat(),
                        None if event.entry_price is None else str(event.entry_price),
                        None if event.last_price is None else str(event.last_price),
                        None if event.distance_pct is None else str(event.distance_pct),
                        event.flow_state,
                        event.oi_state,
                        event.reason,
                        _payload(event),
                        created_at,
                    ),
                )
                inserted += cursor.rowcount
        self._count += inserted
        return inserted

    def export_csv(self, output_path: Path) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self._connection.execute(
            """
            SELECT event_id, occurred_at, symbol, event_type, status, candidate_id,
                   direction, candidate_bar_at, entry_price, last_price, distance_pct,
                   flow_state, oi_state, reason, payload_json
            FROM entry_bot_candidate_events
            ORDER BY occurred_at, id
            """
        ).fetchall()
        fields = (
            "event_id",
            "occurred_at",
            "symbol",
            "event_type",
            "status",
            "candidate_id",
            "direction",
            "candidate_bar_at",
            "entry_price",
            "last_price",
            "distance_pct",
            "flow_state",
            "oi_state",
            "reason",
            "payload_json",
        )
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row[field] for field in fields})
        return len(rows)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export persistent Entry Bot audit history")
    parser.add_argument("--database", type=Path, default=Path("var/workbench.db"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/entry_bot_live_audit/entry_bot_history.csv"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    store = EntryBotAuditStore(args.database)
    try:
        count = store.export_csv(args.output)
    finally:
        store.close()
    print(f"Entry Bot audit export: {args.output.resolve()}")
    print(f"Rows: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
