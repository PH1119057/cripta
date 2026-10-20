"""Pure account-lineage guard before individual Entry admission.

The guard does not call EntryAdmissionPort, reserve capital or send orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bybit_workbench.entry_account_admission_scope import ScopedEntryLeg
    from bybit_workbench.entry_admission import EntryAdmissionRequest


@dataclass(frozen=True, slots=True)
class AccountLineageCheck:
    ready: bool
    reasons: tuple[str, ...]


def check_scoped_entry_request(
    leg: ScopedEntryLeg, request: EntryAdmissionRequest
) -> AccountLineageCheck:
    """Require exact account, binding, snapshot and physical slot identity."""
    binding = leg.binding
    evidence = leg.evidence
    reasons: set[str] = set()
    if request.strategy_activation_id != binding.strategy_activation_id:
        reasons.add("STRATEGY_ACTIVATION_MISMATCH")
    if request.strategy_execution_binding_id != binding.strategy_execution_binding_id:
        reasons.add("STRATEGY_EXECUTION_BINDING_MISMATCH")
    if request.execution_leg != binding.execution_leg:
        reasons.add("EXECUTION_LEG_MISMATCH")
    if request.trading_account_id != binding.trading_account_id:
        reasons.add("TRADING_ACCOUNT_MISMATCH")
    if request.account_ref != evidence.account_ref:
        reasons.add("EXCHANGE_ACCOUNT_MISMATCH")
    if request.account_state_generation_id != evidence.account_state_generation_id:
        reasons.add("ACCOUNT_GENERATION_MISMATCH")
    if request.capacity_snapshot_id != evidence.capacity_snapshot_id:
        reasons.add("CAPACITY_SNAPSHOT_MISMATCH")
    if request.capacity_available != evidence.capacity_available:
        reasons.add("CAPACITY_VALUE_MISMATCH")
    if request.exchange_position_key != evidence.exchange_position_key:
        reasons.add("EXCHANGE_POSITION_KEY_MISMATCH")
    if not evidence.private_exchange_verified:
        reasons.add("PRIVATE_EXCHANGE_UNVERIFIED")
    if not evidence.protection_capability_verified:
        reasons.add("PROTECTION_CAPABILITY_UNVERIFIED")
    if evidence.trading_account_id != binding.trading_account_id:
        reasons.add("EVIDENCE_ACCOUNT_MISMATCH")
    return AccountLineageCheck(not reasons, tuple(sorted(reasons)))
