"""Run the production reconnect gate writer on disposable PG as role cripta."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from test_p90_private_reconnect_gate import RECONNECT, _writer

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL required")


@pytest.mark.parametrize("enabled,reason,expected", [
    (0, "OWNER_DISARM", "OWNER_DISARM"),
    (0, "REAL_ENTRY_RUNTIME_NOT_READY", "REAL_ENTRY_RUNTIME_NOT_READY"),
    (0, "AMBIGUOUS_MUTATION", "AMBIGUOUS_MUTATION"),
    (0, RECONNECT, RECONNECT),
    (1, "OWNER_ARM", RECONNECT),
])
def test_native_pg_reconnect_preserves_prior_closed_barrier_and_never_opens_gate(
    enabled, reason, expected,
):
    psycopg = pytest.importorskip("psycopg")
    assert DSN is not None
    connection = psycopg.connect(DSN)
    assert connection.info.dbname != "cripta", "production DB forbidden"
    schema = "reconnect466_" + uuid4().hex
    try:
        connection.execute(f"CREATE SCHEMA {schema}")
        connection.execute(f"CREATE TABLE {schema}.execution_gates("
                           "mode text PRIMARY KEY,enabled smallint NOT NULL,"
                           "reason text NOT NULL,updated_at_epoch_ms bigint NOT NULL)")
        connection.execute(f"INSERT INTO {schema}.execution_gates VALUES('mainnet',%s,%s,1000)",
                           (enabled, reason))
        connection.execute(f"INSERT INTO {schema}.execution_gates VALUES('paper',1,'OTHER',1000)")
        connection.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta")
        connection.execute(f"GRANT SELECT,UPDATE ON {schema}.execution_gates TO cripta")
        connection.execute("SET LOCAL ROLE cripta")
        assert connection.execute("SELECT current_user").fetchone()[0] == "cripta"

        class ScopedConnection:
            def execute(self, sql, params=()):
                return connection.execute(sql.replace("control.", schema + "."), params)

            def commit(self):
                pass  # Preserve disposable schema and role in this rolled-back transaction.

        for _retry in range(3):
            _writer()(ScopedConnection(), RECONNECT)
        result = connection.execute(
            f"SELECT enabled,reason,updated_at_epoch_ms FROM {schema}.execution_gates "
            "WHERE mode='mainnet'",
        ).fetchone()
        assert result == (0, expected, 2000 if enabled else 1000)
        assert connection.execute(f"SELECT enabled,reason FROM {schema}.execution_gates "
                                  "WHERE mode='paper'").fetchone() == (1, "OTHER")
    finally:
        connection.rollback()
        connection.close()
