from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
PRIVATE_PATH = ROOT / "operations/connectivity/private_runtime.py"
PRIVATE = PRIVATE_PATH.read_text(encoding="utf-8")
UNIVERSAL_ENGINE = (
    ROOT / "src/bybit_workbench/universal_entry/engine.py"
).read_text(encoding="utf-8")
UNIVERSAL_BRIDGE = (
    ROOT / "src/bybit_workbench/universal_entry/execution_bridge.py"
).read_text(encoding="utf-8")
UNIVERSAL_CONSUMER = (
    ROOT / "operations/connectivity/universal_entry_consumer.py"
).read_text(encoding="utf-8")
UNIVERSAL_STORAGE = (
    ROOT / "src/bybit_workbench/universal_entry/storage.py"
).read_text(encoding="utf-8")
PROTECTION = (ROOT / "operations/connectivity/protection_math.py").read_text(encoding="utf-8")
EXIT_RUNTIME = (ROOT / "operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
RUNTIME_SCHEMA = (ROOT / "operations/connectivity/runtime_schema.py").read_text(
    encoding="utf-8"
)
MIGRATION = (ROOT / "operations/sql/20260901_observed_entry_context.sql").read_text(
    encoding="utf-8"
)


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(PRIVATE)
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_dispatcher_context_api_does_not_return_boolean_permission() -> None:
    function = _function("observe_m3_entry_context")
    assert isinstance(function.returns, ast.Subscript)
    assert isinstance(function.returns.value, ast.Name)
    assert function.returns.value.id == "dict"
    assert "return allowed" not in ast.get_source_segment(PRIVATE, function)


def test_observe_context_cannot_suppress_entry_without_strategy_decision_mode() -> None:
    assert "_DECISION_MODES = {ContextMode.CONDITION, ContextMode.RANKING}" in UNIVERSAL_ENGINE
    assert "if requirement.mode in _DECISION_MODES:" in UNIVERSAL_ENGINE
    assert (
        "is_consumed = requirement.mode in _DECISION_MODES and status == \"FRESH\""
        in UNIVERSAL_ENGINE
    )
    assert "ContextMode.OBSERVE" not in UNIVERSAL_ENGINE.split("_DECISION_MODES =", 1)[1].split(
        "\n", 1
    )[0]


def test_context_only_semantics_are_explicit_and_backward_compatible() -> None:
    consumer = ast.get_source_segment(PRIVATE, _function("observe_m3_entry_context"))
    assert '"decision": "OBSERVED"' in consumer
    assert '"trading_effect": "NONE"' in consumer
    assert "'OBSERVED_CONTEXT','NONE'" in consumer
    assert "'CONSUMED_CONTEXT', 'OBSERVED_CONTEXT'" in MIGRATION
    assert "UPDATE runtime.m3_consumed_context" not in MIGRATION


def test_missing_or_stale_dispatcher_context_is_observed_not_blocked() -> None:
    consumer = ast.get_source_segment(PRIVATE, _function("observe_m3_entry_context"))
    assert 'status = "NO_CONTEXT"' in consumer
    assert 'freshness = "MISSING"' in consumer
    assert 'freshness = "FRESH" if 0 <= age_seconds <= 90 else "STALE"' in consumer
    assert '"decision": "OBSERVED"' in consumer


def test_dispatcher_or_mayak_cannot_create_trade_commands() -> None:
    forbidden = (
        ROOT / "operations/monitoring/mayak_v2.py",
        ROOT / "operations/strategy_dispatcher/passive_runtime.py",
        ROOT / "production/src/bybit_workbench/strategy_dispatcher/service.py",
    )
    for path in forbidden:
        source = path.read_text(encoding="utf-8")
        assert "INSERT INTO runtime.trade_commands" not in source
        assert "UPDATE runtime.trade_commands" not in source


def test_legacy_market_policy_cannot_restore_advisory_entry_veto() -> None:
    worker = ast.get_source_segment(PRIVATE, _function("command_worker_loop"))
    assert "market_guard_v1" not in worker
    assert "mayak_v2.snapshots" not in worker
    assert "up_share" not in worker
    assert "down_share" not in worker


def test_observed_context_schema_preserves_historical_consumed_rows() -> None:
    assert "OBSERVED_CONTEXT" in MIGRATION
    assert "CONSUMED_CONTEXT" in MIGRATION
    assert "UPDATE runtime.m3_consumed_context" not in MIGRATION
    assert "analytics.entry_advisory_contexts" in MIGRATION


def test_universal_entry_fact_and_admission_gates_remain_fail_closed() -> None:
    assert "if not _execution_gate_enabled(connection):" in UNIVERSAL_CONSUMER
    assert "request.exchange_position_slot_claim_id" in UNIVERSAL_CONSUMER
    assert "request.position_mode_state_ref" in UNIVERSAL_CONSUMER
    assert "request.strategy_execution_binding_id" in UNIVERSAL_CONSUMER
    assert "STRATEGY_EXECUTION_BINDING_NOT_READY" in UNIVERSAL_CONSUMER
    assert "ExecutionRequest.signal_fact" in UNIVERSAL_BRIDGE
    assert "execution reference_value_path must start with fact." in UNIVERSAL_BRIDGE


def test_causal_signal_fact_is_frozen_before_execution_request_insert() -> None:
    assert '"signal_fact": {' in UNIVERSAL_ENGINE
    assert '"source_refs": signal_fact.source_refs' in UNIVERSAL_ENGINE
    assert '"attributes": signal_fact.attributes.to_dict()' in UNIVERSAL_ENGINE
    assert "execution_requests(" in UNIVERSAL_STORAGE
    assert "payload" in UNIVERSAL_STORAGE
    assert "'PROVEN'" in UNIVERSAL_STORAGE


def test_v36_strategy_owns_initial_protection_without_retuning_other_risk() -> None:
    assert 'entry * Decimal("0.99")' not in PROTECTION
    assert 'entry * Decimal("1.01")' not in PROTECTION
    assert 'take_profit_pct: Decimal = Decimal("3.00")' not in PROTECTION
    assert "stop_loss_pct: Decimal," in PROTECTION
    assert "take_profit_pct: Decimal," in PROTECTION
    assert 'trailing_pct = Decimal(str(settings[8] or "0.30"))' not in PRIVATE
    assert 'trailing_pct = Decimal(str(settings[2] or "0.30"))' in EXIT_RUNTIME
    assert '("runtime", "trade_settings", "trailing_distance_pct")' in RUNTIME_SCHEMA
    assert "entry_limit_ttl_seconds" in PRIVATE
    assert "entry_offset_pct" in PRIVATE


def test_initial_entry_decision_remains_immutable() -> None:
    decision = ast.get_source_segment(PRIVATE, _function("record_entry_decision"))
    assert "ON CONFLICT(signal_id) DO NOTHING" in decision
    assert "DO UPDATE SET" not in decision
