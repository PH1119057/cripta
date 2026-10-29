"""No-order source-level regression for immediate P90 v3 first-fill handoff."""
from __future__ import annotations

import ast
from pathlib import Path


def test_private_worker_handoffs_proven_first_fill_before_command_completion():
    runtime = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    node = next(
        x for x in ast.parse(runtime).body
        if isinstance(x, ast.FunctionDef) and x.name == "execute_command"
    )
    body = ast.get_source_segment(runtime, node)
    assert body is not None
    assert "p90_real_postfill: tuple[str, str, tuple[str, ...]] | None = None" in body
    assert "p90_real_postfill = (" in body
    assert "handoff_real_first_protected_fill(" in body
    assert 'if p90_real_postfill is not None:' in body
    assert body.index('reconcile(connection, key, secret, "after_command")') < (
        body.index("handoff_real_first_protected_fill(")
    )
    assert body.index("handoff_real_first_protected_fill(") < (
        body.index("record_protection_or_owner_event(")
    )
    assert body.index("handoff_real_first_protected_fill(") < (
        body.index("SET state='completed',finished_at_epoch_ms")
    )
    assert "P90_REAL_FIRST_FILL_NATIVE_EXIT_HANDOFF_UNVERIFIED" in body
    assert "signed_position_reader=(" in body
    assert "current_private_ws=current_p90_real_private_ws_epoch" in body
    assert "no_pending_exit_command=(" in body


def test_source_broker_is_bound_to_live_observer_not_private_credentials():
    runtime = Path("operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    node = next(
        x for x in ast.parse(runtime).body
        if isinstance(x, ast.FunctionDef) and x.name == "_run_observer_epoch"
    )
    body = ast.get_source_segment(runtime, node)
    assert body is not None
    assert "target=serve_source_windows" in body
    assert "real_source_broker_thread.start()" in body
    assert "source_broker_ready.wait(timeout=3.0)" in body
    assert "read_exact_source_position(" in body
    assert "real_source_broker_stop.set()" in body
    assert "P90_REAL_POSTFILL_SOURCE_BROKER_NOT_BOUND" in body


def test_first_fill_source_helpers_contain_no_direct_bybit_mutation():
    source = Path(
        "operations/monitoring/p90l90_real_postfill_source_broker.py"
    ).read_text(encoding="utf-8")
    client = Path(
        "operations/connectivity/p90l90_real_postfill_source_client.py"
    ).read_text(encoding="utf-8")
    helper = Path(
        "operations/connectivity/p90l90_real_postfill_handoff.py"
    ).read_text(encoding="utf-8")
    assert "api_post(" not in source + client + helper
    assert "POST /v5/" not in source + client + helper
    assert "P90RealPostfillExitPort(" in helper
    assert "P90RealExitExecutionPort(" in helper
    assert "read_real_postfill_source_window(" in helper



def test_generic_reconcile_does_not_erase_signed_first_fill_order_execution_ids():
    """REAL postfill reads original signed IDs after generic account readback."""
    src = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    module = ast.parse(src)
    reconcile_fn = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "reconcile"
    )
    body = ast.get_source_segment(src, reconcile_fn)
    assert body is not None
    assert "initial_protection_evidence=" in body
    preserve = body.index("WHEN strategy_version=%s")
    signed = body.index("='SIGNED_BYBIT_MARKET_FILL_AND_POSITION'", preserve)
    keep = body.index("THEN initial_protection_evidence", signed)
    fallback = body.index("THEN %s::jsonb", keep)
    assert preserve < signed < keep < fallback
    assert '"2.0-p90l90-real-main-sub-20261028"' in body
    # A signed opening order is NOT the same as generic position-state
    # reconciliation; general account reads cannot rewrite its exact IDs.
    assert '"source": "BYBIT_POSITION_RECONCILIATION"' in body
