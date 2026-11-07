"""P90 OPEN recovery failure must not kill independent public source ingest.

A read/append-only fallback is permitted only for the exact disarmed,
single-owner situation; it is NOT a substitute for Exchange protection or
Exit/MFE proof, and never establishes Observer READY.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from operations.monitoring.universal_entry_shadow import (
    _p90_native_recovery_failure,
    _scoped_p90_repair_ingest_allowed,
)

SOURCE = Path("operations/monitoring/universal_entry_shadow.py")


def test_disarmed_unambiguous_p90_open_repair_can_ingest_new_ws_facts() -> None:
    assert _scoped_p90_repair_ingest_allowed(
        degraded=True,
        mainnet_gate_enabled=False,
        selected_real_activations=frozenset(),
        paper_consumer_count=0,
    )


@pytest.mark.parametrize("changed", (
    {"degraded": False},
    {"mainnet_gate_enabled": True},
    {"selected_real_activations": frozenset({"p90-main"})},
    {"paper_consumer_count": 1},
))
def test_repair_source_only_refuses_financial_or_mixed_consumer_scope(
    changed: dict[str, object],
) -> None:
    args: dict[str, object] = {
        "degraded": True,
        "mainnet_gate_enabled": False,
        "selected_real_activations": frozenset(),
        "paper_consumer_count": 0,
    }
    args.update(changed)
    assert not _scoped_p90_repair_ingest_allowed(**args)  # type: ignore[arg-type]


def test_repair_ingest_is_after_journal_but_before_any_entry_exit_consumer() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    process = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.FunctionDef) and node.name == "_process_fact_body"
    )
    body = ast.get_source_segment(source, process)
    assert body is not None
    assert body.index(
        "if p90_repair_ingest_only and not observer_split.ordinary:"
    ) < body.index(
        "refresh_inputs(fact.observed_at)"
    )
    # Native append belongs to the existing MarketSource source transaction,
    # never a second producer or a P90 source checkpoint spoof.
    dispatch = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.FunctionDef) and node.name == "process_fact"
    )
    dispatch_body = ast.get_source_segment(source, dispatch)
    assert dispatch_body is not None
    assert "NativePublicTradeJournal(connection).append(" in dispatch_body
    assert "with connection.transaction():" in dispatch_body
    assert "dispatch_observer_fact_atomically(" in dispatch_body


def test_open_position_source_repair_cannot_be_reported_as_financial_ready() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    status = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_observer_status"
    )
    body = ast.get_source_segment(source, status)
    assert body is not None
    assert '"observer_ready": state in {"IDLE", "WARMUP", "RUNNING", "RELOADING"}' in body
    assert "SOURCE_REPAIR_ONLY" not in body

    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    runtime = ast.get_source_segment(source, epoch)
    assert runtime is not None
    assert '"SOURCE_REPAIR_ONLY"' in runtime
    assert "if p90_repair_ingest_only and not observer_split.ordinary" in runtime
    assert "P90_OPEN_EXIT_BLOCKED_MARKET_SOURCE_INGEST_ONLY" in runtime
    assert "active_bundles=active_source_consumers" in runtime
    assert "P90_OPEN_EXIT_BLOCKED_ORDINARY_CONSUMERS_ACTIVE" in runtime
    assert "tuple(observer_split.ordinary) if p90_repair_ingest_only else bundles" in runtime
    # Recovery exception classification is intentionally centralized in
    # _p90_native_recovery_failure(), not duplicated inside this epoch.
    # Old-epoch P90 failure must escape to the one outer durable fault
    # handler, which restarts a *fresh* source-only WS session.
    assert "preflight_restart_epoch_id = inspect_p90_real_open_restart_epoch(" in runtime
    assert "_record_observer_runtime_fault(" not in runtime
    assert "p90_real_live = False" in runtime


def test_only_exact_p90_recovery_failures_can_trigger_repair_source_restart() -> None:
    from bybit_workbench.universal_entry.p90l90_real_bounded_native_restart import (
        P90RealNativeRestartUnverified,
    )
    from bybit_workbench.universal_entry.p90l90_real_open_restart import (
        P90RealOpenRestartUnverified,
    )
    from bybit_workbench.universal_entry.p90l90_real_restart_recovery import (
        P90RealOpenSourceRecoveryUnverified,
    )
    from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (
        P90RealSourceCompatibilityUnverified,
    )
    from bybit_workbench.universal_exit.p90l90_real_mfe import (
        P90RealMfeUnverified,
    )

    for error in (
        P90RealOpenRestartUnverified("old source"),
        P90RealOpenSourceRecoveryUnverified("old native gap"),
        P90RealNativeRestartUnverified("missing original seq"),
        P90RealSourceCompatibilityUnverified("identity"),
        P90RealMfeUnverified("missing current MFE"),
    ):
        assert _p90_native_recovery_failure(error)

    for error in (
        RuntimeError("Unknown Exchange mutation"),
        ValueError("source integrity conflict"),
        ConnectionError("public WS unavailable"),
    ):
        assert not _p90_native_recovery_failure(error)


def test_late_p90_recovery_error_restarts_only_pure_source_rail() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    runner = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_multi_strategy_observer"
    )
    body = ast.get_source_segment(source, runner)
    assert body is not None
    assert "source_repair_only=force_p90_source_repair" in body
    assert "if _p90_native_recovery_failure(exc):" in body
    assert "force_p90_source_repair = True" in body
    assert "SOURCE_REPAIR_PREPARING" in body
    assert "fault_id = _record_observer_runtime_fault(" in body
    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_observer_epoch"
    )
    text = ast.get_source_segment(source, epoch)
    assert text is not None
    assert "source_repair_only: bool = False" in text
    assert "p90_real_force_source_only" in text
    assert "P90_SCOPED_SOURCE_REPAIR_FINANCIAL_SCOPE_INVALID" in text
    assert "if source_repair_only:" in text
    assert "if p90_repair_ingest_only:" in text
    assert "tuple(sorted(ordinary_symbols)) if p90_repair_ingest_only else symbols" in text
    assert "if not p90_repair_ingest_only or observer_split.ordinary:" in text
    assert "and not p90_repair_ingest_only" in text
    assert "for bundle in cast(Any, active_source_consumers)" in text


def test_scoped_p90_fault_keeps_unrelated_ordinary_consumer_ownership() -> None:
    # Ordinary strategy cardinality is intentionally NOT an exclusion gate.
    # Its EntryPlan/geometry/OI remains owned by the original Engine. Only
    # native P90 PAPER cannot be disconnected through this specific route.
    assert _scoped_p90_repair_ingest_allowed(
        degraded=True,
        mainnet_gate_enabled=False,
        selected_real_activations=frozenset(),
        paper_consumer_count=0,
    )
    source = SOURCE.read_text(encoding="utf-8")
    epoch = next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_observer_epoch"
    )
    body = ast.get_source_segment(source, epoch)
    assert body is not None
    assert "if p90_repair_ingest_only and not observer_split.ordinary:" in body
    assert "engine.process(" in body
    assert "p90_consumer = runtime.consumer" in body  # normal REAL path only
    assert "if p90_real_source_only or p90_real_live:" in body
    assert '"SOURCE_REPAIR_ONLY"' in body
    assert "active_bundles=active_source_consumers" in body



def test_source_repair_oi_config_never_constructs_an_empty_symbol_set() -> None:
    """Evaluate the real OI argument for pure ingest and mixed consumers."""
    from bybit_workbench.universal_entry.oi30s_source import Oi30sConfig

    source = SOURCE.read_text(encoding="utf-8")
    epoch = next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_observer_epoch"
    )
    config = next(
        n for n in ast.walk(epoch)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "oi_config" for t in n.targets)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "Oi30sConfig"
    )
    required = next(
        keyword.value for keyword in config.value.keywords
        if keyword.arg == "required_symbols"
    )
    expr = compile(ast.Expression(required), str(SOURCE), "eval")
    for repairing, ordinary, expected in (
        (False, set(), ("APTUSDT", "BTCUSDT")),
        (True, set(), ("APTUSDT", "BTCUSDT")),
        (True, {"BTCUSDT"}, ("BTCUSDT",)),
    ):
        result = eval(
            expr,
            {"tuple": tuple, "sorted": sorted},
            {
                "p90_repair_ingest_only": repairing,
                "ordinary_symbols": ordinary,
                "symbols": ("APTUSDT", "BTCUSDT"),
            },
        )
        assert result == expected
        assert Oi30sConfig(
            fact_source_id="BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1",
            required_symbols=result,
        ).required_symbols == expected
