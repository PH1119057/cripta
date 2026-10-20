"""Fail-closed account-scoped Entry admission input preparation.

This creates *inputs* to per-account admission only. It never authorizes
execution, reserves capital, writes to DB, or calls an exchange.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from bybit_workbench.entry_account_fanout import EntryAccountFanout
from bybit_workbench.universal_entry.contracts import StrategyExecutionBindingRef


@dataclass(frozen=True, slots=True)
class AccountAdmissionEvidence:
    trading_account_id: str
    account_ref: str
    account_state_generation_id: str
    capacity_snapshot_id: str
    capacity_available: Decimal
    exchange_position_key: str
    connector_id: str
    private_exchange_verified: bool
    protection_capability_verified: bool


@dataclass(frozen=True, slots=True)
class ScopedEntryLeg:
    binding: StrategyExecutionBindingRef
    evidence: AccountAdmissionEvidence


@dataclass(frozen=True, slots=True)
class ScopedEntryPlan:
    legs: tuple[ScopedEntryLeg, ...]
    reasons: tuple[str, ...]

    @property
    def ready_for_individual_admission(self) -> bool:
        return bool(self.legs) and not self.reasons


def prepare_scoped_entry_legs(
    *,
    fanout: EntryAccountFanout,
    evidence_by_account: Mapping[str, AccountAdmissionEvidence],
) -> ScopedEntryPlan:
    """Reject an entire fan-out if any account is missing independent proof."""
    if not fanout.ready_for_account_admission:
        return ScopedEntryPlan((), ("FANOUT_NOT_READY",) + fanout.reasons)
    reasons: set[str] = set()
    selected: list[ScopedEntryLeg] = []
    position_keys: set[str] = set()
    connectors: set[str] = set()
    for binding in fanout.legs:
        proof = evidence_by_account.get(binding.trading_account_id)
        if proof is None:
            reasons.add("ACCOUNT_ADMISSION_EVIDENCE_MISSING")
            continue
        if proof.trading_account_id != binding.trading_account_id:
            reasons.add("ACCOUNT_ADMISSION_IDENTITY_MISMATCH")
        if not all((
            proof.account_ref.strip(),
            proof.account_state_generation_id.strip(),
            proof.capacity_snapshot_id.strip(),
            proof.exchange_position_key.strip(),
            proof.connector_id.strip(),
        )):
            reasons.add("ACCOUNT_ADMISSION_LINEAGE_MISSING")
        if proof.capacity_available < Decimal(0):
            reasons.add("ACCOUNT_ADMISSION_CAPACITY_INVALID")
        if not proof.private_exchange_verified:
            reasons.add("ACCOUNT_PRIVATE_EXCHANGE_NOT_VERIFIED")
        if not proof.protection_capability_verified:
            reasons.add("ACCOUNT_PROTECTION_CAPABILITY_NOT_VERIFIED")
        if proof.exchange_position_key in position_keys:
            reasons.add("ACCOUNT_POSITION_KEY_NOT_DISTINCT")
        if proof.connector_id in connectors:
            reasons.add("ACCOUNT_CONNECTOR_NOT_DISTINCT")
        position_keys.add(proof.exchange_position_key)
        connectors.add(proof.connector_id)
        selected.append(ScopedEntryLeg(binding, proof))
    # Never offer a successfully proven subset when other legs failed.
    if reasons:
        return ScopedEntryPlan((), tuple(sorted(reasons)))
    return ScopedEntryPlan(tuple(selected), ())
