"""Authenticated first-fill source rejection: exact original checkout, no Bybit I/O."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.connectivity import p90l90_real_postfill_source_client as client

BROKER = (
    Path(__file__).resolve().parents[1]
    / "operations/monitoring/p90l90_real_postfill_source_broker.py"
)

NONCE = "f" * 32
EPOCH = "p90-real-epoch-" + "a" * 40
COMMIT = "a" * 40


class FakeSocket:
    def __init__(self):
        self.sent = []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def settimeout(self, value):
        assert value == 3.0
    def connect(self, address):
        assert address
    def sendall(self, value):
        self.sent.append(value)


def call_client(monkeypatch, response):
    stream = FakeSocket()
    # Windows CI lacks AF_UNIX: the fake socket must intercept this constant
    # before constructing its in-process test transport (never real network).
    monkeypatch.setattr(client.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(client.socket, "socket", lambda *a, **kw: stream)
    monkeypatch.setattr(client, "peer_same_runtime_uid", lambda sock: sock is stream)
    monkeypatch.setattr(client.secrets, "token_hex", lambda size: NONCE)
    monkeypatch.setattr(client, "_receive", lambda sock: response)
    position = SimpleNamespace(
        strategy_position_id="synthetic-pos",
        trading_account_id="synthetic-account",
        symbol="SYNTHETICUSDT",
    )
    with pytest.raises(client.P90RealPostfillSourceClientUnverified) as exc:
        client.read_real_postfill_source_window(
            position=position,
            source_epoch_id=EPOCH,
            source_commit=COMMIT,
        )
    assert len(stream.sent) == 1, "Request must precede response"
    return str(exc.value)


def good():
    return dict(
        version=1, status="BLOCKED", nonce=NONCE,
        source_epoch_id=EPOCH, position_id="synthetic-pos",
        trading_account_id="synthetic-account",
        reason_code="P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE",
    )


def test_signed_scope_rejection_preserves_safe_source_reason(monkeypatch):
    assert call_client(monkeypatch, good()) == (
        "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE"
    )


@pytest.mark.parametrize(("key", "bad"), [
    ("nonce", "wrong"), ("source_epoch_id", "foreign"),
    ("position_id", "foreign"), ("trading_account_id", "foreign"),
    ("reason_code", "P90_REAL_MARKET_SOURCE_X:SECRET"),
    ("reason_code", "unexpected_raw_error"),
    ("reason_code", "P90_REAL_MARKET_SOURCE_" + "X" * 120),
])
def test_forged_or_unsafe_source_rejection_returns_only_generic_code(
    monkeypatch, key, bad
):
    response = good()
    response[key] = bad
    assert call_client(monkeypatch, response) == (
        "P90_REAL_POSTFILL_SOURCE_RESPONSE_UNVERIFIED"
    )


def test_broker_safe_error_must_follow_verified_peer_and_nonce():
    assert BROKER.is_file(), "Must inspect real server source"
    source = ast.parse(BROKER.read_text(encoding="utf-8"))
    broker = next(
        n for n in source.body
        if isinstance(n, ast.FunctionDef) and n.name == "serve_source_windows"
    )
    body = ast.unparse(broker)
    assert body.index("peer_same_runtime_uid(stream)") < body.index("nonce = None")
    assert body.index("nonce = None") < body.index("checked_request(")
    handlers = [
        n for n in ast.walk(broker)
        if isinstance(n, ast.ExceptHandler)
        and isinstance(n.type, ast.Name)
        and n.type.id == "P90RealPostfillMarketSourceUnverified"
    ]
    assert len(handlers) == 1
    safe = ast.unparse(handlers[0])
    for requirement in (
        "if nonce is None", "reason_code", "source_epoch_id",
        "trading_account_id", "position_id", "stream.sendall",
    ):
        assert requirement in safe
