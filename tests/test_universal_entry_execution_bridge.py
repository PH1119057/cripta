from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.contracts import ExecutionRequest, FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.execution_bridge import (
    BridgePolicyBundle,
    ExecutionBridgeBlockCode,
    ExecutionBridgeBlocked,
    prepare_runtime_entry_command,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)


def _request(*, direction: TradeDirection = TradeDirection.LONG) -> ExecutionRequest:
    return ExecutionRequest(
        execution_request_id="request-1",
        strategy_attempt_id="attempt-1",
        entry_decision_id="decision-1",
        signal_id="sig-1",
        strategy_id="strategy-a",
        strategy_version="2.0",
        strategy_config_fingerprint="strategy-fp",
        entry_plan_fingerprint="entry-fp",
        symbol="UNIUSDT",
        direction=direction,
        requested_at=NOW,
        payload=FrozenPolicy.from_mapping(
            {
                "capital_policy": {
                    "require_capacity": True,
                    "requested_amount": "10",
                    "amount_currency": "USDT",
                    "leverage": 2,
                    "capacity_max_age_seconds": 15,
                    "capacity_min_quality": "GOOD",
                },
                "entry_plan_fingerprint": "entry-fp",
                "signal_fact": {
                    "fact_id": "fact-1",
                    "event_kind": "TOUCH",
                    "symbol": "UNIUSDT",
                    "direction": direction.value,
                    "observed_at": NOW.isoformat(),
                    "event_at": NOW.isoformat(),
                    "received_at": NOW.isoformat(),
                    "source_refs": ["source-1"],
                    "attributes": {"entry_price": "7.50", "price": "7.49"},
                },
            }
        ),
    )


def _bundle(*, enabled: bool = True, limit: bool = False) -> BridgePolicyBundle:
    execution_policy: dict[str, object] = {
        "order_type": "LIMIT_OFFSET" if limit else "MARKET",
        "reference_value_path": "fact.entry_price",
        "max_request_age_seconds": 30,
    }
    if limit:
        execution_policy.update(
            {"entry_offset_pct": "0.20", "entry_limit_ttl_seconds": 45}
        )
    capital = {
        "require_capacity": True,
        "requested_amount": "10",
        "amount_currency": "USDT",
        "leverage": 2,
        "capacity_max_age_seconds": 15,
        "capacity_min_quality": "GOOD",
    }
    identity = {
        "strategy_id": "strategy-a",
        "strategy_version": "2.0",
        "strategy_config_fingerprint": "strategy-fp",
    }
    return BridgePolicyBundle(
        strategy_card={
            **identity,
            "entry_policy": {"execution_policy": execution_policy},
            "capital_policy": capital,
        },
        entry_plan={**identity, "entry_plan_fingerprint": "entry-fp"},
        exit_plan={
            **identity,
            "exit_plan_fingerprint": "exit-fp",
            "protection_policy": {
                "initial_protection": {
                    "stop_loss_pct": "1.00",
                    "take_profit_pct": "3.00",
                    "trigger_by": "LastPrice",
                    "tpsl_mode": "Full",
                }
            },
        },
        activation={
            **identity,
            "activation_id": "activation-1",
            "enabled": enabled,
        },
    )


def test_complete_strategy_policy_builds_deterministic_existing_execution_command() -> None:
    first = prepare_runtime_entry_command(_request(), _bundle(), now=NOW + timedelta(seconds=1))
    second = prepare_runtime_entry_command(_request(), _bundle(), now=NOW + timedelta(seconds=2))
    assert first.command_id == second.command_id
    assert first.command_id.startswith("ue-")
    assert first.execution_request_id == "request-1"
    assert first.strategy_attempt_id == "attempt-1"
    assert first.exit_plan_fingerprint == "exit-fp"
    assert first.payload["source"] == "universal_entry"
    assert first.payload["stake_usdt"] == "10"
    assert first.payload["leverage"] == 2
    assert first.payload["side"] == "Buy"
    assert first.payload["price"] == "7.50"
    assert first.payload["entry_offset_pct"] == "0"
    assert first.payload["entry_limit_ttl_seconds"] is None
    protection = first.payload["initial_protection"]
    assert isinstance(protection, dict)
    assert protection["exit_plan_fingerprint"] == "exit-fp"
    assert protection["strategy_config_fingerprint"] == "strategy-fp"


def test_short_limit_policy_maps_only_explicit_strategy_values() -> None:
    prepared = prepare_runtime_entry_command(
        _request(direction=TradeDirection.SHORT),
        _bundle(limit=True),
        now=NOW + timedelta(seconds=1),
    )
    assert prepared.payload["side"] == "Sell"
    assert prepared.payload["entry_offset_pct"] == "0.20"
    assert prepared.payload["entry_limit_ttl_seconds"] == 45


def test_disabled_exact_activation_is_fail_closed() -> None:
    with pytest.raises(ExecutionBridgeBlocked) as caught:
        prepare_runtime_entry_command(_request(), _bundle(enabled=False), now=NOW)
    assert caught.value.code is ExecutionBridgeBlockCode.ACTIVATION_DISABLED


def test_expired_request_is_fail_closed() -> None:
    with pytest.raises(ExecutionBridgeBlocked) as caught:
        prepare_runtime_entry_command(
            _request(), _bundle(), now=NOW + timedelta(seconds=31)
        )
    assert caught.value.code is ExecutionBridgeBlockCode.REQUEST_EXPIRED


def test_identity_mismatch_is_fail_closed() -> None:
    bundle = _bundle()
    bad_card = dict(bundle.strategy_card)
    bad_card["strategy_version"] = "other"
    bad = BridgePolicyBundle(bad_card, bundle.entry_plan, bundle.exit_plan, bundle.activation)
    with pytest.raises(ExecutionBridgeBlocked) as caught:
        prepare_runtime_entry_command(_request(), bad, now=NOW)
    assert caught.value.code is ExecutionBridgeBlockCode.IDENTITY_MISMATCH


def test_missing_reference_path_is_not_zero_or_neutral() -> None:
    bundle = _bundle()
    card = dict(bundle.strategy_card)
    entry_policy = dict(card["entry_policy"])
    execution = dict(entry_policy["execution_policy"])
    execution["reference_value_path"] = "fact.not_present"
    entry_policy["execution_policy"] = execution
    card["entry_policy"] = entry_policy
    bad = BridgePolicyBundle(card, bundle.entry_plan, bundle.exit_plan, bundle.activation)
    with pytest.raises(ExecutionBridgeBlocked) as caught:
        prepare_runtime_entry_command(_request(), bad, now=NOW)
    assert caught.value.code is ExecutionBridgeBlockCode.REFERENCE_PRICE_MISSING


def test_current_v1_card_cannot_use_legacy_trade_settings_as_hidden_defaults() -> None:
    compatibility = load_v1_compatibility_bundle(ROOT)
    entry_plan, exit_plan = materialize_plans(compatibility.card, compatibility.activation)
    request = ExecutionRequest(
        execution_request_id="request-v1",
        strategy_attempt_id="attempt-v1",
        entry_decision_id="decision-v1",
        signal_id="sig-v1",
        strategy_id=compatibility.card.strategy_id,
        strategy_version=compatibility.card.strategy_version,
        strategy_config_fingerprint=compatibility.card.strategy_config_fingerprint,
        entry_plan_fingerprint=entry_plan.entry_plan_fingerprint,
        symbol="UNIUSDT",
        direction=TradeDirection.LONG,
        requested_at=NOW,
        payload=FrozenPolicy.from_mapping(
            {
                "capital_policy": compatibility.card.capital_policy.to_dict(),
                "entry_plan_fingerprint": entry_plan.entry_plan_fingerprint,
                "signal_fact": {"attributes": {"entry_price": "7.50"}},
            }
        ),
    )
    bundle = BridgePolicyBundle(
        strategy_card={
            "strategy_id": compatibility.card.strategy_id,
            "strategy_version": compatibility.card.strategy_version,
            "strategy_config_fingerprint": compatibility.card.strategy_config_fingerprint,
            "entry_policy": compatibility.card.entry_policy.to_dict(),
            "capital_policy": compatibility.card.capital_policy.to_dict(),
        },
        entry_plan={
            "strategy_id": entry_plan.strategy_id,
            "strategy_version": entry_plan.strategy_version,
            "strategy_config_fingerprint": entry_plan.strategy_config_fingerprint,
            "entry_plan_fingerprint": entry_plan.entry_plan_fingerprint,
        },
        exit_plan={
            "strategy_id": exit_plan.strategy_id,
            "strategy_version": exit_plan.strategy_version,
            "strategy_config_fingerprint": exit_plan.strategy_config_fingerprint,
            "exit_plan_fingerprint": exit_plan.exit_plan_fingerprint,
            "protection_policy": exit_plan.protection_policy.to_dict(),
        },
        activation={
            "activation_id": compatibility.activation.activation_id,
            "enabled": True,
            "strategy_id": compatibility.activation.strategy_id,
            "strategy_version": compatibility.activation.strategy_version,
            "strategy_config_fingerprint": compatibility.activation.strategy_config_fingerprint,
        },
    )
    with pytest.raises(ExecutionBridgeBlocked) as caught:
        prepare_runtime_entry_command(request, bundle, now=NOW)
    assert caught.value.code is ExecutionBridgeBlockCode.POLICY_INCOMPLETE


def test_engine_execution_request_carries_exact_signal_fact_snapshot_source() -> None:
    source = (ROOT / "src/bybit_workbench/universal_entry/engine.py").read_text(encoding="utf-8")
    assert '"signal_fact"' in source
    assert '"fact_id": signal_fact.fact_id' in source
    assert '"attributes": signal_fact.attributes.to_dict()' in source


def test_dormant_consumer_requires_two_explicit_activation_switches() -> None:
    source = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(
        encoding="utf-8"
    )
    assert 'CRIPTA_ENTRY_COMMAND_SOURCE", "LEGACY_V1"' in source
    assert 'CRIPTA_UNIVERSAL_ENTRY_MAINNET_CONSUMER", "DISABLED"' in source
    assert 'ENTRY_COMMAND_SOURCE != "UNIVERSAL_ENTRY" or CONSUMER_ARM != "ENABLED"' in source
    assert "runtime.trade_commands" in source


def test_private_runtime_default_source_and_universal_fill_lineage() -> None:
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert 'CRIPTA_ENTRY_COMMAND_SOURCE", "LEGACY_V1"' in source
    assert 'if ENTRY_COMMAND_SOURCE == "LEGACY_V1"' in source
    assert "strategy_entry.execution_dispatches" in source
    assert 'owner_bot = "universal-entry"' in source
    assert 'geometry_handoff_id = None' in source


def test_dispatch_storage_is_append_only_and_has_exact_lineage() -> None:
    sql = (ROOT / "operations/sql/20260912_universal_entry_dormant_consumer.sql").read_text(
        encoding="utf-8"
    )
    for token in (
        "execution_request_id text NOT NULL UNIQUE",
        "strategy_attempt_id text NOT NULL",
        "entry_decision_id text NOT NULL",
        "signal_id text NOT NULL",
        "entry_plan_fingerprint text NOT NULL",
        "exit_plan_fingerprint text",
        "execution_dispatches_immutable",
        "REVOKE UPDATE, DELETE",
    ):
        assert token in sql


def test_systemd_template_is_hard_disabled_by_default() -> None:
    unit = (ROOT / "operations/systemd/cripta-universal-entry-consumer.service").read_text(
        encoding="utf-8"
    )
    assert "CRIPTA_ENTRY_COMMAND_SOURCE=LEGACY_V1" in unit
    assert "CRIPTA_UNIVERSAL_ENTRY_MAINNET_CONSUMER=DISABLED" in unit
    assert "Restart=on-failure" in unit
