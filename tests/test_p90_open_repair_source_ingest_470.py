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
    _scoped_p90_repair_ingest_allowed,
)

SOURCE = Path("operations/monitoring/universal_entry_shadow.py")


def test_disarmed_unambiguous_p90_open_repair_can_ingest_new_ws_facts() -> None:
    assert _scoped_p90_repair_ingest_allowed(
        degraded=True,
        mainnet_gate_enabled=False,
        selected_real_activations=frozenset(),
        ordinary_consumer_count=0,
        paper_consumer_count=0,
    )


@pytest.mark.parametrize("changed", (
    {"degraded": False},
    {"mainnet_gate_enabled": True},
    {"selected_real_activations": frozenset({"p90-main"})},
    {"ordinary_consumer_count": 1},
    {"paper_consumer_count": 1},
))
def test_repair_source_only_refuses_financial_or_mixed_consumer_scope(
    changed: dict[str, object],
) -> None:
    args: dict[str, object] = {
        "degraded": True,
        "mainnet_gate_enabled": False,
        "selected_real_activations": frozenset(),
        "ordinary_consumer_count": 0,
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
    assert body.index("if p90_repair_ingest_only:") < body.index(
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
    assert '"SOURCE_REPAIR_ONLY" if p90_repair_ingest_only' in runtime
    assert "P90_OPEN_EXIT_BLOCKED_MARKET_SOURCE_INGEST_ONLY" in runtime
    assert "active_bundles=() if p90_repair_ingest_only else bundles" in runtime
    assert "P90RealSourceCompatibilityUnverified" in runtime
    assert "P90RealOpenRestartUnverified" in runtime
    assert "p90_real_live = False" in runtime
