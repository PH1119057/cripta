"""Regression: SUB WS transient latch is recoverable only with independent proof.

Pure planner tests: never a DB DELETE, implicit LIVE arm, or Exchange mutation.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.account_ws_technical_recovery import (
    WsTechnicalRecoveryAction,
    WsTechnicalRecoveryEvidence,
    plan_ws_technical_recovery,
)

NOW = datetime(2026, 11, 4, 11, 30, tzinfo=UTC)
RELEASE = "a" * 40
ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
CONNECTOR = "ac-ec57819f875f1bfd929a382d6dc8a125"


def proven() -> WsTechnicalRecoveryEvidence:
    return WsTechnicalRecoveryEvidence(
        account_id=ACCOUNT,
        connector_id=CONNECTOR,
        expected_account_id=ACCOUNT,
        expected_connector_id=CONNECTOR,
        loaded_commit=RELEASE,
        expected_commit=RELEASE,
        safety_reason="PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN",
        blocked_at=NOW - timedelta(minutes=14),
        reconnect_verified_at=NOW - timedelta(seconds=30),
        ws_heartbeat_at=NOW - timedelta(seconds=2),
        private_topics_confirmed=True,
        independent_proof_verified=True,
        proof_source_ref="bybit:signed-recovery-linear-usdt:exact-account",
        proof_account_id=ACCOUNT,
        signed_uid="12345",
        registered_uid="12345",
        private_generation_id="generation-1",
        complete_generation_id="generation-1",
        generation_complete=True,
        proof_observed_at=NOW - timedelta(seconds=2),
        generation_completed_at=NOW - timedelta(seconds=3),
        positions_count=0,
        open_orders=0,
        pending_commands=0,
        protection_verified=True,
        exit_continuity_verified=True,
        ambiguous_exchange_mutation=False,
        critical_lifecycle_fault=False,
        attempts_used=0,
    )


def test_verified_reconnect_requests_independent_technical_clear_only() -> None:
    result = plan_ws_technical_recovery(proven(), now=NOW)
    assert result.action is WsTechnicalRecoveryAction.REQUEST_INDEPENDENT_CLEAR
    assert result.reason == "INDEPENDENT_TECHNICAL_CLEAR_PROVEN"
    assert result.owner_arm_change is False
    assert result.gate_change is False
    assert result.exchange_mutation is False
    assert result.effective_new_entry_granted is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("account_id", "foreign", "RECOVERY_ACCOUNT_SCOPE_MISMATCH"),
        ("connector_id", "foreign", "RECOVERY_ACCOUNT_SCOPE_MISMATCH"),
        ("expected_commit", "b" * 40, "RECOVERY_RELEASE_MISMATCH"),
        ("loaded_commit", "", "RECOVERY_RELEASE_MISMATCH"),
        ("safety_reason", "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED",
         "RECOVERY_NOT_A_TRANSIENT_WS_LATCH"),
        ("private_topics_confirmed", False, "PRIVATE_WS_NOT_VERIFIED"),
        ("independent_proof_verified", False, "INDEPENDENT_PROOF_NOT_VERIFIED"),
        ("proof_source_ref", "bybit:signed-flat-linear-usdt:other-account",
         "INDEPENDENT_PROOF_NOT_VERIFIED"),
        ("proof_account_id", "foreign", "INDEPENDENT_PROOF_NOT_VERIFIED"),
        ("signed_uid", "54321", "SIGNED_ACCOUNT_IDENTITY_MISMATCH"),
        ("private_generation_id", "generation-2", "GENERATION_NOT_PROVEN"),
        ("generation_complete", False, "GENERATION_NOT_PROVEN"),
        ("open_orders", 1, "UNKNOWN_OR_PENDING_EXCHANGE_EXPOSURE"),
        ("pending_commands", 1, "UNKNOWN_OR_PENDING_EXCHANGE_EXPOSURE"),
        ("ambiguous_exchange_mutation", True, "UNKNOWN_OR_PENDING_EXCHANGE_EXPOSURE"),
        ("critical_lifecycle_fault", True, "CRITICAL_LIFECYCLE_FAULT"),
        ("protection_verified", False, "EXIT_OR_PROTECTION_NOT_VERIFIED"),
        ("exit_continuity_verified", False, "EXIT_OR_PROTECTION_NOT_VERIFIED"),
        ("attempts_used", 2, "RECOVERY_ATTEMPT_BUDGET_EXHAUSTED"),
    ],
)
def test_unproven_or_hard_faults_never_request_clear(
    field: str, value: object, reason: str,
) -> None:
    evidence = replace(proven(), **{field: value})
    result = plan_ws_technical_recovery(evidence, now=NOW)
    assert result.action is WsTechnicalRecoveryAction.KEEP_BLOCKED
    assert result.reason == reason
    assert result.owner_arm_change is False
    assert result.gate_change is False
    assert result.exchange_mutation is False
    assert result.effective_new_entry_granted is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reconnect_verified_at", NOW - timedelta(minutes=15)),
        ("ws_heartbeat_at", NOW - timedelta(seconds=16)),
        ("proof_observed_at", NOW - timedelta(seconds=16)),
        ("generation_completed_at", NOW - timedelta(seconds=16)),
        ("ws_heartbeat_at", NOW + timedelta(seconds=2)),
        ("proof_observed_at", NOW.replace(tzinfo=None)),
        ("reconnect_verified_at", None),
    ],
)
def test_stale_future_or_missing_evidence_stays_blocked(
    field: str, value: object,
) -> None:
    assert plan_ws_technical_recovery(
        replace(proven(), **{field: value}), now=NOW,
    ).action is WsTechnicalRecoveryAction.KEEP_BLOCKED


def test_protected_open_position_requires_explicit_exit_and_protection_proof() -> None:
    evidence = replace(proven(), positions_count=2)
    assert plan_ws_technical_recovery(
        evidence, now=NOW,
    ).action is WsTechnicalRecoveryAction.REQUEST_INDEPENDENT_CLEAR
    assert plan_ws_technical_recovery(
        replace(evidence, protection_verified=False), now=NOW,
    ).action is WsTechnicalRecoveryAction.KEEP_BLOCKED
    assert plan_ws_technical_recovery(
        replace(evidence, exit_continuity_verified=False), now=NOW,
    ).action is WsTechnicalRecoveryAction.KEEP_BLOCKED


def test_owner_disarm_and_strategy_rights_are_never_written_or_inferred() -> None:
    # Technical health may recover while OWNER intentionally keeps LIVE OFF.
    src = Path("src/bybit_workbench/account_ws_technical_recovery.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    forbidden_imports = {"psycopg", "subprocess", "socket", "requests", "urllib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = {name.name.split(".")[0] for name in node.names}
            assert not imported & forbidden_imports
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden_imports
    assert "execution_gates" not in src
    assert "live_arm_sessions" not in src
    assert "execution_permissions" not in src
    assert "DELETE FROM" not in src
    assert "UPDATE " not in src
    assert "INSERT INTO" not in src
    assert "REQUEST_INDEPENDENT_CLEAR" in src
