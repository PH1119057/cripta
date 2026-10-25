"""Offline R05 safety proof for independent private MAIN/SUB workers."""

from __future__ import annotations

import json
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


def test_sub_trade_mutation_barrier_redacts_nested_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both the durable command row and the local status must not expose the
    # signed request URL from a fake Exchange transport exception.
    leaked = "signed_url=https://example.invalid/?api_secret=OFFLINE_FAKE_KEY"
    monkeypatch.setenv("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "SUB")
    db = DB()
    statuses: list[dict[str, object]] = []
    disarms: list[str] = []

    with (
        patch.object(
            private_runtime, "finalize_failed_entry_command_reservation",
            return_value=None,
        ),
        patch.object(private_runtime, "mark_ambiguous_exit_command"),
        patch.object(
            private_runtime, "disarm_new_entries",
            side_effect=lambda _db, reason: disarms.append(reason),
        ),
        patch.object(
            private_runtime, "atomic_status",
            side_effect=lambda _name, fields: statuses.append(fields),
        ),
        patch.object(
            private_runtime, "refresh_recent_executions",
            side_effect=RuntimeError(leaked),
        ),
        patch.object(
            private_runtime, "reconcile",
            side_effect=RuntimeError(leaked),
        ),
        patch.object(private_runtime.os, "_exit", side_effect=SystemExit(75)),
    ):
        private_runtime.handle_pre_mutation_safety_block(
            db, "cmd-a", RuntimeError(leaked),
        )
        with pytest.raises(SystemExit) as abrupt:
            private_runtime.handle_exchange_mutation_barrier(
                db, "fake", "fake", "cmd-b", RuntimeError(leaked),
            )
    assert abrupt.value.code == 75
    assert len(statuses) == 2
    assert statuses[0]["error"] == "PRE_MUTATION_SAFETY_BLOCK:RuntimeError"
    assert statuses[1]["error"] == "EXCHANGE_MUTATION_BARRIER:RuntimeError"
    assert statuses[1]["recovery_errors"] == [
        "executions=RuntimeError", "reconcile=RuntimeError",
    ]
    assert all("OFFLINE_FAKE_KEY" not in s for s in disarms)
    assert leaked not in repr(db.queries)
    assert leaked not in repr(statuses)


def test_failed_account_generation_uses_exact_entry_admission_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    class FailedReconcileDB:
        def __init__(self) -> None:
            self.statements: list[tuple[str, tuple[object, ...]]] = []

        def transaction(self):
            return nullcontext()

        def rollback(self) -> None:
            pass

        def execute(
            self, sql: str, params: tuple[object, ...] = ()
        ) -> object:
            self.statements.append((sql, params))
            return None

    # Independent MAIN and SUB failure paths must use the same advisory
    # account lock that real Entry admission acquires for that account.
    for account in ("ta-main", "ta-sub"):
        conn = FailedReconcileDB()
        with (
            patch.object(private_runtime, "TRADING_ACCOUNT_ID", account),
            patch.object(private_runtime, "ACCOUNT_REF", "BYBIT:UNIFIED"),
            patch.object(private_runtime, "_begin_account_state_generation"),
            patch.object(
                private_runtime,
                "_complete_exchange_inventory",
                side_effect=private_runtime.ExchangeReadUnavailable(
                    "offline Exchange read unavailable"
                ),
            ),
            pytest.raises(
                private_runtime.ExchangeReadUnavailable,
                match="offline Exchange read unavailable",
            ),
        ):
            private_runtime.reconcile(conn, "fake", "fake", "periodic")
        locks = [
            params for sql, params in conn.statements
            if "pg_advisory_xact_lock" in sql
        ]
        assert locks == [(account,)]
        assert any(
            "SET state='FAILED'" in sql and params[1].startswith("acctgen-")
            for sql, params in conn.statements
        )
        assert not any(
            params == ("BYBIT:UNIFIED",) for _sql, params in conn.statements
        )


@pytest.mark.parametrize(
    "invalid_field",
    ("initial_protection", "entry_time_in_force"),
)
def test_invalid_entry_protection_and_tif_never_mutate_exchange(
    invalid_field: str,
) -> None:
    # Real production execute_command branch, but all provider calls are
    # offline stand-ins. An invalid Entry policy cannot send set-leverage.
    payload = {
        "stake_usdt": "10",
        "leverage": 1,
        "side": "Buy",
        "price": "100",
        "entry_offset_pct": "0.2",
        "entry_time_in_force": "POST_ONLY",
        "initial_protection": {
            "stop_loss_pct": "1",
            "take_profit_pct": "3",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
    }
    if invalid_field == "initial_protection":
        payload["initial_protection"] = {}
    else:
        payload["entry_time_in_force"] = "UNSUPPORTED"

    def offline_get(endpoint, _params, *_args):
        if endpoint == "/v5/position/list":
            return {"retCode": 0, "result": {"list": []}}, {}
        if endpoint == "/v5/market/instruments-info":
            return {
                "retCode": 0,
                "result": {
                    "list": [{
                        "priceFilter": {"tickSize": "0.01"},
                        "lotSizeFilter": {"qtyStep": "0.001"},
                    }],
                },
            }, {}
        if endpoint == "/v5/order/realtime":
            return {"retCode": 0, "result": {"list": []}}, {}
        if endpoint == "/v5/account/wallet-balance":
            return {
                "retCode": 0,
                "result": {"list": [{
                    "accountType": "UNIFIED",
                    "totalAvailableBalance": "100",
                }]},
            }, {}
        raise AssertionError(f"Unexpected offline endpoint: {endpoint}")

    with (
        patch.object(private_runtime, "_is_sub_private_worker", return_value=False),
        patch.object(private_runtime, "authenticated_entry_connector_gate"),
        patch.object(private_runtime, "assert_legacy_automated_exit_ownership"),
        patch.object(private_runtime, "_critical_lifecycle_fault_code", return_value=None),
        patch.object(private_runtime, "api_get", side_effect=offline_get),
        patch.object(private_runtime, "api_post") as exchange_mutation,
        pytest.raises(RuntimeError),
    ):
        private_runtime.execute_command(
            object(),
            "offline-key",
            "offline-secret",
            ("entry-offline", "entry", "BTCUSDT", json.dumps(payload)),
        )
    exchange_mutation.assert_not_called()


@pytest.mark.parametrize(
    "bad_path",
    (
        "/v5/position/list",
        "/v5/market/instruments-info",
        "/v5/order/realtime",
        "/v5/account/wallet-balance",
    ),
)
@pytest.mark.parametrize("incomplete", (False, True))
def test_required_entry_exchange_read_failure_never_reaches_first_post(
    bad_path: str, incomplete: bool,
) -> None:
    payload = {
        "stake_usdt": "10",
        "leverage": 1,
        "side": "Buy",
        "price": "100",
        "entry_offset_pct": "0",
        "initial_protection": {
            "stop_loss_pct": "1",
            "take_profit_pct": "3",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
    }

    def exchange_read(path, _params, *_args):
        inventories = {
            "/v5/position/list": [],
            "/v5/market/instruments-info": [
                {
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }
            ],
            "/v5/order/realtime": [],
            "/v5/account/wallet-balance": [
                {"accountType": "UNIFIED", "totalAvailableBalance": "100"}
            ],
        }
        assert path in inventories
        if path == bad_path:
            if incomplete:
                return {"retCode": 0, "result": {}}, {}
            return {"retCode": 10006, "retMsg": "offline rejection"}, {}
        return {"retCode": 0, "result": {"list": inventories[path]}}, {}

    with (
        patch.object(private_runtime, "_is_sub_private_worker", return_value=False),
        patch.object(private_runtime, "authenticated_entry_connector_gate"),
        patch.object(private_runtime, "assert_legacy_automated_exit_ownership"),
        patch.object(private_runtime, "_critical_lifecycle_fault_code", return_value=None),
        patch.object(private_runtime, "api_get", side_effect=exchange_read),
        patch.object(private_runtime, "api_post") as mutation,
        pytest.raises(private_runtime.ExchangeReadUnavailable),
    ):
        private_runtime.execute_command(
            object(),
            "offline-key",
            "offline-secret",
            ("entry-failure", "entry", "BTCUSDT", json.dumps(payload)),
        )
    mutation.assert_not_called()


def test_offline_two_account_entry_orders_carry_independent_initial_sl_tp() -> None:
    """Two isolated provider credentials, two Entry commands, no shared order ID.

    Offline Exchange simulator only. Does not assert real signed UID, production
    permissions, actual Bybit fill or owner-arm readiness.
    """

    class OfflineDB:
        def __init__(self) -> None:
            self.commits = 0
            self.queries: list[str] = []

        def execute(self, sql: str, _args=()):
            self.queries.append(sql)
            return None

        def commit(self) -> None:
            self.commits += 1

    class OfflineExchange:
        def __init__(self, account: str) -> None:
            self.account = account
            self.positions: list[dict[str, object]] = []
            self.posts: list[tuple[str, dict[str, object]]] = []

        def get(self, path, params, *_keys):
            assert params["symbol"] == "BTCUSDT" if "symbol" in params else True
            if path == "/v5/position/list":
                return {"retCode": 0, "result": {"list": self.positions}}, {}
            if path == "/v5/market/instruments-info":
                return {
                    "retCode": 0,
                    "result": {
                        "list": [{
                            "priceFilter": {"tickSize": "0.01"},
                            "lotSizeFilter": {"qtyStep": "0.001"},
                        }],
                    },
                }, {}
            if path == "/v5/order/realtime":
                return {"retCode": 0, "result": {"list": []}}, {}
            if path == "/v5/account/wallet-balance":
                return {
                    "retCode": 0,
                    "result": {"list": [{
                        "accountType": "UNIFIED",
                        "totalAvailableBalance": "100",
                    }]},
                }, {}
            raise AssertionError(path)

        def post(self, path, params, *_keys, **_kwargs):
            self.posts.append((path, dict(params)))
            if path == "/v5/position/set-leverage":
                return {"retCode": 0}
            if path == "/v5/order/create":
                assert params["stopLoss"] == "99.00"
                assert params["takeProfit"] == "103.00"
                self.positions = [{
                    "symbol": "BTCUSDT",
                    "positionIdx": 0,
                    "side": "Buy",
                    "size": params["qty"],
                    "avgPrice": "100",
                    "stopLoss": params["stopLoss"],
                    "takeProfit": params["takeProfit"],
                }]
                return {
                    "retCode": 0,
                    "result": {"orderId": f"{self.account}-exchange-order"},
                }
            if path == "/v5/position/trading-stop":
                assert len(self.positions) == 1
                self.positions[0]["stopLoss"] = params["stopLoss"]
                self.positions[0]["takeProfit"] = params["takeProfit"]
                return {"retCode": 0, "retMsg": "OK"}
            raise AssertionError(path)

    payload = {
        "stake_usdt": "10",
        "leverage": 1,
        "side": "Buy",
        "price": "100",
        "entry_offset_pct": "0",
        "initial_protection": {
            "stop_loss_pct": "1",
            "take_profit_pct": "3",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
    }
    submitted: list[str] = []
    for account in ("ta-main", "ta-sub"):
        exchange = OfflineExchange(account)
        db = OfflineDB()
        command = f"entry-{account}"

        def authenticated(_db, key, secret, *, expected=account):
            assert key == f"offline-key-{expected}"
            assert secret == f"offline-secret-{expected}"

        def observe_reconcile(_db, _key, _secret, _reason, *, provider=exchange):
            assert provider.positions[0]["stopLoss"] == "99.00"
            assert provider.positions[0]["takeProfit"] == "103.00"
            return 1, 0

        with (
            patch.object(private_runtime, "TRADING_ACCOUNT_ID", account),
            patch.object(private_runtime, "ACCOUNT_CONNECTOR_ID", f"connector-{account}"),
            patch.object(private_runtime, "_is_sub_private_worker", return_value=False),
            patch.object(
                private_runtime, "authenticated_entry_connector_gate",
                side_effect=authenticated,
            ),
            patch.object(private_runtime, "assert_legacy_automated_exit_ownership"),
            patch.object(private_runtime, "_critical_lifecycle_fault_code", return_value=None),
            patch.object(private_runtime, "api_get", side_effect=exchange.get),
            patch.object(private_runtime, "api_post", side_effect=exchange.post),
            patch.object(private_runtime, "mark_entry_order_acknowledged") as ack,
            patch.object(private_runtime, "reconcile", side_effect=observe_reconcile),
            patch.object(private_runtime, "record_protection_or_owner_event"),
            patch.object(private_runtime, "record_effective_protection_after_command") as audit,
            patch.object(private_runtime.time, "sleep"),
        ):
            private_runtime.execute_command(
                db,
                f"offline-key-{account}",
                f"offline-secret-{account}",
                (command, "entry", "BTCUSDT", json.dumps(payload)),
            )
        assert [path for path, _ in exchange.posts] == [
            "/v5/position/set-leverage",
            "/v5/order/create",
            "/v5/position/trading-stop",
        ]
        order = exchange.posts[1][1]
        assert order["orderLinkId"] == command
        submitted.append(str(order["orderLinkId"]))
        assert db.commits >= 2
        ack.assert_called_once()
        assert ack.call_args.kwargs["command_id"] == command
        assert ack.call_args.kwargs["exchange_order_id"] == f"{account}-exchange-order"
        audit.assert_called_once()
        assert audit.call_args.kwargs["command_id"] == command

    assert len(set(submitted)) == 2

    # Exchange may acknowledge trading-stop while protection has not actually
    # materialized. Keep the accepted Entry uncertain; never issue another
    # /order/create and never mark the command completed.
    failed_exchange = OfflineExchange("ta-missing-protection")
    failed_db = OfflineDB()
    original_post = failed_exchange.post

    def acknowledged_but_unprotected(path, params, *_keys, **kwargs):
        result = original_post(path, params, *_keys, **kwargs)
        if path == "/v5/position/trading-stop":
            failed_exchange.positions[0]["stopLoss"] = "0"
        return result

    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", "ta-missing-protection"),
        patch.object(
            private_runtime, "ACCOUNT_CONNECTOR_ID",
            "connector-ta-missing-protection",
        ),
        patch.object(private_runtime, "_is_sub_private_worker", return_value=False),
        patch.object(private_runtime, "authenticated_entry_connector_gate"),
        patch.object(private_runtime, "assert_legacy_automated_exit_ownership"),
        patch.object(private_runtime, "_critical_lifecycle_fault_code", return_value=None),
        patch.object(private_runtime, "api_get", side_effect=failed_exchange.get),
        patch.object(private_runtime, "api_post", side_effect=acknowledged_but_unprotected),
        patch.object(private_runtime, "mark_entry_order_acknowledged") as ack,
        patch.object(private_runtime, "reconcile") as reconciler,
        patch.object(private_runtime, "record_effective_protection_after_command") as audit,
        patch.object(private_runtime.time, "sleep"),
        pytest.raises(
            private_runtime.ExchangeMutationBarrier,
            match="initial protection not confirmed",
        ),
    ):
        private_runtime.execute_command(
            failed_db,
            "offline-key",
            "offline-secret",
            ("entry-unprotected", "entry", "BTCUSDT", json.dumps(payload)),
        )
    assert [path for path, _ in failed_exchange.posts] == [
        "/v5/position/set-leverage",
        "/v5/order/create",
        "/v5/position/trading-stop",
    ]
    ack.assert_called_once()
    reconciler.assert_not_called()
    audit.assert_not_called()
    assert not any(
        "SET state='completed'" in query for query in failed_db.queries
    )


@pytest.mark.parametrize("repeated_cursor", (False, True))
def test_entry_second_page_pending_order_prevents_new_exchange_mutation(
    repeated_cursor: bool,
) -> None:
    requests: list[str] = []
    payload = {
        "stake_usdt": "10",
        "leverage": 1,
        "side": "Buy",
        "price": "100",
        "entry_offset_pct": "0",
        "initial_protection": {
            "stop_loss_pct": "1",
            "take_profit_pct": "3",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
    }

    def bybit_get(path, params, *_keys):
        if path == "/v5/position/list":
            return {"retCode": 0, "result": {"list": []}}, {}
        if path == "/v5/market/instruments-info":
            return {
                "retCode": 0,
                "result": {"list": [{
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }]},
            }, {}
        if path == "/v5/order/realtime":
            cursor = str(params.get("cursor") or "FIRST")
            requests.append(cursor)
            if cursor == "FIRST":
                return {
                    "retCode": 0,
                    "result": {
                        "list": [{
                            "symbol": "BTCUSDT",
                            "orderStatus": "Cancelled",
                            "reduceOnly": False,
                        }],
                        "nextPageCursor": "PAGE2",
                    },
                }, {}
            if repeated_cursor:
                return {
                    "retCode": 0,
                    "result": {"list": [], "nextPageCursor": "PAGE2"},
                }, {}
            return {
                "retCode": 0,
                "result": {
                    "list": [{
                        "symbol": "BTCUSDT",
                        "orderStatus": "PartiallyFilled",
                        "reduceOnly": "false",
                    }],
                    "nextPageCursor": "",
                },
            }, {}
        raise AssertionError(f"Should block before wallet read: {path}")

    error = (
        private_runtime.ExchangeReadUnavailable
        if repeated_cursor else RuntimeError
    )
    with (
        patch.object(private_runtime, "_is_sub_private_worker", return_value=False),
        patch.object(private_runtime, "authenticated_entry_connector_gate"),
        patch.object(private_runtime, "assert_legacy_automated_exit_ownership"),
        patch.object(private_runtime, "api_get", side_effect=bybit_get),
        patch.object(private_runtime, "api_post") as exchange_mutation,
        pytest.raises(error),
    ):
        private_runtime.execute_command(
            object(),
            "offline-key",
            "offline-secret",
            ("entry-paged", "entry", "BTCUSDT", json.dumps(payload)),
        )
    assert requests == ["FIRST", "PAGE2"]
    exchange_mutation.assert_not_called()


def test_wrong_wallet_account_type_rejects_entry_before_mutation() -> None:
    payload = {
        "stake_usdt": "10",
        "leverage": 1,
        "side": "Buy",
        "price": "100",
        "entry_offset_pct": "0",
        "initial_protection": {
            "stop_loss_pct": "1",
            "take_profit_pct": "3",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
    }

    def wrong_wallet(path, _params, *_key):
        if path == "/v5/market/instruments-info":
            return {
                "retCode": 0,
                "result": {"list": [{
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }]},
            }, {}
        if path == "/v5/account/wallet-balance":
            return {
                "retCode": 0,
                "result": {"list": [{
                    "accountType": "CONTRACT",
                    "totalAvailableBalance": "100",
                }]},
            }, {}
        if path in {"/v5/position/list", "/v5/order/realtime"}:
            return {"retCode": 0, "result": {"list": []}}, {}
        raise AssertionError(path)

    with (
        patch.object(private_runtime, "_is_sub_private_worker", return_value=False),
        patch.object(private_runtime, "authenticated_entry_connector_gate"),
        patch.object(private_runtime, "assert_legacy_automated_exit_ownership"),
        patch.object(private_runtime, "api_get", side_effect=wrong_wallet),
        patch.object(private_runtime, "api_post") as mutation,
        pytest.raises(
            private_runtime.ExchangeReadUnavailable,
            match="accountType is not UNIFIED",
        ),
    ):
        private_runtime.execute_command(
            object(), "offline", "offline",
            ("entry-wrong-wallet", "entry", "BTCUSDT", json.dumps(payload)),
        )
    mutation.assert_not_called()
