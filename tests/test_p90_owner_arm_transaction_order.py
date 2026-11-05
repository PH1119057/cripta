"""Execute the actual Dashboard orchestration; no Exchange or production writes."""
import ast
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def _handler(connect, *, fail_final=False, fail_wait=False):
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_u6_p90_real_v3_arm")
    calls = []

    def phase(connection, request_id, value):
        connection.execute("SELECT 1")  # Starts a real implicit transaction.
        calls.append(value)

    def financial(connection, name):
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            connection.execute("SELECT 1")
            calls.append(name)
            if name == "finalize" and fail_final:
                raise ValueError("final proof blocked")
        return {"gate_enabled": name == "finalize"}

    namespace = {
        "psycopg": SimpleNamespace(connect=connect), "datetime": datetime,
        "UTC": UTC, "LOADED_RELEASE_COMMIT": "test-release",
        "_u6_financial_account_same_origin": lambda _: True,
        "_u6_prepare_p90_real_v3_readiness": lambda **_: {
            "state": "READY_FOR_OWNER_ARM"},
        "_wait_p90_real_v3_technical_evidence": lambda **_: None,
        "_u6_multi_strategy_observer_ready": lambda: True,
        "_p90_arm_phase": phase,
        "stage_p90l90_real_v3": lambda c, **_: financial(c, "stage"),
        "finalize_p90l90_real_v3": lambda c, **_: financial(c, "finalize"),
        "_u6_wait_p90_real_v3_stage_ready": lambda **_: (not fail_wait, "blocked"),
        "_u6_p90_real_v3_stage_ready": lambda: (True, "ready"),
        "disarm_p90l90_real_v3": lambda *_, **kw: calls.append("disarm"),
        "_u6_json": lambda _, status, result: calls.append(result),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "dashboard-arm", "exec"),
         namespace)
    return namespace["_u6_p90_real_v3_arm"], calls


class Connection:
    def __init__(self):
        self.queried = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.queried = False

    @contextmanager
    def transaction(self):
        outer_queried = self.queried
        try:
            yield
        finally:
            self.queried = outer_queried

    def execute(self, sql):
        if sql.startswith("SET TRANSACTION") and self.queried:
            raise RuntimeError("isolation must precede first query")
        self.queried = True


@pytest.mark.parametrize("failure", [None, "wait", "final"])
def test_dashboard_financial_transactions_start_before_any_progress_query(failure):
    handler, calls = _handler(lambda _: Connection(), fail_wait=failure == "wait",
                              fail_final=failure == "final")
    owner = SimpleNamespace(session_user=lambda: "test-owner")
    request = {"enabled": True, "confirmed": True, "request_id": "test-request"}
    if failure:
        with pytest.raises(ValueError):
            handler(owner, request)
        assert calls[-1] == "disarm"
        assert "opened" not in calls
        assert {"gate_enabled": True} not in calls
    else:
        handler(owner, request)
        assert calls == ["arming_closed_gate", "stage", "finalizing", "finalize",
                         "opened", {"gate_enabled": True}]


@pytest.mark.skipif(not os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN"),
                    reason="explicit disposable PostgreSQL required")
def test_dashboard_phase_order_on_actual_postgresql():
    import psycopg

    dsn = os.environ["CRIPTA_TRADE_LIFECYCLE_TEST_DSN"]
    with psycopg.connect(dsn) as check:
        assert check.info.dbname != "cripta", "production DB forbidden"
    # Reproduce the installed failure independently of the model connection.
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT 1")
        with pytest.raises(psycopg.errors.ActiveSqlTransaction), connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.rollback()
    for failure in (None, "wait", "final"):
        handler, calls = _handler(lambda _: psycopg.connect(dsn),
                                  fail_wait=failure == "wait",
                                  fail_final=failure == "final")
        owner = SimpleNamespace(session_user=lambda: "test-owner")
        request = {"enabled": True, "confirmed": True, "request_id": "pg-test"}
        if failure:
            with pytest.raises(ValueError):
                handler(owner, request)
            assert calls[-1] == "disarm"
            assert "opened" not in calls
        else:
            handler(owner, request)
            assert calls[-2:] == ["opened", {"gate_enabled": True}]
