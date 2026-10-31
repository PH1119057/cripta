"""Owner-approved full-contour REAL diagnostic Strategy contract.

The diagnostic is deliberately NOT a P90 Strategy.  It can consume only an
explicit OWNER_FULL_CONTOUR_TRIGGER created by the authenticated owner control
plane.  Market observation never feeds this Strategy automatically.

Registration is inert with respect to Exchange mutation: execution permission
and release-bound LIVE-arm are temporary per diagnostic run.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .contracts import StrategyCard
from .dashboard_control import card_from_editable, strategy_authoring_template

VERSION = "1.0-owner-full-contour-20261101"
SOURCE = "owner-2026-11-01-full-contour-real-diagnostic"
RUNTIME_SOURCE = "owner_full_contour_real_diagnostic"
TRIGGER_EVENT_KIND = "OWNER_FULL_CONTOUR_TRIGGER"

MAIN_ID = "owner_full_contour_diag_main"
SUB_ID = "owner_full_contour_diag_sub"
MAIN_ACTIVATION_ID = "activation-owner-full-contour-main-20261101"
SUB_ACTIVATION_ID = "activation-owner-full-contour-sub-20261101"
MAIN_BINDING_ID = "binding-owner-full-contour-main-20261101"
SUB_BINDING_ID = "binding-owner-full-contour-sub-20261101"

MAIN_ACCOUNT_ID = "ta-2122c81ecff14f3b85b102db605212f7"
SUB_ACCOUNT_ID = "ta-853ac5fcd58185697396d33cd57459fa"
MAIN_CONNECTOR_ID = "ac-ed73de20410548d6841aeafa5f196f70"
SUB_CONNECTOR_ID = "ac-ec57819f875f1bfd929a382d6dc8a125"

MAIN_SYMBOLS = ("INJUSDT", "LTCUSDT")
SUB_SYMBOLS = ("APTUSDT", "ARBUSDT", "DOTUSDT")
SYMBOLS = frozenset(MAIN_SYMBOLS + SUB_SYMBOLS)

REQUESTED_USDT = Decimal("10")
LEVERAGE = 1
STOP_LOSS_PCT = Decimal("0.5")
TAKE_PROFIT_PCT = Decimal("0.5")


def strategy_id_for_symbol(symbol: str) -> str:
    selected = symbol.upper()
    if selected in MAIN_SYMBOLS:
        return MAIN_ID
    if selected in SUB_SYMBOLS:
        return SUB_ID
    raise ValueError("OWNER_FULL_CONTOUR_SYMBOL_NOT_APPROVED")


def strategy_scope(strategy_id: str) -> tuple[
    tuple[str, ...], str, str, str, str
]:
    if strategy_id == MAIN_ID:
        return (
            MAIN_SYMBOLS,
            MAIN_ACTIVATION_ID,
            MAIN_BINDING_ID,
            MAIN_ACCOUNT_ID,
            MAIN_CONNECTOR_ID,
        )
    if strategy_id == SUB_ID:
        return (
            SUB_SYMBOLS,
            SUB_ACTIVATION_ID,
            SUB_BINDING_ID,
            SUB_ACCOUNT_ID,
            SUB_CONNECTOR_ID,
        )
    raise ValueError("OWNER_FULL_CONTOUR_STRATEGY_NOT_APPROVED")


def _policy(strategy_id: str) -> dict[str, object]:
    symbols, _activation, _binding, _account, _connector = strategy_scope(strategy_id)
    raw = strategy_authoring_template()
    raw["strategy_id"] = strategy_id
    raw["strategy_version"] = VERSION
    raw["name"] = (
        "OWNER FULL CONTOUR DIAGNOSTIC · MAIN"
        if strategy_id == MAIN_ID
        else "OWNER FULL CONTOUR DIAGNOSTIC · SUB"
    )
    raw["description"] = (
        "Owner-triggered diagnostic only. Verifies the ordinary production "
        "Universal Entry -> real admission -> Execution -> StrategyPosition -> "
        "Universal Exit lifecycle. Excluded from P90 performance semantics."
    )
    raw["scope"] = {
        "kind": "OWNER_FULL_CONTOUR_DIAGNOSTIC",
        "symbols": list(symbols),
    }
    raw["symbols"] = list(symbols)
    raw["direction_policy"] = ["LONG", "SHORT"]
    raw["entry_policy"] = {
        "entry_plan_version": "owner-full-contour-trigger-v1",
        "predicate": {
            "op": "COMPARE",
            "params": {
                "path": "event_kind",
                "comparator": "EQ",
                "value": TRIGGER_EVENT_KIND,
            },
        },
        "entry_reference_policy": {"enabled": False},
        "local_entry_policy": {"enabled": False},
        # The market Observer must never own this trigger.
        "watch_policy": {"enabled": False},
        "execution_policy": {
            "order_type": "MARKET",
            "reference_value_path": "fact.price",
            "max_request_age_seconds": 15,
        },
        "context_feature_policy": [],
        "context_ranking_policy": {"enabled": False},
    }
    raw["exit_policy"] = {
        "exit_plan_version": "owner-full-contour-fixed-05-v1",
        "conflict_policy": {
            "mode": "PRIORITY",
            "priority_order": "HIGHER_WINS",
            "tie_break": "FAIL_CLOSED",
        },
        "rules": [
            {
                "rule_id": "OWNER_DIAGNOSTIC_TP_0_5",
                "priority": 200,
                "repeat_policy": "ONCE_PER_POSITION",
                "required_fact_paths": ["fact.directional_pnl_pct"],
                "predicate": {
                    "op": "COMPARE",
                    "params": {
                        "path": "fact.directional_pnl_pct",
                        "comparator": "GTE",
                        "value": str(TAKE_PROFIT_PCT),
                    },
                },
                "action": {
                    "kind": "CLOSE",
                    "mutation": {"quantity": "ALL", "order_type": "MARKET"},
                },
            },
            {
                "rule_id": "OWNER_DIAGNOSTIC_SL_0_5",
                "priority": 100,
                "repeat_policy": "ONCE_PER_POSITION",
                "required_fact_paths": ["fact.directional_pnl_pct"],
                "predicate": {
                    "op": "COMPARE",
                    "params": {
                        "path": "fact.directional_pnl_pct",
                        "comparator": "LTE",
                        "value": str(-STOP_LOSS_PCT),
                    },
                },
                "action": {
                    "kind": "CLOSE",
                    "mutation": {"quantity": "ALL", "order_type": "MARKET"},
                },
            },
        ],
        "hard_stop": {
            "enabled": True,
            "value": str(STOP_LOSS_PCT),
            "unit": "PERCENT_FROM_ACTUAL_FILL",
            "scope": "POSITION",
        },
        "take_profit": {
            "enabled": True,
            "value": str(TAKE_PROFIT_PCT),
            "unit": "PERCENT_FROM_ACTUAL_FILL",
            "scope": "POSITION",
        },
        "break_even": {"enabled": False},
        "trailing": {"enabled": False},
        "geometry_exit": {"enabled": False},
        "local_zone_exit": {"enabled": False},
        "time_exit": {"enabled": False},
        "execution_policy": {"max_request_age_seconds": 10},
        "context_feature_policy": [],
        "context_ranking_policy": {"enabled": False},
    }
    raw["capital_policy"] = {
        "require_capacity": True,
        "capacity_max_age_seconds": 15,
        "capacity_min_quality": "HIGH",
        "amount_currency": "USDT",
        "requested_amount": str(REQUESTED_USDT),
        "leverage": LEVERAGE,
        "margin_mode": "ISOLATED",
        "max_simultaneous_committed_usdt": str(REQUESTED_USDT),
        "count_pending_reservations": True,
        "max_open_positions_per_symbol": 1,
        "exchange_min_notional_if_exceeds_requested": "REJECT",
    }
    raw["protection_policy"] = {
        "initial_protection": {
            "role": "DIAGNOSTIC_HARD_STOP",
            "stop_loss_enabled": True,
            "stop_loss_pct": str(STOP_LOSS_PCT),
            "stop_loss_price_basis": "ACTUAL_EXECUTED_AVERAGE_ENTRY",
            "exchange_resident_required": True,
            # TP is deliberately owned by the generic Universal Exit rule above,
            # so a profitable run proves ExitDecision -> ExitExecutionRequest.
            "take_profit_enabled": False,
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        }
    }
    raw["lifecycle_policy"] = {
        "post_signal_outcome_policy": {"enabled": False},
        "hedge_policy": {"enabled": False},
        "reverse_on_opposite_signal": {"enabled": False},
        "position_mode": "ONE_WAY",
        "max_open_positions_per_symbol": 1,
        "emergency_policy": {
            "enabled": True,
            "terminal_loss_containment": "INITIAL_PROTECTION",
            "initial_stop_loss_pct": str(STOP_LOSS_PCT),
            "operator_kill": "MAINNET_GATE_OFF",
        },
    }
    return raw


def cards(*, approved_at: datetime) -> tuple[StrategyCard, StrategyCard]:
    if approved_at.tzinfo is None:
        raise ValueError("OWNER_FULL_CONTOUR_APPROVAL_TIME_REQUIRED")
    at = approved_at.astimezone(UTC)
    return tuple(
        card_from_editable(
            _policy(strategy_id),
            approved_at=at,
            approved_source=SOURCE,
        )
        for strategy_id in (MAIN_ID, SUB_ID)
    )  # type: ignore[return-value]


def is_diagnostic_strategy(strategy_id: str, strategy_version: str) -> bool:
    return (
        strategy_version == VERSION
        and strategy_id in {MAIN_ID, SUB_ID}
    )
