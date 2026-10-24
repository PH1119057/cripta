"""Offline R05 safety proof for independent private MAIN/SUB workers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bybit_workbench.account_entry_safety import (
    account_entry_safety_ready,
    latch_account_entry_safety,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/server/connectivity"))
sys.path.insert(0, str(ROOT / "operations/connectivity"))

import private_runtime  # noqa: E402


class Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class DB:
    def __init__(self, *, blocked: bool = False, schema: bool = True) -> None:
        self.blocked = blocked
        self.schema = schema
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Rows:
        self.queries.append((sql, params))
        if "to_regclass" in sql:
            return Rows([("runtime.account_entry_safety_blocks",)] if self.schema else [(None,)])
        if "FROM runtime.account_entry_safety_blocks" in sql:
            return Rows([(1,)] if self.blocked else [])
        return Rows([])

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_account_latch_checks_schema_and_only_exact_account_connector() -> None:
    db = DB()
    key = {"trading_account_id": "ta-sub", "account_connector_id": "connector-sub"}
    assert account_entry_safety_ready(db, **key) == (True, ())
    assert db.queries[1][1] == ("ta-sub", "connector-sub")
    blocked = DB(blocked=True)
    assert account_entry_safety_ready(blocked, **key) == (
        False, ("ACCOUNT_ENTRY_SAFETY_BLOCKED",),
    )
    absent = DB(schema=False)
    assert account_entry_safety_ready(absent, **key) == (
        False, ("ACCOUNT_ENTRY_SAFETY_SCHEMA_MISSING",),
    )
    assert len(absent.queries) == 1


def test_sub_safety_failure_latches_exact_account_without_global_disarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = DB()
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", "ta-sub"),
        patch.object(private_runtime, "ACCOUNT_CONNECTOR_ID", "connector-sub"),
    ):
        private_runtime.disarm_new_entries(db, "SUB_EXCHANGE_RECOVERY_UNPROVEN")
    assert db.commits == 1
    assert db.rollbacks == 1
    assert len(db.queries) == 1
    sql, params = db.queries[0]
    assert "INSERT INTO runtime.account_entry_safety_blocks" in sql
    assert "ON CONFLICT" in sql
    assert params == ("ta-sub", "connector-sub", "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED")
    assert not any("UPDATE control.execution_gates" in q for q, _ in db.queries)


def test_main_safety_failure_retains_existing_global_gate_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "MAIN")
    db = DB()
    private_runtime.disarm_new_entries(db, "MAIN_SAFETY_BLOCKED")
    assert db.commits == 1
    assert len(db.queries) == 1
    assert "UPDATE control.execution_gates" in db.queries[0][0]
    assert not any("account_entry_safety_blocks" in q for q, _ in db.queries)


def test_sub_worker_prevents_entry_mutation_before_any_exchange_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    db = DB()
    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", "ta-sub"),
        patch.object(private_runtime, "ACCOUNT_CONNECTOR_ID", "connector-sub"),
        patch.object(
            private_runtime, "account_entry_safety_ready",
            return_value=(False, ("ACCOUNT_ENTRY_SAFETY_BLOCKED",)),
        ),
        patch.object(private_runtime, "authenticated_entry_connector_gate") as signed,
        patch.object(private_runtime, "api_get") as exchange,
        pytest.raises(private_runtime.PreMutationSafetyBlock),
    ):
        private_runtime.execute_command(
            db, "offline-key", "offline-secret",
            ("command-test", "entry", "BTCUSDT", "{}"),
        )
    signed.assert_not_called()
    exchange.assert_not_called()


def test_sub_private_ws_reconnect_latches_own_account_without_global_disarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    db = DB()
    monkeypatch.setattr(private_runtime, "running", True)
    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", "ta-sub"),
        patch.object(private_runtime, "ACCOUNT_CONNECTOR_ID", "connector-sub"),
        patch.object(private_runtime, "db", return_value=db),
        patch.object(private_runtime, "authenticated_entry_connector_gate") as signed,
        patch.object(private_runtime, "reconcile", return_value=(0, 0)),
        patch.object(
            private_runtime.websocket, "create_connection",
            side_effect=RuntimeError("OFFLINE_PRIVATE_WEBSOCKET_DOWN"),
        ),
        patch.object(private_runtime, "connection_event"),
        patch.object(private_runtime, "atomic_status"),
        patch.object(private_runtime.time, "sleep", side_effect=lambda _: setattr(
            private_runtime, "running", False
        )),
        patch.object(private_runtime, "restore_r1_gate_after_verified_reconnect") as restore,
    ):
        private_runtime.private_loop("offline", "offline")
    assert db.commits == 1
    assert any("INSERT INTO runtime.account_entry_safety_blocks" in q for q, _ in db.queries)
    assert not any("UPDATE control.execution_gates" in q for q, _ in db.queries)
    restore.assert_not_called()
    signed.assert_called_once_with(db, "offline", "offline")
    assert db.queries[0][1][2] == "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"


def test_sub_error_text_never_persisted_in_denial_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    db = DB()
    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", "ta-sub"),
        patch.object(private_runtime, "ACCOUNT_CONNECTOR_ID", "connector-sub"),
    ):
        private_runtime.disarm_new_entries(
            db, "signed_url=https://bad.example/?api_secret=SENSITIVE_FAKE"
        )
    assert len(db.queries) == 1
    sql, params = db.queries[0]
    assert "SENSITIVE_FAKE" not in sql and "SENSITIVE_FAKE" not in repr(params)
    assert params[2] == "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED"


def test_invalid_latch_scope_is_rejected_before_sql() -> None:
    db = DB()
    with pytest.raises(ValueError, match="ACCOUNT_ENTRY_SAFETY_LATCH_IDENTITY_INVALID"):
        latch_account_entry_safety(
            db, trading_account_id="", account_connector_id="conn", reason="bad"
        )
    assert not db.queries


def test_sub_untrusted_transport_error_is_not_written_to_status_or_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    db = DB()
    leaked = "signed_url=https://example.invalid/?api_secret=OFFLINE_SENSITIVE"
    statuses: list[str] = []
    events: list[str] = []
    monkeypatch.setattr(private_runtime, "running", True)
    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", "ta-sub"),
        patch.object(private_runtime, "ACCOUNT_CONNECTOR_ID", "connector-sub"),
        patch.object(private_runtime, "db", return_value=db),
        patch.object(
            private_runtime, "authenticated_entry_connector_gate",
            side_effect=RuntimeError(leaked),
        ),
        patch.object(private_runtime, "reconcile") as recovery,
        patch.object(
            private_runtime, "connection_event",
            side_effect=lambda _c, _channel, _state, **fields:
            events.append(str(fields.get("error", ""))),
        ),
        patch.object(
            private_runtime, "atomic_status",
            side_effect=lambda _channel, fields:
            statuses.append(str(fields.get("error", ""))),
        ),
        patch.object(
            private_runtime.time, "sleep",
            side_effect=lambda _: setattr(private_runtime, "running", False),
        ),
    ):
        private_runtime.private_loop("offline", "offline")
    recovery.assert_not_called()
    assert events == ["RuntimeError"]
    assert statuses == ["RuntimeError"]
    assert leaked not in repr(db.queries)
    assert db.commits == 1
    assert db.rollbacks == 1
    assert db.queries[0][1][2] == "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"


def test_sub_error_logging_omits_signed_urls_but_main_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = RuntimeError("signed_url=https://example.invalid?api_secret=FAKE")
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    assert private_runtime._private_worker_safe_error(exc) == "RuntimeError"
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "MAIN")
    assert "signed_url=" in private_runtime._private_worker_safe_error(exc)
