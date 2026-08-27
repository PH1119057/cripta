import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import open_database


@dataclass(frozen=True, slots=True)
class SystemEvent:
    event_id: int
    occurred_at: datetime
    severity: str
    event_type: str
    message: str
    details: dict[str, Any]


class EventJournal:
    """Small Stage-A SQLite journal; later repositories share this connection policy."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection = open_database(self.path)

    def close(self) -> None:
        self._connection.close()

    def append(
        self,
        event_type: str,
        message: str,
        *,
        severity: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> int:
        if not event_type.strip() or not message.strip():
            raise ValueError("event_type and message are required")
        occurred_at = datetime.now(UTC).isoformat()
        payload = json.dumps(details or {}, sort_keys=True, default=str)
        cursor = self._connection.execute(
            """
            INSERT INTO system_events
                (occurred_at, severity, event_type, message, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (occurred_at, severity.upper(), event_type, message, payload),
        )
        self._connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an event id")
        return cursor.lastrowid

    def recent(self, limit: int = 100) -> list[SystemEvent]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = self._connection.execute(
            """
            SELECT id, occurred_at, severity, event_type, message, details_json
            FROM system_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            SystemEvent(
                event_id=row["id"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                severity=row["severity"],
                event_type=row["event_type"],
                message=row["message"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]
