"""No-silent-fallback split between ordinary Observer and P90 PAPER plans.

The existing ordinary Strategy route is never turned into a P90 proxy. Until
the reviewed durable owner/consumer connection is end-to-end verified, active
P90 strategies must be refused BEFORE the shared observer registers plans
or writes any PAPER/evaluation row. This is an intentional hard stop, not a
completed P90 StrategyActivation route.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .readiness import assess_strategy_runtime_readiness

_P90_OPERATOR = "P90L90_RAW_H9_UNION_CROSSING"
_P90_V2 = "1.1-p90l90-strategy-second48-20261028"
_MAIN = ("INJUSDT", "LTCUSDT")
_SUB = ("APTUSDT", "ARBUSDT", "DOTUSDT")
_SCOPE: dict[str, tuple[str, ...]] = {
    "p90l90_legacy_main_ltc_inj": _MAIN,
    "p90l90_legacy_sub_dot_arb_apt": _SUB,
}


_SOURCE_COMMIT = re.compile(r"^[a-f0-9]{40}$")


@dataclass(frozen=True, slots=True)
class P90NativeBarRuntimeProof:
    """An epoch-scoped proof, never a StrategyCard/manual activation flag.

    The Observer can issue this only after the exact native candle ledger
    and transport Entry checkpoints have been restored for EVERY P90
    instrument and the real Bybit WS subscription/mirror are established.
    On reconnect this proof expires with the whole Observer epoch.
    """

    source_commit: str
    restored_symbols: frozenset[str]
    subscribed_symbols: frozenset[str]
    subscribed_intervals: frozenset[str]
    mirror_active: bool
    subscribed_local_at: datetime
    subscribed_server_at: datetime
    epoch_started_at: datetime

    def verified_for(self, expected_symbols: frozenset[str]) -> bool:
        dates = (
            self.subscribed_local_at,
            self.subscribed_server_at,
            self.epoch_started_at,
        )
        if any(x.tzinfo is None or x.utcoffset() != timedelta() for x in dates):
            return False
        return bool(
            _SOURCE_COMMIT.fullmatch(self.source_commit)
            and self.restored_symbols == expected_symbols
            and expected_symbols.issubset(self.subscribed_symbols)
            and {"5", "15"}.issubset(self.subscribed_intervals)
            and self.mirror_active
            and self.subscribed_local_at >= self.epoch_started_at
            and abs(
                (self.subscribed_local_at - self.subscribed_server_at).total_seconds()
            ) <= 15
            and self.subscribed_local_at <= datetime.now(UTC)
        )


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


def require_p90_policy_before_registry(split: ObserverStrategySplit) -> None:
    """Reject fake P90 v2/REAL policy BEFORE any registry or DB audit write."""
    for raw in split.p90_paper:
        bundle: Any = raw
        try:
            status = assess_strategy_runtime_readiness(
                bundle.card, observer_ready=True,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "P90L90_ATOMIC_OBSERVER_CONNECTION_NOT_PROVEN: "
                "invalid exact StrategyCard readiness evidence"
            ) from exc
        codes = {reason.code for reason in status.reasons}
        if (
            not status.policy_ready
            or status.execution_ready
            or "P90L90_EXCHANGE_ORDER_NOT_APPROVED" not in codes
            or "P90L90_NATIVE_BAR_BOUNDARY_ORDER_NOT_PROVEN" not in codes
        ):
            raise RuntimeError(
                "P90L90_ATOMIC_OBSERVER_CONNECTION_NOT_PROVEN: "
                "unverified immutable PAPER-only Strategy policy"
            )


def require_p90_atomic_observer_connection(
    split: ObserverStrategySplit,
    *,
    live_proof: P90NativeBarRuntimeProof | None = None,
) -> None:
    """Allow PAPER only with reconstructed PG state and LIVE source evidence.

    No generic observer_ready or owner wish may substitute the independently
    verified Bybit WS subscription + active publicTrade mirror, and the exact
    source-bound Entry/Exit restart state for ALL enabled P90 instruments.
    REAL execution is *always* disqualified by immutable NOT_APPROVED order.
    """
    require_p90_policy_before_registry(split)
    if not split.p90_paper:
        return
    symbols = frozenset(
        symbol for raw in split.p90_paper
        for symbol in getattr(raw.entry_plan, "symbols", ())
    )
    proven = bool(live_proof and live_proof.verified_for(symbols))
    for raw in split.p90_paper:
        bundle: Any = raw
        status = assess_strategy_runtime_readiness(
            bundle.card, observer_ready=True,
            p90_native_bar_runtime_proven=proven,
        )
        if not (
            proven and status.policy_ready and status.paper_ready
            and status.active_ready and not status.execution_ready
        ):
            raise RuntimeError(
                "P90L90_ATOMIC_OBSERVER_CONNECTION_NOT_PROVEN: "
                "verified native/bar/PG restart evidence required for PAPER"
            )
