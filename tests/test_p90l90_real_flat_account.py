"""No-order flat-account proof: signed source + IPC negative tests."""
from __future__ import annotations

import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bybit_workbench.exchange.bybit.account_connector_attestation import (
    AuthenticatedConnectorAttestation,
)
from bybit_workbench.universal_entry.p90l90_real_source_epoch_start import (
    P90RealSignedFlatAccount,
)
from bybit_workbench.universal_exit import p90l90_real_flat_account as flat
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
    P90PrivateProofClient,
    checked_flat_evidence,
    checked_flat_request,
    flat_to_wire,
    serve_private_proof,
)

ACCOUNT = "ta-" + "a" * 32
CONNECTOR = "connector-flat-test"
AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class FakeDb:
    def __init__(self, now: datetime) -> None:
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS")
        )
        self.generation = (
            "generation-10", "COMPLETE", now - timedelta(seconds=1),
            now - timedelta(seconds=1), 0, 0,
        )
        self.pending = (0,)

    def execute(self, query: str, params=()):
        if "FROM runtime.account_state_generations" in query:
            return SimpleNamespace(fetchone=lambda: self.generation)
        if "FROM runtime.trade_commands" in query:
            return SimpleNamespace(fetchone=lambda: self.pending)
        raise AssertionError("unexpected DB read")


def _raw(category: str | None = None, items=None, cursor: str = ""):
    if category is None:
        return {"retCode": 0, "result": {"id": "signed-test"}}
    return {
        "retCode": 0,
        "result": {
            "category": category, "list": [] if items is None else items,
            "nextPageCursor": cursor,
        },
    }


def _reader(monkeypatch, now=AT):
    db = FakeDb(now)
    requested = []
    responses = {
        "/v5/user/query-api": _raw(),
        "/v5/position/list": _raw("linear"),
        "/v5/order/realtime": _raw("linear"),
    }

    def signed(endpoint, params):
        requested.append((endpoint, params))
        return responses[endpoint]

    monkeypatch.setattr(
        flat, "normalize_authenticated_api_key_info",
        lambda *_args, **_kwargs: {"account_user_id": "12345678"},
    )
    monkeypatch.setattr(
        flat, "attest_bybit_account_connector",
        lambda *_args, **_kwargs: AuthenticatedConnectorAttestation(True, ()),
    )
    reader = flat.P90RealSignedFlatAccountReader(
        db, trading_account_id=ACCOUNT, account_connector_id=CONNECTOR,
        mounted_credential_ref="mounted-ref-test", signed_get=signed,
        verified_private_ws_continuity=lambda: True, clock=lambda: now,
    )
    return reader, db, responses, requested


def test_signed_flat_requires_current_db_and_two_complete_bybit_inventories(
    monkeypatch,
) -> None:
    reader, _, _, requested = _reader(monkeypatch)
    evidence = reader()
    assert evidence.trading_account_id == ACCOUNT
    assert evidence.private_generation_id == "generation-10"
    assert evidence.signed_uid == "12345678"
    assert (evidence.open_positions, evidence.open_orders,
            evidence.pending_commands) == (0, 0, 0)
    assert [ep for ep, _ in requested] == [
        "/v5/user/query-api", "/v5/position/list", "/v5/order/realtime",
    ]
    assert requested[1][1]["settleCoin"] == "USDT"
    assert requested[2][1]["openOnly"] == "0"


@pytest.mark.parametrize("failure", (
    "open_position", "open_order", "unread_page", "bad_category",
    "pending_command", "failed_generation", "stale_generation",
    "account_positions", "account_orders", "signed_api_error",
))
def test_signed_flat_fails_closed_on_inventory_or_generation(
    monkeypatch, failure,
) -> None:
    reader, db, responses, _ = _reader(monkeypatch)
    if failure == "open_position":
        responses["/v5/position/list"]["result"]["list"] = [{"size": "1"}]
    elif failure == "open_order":
        responses["/v5/order/realtime"]["result"]["list"] = [{"orderId": "active"}]
    elif failure == "unread_page":
        responses["/v5/order/realtime"]["result"]["nextPageCursor"] = "hidden"
    elif failure == "bad_category":
        responses["/v5/position/list"]["result"]["category"] = "spot"
    elif failure == "pending_command":
        db.pending = (1,)
    elif failure == "failed_generation":
        db.generation = (db.generation[0], "FAILED", *db.generation[2:])
    elif failure == "stale_generation":
        db.generation = (
            *db.generation[:3], AT - timedelta(minutes=1),
            *db.generation[4:],
        )
    elif failure == "account_positions":
        db.generation = (*db.generation[:4], 1, 0)
    elif failure == "account_orders":
        db.generation = (*db.generation[:5], 1)
    else:
        responses["/v5/user/query-api"]["retCode"] = 10001
    with pytest.raises(flat.P90RealFlatAccountUnverified):
        reader()


def test_signed_flat_blocks_ws_gap_and_connector_mismatch(monkeypatch) -> None:
    reader, _, _, _ = _reader(monkeypatch)
    reader._ws = lambda: False
    with pytest.raises(flat.P90RealFlatAccountUnverified, match="WS_UNVERIFIED"):
        reader()
    reader, _, _, _ = _reader(monkeypatch)
    monkeypatch.setattr(
        flat, "attest_bybit_account_connector",
        lambda *_args, **_kwargs: AuthenticatedConnectorAttestation(False, ("NO",)),
    )
    with pytest.raises(flat.P90RealFlatAccountUnverified, match="UID_MISMATCH"):
        reader()


def _request():
    return {
        "version": 1, "account": ACCOUNT, "connector": CONNECTOR,
        "nonce": "c" * 32, "kind": "FLAT",
        "event_at": datetime.now(UTC).isoformat(),
    }


def _evidence():
    return P90RealSignedFlatAccount(
        trading_account_id=ACCOUNT, signed_uid="12345678",
        private_generation_id="generation-live",
        source_ref="bybit:signed-flat-linear-usdt:exact-account",
        observed_at=datetime.now(UTC),
        open_positions=0, open_orders=0, pending_commands=0,
    )


def test_flat_ipc_accepts_only_exact_shape_and_signed_account() -> None:
    assert checked_flat_request(
        _request(), expected_account=ACCOUNT, expected_connector=CONNECTOR,
    ) == "c" * 32
    for change in (
        {"position_id": "forged"},
        {"account": "ta-" + "b" * 32},
        {"connector": "foreign"},
        {"nonce": "bad"},
        {"kind": "TRADE"},
        {"event_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
    ):
        with pytest.raises(P90PrivateIpcUnverified):
            checked_flat_request(
                {**_request(), **change},
                expected_account=ACCOUNT, expected_connector=CONNECTOR,
            )
    raw = flat_to_wire(_evidence())
    assert checked_flat_evidence(
        raw, expected_account=ACCOUNT, received_at=datetime.now(UTC),
    ).open_orders == 0
    for change in (
        {"account": "ta-" + "b" * 32},
        {"uid": "invalid"},
        {"open_orders": 1},
        {"pending_commands": True},
        {"source_ref": "forged"},
        {"observed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
    ):
        with pytest.raises(P90PrivateIpcUnverified):
            checked_flat_evidence(
                {**raw, **change}, expected_account=ACCOUNT,
                received_at=datetime.now(UTC),
            )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux peer-UID abstract socket")
def test_flat_ipc_roundtrip_is_independent_of_trade_position() -> None:
    account = "ta-" + uuid4().hex
    ready, running = threading.Event(), threading.Event()
    running.set()
    seen = []
    errors = []

    def serve():
        try:
            serve_private_proof(
                account=account, connector=CONNECTOR,
                handler=lambda *_: (_ for _ in ()).throw(AssertionError("TRADE")),
                flat_handler=lambda: replace(
                    _evidence(), trading_account_id=account,
                    observed_at=datetime.now(UTC),
                ),
                ready=ready.set, running=running.is_set,
            )
        except Exception as exc:
            errors.append(type(exc).__name__)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        assert ready.wait(3)
        flat_state = P90PrivateProofClient(
            account=account, connector=CONNECTOR, timeout_seconds=3,
        ).read_flat_account()
        seen.append(flat_state)
        assert flat_state.trading_account_id == account
        assert flat_state.open_positions == 0
    finally:
        running.clear()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert not errors and len(seen) == 1
