from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
PRIVATE_PATH = ROOT / "operations/connectivity/private_runtime.py"
PRIVATE = PRIVATE_PATH.read_text(encoding="utf-8")
PROTECTION = (ROOT / "operations/connectivity/protection_math.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "operations/sql/20260901_dispatcher_context_only.sql").read_text(
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
    function = _function("consume_m3_entry_context")
    assert isinstance(function.returns, ast.Subscript)
    assert isinstance(function.returns.value, ast.Name)
    assert function.returns.value.id == "dict"
    assert "return allowed" not in ast.get_source_segment(PRIVATE, function)


def test_incompatible_context_cannot_suppress_entry_command() -> None:
    worker = ast.get_source_segment(PRIVATE, _function("command_worker_loop"))
    assert "context_allowed" not in worker
    assert "if not context_allowed" not in worker
    assert "consumed_context = consume_m3_entry_context" in worker
    assert "runtime.trade_commands" in worker


def test_context_only_semantics_are_explicit_and_backward_compatible() -> None:
    consumer = ast.get_source_segment(PRIVATE, _function("consume_m3_entry_context"))
    assert '"decision": "OBSERVED"' in consumer
    assert '"trading_effect": "NONE"' in consumer
    assert "'CONSUMED_CONTEXT','NONE'" in consumer
    assert "'FULL_LIVE_V1', 'NONE', 'CONTEXT_ONLY'" in MIGRATION
    assert "UPDATE runtime.m3_consumed_context" not in MIGRATION


def test_missing_or_stale_dispatcher_context_is_observed_not_blocked() -> None:
    consumer = ast.get_source_segment(PRIVATE, _function("consume_m3_entry_context"))
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


def test_existing_entry_geometry_and_safety_gates_remain_fail_closed() -> None:
    worker = ast.get_source_segment(PRIVATE, _function("command_worker_loop"))
    assert "нет причинной неизменяемой геометрии Entry" in worker
    assert "if not gate_enabled" in worker
    assert "по монете уже есть позиция, заявка или команда" in worker


def test_geometry_jsonb_is_adapted_before_binding_insert() -> None:
    worker = ast.get_source_segment(PRIVATE, _function("command_worker_loop"))
    assert "json.dumps(geometry[3], ensure_ascii=False, default=str)" in worker


def test_entry_and_risk_settings_are_not_changed_by_p0() -> None:
    assert 'entry * Decimal("0.99")' in PROTECTION
    assert 'entry * Decimal("1.01")' in PROTECTION
    assert 'take_profit_pct: Decimal = Decimal("3.00")' in PROTECTION
    assert 'trailing_distance_pct TEXT NOT NULL DEFAULT \'0.30\'' in PRIVATE
    assert "entry_limit_ttl_seconds" in PRIVATE
    assert "entry_offset_pct" in PRIVATE


def test_initial_entry_decision_remains_immutable() -> None:
    decision = ast.get_source_segment(PRIVATE, _function("record_entry_decision"))
    assert "ON CONFLICT(signal_id) DO NOTHING" in decision
    assert "DO UPDATE SET" not in decision
