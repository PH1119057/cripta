"""Signed account-wide inventory used only by P90 REAL restart recovery."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.exchange.bybit.account_connector_attestation import (
    AuthenticatedConnectorAttestation,
)
from bybit_workbench.universal_exit import p90l90_real_recovery_account as recovery

ACCOUNT = "ta-" + "a" * 32
CONNECTOR = "connector-restart-test"
AT = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)


class FakeDb:
    def __init__(self) -> None:
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS")
        )
        self.generation = (
            "generation-restart", "COMPLETE",
            AT - timedelta(seconds=1), AT - timedelta(seconds=1), 1, 0,
        )
        self.pending = (0,)

    def execute(self, query: str, params=()):
        del params
        if "FROM runtime.account_state_generations" in query:
            return SimpleNamespace(fetchone=lambda: self.generation)
        if "FROM runtime.trade_commands" in query:
            return SimpleNamespace(fetchone=lambda: self.pending)
        raise AssertionError("unexpected DB read: " + query)


def _position(symbol: str = "LTCUSDT") -> dict[str, object]:
    return {
        "symbol": symbol, "side": "Buy", "positionIdx": 0,
        "size": "0.1", "avgPrice": "100",
        "stopLoss": "90", "takeProfit": "105",
        "trailingStop": "0.35", "markPrice": "101",
    }


def _raw(category: str | None = None, items=None, cursor: str = ""):
    if category is None:
        return {"retCode": 0, "result": {"id": "signed-restart"}}
    return {
        "retCode": 0,
        "result": {
            "category": category,
            "list": [] if items is None else items,
            "nextPageCursor": cursor,
        },
    }


def _reader(monkeypatch):
    db = FakeDb()
    responses = {
        "/v5/user/query-api": _raw(),
        "/v5/position/list": _raw("linear", [_position()]),
        "/v5/order/realtime": _raw("linear"),
    }
    requested: list[tuple[str, dict[str, str]]] = []

    def signed(endpoint: str, params: dict[str, str]):
        requested.append((endpoint, params))
        return responses[endpoint]

    monkeypatch.setattr(
        recovery, "normalize_authenticated_api_key_info",
        lambda *_args, **_kwargs: {"account_user_id": "12345678"},
    )
    monkeypatch.setattr(
        recovery, "attest_bybit_account_connector",
        lambda *_args, **_kwargs: AuthenticatedConnectorAttestation(True, ()),
    )
    reader = recovery.P90RealSignedRecoveryAccountReader(
        db,
        trading_account_id=ACCOUNT,
        account_connector_id=CONNECTOR,
        mounted_credential_ref="mounted-ref-test",
        signed_get=signed,
        verified_private_ws_continuity=lambda: True,
        clock=lambda: AT,
    )
    return reader, db, responses, requested


def test_restart_reader_proves_complete_nonflat_account_inventory(monkeypatch) -> None:
    reader, _db, _responses, requested = _reader(monkeypatch)
    proof = reader()
    assert proof.trading_account_id == ACCOUNT
    assert proof.signed_uid == "12345678"
    assert proof.private_generation_id == "generation-restart"
    assert tuple(row["symbol"] for row in proof.positions) == ("LTCUSDT",)
    assert proof.open_orders == proof.pending_commands == 0
    assert [endpoint for endpoint, _ in requested] == [
        "/v5/user/query-api", "/v5/position/list", "/v5/order/realtime",
    ]
    assert requested[1][1]["settleCoin"] == "USDT"


@pytest.mark.parametrize("failure", (
    "hidden_position_page", "open_order", "hidden_order_page",
    "generation_count", "pending_command", "wrong_position_idx",
    "bad_take_profit", "connector_mismatch",
))
def test_restart_reader_fails_closed_on_incomplete_or_conflicting_state(
    monkeypatch, failure: str,
) -> None:
    reader, db, responses, _requested = _reader(monkeypatch)
    if failure == "hidden_position_page":
        responses["/v5/position/list"]["result"]["nextPageCursor"] = "hidden"
    elif failure == "open_order":
        responses["/v5/order/realtime"]["result"]["list"] = [{"orderId": "active"}]
    elif failure == "hidden_order_page":
        responses["/v5/order/realtime"]["result"]["nextPageCursor"] = "hidden"
    elif failure == "generation_count":
        db.generation = (*db.generation[:4], 2, 0)
    elif failure == "pending_command":
        db.pending = (1,)
    elif failure == "wrong_position_idx":
        responses["/v5/position/list"]["result"]["list"][0]["positionIdx"] = 1
    elif failure == "bad_take_profit":
        responses["/v5/position/list"]["result"]["list"][0]["takeProfit"] = "0"
    else:
        monkeypatch.setattr(
            recovery, "attest_bybit_account_connector",
            lambda *_args, **_kwargs: AuthenticatedConnectorAttestation(
                False, ("UID_MISMATCH",)
            ),
        )
    with pytest.raises(recovery.P90RealRecoveryAccountUnverified):
        reader()


def test_restart_reader_rechecks_private_ws_after_signed_reads(monkeypatch) -> None:
    reader, _db, _responses, _requested = _reader(monkeypatch)
    states = iter((True, False))
    reader._ws = lambda: next(states)
    with pytest.raises(
        recovery.P90RealRecoveryAccountUnverified,
        match="PRIVATE_WS_LOST_DURING_READ",
    ):
        reader()
