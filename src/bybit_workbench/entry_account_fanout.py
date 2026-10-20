"""Deterministic, read-only Entry fan-out selection for one StrategyActivation.

Selecting legs is not admitting Entry, reserving funds or sending orders.
Each selected leg must independently pass Strategy, account, slot, risk,
exchange and release-bound LIVE checks before an execution request exists.
"""
from __future__ import annotations

from dataclasses import dataclass

from bybit_workbench.universal_entry.contracts import StrategyExecutionBindingRef


@dataclass(frozen=True, slots=True)
class EntryAccountFanout:
    strategy_activation_id: str
    legs: tuple[StrategyExecutionBindingRef, ...]
    reasons: tuple[str, ...]

    @property
    def ready_for_account_admission(self) -> bool:
        return bool(self.legs) and not self.reasons


def select_entry_account_legs(
    *,
    strategy_activation_id: str,
    bindings: tuple[StrategyExecutionBindingRef, ...],
    authorized_binding_ids: frozenset[str],
) -> EntryAccountFanout:
    """Fail closed if the snapshot is ambiguous, incomplete or unauthorized."""
    reasons: set[str] = set()
    activation = strategy_activation_id.strip()
    if not activation:
        reasons.add("STRATEGY_ACTIVATION_MISSING")
    if not bindings:
        reasons.add("EXECUTION_BINDINGS_MISSING")
    seen_ids: set[str] = set()
    seen_accounts: set[str] = set()
    seen_legs: set[str] = set()
    selected: list[StrategyExecutionBindingRef] = []
    for binding in bindings:
        if binding.strategy_activation_id != activation:
            reasons.add("BINDING_ACTIVATION_MISMATCH")
        if binding.strategy_execution_binding_id in seen_ids:
            reasons.add("BINDING_ID_DUPLICATE")
        if binding.trading_account_id in seen_accounts:
            reasons.add("ACCOUNT_BOUND_TWICE")
        if binding.execution_leg in seen_legs:
            reasons.add("EXECUTION_LEG_DUPLICATE")
        if binding.strategy_execution_binding_id not in authorized_binding_ids:
            reasons.add("BINDING_NOT_AUTHORIZED")
        seen_ids.add(binding.strategy_execution_binding_id)
        seen_accounts.add(binding.trading_account_id)
        seen_legs.add(binding.execution_leg)
        selected.append(binding)
    # Never expose a subset as executable if any requested leg fails validation.
    if reasons:
        return EntryAccountFanout(activation, (), tuple(sorted(reasons)))
    return EntryAccountFanout(
        activation,
        tuple(sorted(selected, key=lambda x: (x.execution_leg, x.trading_account_id))),
        (),
    )
