"""P90 REAL Exit has one atomic native owner, never polling fallback."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = (
    ROOT / "operations/connectivity/universal_exit_consumer.py"
).read_text(encoding="utf-8")
PRIVATE = (
    ROOT / "operations/connectivity/private_runtime.py"
).read_text(encoding="utf-8")


def _source(name: str) -> str:
    tree = ast.parse(CONSUMER)
    function = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    result = ast.get_source_segment(CONSUMER, function)
    assert result is not None
    return result


def test_global_exit_decision_materializer_never_steals_p90_real_v3() -> None:
    source = _source("_next_decision")
    assert "d.strategy_version=%s AND d.strategy_id IN (%s,%s)" in source
    assert "NOT (" in source
    assert "(P90_REAL_VERSION, MAIN_ID, SUB_ID)" in source
    assert "NOT EXISTS" in source
    assert "execution_materialization_blocks" in source


def test_global_exit_dispatcher_never_steals_p90_real_v3() -> None:
    source = _source("_next_request")
    assert "r.strategy_version=%s AND r.strategy_id IN (%s,%s)" in source
    assert "NOT (" in source
    assert "(P90_REAL_VERSION, MAIN_ID, SUB_ID)" in source
    assert "r.identity_status='PROVEN'" in source


def test_account_native_execution_capability_remains_in_private_runtime() -> None:
    assert "def _execute_universal_exit_command(" in PRIVATE
    assert "verify_p90_real_exchange_mutation(" in PRIVATE
    assert "if kind == \"strategy_exit\":" in PRIVATE
    assert "P90_REAL_EXIT_SOURCE_TRANSACTION_NOT_OPEN" in (
        ROOT / "src/bybit_workbench/universal_exit/p90l90_real_exit_execution.py"
    ).read_text(encoding="utf-8")
