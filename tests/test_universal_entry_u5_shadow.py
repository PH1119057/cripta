from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.universal_entry import MarketFactEnvelope
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.shadow_runtime import (
    CausalFactFanout,
    ShadowComparability,
    ShadowComparabilityGate,
    derive_unknown_prestart_horizon_seconds,
)
from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
MIGRATION = ROOT / "operations/sql/20260908_strategy_entry_u5_shadow.sql"
UNIT = ROOT / "operations/systemd/cripta-universal-entry-shadow.service"
RUNTIME = ROOT / "operations/monitoring/universal_entry_shadow.py"


def _fact(index: int = 1) -> MarketFactEnvelope:
    at = NOW + timedelta(seconds=index)
    return MarketFactEnvelope(
        fact_id=f"fact-{index}",
        event_kind="PUBLIC_TRADE",
        symbol="UNIUSDT",
        observed_at=at,
        event_at=at,
        received_at=at,
        source_refs=(f"trade:{index}",),
    )


def test_u5_contract_is_frozen_before_runtime_source() -> None:
    text = (ROOT / "docs/UNIVERSAL_STRATEGY_ENTRY_IMPLEMENTATION_RU.md").read_text(encoding="utf-8")
    assert "**Версия:** 1.2" in text
    assert "49670cb0631a8742b2bf8dace9ab33d6b29a107d" in text
    assert "один и тот же `MarketFactEnvelope`" in text
    assert "WARMUP / NOT_COMPARABLE" in text
    assert "420 минут" in text


def test_unknown_prestart_horizon_is_derived_from_entry_plan_not_runtime_constant() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    plan, _ = materialize_plans(bundle.card, bundle.activation)
    assert derive_unknown_prestart_horizon_seconds(plan) == 25_200


def test_same_fact_object_is_fanned_to_both_consumers() -> None:
    seen_a: list[MarketFactEnvelope] = []
    seen_b: list[MarketFactEnvelope] = []
    fanout = CausalFactFanout(seen_a.append, seen_b.append)
    event = _fact()
    fanout.dispatch(event)
    assert seen_a == [event]
    assert seen_b == [event]
    assert seen_a[0] is seen_b[0]


def test_comparability_gate_is_fail_closed_until_all_requirements_are_ready() -> None:
    gate = ShadowComparabilityGate(NOW, required_warmup_seconds=60)
    assert gate.state_at(NOW) is ShadowComparability.WARMUP
    gate.mark_seed_complete()
    gate.mark_live_sensor_complete()
    assert gate.state_at(NOW + timedelta(seconds=59)) is ShadowComparability.WARMUP
    assert gate.state_at(NOW + timedelta(seconds=60)) is ShadowComparability.PARITY_COMPARABLE


def test_restart_gap_never_silently_continues_comparable_state() -> None:
    gate = ShadowComparabilityGate(NOW, required_warmup_seconds=0)
    gate.mark_seed_complete()
    gate.mark_live_sensor_complete()
    assert gate.state_at(NOW) is ShadowComparability.PARITY_COMPARABLE
    gate.mark_uncovered_gap("service restart created uncovered public-stream interval")
    assert gate.state_at(NOW + timedelta(seconds=1)) is ShadowComparability.NOT_COMPARABLE
    assert "uncovered" in gate.reason


def test_u5_migration_supports_online_run_state_without_mutable_identity() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    required_columns = (
        "entry_plan_fingerprint",
        "calibration_sha256",
        "calibration_size",
        "fact_source_id",
        "service_instance_id",
        "category",
        "observed_at",
        "source_refs",
    )
    for name in required_columns:
        assert name in sql
    assert "DROP NOT NULL" in sql
    assert "WARMUP" in sql
    assert "PARITY_COMPARABLE" in sql
    assert "NOT_COMPARABLE" in sql
    assert "guard_shadow_parity_run_update" in sql
    assert "OLD.strategy_config_fingerprint" in sql
    assert "OLD.entry_plan_fingerprint" in sql
    assert "OLD.calibration_sha256" in sql
    assert "GRANT UPDATE (status, finished_at, summary)" in sql
    assert "REVOKE DELETE" in sql


def test_shadow_parity_events_remain_append_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "shadow_parity_events_immutable" in sql
    assert "reject_immutable_change" in sql
    assert "GRANT UPDATE" not in "\n".join(
        line for line in sql.splitlines() if "shadow_parity_events" in line and "GRANT" in line
    )


def test_systemd_shadow_service_is_isolated_and_public_only() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "Description=Cripta Universal Entry U5 parity shadow" in unit
    assert "User=cripta" in unit
    assert "ExecStart=/usr/bin/python3 /srv/cripta/monitoring/universal_entry_shadow.py" in unit
    assert "ReadWritePaths=/var/lib/cripta/universal_entry_shadow" in unit
    assert "ProtectSystem=strict" in unit
    assert "PrivateTmp=true" in unit
    assert "entry_shadow_scanner.py" not in unit
    assert "cripta-entry-shadow-scanner.service" not in unit
    lowered = unit.lower()
    assert "cripta-private-runtime" not in lowered
    assert "private_ws" not in lowered
    assert "api_key" not in lowered
    assert "api_secret" not in lowered


def test_shadow_runtime_source_has_no_execution_or_legacy_write_path() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    forbidden = (
        "runtime.trade_commands",
        "monitoring.opportunities",
        "entry_geometry_handoffs",
        "place_order",
        "cancel_order",
        "amend_order",
        "MainnetExecution",
        "TestnetExecution",
        "PositionHandoffStore",
        "EntryBotRuntime(",
        "entry_shadow_scanner",
    )
    for token in forbidden:
        assert token not in source
    assert "V1DeterministicParityRunner" in source
    assert "MarketFactEnvelope" in source


def test_u5_source_does_not_import_entry_v2_research() -> None:
    bodies = []
    for path in (
        ROOT / "src/bybit_workbench/universal_entry/shadow_runtime.py",
        RUNTIME,
    ):
        bodies.append(path.read_text(encoding="utf-8"))
    body = "\n".join(bodies).lower()
    assert "entry v2" not in body
    assert "entry_v2" not in body
    assert "research" not in body


def test_first_mismatch_evidence_contract_is_explicit_in_runtime_source() -> None:
    source = (ROOT / "src/bybit_workbench/universal_entry/shadow_runtime.py").read_text(
        encoding="utf-8"
    )
    for field in (
        "parity_run_id",
        "causal_key",
        "category",
        "legacy_payload",
        "universal_payload",
        "source_refs",
        "strategy_config_fingerprint",
        "entry_plan_fingerprint",
    ):
        assert field in source


def test_shadow_has_no_callable_exchange_mutation_import() -> None:
    package_source = (ROOT / "src/bybit_workbench/universal_entry/shadow_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "bybit_workbench.execution" not in package_source
    assert "bybit_workbench.exchange" not in package_source
    assert "mainnet" not in package_source.lower()
    assert "testnet" not in package_source.lower()


def test_invalid_negative_warmup_is_rejected() -> None:
    with pytest.raises(ValueError, match="warmup"):
        ShadowComparabilityGate(NOW, required_warmup_seconds=-1)
