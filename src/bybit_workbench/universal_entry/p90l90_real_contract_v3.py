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
