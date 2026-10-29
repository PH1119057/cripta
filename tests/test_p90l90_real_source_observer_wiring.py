"""Integration contract: no-order REAL source reaches live Observer epoch."""
from __future__ import annotations

import ast
from pathlib import Path


def test_real_source_is_physically_bootstrapped_after_live_ws_and_mirror() -> None:
    src = Path("operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    epoch = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_observer_epoch"
    )
    body = ast.get_source_segment(src, epoch)
    assert body is not None
    assert 'os.environ.get("CRIPTA_P90_REAL_SOURCE_ONLY", "DISABLED")' in body
    assert "P90L90_REAL_NATIVE_EXECUTION_OWNER_NOT_CONNECTED" in body
    assert body.index("_connect_and_subscribe(symbols, intervals)") < (
        body.index("start_current_p90_real_observer_epoch(")
    )
    assert "source_commit=LOADED_COMMIT" in body
    assert "mirror=trade_mirror" in body
    assert "no_order_source_diagnostic=True" in body
    assert "require_no_real_exposure" in body
    assert "P90_REAL_SOURCE_ONLY_EXECUTION_GATE_DRIFT" in body


def test_real_source_diagnostic_never_wires_paper_or_execution_commands() -> None:
    src = Path(
        "src/bybit_workbench/universal_entry/p90l90_observer_real_consumer.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    real = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef)
        and n.name == "P90L90ObserverRealConsumer"
    )
    candidate = next(
        x for x in real.body if isinstance(x, ast.FunctionDef)
        and x.name == "_on_candidate"
    )
    body = ast.get_source_segment(src, candidate)
    assert body is not None
    assert "if getattr(self, "_no_order_source_diagnostic", False):" in body
    assert body.index("if getattr(self, "_no_order_source_diagnostic", False):") < (
        body.index("owner = self._candidate_owners.get(fact.symbol)")
    )
