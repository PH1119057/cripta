"""Account-scoped signed Bybit execution safety for P90L90 REAL v3.

No Bybit credentials, network calls, server or production DB are used.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_exit.contracts import ExitActionKind
from bybit_workbench.universal_exit.p90l90_exchange_safety import (
    P90RealExchangeSafetyBlock,
    p90_real_exchange_position_key,
    verify_p90_real_exchange_mutation,
)


def _payload(*, main: bool = True) -> dict[str, object]:
    account = "ta-main" if main else "ta-sub"
    strategy = MAIN_ID if main else SUB_ID
    symbol = "LTCUSDT" if main else "DOTUSDT"
    return {
        "strategy_id": strategy,
        "strategy_version": VERSION,
        "trading_account_id": account,
        "identity_status": "PROVEN",
        "strategy_execution_binding_id": "binding-" + account,
        "exchange_position_key": exchange_position_key(account, symbol, 0),
        "direction": "LONG",
    }


def _position(*, side: str = "Buy") -> dict[str, object]:
    long = side == "Buy"
    return {
        "symbol": "LTCUSDT",
        "positionIdx": 0,
        "side": side,
        "size": "0.1",
        "avgPrice": "100",
        "stopLoss": "90" if long else "110",
        "takeProfit": "110" if long else "90",
        "markPrice": "101" if long else "99",
        "trailingStop": "0",
    }


def _guard(
    *,
    payload: dict[str, object] | None = None,
    position: dict[str, object] | None = None,
    action: ExitActionKind = ExitActionKind.SET_STOP,
    mutation: dict[str, object] | None = None,
    account: str = "ta-main",
) -> None:
    verify_p90_real_exchange_mutation(
        payload=payload if payload is not None else _payload(),
        trading_account_id=account,
        symbol="LTCUSDT",
        position=position if position is not None else _position(),
        action_kind=action,
        mutation=mutation if mutation is not None else {"stop_price": "100.50"},
        tick=Decimal("0.01"),
    )


def test_main_and_sub_have_distinct_account_qualified_exchange_keys() -> None:
    main = p90_real_exchange_position_key(
        _payload(), trading_account_id="ta-main", symbol="LTCUSDT", position_idx=0
    )
    sub = p90_real_exchange_position_key(
        _payload(main=False), trading_account_id="ta-sub",
        symbol="DOTUSDT", position_idx=0,
    )
    assert main == "BYBIT:ta-main:LINEAR:USDT:LTCUSDT:0"
    assert sub == "BYBIT:ta-sub:LINEAR:USDT:DOTUSDT:0"
    assert main != sub


def test_non_p90_strategy_is_not_rerouted_to_new_execution_owner() -> None:
    assert p90_real_exchange_position_key(
        {"strategy_id": "unrelated", "strategy_version": "1"},
        trading_account_id="ta-main", symbol="BTCUSDT", position_idx=0,
    ) is None


@pytest.mark.parametrize("field,value", (
    ("trading_account_id", "ta-sub"),
    ("strategy_version", "1.1-p90l90-strategy-second48-20261028"),
    ("strategy_id", SUB_ID),
    ("identity_status", "LEGACY_UNKNOWN"),
    ("strategy_execution_binding_id", ""),
    ("exchange_position_key", "BYBIT:UNIFIED:LINEAR:USDT:LTCUSDT:0"),
))
def test_wrong_account_or_card_must_never_reach_mutation(
    field: str, value: str,
) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(payload=payload)


@pytest.mark.parametrize("field,value", (
    ("stopLoss", ""),
    ("stopLoss", "NaN"),
    ("takeProfit", ""),
    ("takeProfit", "100"),
    ("markPrice", "89"),
    ("size", "0"),
))
def test_missing_or_unprotected_signed_exchange_position_is_rejected(
    field: str, value: str,
) -> None:
    position = _position()
    position[field] = value
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(position=position)


def test_long_and_short_live_stop_updates_must_only_tighten() -> None:
    _guard(mutation={"stop_price": "100.50"})
    for requested in ("90", "89.99", "101.01", "NaN"):
        with pytest.raises(P90RealExchangeSafetyBlock):
            _guard(mutation={"stop_price": requested})
    short_payload = _payload()
    short_payload["direction"] = "SHORT"
    _guard(
        payload=short_payload,
        position=_position(side="Sell"),
        mutation={"stop_price": "99.50"},
    )
    for requested in ("110", "111", "98.99"):
        with pytest.raises(P90RealExchangeSafetyBlock):
            _guard(
                payload=short_payload, position=_position(side="Sell"),
                mutation={"stop_price": requested},
            )


def test_raw_tp_must_remain_favorable_and_nonmarketable_on_live_exchange() -> None:
    _guard(action=ExitActionKind.SET_TP, mutation={"take_profit_price": "106"})
    for requested in ("100", "101", "99", "NaN"):
        with pytest.raises(P90RealExchangeSafetyBlock):
            _guard(
                action=ExitActionKind.SET_TP,
                mutation={"take_profit_price": requested},
            )
    short_payload = _payload()
    short_payload["direction"] = "SHORT"
    _guard(
        payload=short_payload, position=_position(side="Sell"),
        action=ExitActionKind.SET_TP,
        mutation={"take_profit_price": "95"},
    )
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(
            payload=short_payload, position=_position(side="Sell"),
            action=ExitActionKind.SET_TP,
            mutation={"take_profit_price": "99.50"},
        )


def test_unsupported_universal_action_and_unproven_symbol_are_rejected() -> None:
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(action=ExitActionKind.REDUCE, mutation={"quantity": "0.1"})
    position = _position()
    position["symbol"] = "BTCUSDT"
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(position=position)


def test_emergency_reduce_only_close_remains_possible_without_exchange_stop() -> None:
    _guard(action=ExitActionKind.CLOSE, mutation={"order_type": "MARKET"})
    position = _position()
    position["stopLoss"] = "0"
    position["takeProfit"] = "0"
    _guard(
        position=position, action=ExitActionKind.CLOSE,
        mutation={"order_type": "MARKET"},
    )
    wrong = _payload()
    wrong["trading_account_id"] = "ta-other"
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(
            payload=wrong, position=position, action=ExitActionKind.CLOSE,
            mutation={"order_type": "MARKET"},
        )


def test_existing_profit_side_trailing_stop_can_be_tightened_without_widening() -> None:
    long_position = _position()
    long_position["stopLoss"] = "100.20"
    _guard(position=long_position, mutation={"stop_price": "100.50"})
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(position=long_position, mutation={"stop_price": "100.10"})

    short_position = _position(side="Sell")
    short_position["stopLoss"] = "99.75"
    short_payload = _payload()
    short_payload["direction"] = "SHORT"
    _guard(
        payload=short_payload, position=short_position,
        mutation={"stop_price": "99.50"},
    )
    with pytest.raises(P90RealExchangeSafetyBlock):
        _guard(
            payload=short_payload, position=short_position,
            mutation={"stop_price": "99.90"},
        )


@pytest.mark.parametrize("side,activation", (("Buy", "100.5"), ("Sell", "99.5")))
def test_bybit_native_trailing_requires_exact_price_distance_and_activation(
    side: str, activation: str,
) -> None:
    position = _position(side=side)
    position["markPrice"] = "100.2" if side == "Buy" else "99.8"
    payload = _payload()
    payload["direction"] = "LONG" if side == "Buy" else "SHORT"
    request = {
        "distance": "0.35", "active_price": activation, "tpsl_mode": "Full",
    }
    _guard(payload=payload, position=position,
           action=ExitActionKind.SET_TRAILING, mutation=request)
    for field, value in (
        ("distance", "0.36"), ("distance", "0.0035"),
        ("active_price", "100.49" if side == "Buy" else "99.51"),
        ("tpsl_mode", "Partial"),
    ):
        bad = dict(request)
        bad[field] = value
        with pytest.raises(P90RealExchangeSafetyBlock):
            _guard(payload=payload, position=position,
                   action=ExitActionKind.SET_TRAILING, mutation=bad)
    position["trailingStop"] = "0.35"
    with pytest.raises(P90RealExchangeSafetyBlock, match="ALREADY_INSTALLED"):
        _guard(payload=payload, position=position,
               action=ExitActionKind.SET_TRAILING, mutation=request)


def test_late_native_trailing_or_missing_signed_state_is_not_accepted() -> None:
    position = _position()
    for field, value in (
        ("markPrice", "100.5"), ("markPrice", "101"),
        ("trailingStop", ""), ("trailingStop", "NaN"),
    ):
        bad = dict(position)
        bad[field] = value
        with pytest.raises(P90RealExchangeSafetyBlock):
            _guard(position=bad, action=ExitActionKind.SET_TRAILING,
                   mutation={"distance": "0.35",
                             "active_price": "100.5", "tpsl_mode": "Full"})
