from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STRESS_INPUT_VERSION = "coin-stress-inputs-v1"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    n = _number(numerator)
    d = _number(denominator)
    if n is None or d is None or d == 0.0:
        return None
    return n / d


def _directional_price_response(return_pct: Any, *, forced_direction: str) -> float | None:
    value = _number(return_pct)
    if value is None:
        return None
    if forced_direction == "DOWN":
        return -value
    if forced_direction == "UP":
        return value
    raise ValueError(f"unsupported forced_direction={forced_direction}")


def _impact_proxy(
    direction_return_pct: float | None, liquidation_to_oi: float | None
) -> float | None:
    if direction_return_pct is None or liquidation_to_oi is None or liquidation_to_oi == 0.0:
        return None
    return direction_return_pct / liquidation_to_oi


def build_coin_stress_inputs(
    *,
    returns_by_horizon: Mapping[str, Any],
    liquidations: Mapping[str, Any],
    positioning: Mapping[str, Any],
    derivatives_flow: Mapping[str, Any],
) -> dict[str, Any]:
    """Build scale-aware objective inputs for one symbol.

    This function intentionally does not classify the symbol, approve Entry/Exit,
    or choose percentile thresholds. It only combines causal facts for the same
    symbol into dimensionless/raw inputs suitable for replay and later research.
    """

    oi_value = _number(positioning.get("open_interest_value"))
    horizons: dict[str, Any] = {}

    for horizon in ("1m", "5m", "15m"):
        liq = liquidations.get(horizon)
        if not isinstance(liq, Mapping):
            liq = {}
        flow = derivatives_flow.get(horizon)
        if not isinstance(flow, Mapping):
            flow = {}

        liq_valid = liq.get("status") == "VALID"
        flow_valid = flow.get("status") == "VALID"
        return_pct = _number(returns_by_horizon.get(horizon))

        long_notional = _number(liq.get("long_notional_usd")) if liq_valid else None
        short_notional = _number(liq.get("short_notional_usd")) if liq_valid else None
        total_notional = _number(liq.get("total_notional_usd")) if liq_valid else None
        turnover = _number(flow.get("turnover_usd")) if flow_valid else None

        long_to_oi = _ratio(long_notional, oi_value)
        short_to_oi = _ratio(short_notional, oi_value)
        total_to_oi = _ratio(total_notional, oi_value)
        long_to_turnover = _ratio(long_notional, turnover)
        short_to_turnover = _ratio(short_notional, turnover)
        total_to_turnover = _ratio(total_notional, turnover)

        long_response = _directional_price_response(
            return_pct, forced_direction="DOWN"
        )
        short_response = _directional_price_response(
            return_pct, forced_direction="UP"
        )

        horizons[horizon] = {
            "status": "VALID" if liq_valid else liq.get("status", "NO_DATA"),
            "price_return_pct": return_pct,
            "abs_price_return_pct": abs(return_pct) if return_pct is not None else None,
            "derivatives_turnover_usd": turnover,
            "open_interest_value_usd": oi_value,
            "long_liquidation_notional_usd": long_notional,
            "short_liquidation_notional_usd": short_notional,
            "total_liquidation_notional_usd": total_notional,
            "long_liquidation_to_oi": long_to_oi,
            "short_liquidation_to_oi": short_to_oi,
            "total_liquidation_to_oi": total_to_oi,
            "long_liquidation_to_derivatives_turnover": long_to_turnover,
            "short_liquidation_to_derivatives_turnover": short_to_turnover,
            "total_liquidation_to_derivatives_turnover": total_to_turnover,
            "long_liquidation_direction_return_pct": long_response,
            "short_liquidation_direction_return_pct": short_response,
            "long_price_impact_per_liq_oi": _impact_proxy(long_response, long_to_oi),
            "short_price_impact_per_liq_oi": _impact_proxy(short_response, short_to_oi),
        }

    oi_clearance: dict[str, float | None] = {}
    for horizon in ("5m", "15m", "30m", "60m"):
        change = _number(positioning.get(f"open_interest_change_{horizon}_pct"))
        oi_clearance[horizon] = max(0.0, -change) if change is not None else None

    side_semantics = liquidations.get("side_semantics")
    if not isinstance(side_semantics, Mapping):
        side_semantics = {}

    dynamics = liquidations.get("side_dynamics")
    if not isinstance(dynamics, Mapping):
        dynamics = {}

    return {
        "version": STRESS_INPUT_VERSION,
        "trading_effect": "NONE",
        "side_semantics": dict(side_semantics),
        "horizons": horizons,
        "oi_clearance_pct": oi_clearance,
        "liquidation_dynamics": dict(dynamics),
        "self_baseline": {
            "status": "RESEARCH_NOT_APPROVED",
            "normalized_values": None,
            "percentiles": None,
            "baseline_spec_id": None,
        },
        "multi_venue_confirmation": {
            "status": "NO_DATA",
            "observed_venues": 1,
            "confirming_venues": None,
            "reason": "CURRENT_OBSERVATION_SOURCE_IS_SINGLE_VENUE",
        },
    }
