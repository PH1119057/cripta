from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class ContextFeatureSpec:
    feature_id: str
    scope: str
    base_path: tuple[str, ...]
    scalar_value_key: str | None = None


_GLOBAL_IDS = (
    "btc.state",
    "eth.state",
    "event.context",
    "market.breadth",
    "money.pressure",
    "liquidity.trend",
    "event.importance",
    "market.direction",
    "liquidation.phase",
    "liquidation.breadth",
    "money.spot_pressure",
    "liquidation.intensity",
    "positioning.oi_regime",
    "market.synchronization",
    "liquidation.acceleration",
    "market.timeframe_alignment",
    "money.derivatives_pressure",
    "positioning.price_oi_state",
    "money.spot_derivatives_alignment",
)

_SPECS: dict[str, ContextFeatureSpec] = {
    feature_id: ContextFeatureSpec(
        feature_id,
        "GLOBAL",
        ("objective_facts", "objective_features", feature_id),
        "value",
    )
    for feature_id in _GLOBAL_IDS
}
_SPECS.update(
    {
        "money.spot": ContextFeatureSpec(
            "money.spot", "COIN", ("objective_facts", "money", "spot")
        ),
        "money.derivatives": ContextFeatureSpec(
            "money.derivatives", "COIN", ("objective_facts", "money", "derivatives")
        ),
        "money.flow_dynamics": ContextFeatureSpec(
            "money.flow_dynamics", "COIN", ("objective_facts", "money")
        ),
        "money.large_trades": ContextFeatureSpec(
            "money.large_trades", "COIN", ("objective_facts", "money")
        ),
        "price.returns": ContextFeatureSpec(
            "price.returns", "COIN", ("objective_facts", "price", "returns")
        ),
        "liquidity.spot": ContextFeatureSpec(
            "liquidity.spot", "COIN", ("objective_facts", "liquidity", "spot")
        ),
        "liquidity.derivatives": ContextFeatureSpec(
            "liquidity.derivatives",
            "COIN",
            ("objective_facts", "liquidity", "derivatives"),
        ),
        "positioning.open_interest": ContextFeatureSpec(
            "positioning.open_interest", "COIN", ("objective_facts", "positioning")
        ),
        "positioning.funding": ContextFeatureSpec(
            "positioning.funding", "COIN", ("objective_facts", "positioning")
        ),
        "positioning.long_short": ContextFeatureSpec(
            "positioning.long_short", "COIN", ("objective_facts", "positioning")
        ),
        "positioning.mark_index_premium": ContextFeatureSpec(
            "positioning.mark_index_premium", "COIN", ("objective_facts", "positioning")
        ),
        "liquidations": ContextFeatureSpec(
            "liquidations", "COIN", ("objective_facts", "liquidations")
        ),
        "relative_strength": ContextFeatureSpec(
            "relative_strength", "COIN", ("objective_facts", "relative_strength")
        ),
        "event_context": ContextFeatureSpec(
            "event_context", "COIN", ("objective_facts", "event_context")
        ),
        "data_quality": ContextFeatureSpec(
            "data_quality", "COIN", ("objective_facts", "data_quality")
        ),
    }
)


def context_feature_specs() -> tuple[ContextFeatureSpec, ...]:
    return tuple(_SPECS[key] for key in sorted(_SPECS))


def require_context_feature(feature_id: str) -> ContextFeatureSpec:
    try:
        return _SPECS[feature_id]
    except KeyError:
        raise KeyError(f"unknown Strategy context feature: {feature_id}") from None


def _descend(value: object, parts: Sequence[str]) -> object | None:
    current = value
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def extract_context_feature(
    payload: Mapping[str, object],
    *,
    feature_id: str,
    value_path: str = "",
) -> tuple[object | None, str]:
    spec = require_context_feature(feature_id)
    base = _descend(payload, spec.base_path)
    if base is None:
        return None, "MISSING"
    status = "VALID"
    if isinstance(base, Mapping):
        raw_status = base.get("status")
        if raw_status not in (None, ""):
            status = str(raw_status).upper()
    if spec.scalar_value_key is not None:
        if value_path:
            raise ValueError(f"global scalar feature {feature_id} cannot use value_path")
        if not isinstance(base, Mapping) or spec.scalar_value_key not in base:
            return None, "MISSING"
        return base[spec.scalar_value_key], status
    if not value_path:
        return base, status
    current: object = base
    for part in value_path.split("."):
        if isinstance(current, Mapping) and current.get("status") not in (None, ""):
            status = str(current.get("status")).upper()
        if not isinstance(current, Mapping) or part not in current:
            return None, "MISSING"
        current = current[part]
    if isinstance(current, Mapping) and current.get("status") not in (None, ""):
        status = str(current.get("status")).upper()
    return current, status


def compare_context_value(left: object, operator: str, right: object) -> bool:
    op = operator.upper()
    if op == "EQ":
        return left == right or str(left) == str(right)
    if op == "NE":
        return not compare_context_value(left, "EQ", right)
    if op in {"GT", "GTE", "LT", "LTE"}:
        try:
            a = Decimal(str(left))
            b = Decimal(str(right))
        except (InvalidOperation, ValueError, TypeError):
            return False
        if not a.is_finite() or not b.is_finite():
            return False
        if op == "GT":
            return a > b
        if op == "GTE":
            return a >= b
        if op == "LT":
            return a < b
        return a <= b
    if op in {"IN", "NOT_IN"}:
        if not isinstance(right, Sequence) or isinstance(right, (str, bytes)):
            return False
        present = left in right
        return present if op == "IN" else not present
    raise ValueError(f"unsupported context comparator: {operator}")
