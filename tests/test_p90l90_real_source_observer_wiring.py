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
    assert 'if getattr(self, "_no_order_source_diagnostic", False):' in body
    assert body.index('if getattr(self, "_no_order_source_diagnostic", False):') < (
        body.index("owner = self._candidate_owners.get(fact.symbol)")
    )


def test_real_live_path_is_connected_to_independent_source_and_private_exit() -> None:
    src = Path("operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_observer_epoch"
    )
    body = ast.get_source_segment(src, epoch)
    assert body is not None
    # A real activation alone cannot bypass both owner-selected execution
    # permissions or the explicitly enabled mainnet gate.
    assert "p90_real_live = False" in body
    assert "elif not gate_enabled and not selected:" in body
    assert "gate_reason == OWNER_ARM_STAGE_GATE_REASON" in body
    assert "gate_reason == OWNER_ARM_OPEN_GATE_REASON" in body
    assert "selected == expected" in body
    assert "gate_enabled = bool(gate and gate[0])" in body
    assert "p90_real_staged = True" in body
    assert "P90L90_REAL_NATIVE_EXECUTION_OWNER_NOT_CONNECTED" in body
    assert "if p90_real_source_only or p90_real_live:" in body
    assert body.index("_connect_and_subscribe(symbols, intervals)") < (
        body.index("start_current_p90_real_observer_epoch(")
    )
    # The production rail must never reuse a silent PAPER/native source flag,
    # and must instantiate signed MAIN/SUB mutation safety and original cursor
    # coverage before constructing its REAL consumer.
    assert "read_p90_real_account_routes(" in body
    assert "P90RealLiveSourcePorts(" in body
    assert "P90RealPrivateEvidenceRouter(" in body
    assert "P90RealIndependentMutationSafety(" in body
    assert "verified_native_coverage=source_ports.native_coverage" in body
    assert "verified_closed_history=source_ports.closed_history" in body
    assert "verified_closed_continuity=source_ports.closed_continuity" in body
    assert "verified_account_mutation_safety=safety" in body
    assert "assemble_p90_real_observer_runtime(" in body
    assert "source_ports._source_transaction()" in body
    assert "p90_consumer = runtime.consumer" in body
    assert "p90_consumer.recover_all()" in body
    # The running source broker still owns original publicTrades, including
    # the strict independent recentTrade audit when no after-fill tick exists.
    assert 'fetch_recent_trades": _fetch_recent_trade_snapshot' in body
    assert "buffered_messages = (" in body



def test_real_closed_candle_history_reads_same_ledger_advanced_by_consumer() -> None:
    """Two ledger instances silently strand 5m Exit on the bootstrap watermark."""
    src = Path(
        "src/bybit_workbench/universal_entry/p90l90_observer_real_consumer.py"
    ).read_text(encoding="utf-8")
    runtime = Path(
        "src/bybit_workbench/universal_entry/p90l90_real_observer_runtime.py"
    ).read_text(encoding="utf-8")
    observer = Path("operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    source = Path(
        "operations/monitoring/p90l90_real_live_source_ports.py"
    ).read_text(encoding="utf-8")
    assert "shared_candle_ledger: P90RealSharedCandleLedger | None" in src
    assert "shared_candle_ledger._epoch_id != source_epoch_id" in src
    assert "shared_candle_ledger._db is not connection" in src
    assert "self._archive = (" in src
    assert "shared_candle_ledger=shared_candle_ledger" in runtime
    assert "ledger=shared_p90_real_ledger" in observer
    assert "shared_candle_ledger=shared_p90_real_ledger" in observer
    assert "self._ledger.history(" in source
    assert "self._ledger.restart_watermarks(symbol)" in source


def test_real_observer_unit_can_import_runtime_operations_namespace() -> None:
    unit = Path(
        "operations/systemd/cripta-universal-entry-observer.service"
    ).read_text(encoding="utf-8")
    line = next(
        row for row in unit.splitlines()
        if row.startswith("Environment=PYTHONPATH=")
    )
    paths = line.split("=", 2)[2].split(":")
    assert "/srv/cripta/runtime/current/src" in paths
    assert "/srv/cripta/runtime/current/.venv/lib/python3.12/site-packages" in paths
    assert "/srv/cripta/runtime/current" in paths

    observer = Path(
        "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    assert "from operations.monitoring.p90l90_real_source_bootstrap import" in observer
    assert "from operations.monitoring.p90l90_real_account_mutation_safety import" in observer
    assert "from operations.monitoring.p90l90_real_live_source_ports import" in observer
    assert "from operations.monitoring.p90l90_real_postfill_source_broker import" in observer
