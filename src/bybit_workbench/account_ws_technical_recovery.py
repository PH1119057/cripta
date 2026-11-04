"""Pure evidence gate for independent account-scoped WS technical recovery.

This module does NOT clear a durable latch, touch PostgreSQL/Bybit, restart a
service, or grant trading rights.  The approved control owner must independently
obtain/verify each supplied fact and perform a separate guarded transition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SIGNED_SOURCES = frozenset({
    "bybit:signed-flat-linear-usdt:exact-account",
    "bybit:signed-recovery-linear-usdt:exact-account",
})
_FRESH_SECONDS = 15
_ATTEMPT_BUDGET = 2


class WsTechnicalRecoveryAction(StrEnum):
    KEEP_BLOCKED = "KEEP_BLOCKED"
    REQUEST_INDEPENDENT_CLEAR = "REQUEST_INDEPENDENT_CLEAR"


@dataclass(frozen=True, slots=True)
class WsTechnicalRecoveryEvidence:
    account_id: str
    connector_id: str
    expected_account_id: str
    expected_connector_id: str
    loaded_commit: str
    expected_commit: str
    safety_reason: str
    blocked_at: datetime
    reconnect_verified_at: datetime
    ws_heartbeat_at: datetime
    private_topics_confirmed: bool
    independent_proof_verified: bool
    proof_source_ref: str
    proof_account_id: str
    signed_uid: str
    registered_uid: str
    private_generation_id: str
    complete_generation_id: str
    generation_complete: bool
    proof_observed_at: datetime
    generation_completed_at: datetime
    positions_count: int
    open_orders: int
    pending_commands: int
    protection_verified: bool
    exit_continuity_verified: bool
    ambiguous_exchange_mutation: bool
    critical_lifecycle_fault: bool
    attempts_used: int


@dataclass(frozen=True, slots=True)
class WsTechnicalRecoveryDecision:
    action: WsTechnicalRecoveryAction
    reason: str
    # Technical recoverability never creates effective new Entry permission.
    # The actual control transition and OWNER authorization are separate.
    owner_arm_change: bool = False
    gate_change: bool = False
    exchange_mutation: bool = False
    effective_new_entry_granted: bool = False


def _ordered_aware(timestamp: object, *, now: datetime) -> bool:
    return (
        isinstance(timestamp, datetime)
        and timestamp.tzinfo is not None
        and timestamp.utcoffset() is not None
        and timestamp <= now
    )


def _fresh(timestamp: object, *, now: datetime) -> bool:
    return (
        _ordered_aware(timestamp, now=now)
        and 0 <= (now - timestamp).total_seconds() <= _FRESH_SECONDS
    )


def plan_ws_technical_recovery(
    evidence: WsTechnicalRecoveryEvidence,
    *,
    now: datetime,
) -> WsTechnicalRecoveryDecision:
    """Classify one transient WS latch, without performing any mutation.

    The independent adapter must use authenticated Exchange truth, a COMPLETE
    account generation, actual post-latch private subscriptions/heartbeat and
    separately verified Exit/protection ownership. A planner boolean is never
    itself a credential or authorization to delete the safety latch.
    """

    def block(reason: str) -> WsTechnicalRecoveryDecision:
        return WsTechnicalRecoveryDecision(WsTechnicalRecoveryAction.KEEP_BLOCKED, reason)

    if not _ordered_aware(now, now=now):
        return block("RECOVERY_CLOCK_UNVERIFIED")
    if (
        not evidence.account_id
        or not evidence.connector_id
        or evidence.account_id != evidence.expected_account_id
        or evidence.connector_id != evidence.expected_connector_id
    ):
        return block("RECOVERY_ACCOUNT_SCOPE_MISMATCH")
    if (
        not _SHA.fullmatch(evidence.expected_commit)
        or evidence.loaded_commit != evidence.expected_commit
    ):
        return block("RECOVERY_RELEASE_MISMATCH")
    if evidence.safety_reason != "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN":
        return block("RECOVERY_NOT_A_TRANSIENT_WS_LATCH")
    if evidence.attempts_used < 0 or evidence.attempts_used >= _ATTEMPT_BUDGET:
        return block("RECOVERY_ATTEMPT_BUDGET_EXHAUSTED")
    if (
        not _ordered_aware(evidence.blocked_at, now=now)
        or not _ordered_aware(evidence.reconnect_verified_at, now=now)
        or evidence.reconnect_verified_at < evidence.blocked_at
        or not _fresh(evidence.ws_heartbeat_at, now=now)
        or evidence.ws_heartbeat_at < evidence.reconnect_verified_at
        or not evidence.private_topics_confirmed
    ):
        return block("PRIVATE_WS_NOT_VERIFIED")
    if (
        not evidence.independent_proof_verified
        or evidence.proof_source_ref not in _SIGNED_SOURCES
        or evidence.proof_account_id != evidence.account_id
        or not _fresh(evidence.proof_observed_at, now=now)
    ):
        return block("INDEPENDENT_PROOF_NOT_VERIFIED")
    if (
        not evidence.signed_uid
        or not evidence.signed_uid.isascii()
        or not evidence.signed_uid.isdigit()
        or int(evidence.signed_uid) <= 0
        or evidence.signed_uid != evidence.registered_uid
    ):
        return block("SIGNED_ACCOUNT_IDENTITY_MISMATCH")
    if (
        not evidence.private_generation_id
        or evidence.private_generation_id != evidence.complete_generation_id
        or not evidence.generation_complete
        or not _fresh(evidence.generation_completed_at, now=now)
        or evidence.generation_completed_at > evidence.proof_observed_at
    ):
        return block("GENERATION_NOT_PROVEN")
    if (
        type(evidence.positions_count) is not int
        or evidence.positions_count < 0
        or type(evidence.open_orders) is not int
        or evidence.open_orders != 0
        or type(evidence.pending_commands) is not int
        or evidence.pending_commands != 0
        or evidence.ambiguous_exchange_mutation
    ):
        return block("UNKNOWN_OR_PENDING_EXCHANGE_EXPOSURE")
    if (
        evidence.positions_count > 0
        and evidence.proof_source_ref
        != "bybit:signed-recovery-linear-usdt:exact-account"
    ):
        return block("UNKNOWN_OR_PENDING_EXCHANGE_EXPOSURE")
    if evidence.critical_lifecycle_fault:
        return block("CRITICAL_LIFECYCLE_FAULT")
    if not evidence.protection_verified or not evidence.exit_continuity_verified:
        return block("EXIT_OR_PROTECTION_NOT_VERIFIED")

    return WsTechnicalRecoveryDecision(
        WsTechnicalRecoveryAction.REQUEST_INDEPENDENT_CLEAR,
        "INDEPENDENT_TECHNICAL_CLEAR_PROVEN",
    )
