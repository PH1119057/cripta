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


def test_trailing_stop_does_not_depend_on_automatic_break_even_toggle() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "protection_entries = owned_entries if settings and settings[6] else []" in source
    assert "for entry_id, symbol in owned_entries:" in source
    assert 'required_protection = protection_plan(' in source


def test_post_fill_automation_uses_only_current_exact_position_owner() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "JOIN runtime.hot_positions p" in source
    assert "p.position_idx=o.position_idx" in source
    assert "p.size=o.actual_qty" in source
    assert "p.entry_price=o.actual_avg_fill" in source
    assert "o.state='OPEN' AND o.close_link_status='OPEN'" in source


def test_trailing_stop_is_idempotent_for_current_position() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert '"not modified" not in str(exc).lower()' in source
    assert "already_enabled = connection.execute" in source
    assert "requested_at_epoch_ms >= %s" in source


def test_position_close_reconciliation_uses_exchange_inventory_not_nearest_time() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "resolve_exchange_position_close" in source
    assert '"/v5/order/history"' in source
    assert "upsert_exchange_order_history" in source
    assert "UNRESOLVED_EXACT_LINK" in source
    assert "nearest" not in source.lower()


def test_current_bybit_position_matches_only_latest_exact_owned_cycle() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "latest_by_key" in source
    assert 'current.get("size")' in source
    assert 'current.get("avgPrice")' in source


def test_exchange_flat_position_is_closed_even_when_exit_link_is_unresolved() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "WHERE state='OPEN' OR close_link_status='UNRESOLVED_EXACT_LINK'" in source
    unresolved_branch = source.split(
        'if close.status != "EXACT" or close.exit_order_id is None:', 1
    )[1].split("protection_rows =", 1)[0]
    assert "SET state='CLOSED'" in unresolved_branch
    assert "close_link_status='UNRESOLVED_EXACT_LINK'" in unresolved_branch
