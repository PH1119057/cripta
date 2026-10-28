"""Immutable owner-approved REAL policy authoring for two independent P90L90 profiles.

This module declares the exact finance/execution contract, NEVER submits an
order, does not activate a Strategy, and never proves private account safety.
The native P90 real consumer/admission/Exit and full release gates must be
separately verified; authoring a valid card is not an Exchange permit.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .contracts import StrategyCard
from .dashboard_control import card_from_editable
from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_strategy_embargo import StrategyHardStopEmbargoPolicy
from .p90l90_strategy_v2 import v2_authoring_payload

VERSION = "2.0-p90l90-real-main-sub-20261028"
OWNER_SOURCE = "owner-2026-10-28-p90l90-10usdt-market-isolated1x-issue243"
REQUESTED_USDT = Decimal("10")
MAX_ACCOUNT_COMMITTED: dict[str, Decimal] = {
    MAIN_ID: Decimal("20"),
    SUB_ID: Decimal("30"),
}
ALLOWED_SYMBOLS = {
    MAIN_ID: frozenset(("LTCUSDT", "INJUSDT")),
    SUB_ID: frozenset(("DOTUSDT", "ARBUSDT", "APTUSDT")),
}


def _positive_money(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError(f"P90_REAL_{name}_NOT_DECIMAL") from None
    if not result.is_finite() or result < 0:
        raise ValueError(f"P90_REAL_{name}_INVALID")
    return result


def real_v3_authoring_payload(
    *, strategy_id: str, policy: StrategyHardStopEmbargoPolicy,
) -> dict[str, object]:
    """New StrategyVersion; old no-order v2 remains immutable."""
    if strategy_id not in MAX_ACCOUNT_COMMITTED:
        raise ValueError("P90_REAL_STRATEGY_ID_NOT_APPROVED")
    raw = v2_authoring_payload(strategy_id=strategy_id, policy=policy)
    raw["strategy_version"] = VERSION
    raw["description"] = (
        "Owner-approved P90L90 MAIN/SUB REAL execution contract: "
        "10 USDT/position, isolated 1x, MARKET, exchange-resident SL 10%, "
        "independent Strategy SECOND48. Real adapter/account/WS+Exit/Exchange "
        "proof and separately gated owner arm REQUIRED before activation."
    )

    entry = raw["entry_policy"]
    if not isinstance(entry, dict):
        raise ValueError("P90_REAL_ENTRY_POLICY_INVALID")
    execution = entry["execution_policy"]
    if not isinstance(execution, dict):
        raise ValueError("P90_REAL_EXECUTION_POLICY_INVALID")
    execution.update({
        "order_type": "MARKET",
        "reference_value_path": "fact.p90l90.candidate_price",
        # Conservative operational freshness fences. An old signal is discarded,
        # never reinterpreted as a new trade. Not a strategy Entry filter.
        "max_request_age_seconds": 5,
    })
    capital = raw["capital_policy"]
    if not isinstance(capital, dict):
        raise ValueError("P90_REAL_CAPITAL_POLICY_INVALID")
    capital.clear()
    capital.update({
        "require_capacity": True,
        "capacity_max_age_seconds": 15,
        "capacity_min_quality": "HIGH",
        "amount_currency": "USDT",
        "requested_amount": str(REQUESTED_USDT),
        "leverage": 1,
        "margin_mode": "ISOLATED",
        "max_simultaneous_committed_usdt": str(MAX_ACCOUNT_COMMITTED[strategy_id]),
        "count_pending_reservations": True,
        "max_open_positions_per_symbol": 1,
        "exchange_min_notional_if_exceeds_requested": "REJECT",
    })
    protection = raw["protection_policy"]
    if not isinstance(protection, dict):
        raise ValueError("P90_REAL_PROTECTION_POLICY_INVALID")
    initial = protection["initial_protection"]
    if not isinstance(initial, dict):
        raise ValueError("P90_REAL_INITIAL_PROTECTION_INVALID")
    initial.update({
        "role": "CATASTROPHIC_GUARD",
        "stop_loss_enabled": True,
        "stop_loss_pct": "10",
        "stop_loss_price_basis": "ACTUAL_EXECUTED_AVERAGE_ENTRY",
        "exchange_resident_required": True,
        # Owner correction: install an immediate exchange-resident TP
        # at the opposite causal RAW L5-3 boundary after MARKET fill.
        # +0.5% is the trailing activation, not the opening TP gate.
        "take_profit_enabled": True,
        "take_profit_reference_path": "fact.p90l90.opposite_raw_l53_target",
        "trigger_by": "LastPrice",
        "tpsl_mode": "Full",
    })
    # Entire exact P90 RAW Entry, dynamic trailing/TP and SECOND48 inherited
    # from the owner-approved P90 v2 (not from any retired policy).
    return raw


def real_v3_cards(
    *, approved_at: datetime,
    policy_by_strategy_id: Mapping[str, StrategyHardStopEmbargoPolicy],
    approved_source: str = OWNER_SOURCE,
) -> tuple[StrategyCard, StrategyCard]:
    if approved_at.tzinfo is None:
        raise ValueError("P90_REAL_APPROVAL_NEEDS_TIMEZONE")
    if set(policy_by_strategy_id) != {MAIN_ID, SUB_ID}:
        raise ValueError("P90_REAL_BOTH_INDEPENDENT_POLICIES_REQUIRED")
    if not approved_source.strip():
        raise ValueError("P90_REAL_APPROVAL_SOURCE_REQUIRED")
    at = approved_at.astimezone(UTC)
    cards = tuple(
        card_from_editable(
            real_v3_authoring_payload(
                strategy_id=strategy_id,
                policy=policy_by_strategy_id[strategy_id],
            ),
            approved_at=at,
            approved_source=approved_source,
        )
        for strategy_id in (MAIN_ID, SUB_ID)
    )
    if len({c.strategy_config_fingerprint for c in cards}) != 2:
        raise ValueError("P90_REAL_FINGERPRINT_COLLISION")
    return cards  # type: ignore[return-value]


def check_source_scoped_capital(
    *, strategy_id: str, symbol: str, requested_usdt: object,
    committed_usdt: object, occupied_symbols: frozenset[str],
) -> Decimal:
    """Pure fail-closed P90 account-budget preflight, NOT trading admission.

    committed_usdt MUST come from an independently verified, current, atomic
    account/Strategy snapshot including *pending and open* reservations,
    not just active positions. Actual admission requires a DB-backed same-
    transaction cap/slot check and private Exchange reconciliation.
    """
    if strategy_id not in MAX_ACCOUNT_COMMITTED:
        raise ValueError("P90_REAL_STRATEGY_ID_NOT_APPROVED")
    if symbol not in ALLOWED_SYMBOLS[strategy_id]:
        raise ValueError("P90_REAL_SYMBOL_OUTSIDE_APPROVED_UNIVERSE")
    if symbol in occupied_symbols:
        raise ValueError("P90_REAL_ONE_POSITION_PER_SYMBOL")
    if not occupied_symbols <= ALLOWED_SYMBOLS[strategy_id]:
        raise ValueError("P90_REAL_ACCOUNT_SCOPE_MISMATCH")
    request = _positive_money(requested_usdt, "REQUEST")
    commitment = _positive_money(committed_usdt, "COMMITMENT")
    if request != REQUESTED_USDT:
        raise ValueError("P90_REAL_REQUEST_MUST_BE_EXACTLY_10_USDT")
    cap = MAX_ACCOUNT_COMMITTED[strategy_id]
    if commitment + request > cap:
        raise ValueError("P90_REAL_STRATEGY_CAP_EXCEEDED")
    return cap - commitment - request


def validate_real_v3_exchange_entry(
    *,
    payload: Mapping[str, object],
    trading_account_id: str,
    signed_account_info: Mapping[str, object],
) -> bool:
    """Fail-closed P90 REAL pre-mutation gate; never changes account margin mode.

    Account mode is ACCOUNT-scoped on Bybit UTA2. A leverage=1 request does
    NOT prove isolated margin. Only a fresh signed /v5/account/info response
    proving ISOLATED_MARGIN is acceptable for this owner-approved Strategy.
    For unrelated Strategies this function returns False without intervention.
    """
    strategy_id = str(payload.get("strategy_id") or "")
    version = str(payload.get("strategy_version") or "")
    if strategy_id not in MAX_ACCOUNT_COMMITTED and version != VERSION:
        return False
    if strategy_id not in MAX_ACCOUNT_COMMITTED or version != VERSION:
        raise ValueError("P90_REAL_WRONG_STRATEGY_VERSION")
    if str(payload.get("trading_account_id") or "") != trading_account_id or not trading_account_id:
        raise ValueError("P90_REAL_TRADING_ACCOUNT_IDENTITY_UNPROVEN")
    if str(payload.get("source") or "") != "universal_entry":
        raise ValueError("P90_REAL_ORDER_SOURCE_UNVERIFIED")
    symbol = str(payload.get("symbol") or "")
    # The transport command symbol is separate; the caller must additionally
    # compare it with the outer runtime.trade_commands.symbol.
    if symbol and symbol not in ALLOWED_SYMBOLS[strategy_id]:
        raise ValueError("P90_REAL_SYMBOL_OUTSIDE_APPROVED_UNIVERSE")
    for name in (
        "capital_reservation_id",
        "exchange_position_slot_claim_id",
        "strategy_execution_binding_id",
        "entry_plan_fingerprint",
        "exit_plan_fingerprint",
    ):
        if not str(payload.get(name) or "").strip():
            raise ValueError("P90_REAL_ENTRY_LINEAGE_MISSING")
    try:
        amount = Decimal(str(payload.get("stake_usdt")))
        price = Decimal(str(payload.get("price")))
        offset = Decimal(str(payload.get("entry_offset_pct")))
        leverage = int(str(payload.get("leverage")))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("P90_REAL_ENTRY_NUMERIC_INVALID") from None
    if (
        not amount.is_finite() or amount != REQUESTED_USDT
        or not price.is_finite() or price <= 0
        or not offset.is_finite() or offset != 0
        or leverage != 1
        or payload.get("margin_mode") != "ISOLATED"
    ):
        raise ValueError("P90_REAL_MARKET_ISOLATED_1X_CONTRACT_MISMATCH")
    side = str(payload.get("side") or "")
    if side not in {"Buy", "Sell"}:
        raise ValueError("P90_REAL_DIRECTION_INVALID")
    initial = payload.get("initial_protection")
    if not isinstance(initial, Mapping):
        raise ValueError("P90_REAL_INITIAL_PROTECTION_MISSING")
    try:
        stop = Decimal(str(initial.get("stop_loss_pct")))
        target = Decimal(str(initial.get("take_profit_price")))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("P90_REAL_INITIAL_PROTECTION_NUMERIC_INVALID") from None
    if (
        initial.get("stop_loss_enabled") is not True
        or stop != Decimal("10")
        or initial.get("take_profit_enabled") is not True
        or initial.get("take_profit_reference_path")
        != "fact.p90l90.opposite_raw_l53_target"
        or initial.get("take_profit_pct") is not None
        or not target.is_finite() or target <= 0
        or not (target > price if side == "Buy" else target < price)
        or initial.get("trigger_by") != "LastPrice"
        or initial.get("tpsl_mode") != "Full"
    ):
        raise ValueError("P90_REAL_INITIAL_SL_TP_CONTRACT_MISMATCH")
    if signed_account_info.get("retCode") != 0:
        raise ValueError("P90_REAL_SIGNED_ACCOUNT_READ_NOT_OK")
    data = signed_account_info.get("result")
    if not isinstance(data, Mapping) or data.get("marginMode") != "ISOLATED_MARGIN":
        raise ValueError("P90_REAL_ACCOUNT_NOT_PROVEN_ISOLATED")
    # UTA 1.0/2.0 return 3/4/5/6 respectively, classic is disallowed.
    if str(data.get("unifiedMarginStatus") or "") not in {"3", "4", "5", "6"}:
        raise ValueError("P90_REAL_UNIFIED_ACCOUNT_MODE_UNKNOWN")
    return True
