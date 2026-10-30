"""Private signed-evidence transport: synthetic local protocol tests only.

An IPC fixture's 'VERIFIED' is NOT evidence of real Bybit signature, private
WS epoch, fill-to-native coverage, Exchange protection or PAPER/REAL parity.
"""
from __future__ import annotations

import socket
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_p90l90_real_observation import _scope

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
    P90PrivateProofClient,
    _checked_evidence,
    _frame,
    _receive,
    checked_request,
    evidence_to_wire,
    peer_same_runtime_uid,
    private_ipc_address,
    serve_private_proof,
)

CONNECTOR = "connector-p90-test"


def _account() -> str:
    return "ta-" + uuid4().hex


def _request(account: str) -> dict[str, object]:
    return {
        "version": 1,
        "account": account,
        "connector": CONNECTOR,
        "nonce": "c" * 32,
        "position_id": "position-p90-real-test",
        "symbol": "LTCUSDT",
        "kind": "TRADE",
        "event_at": datetime.now(UTC).isoformat(),
        "seq": 101,
        "exec_id": "bybit-native-101",
    }


def _scope_for_account(account: str):
    position, _plan, _state, _cursor, evidence, _price = _scope()
    event = datetime.now(UTC) - timedelta(milliseconds=20)
    position = replace(
        position,
        fill_at=event - timedelta(seconds=1),
        initial_protection_confirmed_at=event - timedelta(milliseconds=5),
        trading_account_id=account,
        exchange_position_key=exchange_position_key(account, position.symbol, 0),
    )
    evidence = replace(
        evidence,
        trading_account_id=account,
        exchange_position_key=position.exchange_position_key,
        connector_id=CONNECTOR,
        observed_at=event,
        source_ref="bybit:signed-position-list:exact-account",
    )
    cursor = TradeCursor(
        symbol=position.symbol, seq=101,
        exec_id="bybit-native-101", traded_at=event,
    )
    return position, cursor, evidence


@pytest.mark.parametrize("invalid", (
    "", "ta-main", "ta-" + "g" * 32, "ta-" + "1" * 31,
    "ta-" + "1" * 33, "ta-" + "1" * 31 + "/",
))
def test_address_rejects_foreign_or_noncanonical_account(invalid: str) -> None:
    with pytest.raises(P90PrivateIpcUnverified, match="ACCOUNT_INVALID"):
        private_ipc_address(invalid)


def test_checked_request_accepts_only_exact_account_connector_and_trade_cursor() -> None:
    account = _account()
    request = _request(account)
    position_id, symbol, kind, when, seq, exec_id = checked_request(
        request, expected_account=account, expected_connector=CONNECTOR,
    )
    assert position_id == "position-p90-real-test"
    assert (symbol, kind, seq, exec_id) == (
        "LTCUSDT", "TRADE", 101, "bybit-native-101",
    )
    assert (datetime.now(UTC) - when).total_seconds() < 15


@pytest.mark.parametrize(("key", "bad"), (
    ("version", 2), ("account", "ta-" + "a" * 32),
    ("connector", "foreign-connector"), ("nonce", "invalid"),
    ("kind", "ORDER"), ("seq", True), ("seq", -1),
    ("exec_id", ""), ("position_id", ""), ("symbol", ""),
))
def test_request_wrong_identity_or_cursor_denied(key: str, bad: object) -> None:
    account = _account()
    request = _request(account)
    request[key] = bad
    with pytest.raises(P90PrivateIpcUnverified):
        checked_request(
            request, expected_account=account, expected_connector=CONNECTOR,
        )


def test_request_cannot_replay_old_or_future_source_event() -> None:
    account = _account()
    request = _request(account)
    for shift in (timedelta(minutes=-1), timedelta(minutes=1)):
        request["event_at"] = (datetime.now(UTC) + shift).isoformat()
        with pytest.raises(P90PrivateIpcUnverified, match="EVENT_STALE"):
            checked_request(
                request, expected_account=account,
                expected_connector=CONNECTOR,
            )


def test_closed_boundary_requires_absence_of_native_trade_cursor() -> None:
    account = _account()
    request = _request(account)
    request["kind"] = "CLOSED"
    with pytest.raises(P90PrivateIpcUnverified, match="CLOSED_CURSOR"):
        checked_request(
            request, expected_account=account, expected_connector=CONNECTOR,
        )
    request["seq"] = None
    request["exec_id"] = None
    assert checked_request(
        request, expected_account=account, expected_connector=CONNECTOR,
    )[2] == "CLOSED"


def test_signed_evidence_wire_roundtrip_preserves_account_provenance() -> None:
    account = _account()
    position, _cursor, evidence = _scope_for_account(account)
    wire = evidence_to_wire(evidence)
    restored = _checked_evidence(
        wire, position=position, expected_connector=CONNECTOR,
        received_at=datetime.now(UTC),
    )
    assert restored == evidence
    for changed in (
        {"account": _account()},
        {"connector": "foreign-connector"},
        {"binding": "wrong"},
        {"position_idx": 1},
        {"source_ref": "untrusted-unverified"},
        {"pending_exit_mutation": True},
    ):
        with pytest.raises(P90PrivateIpcUnverified):
            _checked_evidence(
                {**wire, **changed},
                position=position, expected_connector=CONNECTOR,
                received_at=datetime.now(UTC),
            )


def test_wire_evidence_fails_closed_for_stale_or_bad_tick() -> None:
    account = _account()
    position, _cursor, evidence = _scope_for_account(account)
    wire = evidence_to_wire(evidence)
    for value in ("NaN", "Infinity", "0", "invalid"):
        with pytest.raises(P90PrivateIpcUnverified, match="TICK"):
            _checked_evidence(
                {**wire, "tick_size": value},
                position=position, expected_connector=CONNECTOR,
                received_at=datetime.now(UTC),
            )
    with pytest.raises(P90PrivateIpcUnverified, match="STALE"):
        _checked_evidence(
            {**wire, "observed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            position=position, expected_connector=CONNECTOR,
            received_at=datetime.now(UTC),
        )


def test_frame_rejects_oversized_or_nan_and_requires_single_newline() -> None:
    with pytest.raises(P90PrivateIpcUnverified, match="TOO_LARGE"):
        _frame({"oversized": "z" * 35_000})
    with pytest.raises(P90PrivateIpcUnverified, match="SERIALIZATION"):
        _frame({"nan": float("nan")})
    left, right = socket.socketpair()
    try:
        left.sendall(_frame({"kind": "proof"}))
        assert _receive(right) == {"kind": "proof"}
        left.sendall(b'{"kind": "proof"}\nEXTRA')
        with pytest.raises(P90PrivateIpcUnverified, match="FRAME"):
            _receive(right)
    finally:
        left.close()
        right.close()


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED Linux transport")
def test_local_peer_uid_is_proven_by_kernel() -> None:
    left, right = socket.socketpair()
    try:
        assert peer_same_runtime_uid(left)
        assert peer_same_runtime_uid(right)
    finally:
        left.close()
        right.close()


@pytest.mark.skipif(sys.platform != "linux", reason="abstract AF_UNIX Linux transport")
def test_synthetic_local_end_to_end_uses_exact_account_and_nonce() -> None:
    account = _account()
    position, cursor, evidence = _scope_for_account(account)
    ready = threading.Event()
    running = threading.Event()
    running.set()
    received: list[str] = []
    failures: list[str] = []

    def handler(request):
        received.append(str(request["position_id"]))
        return replace(evidence, observed_at=datetime.now(UTC))

    def producer() -> None:
        try:
            serve_private_proof(
                account=account, connector=CONNECTOR, handler=handler,
                running=running.is_set, ready=ready.set,
            )
        except Exception as exc:
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    try:
        assert ready.wait(3.0), failures
        client = P90PrivateProofClient(
            account=account, connector=CONNECTOR, timeout_seconds=3.0,
        )
        actual = client(position, cursor)
        assert actual.trading_account_id == account
        assert actual.strategy_execution_binding_id == evidence.strategy_execution_binding_id
        assert received == [position.strategy_position_id]
        with pytest.raises(P90PrivateIpcUnverified, match="FOREIGN_ACCOUNT"):
            other_account, _, _ = _scope_for_account(_account())
            client(other_account, cursor)
    finally:
        running.clear()
        worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert not failures


def test_private_worker_starts_ipc_only_when_bound_and_thread_alive() -> None:
    """Source guard keeps broker startup fail closed, without enabling REAL."""
    import ast
    from pathlib import Path

    runtime = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(runtime)
    broker = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "p90_private_ipc_loop"
    )
    main = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    broker_source = ast.get_source_segment(runtime, broker)
    main_source = ast.get_source_segment(runtime, main)
    assert broker_source is not None
    assert main_source is not None
    assert "ready: threading.Event" in broker_source
    assert "ready=ready.set" in broker_source
    assert 'os.environ.get("CRIPTA_P90_REAL_PRIVATE_IPC", "DISABLED")' in main_source
    assert "proof_thread.start()" in main_source
    assert "proof_ready.wait(timeout=3.0)" in main_source
    assert "not proof_thread.is_alive()" in main_source
    assert "P90_REAL_PRIVATE_IPC_NOT_BOUND" in main_source
    assert "thread = threading.Thread(target=trade_loop" in main_source
    assert main_source.index("proof_ready.wait(timeout=3.0)") < (
        main_source.index("thread = threading.Thread(target=trade_loop")
    )
