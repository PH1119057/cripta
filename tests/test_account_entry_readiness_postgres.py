"""Real SQL regression for the closed-LTC fault that vetoed the first candidate."""
import os
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bybit_workbench import account_entry_readiness as readiness

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicitly disposable PostgreSQL required")


def test_closed_fault_is_history_but_current_unknown_and_other_account_are_checked(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    schema = "entry_audit_" + uuid4().hex
    at = datetime.now(UTC)
    generation = SimpleNamespace(
        position_mode_refs={"LTCUSDT": "mode-proof"}, generation_id="generation-proof",
        completed_at=at, available_balance=Decimal("100"),
    )
    monkeypatch.setattr(
        readiness, "current_complete_account_state_generation", lambda *_a, **_kw: generation,
    )
    with psycopg.connect(DSN) as connection:
        assert connection.info.dbname != "cripta", "production DB is forbidden"
        with connection.transaction():
            connection.execute(f"CREATE SCHEMA {schema}")
            connection.execute(
                f"CREATE TABLE {schema}.position_ownership(position_id text,state text)"
            )
            connection.execute(
                f"CREATE TABLE {schema}.lifecycle_faults("
                "fault_id text,fault_code text,severity text,state text,"
                "detected_at timestamptz,exact_ids jsonb)"
            )
            connection.execute(
                f"INSERT INTO {schema}.position_ownership VALUES ('old-ltc','CLOSED')"
            )
            connection.execute(
                f"INSERT INTO {schema}.lifecycle_faults VALUES "
                "('fault-ltc','OWNERSHIP','CRITICAL','OPEN',%s,%s::jsonb)",
                (at, '{"strategy_position_id":"old-ltc","trading_account_id":"ta-main"}'),
            )

            class IsolatedConnection:
                def execute(self, sql, parameters=()):
                    return connection.execute(sql.replace("runtime.", schema + "."), parameters)

            def read(account="ta-main"):
                return readiness.real_entry_account_state(
                    IsolatedConnection(), symbol="LTCUSDT", observed_at=at,
                    trading_account_id=account,
                )

            assert read()[0].ready  # exact production old-fault shape, no ACK/repair
            connection.execute(f"UPDATE {schema}.position_ownership SET state='OPEN'")
            assert not read()[0].ready
            assert read("ta-sub")[0].ready  # current MAIN issue cannot veto SUB
            connection.execute(f"UPDATE {schema}.position_ownership SET state='CLOSED'")
            connection.execute(
                f"UPDATE {schema}.lifecycle_faults SET exact_ids="
                "'{\"strategy_position_id\":\"unknown\",\"trading_account_id\":\"ta-main\"}'"
            )
            assert not read()[0].ready  # unknown current lineage remains blocked
            # This test never edits a production/historical fault into RESOLVED.
            assert connection.execute(
                f"SELECT state FROM {schema}.lifecycle_faults"
            ).fetchone() == ("OPEN",)
            connection.execute(f"DROP SCHEMA {schema} CASCADE")
