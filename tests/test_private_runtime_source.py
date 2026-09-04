from __future__ import annotations

import ast
from pathlib import Path


def test_command_loop_does_not_overwrite_exchange_credentials() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_loop = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "command_worker_loop"
    )
    assigned_names = {
        target.id
        for node in ast.walk(command_loop)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    assert "key" not in assigned_names
    assert "secret" not in assigned_names


def test_command_worker_keeps_operational_safety_without_market_hard_gate() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "market_guard_v1" not in source
    assert "mayak_v2.snapshots" not in source
    assert 'up_share < Decimal("0.55")' not in source
    assert 'down_share < Decimal("0.55")' not in source
    assert "по монете уже есть позиция, заявка или команда" in source
    assert "if not gate_enabled" in source
    assert '"теневой допуск"' in source
    assert 'runtime.entry_decisions' in source
    for symbol in ("1000PEPEUSDT", "DOGEUSDT", "NEARUSDT", "XLMUSDT"):
        assert symbol in source


def test_m3_full_live_consumes_only_causal_dispatcher_context() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert 'configured_entry_policy = str(settings[9] or "base_entry_v1")' in source
    assert "observe_m3_entry_context" in source
    assert "observed_at<=%s" in source
    assert "1.0.0-owner-live" in source
    assert "OBSERVED_CONTEXT" in source
    assert '"trading_effect": "NONE"' in source
    assert "NO_CONTEXT" in source
    assert "context_allowed" not in source


def test_command_loop_restarts_after_internal_failure() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_loop = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "command_loop"
    )
    assert any(isinstance(node, ast.Try) for node in ast.walk(command_loop))
    assert "command_worker_loop(key, secret)" in source


def test_exit_trailing_does_not_depend_on_automatic_break_even_toggle() -> None:
    private = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    exit_runtime = Path("operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert "auto-be-" not in private
    assert "auto-trail-" not in private
    assert "if auto_profit:" in exit_runtime
    assert "if auto_trailing:" in exit_runtime
    assert "required_stop = protection_plan(" in exit_runtime


def test_exit_automation_uses_stable_position_owner_across_partial_fills() -> None:
    private = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    exit_runtime = Path("operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert "ON CONFLICT(entry_command_id) DO UPDATE SET" in private
    assert "FROM runtime.position_ownership o" in exit_runtime
    assert "JOIN runtime.hot_positions p" in exit_runtime
    assert "p.position_idx=o.position_idx" in exit_runtime
    assert "p.side=o.side" in exit_runtime
    assert "p.size=o.actual_qty" not in exit_runtime
    assert "p.entry_price=o.actual_avg_fill" not in exit_runtime
    assert "o.state='OPEN' AND o.close_link_status='OPEN'" in exit_runtime


def test_trailing_stop_is_idempotent_for_current_position() -> None:
    private = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    exit_runtime = Path("operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert '"not modified" not in str(exc).lower()' in private
    assert "already_enabled = connection.execute" in exit_runtime
    assert "requested_at_epoch_ms >= %s" in exit_runtime


def test_position_close_reconciliation_uses_exchange_inventory_not_nearest_time() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "resolve_exchange_position_close" in source
    assert '"/v5/order/history"' in source
    assert "upsert_exchange_order_history" in source
    assert "UNRESOLVED_EXACT_LINK" in source
    assert "nearest" not in source.lower()


def test_current_bybit_position_matches_latest_stable_owned_cycle() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "latest_by_key" in source
    assert 'and str(current.get("side") or "") == side' in source
    assert 'and Decimal(str(current.get("size") or 0)) == Decimal(str(row[5]))' not in source
    assert 'and Decimal(str(current.get("avgPrice") or 0)) == Decimal(str(row[4]))' not in source


def test_exchange_flat_position_is_closed_even_when_exit_link_is_unresolved() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "WHERE state='OPEN' OR close_link_status='UNRESOLVED_EXACT_LINK'" in source
    unresolved_branch = source.split(
        'if close.status != "EXACT" or close.exit_order_id is None:', 1
    )[1].split("protection_rows =", 1)[0]
    assert "SET state='CLOSED'" in unresolved_branch
    assert "close_link_status='UNRESOLVED_EXACT_LINK'" in unresolved_branch


def test_live_dashboard_request_does_not_run_schema_ddl() -> None:
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    live_state = source.split("def live_trading_state()", 1)[1].split("def ", 1)[0]
    assert "ALTER TABLE" not in live_state
    assert "CREATE TABLE" not in live_state
