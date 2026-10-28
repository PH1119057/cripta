"""P90L90 REAL v3 Exchange mutation gate, independent of Entry/PAPER math.

This is validation of an authenticated Bybit position inventory already read
by the correctly scoped private worker. It does not issue API calls, infer a
missing stop/TP, select a Strategy, or authorize LIVE.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION

from .contracts import ExitActionKind


class P90RealExchangeSafetyBlock(ValueError):
    """Reject wrong signed account/position or unsafe Exchange-side mutation."""


def _positive(value: object, label: str) -> Decimal:
    try:
        price = Decimal(str(value))
    except (TypeError, InvalidOperation, ValueError):
        raise P90RealExchangeSafetyBlock(f"P90_REAL_EXIT_{label}_INVALID") from None
    if not price.is_finite() or price <= 0:
        raise P90RealExchangeSafetyBlock(f"P90_REAL_EXIT_{label}_INVALID")
    return price


def p90_real_exchange_position_key(
    payload: Mapping[str, object],
    *,
    trading_account_id: str,
    symbol: str,
    position_idx: int,
) -> str | None:
    """Return account-qualified key if this exact v3 Strategy owns the Exit.

    None means another, unrelated Strategy; malformed P90 identity NEVER
    downgrades to an unrelated Strategy's legacy key.
    """
    strategy_id = str(payload.get("strategy_id") or "")
    version = str(payload.get("strategy_version") or "")
    if strategy_id not in ALLOWED_SYMBOLS and version != VERSION:
        return None
    if (
        strategy_id not in ALLOWED_SYMBOLS
        or version != VERSION
        or symbol not in ALLOWED_SYMBOLS[strategy_id]
        or not trading_account_id
        or str(payload.get("trading_account_id") or "") != trading_account_id
        or position_idx != 0
        or str(payload.get("identity_status") or "") != "PROVEN"
        or not str(payload.get("strategy_execution_binding_id") or "")
    ):
        raise P90RealExchangeSafetyBlock("P90_REAL_EXIT_EXACT_ACCOUNT_IDENTITY_INVALID")
    return exchange_position_key(trading_account_id, symbol, 0)


def verify_p90_real_exchange_mutation(
    *,
    payload: Mapping[str, object],
    trading_account_id: str,
    symbol: str,
    position: Mapping[str, object],
    action_kind: ExitActionKind,
    mutation: Mapping[str, object],
    tick: Decimal,
) -> None:
    """Check *fresh signed inventory* before exchange mutation, never widen SL.

    The caller must have authenticated the exact per-account connector and
    verified the Bybit /v5/position/list retCode/result inventory. This gate
    also refuses P90 real mutations when catastrophic SL or initial RAW TP
    has disappeared from the Exchange. No replacement default is permitted.
    """
    idx = int(str(position.get("positionIdx") or "0"))
    exact_key = p90_real_exchange_position_key(
        payload,
        trading_account_id=trading_account_id,
        symbol=symbol,
        position_idx=idx,
    )
    if exact_key is None:
        return
    if (
        str(payload.get("exchange_position_key") or "") != exact_key
        or str(position.get("symbol") or "") != symbol
        or str(position.get("side") or "") not in {"Buy", "Sell"}
        or str(payload.get("direction") or "")
        != ("LONG" if position.get("side") == "Buy" else "SHORT")
        or action_kind not in {
            ExitActionKind.SET_STOP, ExitActionKind.SET_TP, ExitActionKind.CLOSE,
        }
    ):
        raise P90RealExchangeSafetyBlock("P90_REAL_EXIT_ACCOUNT_POSITION_MISMATCH")
    if not tick.is_finite() or tick <= 0:
        raise P90RealExchangeSafetyBlock("P90_REAL_EXIT_TICK_INVALID")
    _positive(position.get("size"), "LIVE_SIZE")
    # CLOSE is reduceOnly, not a protection replacement. It must remain
    # possible during an emergency even if the stop/TP disappeared or mark
    # already crossed the stop; blocking CLOSE would trap unprotected exposure.
    if action_kind is ExitActionKind.CLOSE:
        return
    actual_fill = _positive(position.get("avgPrice"), "FILL")
    live_stop = _positive(position.get("stopLoss"), "LIVE_STOP")
    live_tp = _positive(position.get("takeProfit"), "LIVE_TP")
    mark = _positive(position.get("markPrice"), "SIGNED_MARK")
    long = str(position["side"]) == "Buy"
    # The original catastrophic stop can legitimately become a profitable
    # trailing stop. Never require it to stay adverse to the Entry price.
    if (
        not (live_stop < mark and live_tp > actual_fill if long
             else live_stop > mark and live_tp < actual_fill)
    ):
        raise P90RealExchangeSafetyBlock("P90_REAL_EXIT_EXISTING_PROTECTION_NOT_PROVEN")
    if action_kind is ExitActionKind.SET_STOP:
        requested = _positive(mutation.get("stop_price"), "NEW_STOP")
        if (
            requested % tick != 0
            or not (live_stop < requested < mark if long else mark < requested < live_stop)
        ):
            raise P90RealExchangeSafetyBlock("P90_REAL_EXIT_STOP_MUST_TIGHTEN")
    elif action_kind is ExitActionKind.SET_TP:
        requested = _positive(mutation.get("take_profit_price"), "NEW_TP")
        if (
            requested % tick != 0
            or not (requested > actual_fill and requested > mark if long
                    else requested < actual_fill and requested < mark)
        ):
            raise P90RealExchangeSafetyBlock("P90_REAL_EXIT_TP_NOT_RESTING_FAVORABLE")
