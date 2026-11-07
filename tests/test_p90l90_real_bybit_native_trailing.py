"""REAL P90 TRAIL035 uses exact Bybit trading-stop and signed position readback.

No real API, DB, credentials, server or mainnet mutations in these tests.
"""
from __future__ import annotations

import importlib
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch):
    # Account-local daemon uses sibling absolute imports on its service PYTHONPATH.
    # Provide that path only in this isolated, no-order test fixture.
    service_root = Path(__file__).resolve().parents[1] / "operations/connectivity"
    monkeypatch.syspath_prepend(str(service_root))
    monkeypatch.syspath_prepend(str(
        Path(__file__).resolve().parents[1] / "research/server/connectivity"
    ))
    return importlib.import_module("operations.connectivity.private_runtime")


ACCOUNT = "ta-" + "1" * 32


@pytest.mark.parametrize("short,activation", [(False, "100.5"), (True, "99.5")])
def test_actual_postfill_exit_decision_reaches_native_bybit_parameters(
    monkeypatch, runtime, short, activation,
):
    from test_p90l90_real_postfill_port import _setup

    port, _, _, decisions, proof = _setup(short=short)
    evaluation = port(**proof)
    assert decisions[0][0] is evaluation
    position = proof["position"]
    raw = dict(proof["private"].position)
    payload = {
        **_payload(), "strategy_id": position.strategy_id,
        "strategy_version": position.strategy_version,
        "trading_account_id": position.trading_account_id,
        "strategy_execution_binding_id": position.strategy_execution_binding_id,
        "exchange_position_key": position.exchange_position_key,
        "direction": position.direction.value,
        "action_kind": evaluation.decision.action_kind.value,
        "requested_mutation": evaluation.decision.requested_mutation.to_dict(),
    }
    monkeypatch.setattr(runtime, "TRADING_ACCOUNT_ID", position.trading_account_id)
    monkeypatch.setattr(runtime, "_universal_exit_position", lambda *a, **kw: raw)
    posts = []
    def post(endpoint, params, *a):
        posts.append((endpoint, params))
        assert endpoint == "/v5/position/trading-stop"
        assert Decimal(params["activePrice"]) == Decimal(activation)
        assert Decimal(params["trailingStop"]) == Decimal("0.35")
        assert "stopLoss" not in params and "takeProfit" not in params
        return {"retCode": 0, "result": {}}
    monkeypatch.setattr(runtime, "api_post", post)
    monkeypatch.setattr(runtime, "api_get", lambda *a: (
        {"retCode": 0, "result": {"list": [{**raw, "trailingStop": "0.35"}]}}, {},
    ))
    result = runtime._execute_universal_exit_command(
        None, "fake", "fake", command_id="test", symbol=position.symbol,
        payload=payload, positions_payload={"retCode": 0, "result": {"list": [raw]}},
        tick=Decimal("0.01"), qty_step=Decimal("0.001"),
    )
    assert result["retCode"] == 0 and len(posts) == 1


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
    monkeypatch: pytest.MonkeyPatch, wrong: str | None, runtime,
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
    monkeypatch: pytest.MonkeyPatch, runtime,
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
