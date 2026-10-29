"""Exact P90L90 REAL v3 ExitPlan projection into Universal Exit actions.

Pure policy math for owner-approved L5-3 / TRAIL035, never a trade sender.
It consumes verified real StrategyPosition and a source/account-fenced
ExitObservation. The causal observer and authenticated Exchange reconciler
must provide these facts; this module does NOT attest to their provenance.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan, TradeDirection
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)

from .contracts import ExitActionKind, ExitObservation


class P90RealExitUnverified(ValueError):
    """Exact real position, source or protection evidence is insufficient."""


def _positive(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise P90RealExitUnverified(f"P90_REAL_EXIT_{label}_INVALID") from None
    if not result.is_finite() or result <= 0:
        raise P90RealExitUnverified(f"P90_REAL_EXIT_{label}_INVALID")
    return result


def _aligned(value: Decimal, step: Decimal, *, up: bool) -> Decimal:
    multiplier = (value / step).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR
    )
    return multiplier * step


def project_p90l90_real_exit(
    position: StrategyPosition,
    plan: ExitPlan,
    observation: ExitObservation,
) -> tuple[ExitActionKind, dict[str, object], str] | None:
    """Select only an existing approved REAL dynamic TP / trailing action.

    The external REAL observer owns proof of actual Exchange stop/TP/trailing,
    durable source cursor, original 5m geometry and non-duplicated source.
    An unverified observation is rejected, never modeled as a paper fill.
    """
    if (
        position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or plan.strategy_id != position.strategy_id
        or plan.strategy_version != VERSION
        or plan.strategy_config_fingerprint != position.strategy_config_fingerprint
        or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or observation.symbol != position.symbol
        or observation.strategy_position_id != position.strategy_position_id
        or position.identity_status != "PROVEN"
        or not position.trading_account_id
        or not position.strategy_execution_binding_id
        or position.position_idx != 0
        or position.initial_protection_confirmed_at is None
        or observation.event_at <= position.fill_at
        or observation.event_kind not in {
            "P90L90_REAL_NATIVE_TRADE", "P90L90_REAL_CLOSED_5M",
        }
    ):
        raise P90RealExitUnverified("P90_REAL_EXIT_POSITION_OR_SOURCE_NOT_VERIFIED")
    policy = plan.exit_policy.to_dict()
    trailing = policy.get("trailing")
    local_zone = policy.get("local_zone_exit")
    if not isinstance(trailing, Mapping) or not isinstance(local_zone, Mapping):
        raise P90RealExitUnverified("P90_REAL_EXIT_PLAN_POLICY_MISSING")
    if (
        trailing.get("enabled") is not True
        or trailing.get("activation_profit_pct") != "0.5"
        or trailing.get("distance_pct") != "0.35"
        or trailing.get("reference") != "BEST_FAVORABLE_PRICE_SINCE_ENTRY"
        or trailing.get("update_effective_from") != "NEXT_ORDERED_TRADE"
        or trailing.get("never_loosen") is not True
        or local_zone.get("enabled") is not True
        or local_zone.get("operator") != "P90L90_DYNAMIC_OPPOSITE_RAW_L53"
        or local_zone.get("target") != "OPPOSITE_RAW_BOUNDARY"
        or local_zone.get("update_mode") != "EACH_CAUSAL_GEOMETRY_CHANGE"
        or local_zone.get("minimum_favorable_pct") != "0.5"
        or local_zone.get("target_fact_path")
        != "fact.p90l90.opposite_raw_l53_target"
        or local_zone.get("marketable_action") != "CLOSE_MARKET"
    ):
        raise P90RealExitUnverified("P90_REAL_EXIT_PLAN_NOT_EXACT")
    attrs = observation.attributes.to_dict()
    real = attrs.get("p90l90_real")
    if not isinstance(real, Mapping):
        raise P90RealExitUnverified("P90_REAL_EXIT_NATIVE_FACT_MISSING")
    if (
        real.get("trading_account_id") != position.trading_account_id
        or real.get("strategy_execution_binding_id")
        != position.strategy_execution_binding_id
        or real.get("exchange_position_key") != position.exchange_position_key
        or real.get("actual_fill_confirmed") is not True
        or real.get("protection_exchange_confirmed") is not True
        or real.get("durable_cursor_verified") is not True
        or real.get("pending_exit_mutation") is not False
    ):
        raise P90RealExitUnverified("P90_REAL_EXIT_ACCOUNT_OR_EXCHANGE_UNPROVEN")
    last = _positive(real.get("native_trade_price"), "NATIVE_PRICE")
    best = _positive(real.get("durable_best_favorable_price"), "MFE_PRICE")
    live_stop = _positive(real.get("exchange_stop_price"), "EXCHANGE_STOP")
    live_tp = _positive(real.get("exchange_tp_price"), "EXCHANGE_TP")
    tick = _positive(real.get("tick_size"), "TICK_SIZE")
    entry = position.actual_avg_fill
    if (
        not entry.is_finite() or entry <= 0
        or live_stop % tick != 0 or live_tp % tick != 0
        or (position.direction is TradeDirection.LONG and best < entry)
        or (position.direction is TradeDirection.SHORT and best > entry)
    ):
        raise P90RealExitUnverified("P90_REAL_EXIT_PRICE_STATE_UNVERIFIED")
    long = position.direction is TradeDirection.LONG
    favorable = ((best / entry - 1) if long else (1 - best / entry)) * 100
    # Bybit, not the Observer's native ticks or a closed 5m candle, must
    # maintain the subsequent monotone high/low-watermark trailing stop.
    # The only REAL trailing mutation is the one-time Exchange TS arm.
    expected_distance = _aligned(entry * Decimal("0.0035"), tick, up=False)
    if expected_distance <= 0:
        raise P90RealExitUnverified("P90_REAL_NATIVE_TRAILING_TICK_UNREPRESENTABLE")
    try:
        live_trailing = Decimal(str(real.get("exchange_trailing_distance")))
    except (InvalidOperation, ValueError, TypeError):
        raise P90RealExitUnverified("P90_REAL_EXCHANGE_TRAILING_READBACK_MISSING") from None
    if not live_trailing.is_finite() or live_trailing < 0 or live_trailing % tick != 0:
        raise P90RealExitUnverified("P90_REAL_EXCHANGE_TRAILING_READBACK_INVALID")
    if live_trailing not in (Decimal("0"), expected_distance):
        raise P90RealExitUnverified("P90_REAL_EXCHANGE_TRAILING_DISTANCE_DRIFT")
    activation = _aligned(
        entry * (Decimal("1.005") if long else Decimal("0.995")),
        tick, up=long,
    )
    if live_trailing == 0 and observation.event_kind == "P90L90_REAL_NATIVE_TRADE":
        # Arm once, before the trigger: Exchange activePrice will activate the
        # autonomous trailing on +0.5%. No repeated SET_STOP proxy is allowed.
        mark = _positive(real.get("exchange_mark_price"), "SIGNED_MARK")
        if favorable >= Decimal("0.5") or (mark >= activation if long else mark <= activation):
            # A past high/low watermark cannot be reconstructed into an
            # Exchange-native stop after price has already retraced.
            raise P90RealExitUnverified("P90_REAL_NATIVE_TRAILING_LATE_ARM_UNVERIFIED")
        return (
            ExitActionKind.SET_TRAILING,
            {
                "distance": str(expected_distance),
                "active_price": str(activation),
                "tpsl_mode": "Full",
            },
            "P90L90_REAL_BYBIT_NATIVE_TRAIL035_ARM",
        )
    if observation.event_kind == "P90L90_REAL_CLOSED_5M" and favorable >= Decimal("0.5"):
        # A previously missed Exchange-native trailing activation cannot be
        # repaired by a 5m TP update. Fail closed instead of silently
        # continuing an altered Strategy with TP but no autonomous TRAIL035.
        if live_trailing != expected_distance:
            raise P90RealExitUnverified(
                "P90_REAL_CLOSED_5M_BYBIT_TRAILING_NOT_ARMED"
            )
        target = _positive(real.get("opposite_raw_l53_target"), "RAW_TARGET")
        fresh = real.get("closed_geometry_proven")
        if fresh is not True:
            raise P90RealExitUnverified("P90_REAL_EXIT_5M_GEOMETRY_UNPROVEN")
        if not (target > entry if long else target < entry):
            raise P90RealExitUnverified("P90_REAL_EXIT_RAW_TARGET_WRONG_SIDE")
        minimum = (
            entry * (Decimal("1.005") if long else Decimal("0.995"))
        )
        if (target >= minimum if long else target <= minimum):
            chosen = _aligned(target, tick, up=long)
            if (last >= chosen if long else last <= chosen):
                return (
                    ExitActionKind.CLOSE,
                    {"quantity": "ALL", "order_type": "MARKET"},
                    "P90L90_REAL_NEW_MARKETABLE_RAW_TP",
                )
            if chosen != live_tp:
                return (
                    ExitActionKind.SET_TP,
                    {
                        "take_profit_price": str(chosen),
                        "trigger_by": "LastPrice",
                        "tpsl_mode": "Full",
                        "order_type": "MARKET",
                    },
                    "P90L90_REAL_DYNAMIC_RAW_L53_TP",
                )
    # All subsequent favorable price tracking belongs exclusively to Bybit.
    return None
