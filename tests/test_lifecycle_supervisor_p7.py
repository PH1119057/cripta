from __future__ import annotations

from pathlib import Path

import pytest

from bybit_workbench.lifecycle_supervisor import LifecycleSupervisorPolicy

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = (ROOT / "src/bybit_workbench/lifecycle_supervisor.py").read_text(encoding="utf-8")
ACK = (ROOT / "src/bybit_workbench/lifecycle_ack.py").read_text(encoding="utf-8")
ENTRY_OBSERVER = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(
    encoding="utf-8"
)
EXIT_SHADOW_STORE = (ROOT / "src/bybit_workbench/universal_exit/storage.py").read_text(
    encoding="utf-8"
)


def test_lifecycle_supervisor_has_no_trading_authority() -> None:
    for forbidden in (
        "INSERT INTO runtime.trade_commands",
        "UPDATE runtime.trade_commands",
        "DELETE FROM runtime.trade_commands",
        "/v5/order/create",
        "/v5/position/trading-stop",
        "api_post(",
        "EntryDecision(",
        "ExitDecision(",
        "StrategySignal(",
    ):
        assert forbidden not in SUPERVISOR
    assert "runtime.trade_lifecycle_events" in SUPERVISOR
    assert "runtime.lifecycle_faults" in SUPERVISOR


def test_exchange_divergence_has_no_hidden_freshness_default() -> None:
    policy = LifecycleSupervisorPolicy()
    assert policy.exchange_state_max_age_seconds is None
    with pytest.raises(ValueError):
        LifecycleSupervisorPolicy(exchange_state_max_age_seconds=0)


def test_entry_engine_records_real_plan_consumption_ack() -> None:
    assert "record_plan_consumption(" in ENTRY_OBSERVER
    assert 'plan_kind="ENTRY"' in ENTRY_OBSERVER
    assert "bundle.entry_plan.entry_plan_fingerprint" in ENTRY_OBSERVER
    assert "bundle.activation.activation_id" in ENTRY_OBSERVER


def test_shadow_exit_evidence_does_not_fake_live_position_claim() -> None:
    assert "claim_exit_position(" not in EXIT_SHADOW_STORE


def test_exit_claim_is_exact_and_disallows_silent_takeover() -> None:
    assert "runtime.position_exit_claims" in ACK
    assert "StrategyPosition already claimed by another Exit Engine" in ACK
    assert "exact_exit_plan" in ACK
    assert 'plan_kind="EXIT"' in ACK


def test_position_without_exit_owner_is_critical_fault() -> None:
    start = SUPERVISOR.rindex('"POSITION_WITHOUT_EXIT_OWNER"')
    nearby = SUPERVISOR[start : start + 1800]
    assert "FaultSeverity.CRITICAL" in nearby
    assert "open StrategyPosition lacks exact Exit Engine claim" in SUPERVISOR
    assert "Exit Engine claim heartbeat is stale" in SUPERVISOR


def test_supervisor_manages_every_v1_lifecycle_fault_code() -> None:
    for code in (
        "PLAN_PAIR_INCOMPLETE",
        "ENTRY_PLAN_NOT_CONSUMED",
        "ENTRY_REQUEST_NOT_DISPATCHED",
        "ENTRY_EXECUTION_AMBIGUOUS",
        "POSITION_LINEAGE_INCOMPLETE",
        "POSITION_WITHOUT_EXIT_PLAN",
        "POSITION_WITHOUT_EXIT_OWNER",
        "EXIT_REQUEST_NOT_DISPATCHED",
        "EXIT_EXECUTION_AMBIGUOUS",
        "EXCHANGE_POSITION_OWNERSHIP_CONFLICT",
        "EXCHANGE_STATE_DIVERGED",
        "CAPITAL_RESERVATION_STUCK",
    ):
        assert f'"{code}"' in SUPERVISOR
