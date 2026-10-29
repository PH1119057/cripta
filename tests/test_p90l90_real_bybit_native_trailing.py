"""REAL P90 TRAIL035 uses exact Bybit trading-stop and signed position readback.

No real API, DB, credentials, server or mainnet mutations in these tests.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from operations.connectivity import private_runtime as runtime


ACCOUNT = "ta-" + "1" * 32


def _position():
    return {
        "symbol": "LTCUSDT", "side": "Buy", "positionIdx": 0,
        "size": "0.1", "avgPrice": "100", "stopLoss": "90",
        "takeProfit": "110", "trailingStop": "0", "markPrice": "100.2",
    }


def _payload():
    return {
        "strategy_id": MAIN_ID, "strategy_version": VERSION,
        "trading_account_id": ACCOUNT,
        "strategy_execution_binding_id": "binding-real-main",
        "exchange_position_key": exchange_position_key(ACCOUNT, "LTCUSDT", 0),
        "identity_status": "PROVEN", "direction": "LONG",
        "action_kind": "SET_TRAILING",
        "requested_mutation": {
            "distance": "0.35", "active_price": "100.5",
            "tpsl_mode": "Full",
        },
    }


@pytest.mark.parametrize("wrong", (None, "0", "0.36", "NaN"))
def test_real_trailing_ack_alone_never_proves_exchange_protection(
    monkeypatch: pytest.MonkeyPatch, wrong: str | None,
) -> None:
    position = _position()
    called = []

    monkeypatch.setattr(runtime, "TRADING_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setattr(
        runtime, "_universal_exit_position",
        lambda *args, **kwargs: position,
    )

    def post(endpoint, params, key, secret):
        called.append((endpoint, params))
        assert params == {
            "category": "linear", "symbol": "LTCUSDT",
            "positionIdx": 0, "tpslMode": "Full",
            "trailingStop": "0.35", "activePrice": "100.5",
        }
        return {"retCode": 0, "result": {}}

    def read(endpoint, params, key, secret):
        signed = {**position}
        if wrong is None:
            signed.pop("trailingStop")
        else:
            signed["trailingStop"] = wrong
        return {"retCode": 0, "result": {"list": [signed]}}, {}

    monkeypatch.setattr(runtime, "api_post", post)
    monkeypatch.setattr(runtime, "api_get", read)
    with pytest.raises(runtime.ExchangeMutationBarrier, match="signed Exchange reconciliation"):
        runtime._execute_universal_exit_command(
            None, "key", "secret", command_id="cmd", symbol="LTCUSDT",
            payload=_payload(),
            positions_payload={"retCode": 0, "result": {"list": [position]}},
            tick=Decimal("0.01"), qty_step=Decimal("0.001"),
        )
    assert len(called) == 1


def test_real_trailing_requests_one_native_bybit_arm_and_preserves_tp_sl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    position = _position()
    called = []

    monkeypatch.setattr(runtime, "TRADING_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setattr(
        runtime, "_universal_exit_position",
        lambda *args, **kwargs: position,
    )

    def post(endpoint, params, key, secret):
        called.append((endpoint, params))
        assert endpoint == "/v5/position/trading-stop"
        assert params["trailingStop"] == "0.35"
        assert params["activePrice"] == "100.5"
        assert params["tpslMode"] == "Full"
        assert "stopLoss" not in params and "takeProfit" not in params
        return {"retCode": 0, "result": {}}

    def read(endpoint, params, key, secret):
        assert endpoint == "/v5/position/list"
        return {
            "retCode": 0,
            "result": {"list": [{**position, "trailingStop": "0.35"}]},
        }, {}

    monkeypatch.setattr(runtime, "api_post", post)
    monkeypatch.setattr(runtime, "api_get", read)
    result = runtime._execute_universal_exit_command(
        None, "key", "secret", command_id="cmd", symbol="LTCUSDT",
        payload=_payload(),
        positions_payload={"retCode": 0, "result": {"list": [position]}},
        tick=Decimal("0.01"), qty_step=Decimal("0.001"),
    )
    assert result["retCode"] == 0
    assert len(called) == 1
