"""One physical PostgreSQL source transaction, two explicit row representations.

The ordinary Observer and P90 causal ledger use tuple-row cursors. Universal
Exit/ExitPlan loader/MFE/ExecutionStore use named mapping rows. This narrow
facade requests `dict_row` ONLY for those Exit cursors. It never opens a new
connection, starts a second transaction or changes the primary Observer's
row_factory. Its write/lock operations remain on the same PostgreSQL backend.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg.rows import dict_row


class P90RealMappedExitConnection:
    """Scoped named-row Exit SQL on the Observer/account worker's actual PG xact."""

    def __init__(self, source_connection: Any) -> None:
        if (
            not callable(getattr(source_connection, "cursor", None))
            or not callable(getattr(source_connection, "transaction", None))
            or not callable(getattr(source_connection, "execute", None))
            or getattr(source_connection, "info", None) is None
        ):
            raise RuntimeError(
                "P90_REAL_EXIT_SAME_POSTGRES_ROW_MAPPING_UNAVAILABLE"
            )
        self.source_connection = source_connection

    @property
    def info(self) -> Any:
        return self.source_connection.info

    def execute(
        self, statement: str, parameters: Sequence[object] = (),
    ) -> Any:
        # Same underlying PostgreSQL transaction, lock domain and connection.
        return self.source_connection.cursor(row_factory=dict_row).execute(
            statement, parameters,
        )

    def transaction(self) -> Any:
        return self.source_connection.transaction()
