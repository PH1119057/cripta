from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .database import DatabaseConnection, open_database


class SqliteIdempotencyStore:
    """Durable atomic claim used immediately before a Mainnet network mutation."""

    def __init__(self, path: Path | str) -> None:
        self._connection: DatabaseConnection = open_database(path)

    def close(self) -> None:
        self._connection.close()

    def claim_before_send(self, key: str) -> bool:
        if not key.strip():
            raise ValueError("idempotency key is required")
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO mainnet_idempotency_claims(idempotency_key, claimed_at)
                VALUES (?, ?)
                """,
                (key, datetime.now(UTC).isoformat()),
            )
        return cursor.rowcount == 1
