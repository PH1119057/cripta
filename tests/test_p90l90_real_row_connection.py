"""Read-only row factory attestation: one PG transaction for REAL Exit."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from psycopg.rows import dict_row

from bybit_workbench.universal_exit.p90l90_real_row_connection import (
    P90RealMappedExitConnection,
)


class FakeCursor:
    def __init__(self):
        self.stmt = None
        self.params = None

    def execute(self, stmt, params):
        self.stmt = stmt
        self.params = params
        return self

    def fetchone(self):
        return {"position_id": "same-pg-transaction"}


class FakeDB:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS"))
        self.cursor_calls = []
        self.transaction_calls = 0

    def cursor(self, *, row_factory):
        self.cursor_calls.append(row_factory)
        return FakeCursor()

    def execute(self, *_args, **_kwargs):
        raise AssertionError("raw tuple-row Observer SQL must not be rewritten")

    @contextmanager
    def transaction(self):
        self.transaction_calls += 1
        yield


def test_only_exit_cursor_uses_mapping_row_factory_and_original_pg_info():
    original = FakeDB()
    mapped = P90RealMappedExitConnection(original)
    cursor = mapped.execute("SELECT %s AS position_id", ("same-pg-transaction",))
    assert cursor.fetchone() == {"position_id": "same-pg-transaction"}
    assert original.cursor_calls == [dict_row]
    assert mapped.info is original.info
    with mapped.transaction():
        pass
    assert original.transaction_calls == 1
    assert mapped.source_connection is original


@pytest.mark.parametrize("invalid", (object(), None, SimpleNamespace(execute=lambda: None)))
def test_unverified_or_detached_postgres_facade_cannot_be_created(invalid):
    with pytest.raises(RuntimeError, match="SAME_POSTGRES_ROW_MAPPING_UNAVAILABLE"):
        P90RealMappedExitConnection(invalid)
