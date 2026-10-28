"""No-silent-fallback split between ordinary Observer and P90 PAPER plans.

The existing ordinary Strategy route is never turned into a P90 proxy. Until
the reviewed durable owner/consumer connection is end-to-end verified, active
P90 strategies must be refused BEFORE the shared observer registers plans
or writes any PAPER/evaluation row. This is an intentional hard stop, not a
completed P90 StrategyActivation route.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_P90_OPERATOR = "P90L90_RAW_H9_UNION_CROSSING"
_P90_V2 = "1.1-p90l90-strategy-second48-20261028"
_MAIN = ("INJUSDT", "LTCUSDT")
_SUB = ("APTUSDT", "ARBUSDT", "DOTUSDT")
_SCOPE: dict[str, tuple[str, ...]] = {
    "p90l90_legacy_main_ltc_inj": _MAIN,
    "p90l90_legacy_sub_dot_arb_apt": _SUB,
}


@dataclass(frozen=True, slots=True)
class ObserverStrategySplit:
    ordinary: tuple[object, ...]
    p90_paper: tuple[object, ...]


def partition_observer_strategies(bundles: tuple[object, ...]) -> ObserverStrategySplit:
    ordinary: list[object] = []
    p90: list[object] = []
    seen: set[str] = set()
    for bundle in bundles:
        item: Any = bundle
        activation = item.activation
        plan = item.entry_plan
        card = item.card
        if activation.activation_id in seen:
            raise RuntimeError("OBSERVER_DUPLICATE_STRATEGY_ACTIVATION")
        seen.add(activation.activation_id)
        op = plan.watch_policy.to_dict().get("geometry", {}).get("operator")
        is_p90 = op == _P90_OPERATOR
        has_p90_identity = card.strategy_id in _SCOPE
        if is_p90 != has_p90_identity:
            raise RuntimeError("OBSERVER_P90_STRATEGY_PRODUCER_OWNER_MISMATCH")
        if is_p90:
            expected = _SCOPE[card.strategy_id]
            if (
                card.strategy_version != _P90_V2
                or tuple(sorted(card.symbols)) != expected
                or tuple(sorted(plan.symbols)) != expected
                or plan.strategy_activation_id != activation.activation_id
                or activation.strategy_id != card.strategy_id
                or activation.strategy_version != card.strategy_version
            ):
                raise RuntimeError("OBSERVER_P90_STRATEGY_EXACT_IDENTITY_MISMATCH")
            p90.append(bundle)
        else:
            ordinary.append(bundle)
    return ObserverStrategySplit(tuple(ordinary), tuple(p90))


def require_p90_atomic_observer_connection(split: ObserverStrategySplit) -> None:
    """Do not silently use the old shared Watch/Exit route for any P90 plan."""
    if split.p90_paper:
        raise RuntimeError(
            "P90L90_ATOMIC_OBSERVER_CONNECTION_NOT_PROVEN: "
            "dedicated P90 Entry/Exit owner, original-source restart "
            "and full source parity required; old shared route forbidden"
        )
