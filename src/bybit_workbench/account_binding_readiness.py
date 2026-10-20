"""Read-only, fail-closed StrategyActivation -> TradingAccount binding checks.

This module does not grant LIVE admission, create bindings, modify Strategy or
perform exchange calls. The caller supplies an authoritative snapshot of active
assignments for a single evaluation instant.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BindingReadiness:
    ready: bool
    reasons: tuple[str, ...]
    strategy_accounts: Mapping[str, tuple[str, ...]]


def check_binding_readiness(
    *,
    active_strategy_ids: Iterable[str],
    active_bindings: Iterable[Mapping[str, Any]],
) -> BindingReadiness:
    """Validate exclusive-account assignment and multi-account fan-out.

    Input rows must already be restricted by the caller to the same effective
    instant. An empty or inconsistent snapshot is BLOCKED, never accepted as
    proof of readiness. Real Entry still requires every existing live safety gate.
    """
    strategies = tuple(str(x).strip() for x in active_strategy_ids)
    reasons: set[str] = set()
    if not strategies or any(not s for s in strategies):
        reasons.add("ACTIVE_STRATEGY_IDENTITY_MISSING")
    if len(set(strategies)) != len(strategies):
        reasons.add("ACTIVE_STRATEGY_IDENTITY_DUPLICATE")
    expected = set(strategies)
    by_account: dict[str, set[str]] = defaultdict(set)
    by_strategy: dict[str, set[str]] = defaultdict(set)
    by_leg: dict[tuple[str, str], set[str]] = defaultdict(set)
    seen_binding_ids: set[str] = set()
    for binding in active_bindings:
        binding_id = str(binding.get("strategy_execution_binding_id") or "").strip()
        strategy_id = str(binding.get("strategy_activation_id") or "").strip()
        account_id = str(binding.get("trading_account_id") or "").strip()
        leg = str(binding.get("execution_leg") or "").strip()
        if not binding_id or not strategy_id or not account_id or not leg:
            reasons.add("BINDING_IDENTITY_MISSING")
            continue
        if binding_id in seen_binding_ids:
            reasons.add("BINDING_IDENTITY_DUPLICATE")
        seen_binding_ids.add(binding_id)
        if strategy_id not in expected:
            reasons.add("BINDING_UNEXPECTED_STRATEGY")
        if str(binding.get("allocation_mode") or "") != "DEDICATED_STRATEGY":
            reasons.add("BINDING_NOT_DEDICATED")
        if str(binding.get("binding_state") or "") != "ACTIVE":
            reasons.add("BINDING_NOT_ACTIVE")
        by_account[account_id].add(strategy_id)
        by_strategy[strategy_id].add(account_id)
        by_leg[(strategy_id, leg)].add(account_id)
    if any(len(owners) > 1 for owners in by_account.values()):
        reasons.add("TRADING_ACCOUNT_SHARED_BY_STRATEGIES")
    if any(len(accounts) > 1 for accounts in by_leg.values()):
        reasons.add("EXECUTION_LEG_AMBIGUOUS")
    if any(not by_strategy.get(strategy_id) for strategy_id in expected):
        reasons.add("STRATEGY_BINDING_MISSING")
    return BindingReadiness(
        ready=not reasons,
        reasons=tuple(sorted(reasons)),
        strategy_accounts={
            strategy_id: tuple(sorted(by_strategy.get(strategy_id, ())))
            for strategy_id in sorted(expected)
        },
    )
