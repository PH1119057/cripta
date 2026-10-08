from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_closing_sounds_have_persistent_exact_cycle_keys():
    source=(ROOT/"operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "cripta-seen-closed-cycles-v1" in source
    assert "x.cycle_key" in source
    assert "seenCycleSet.has(key)" in source
    assert "storedCycleKeys!==null||legacyCloseCursor>0" in source
    assert "mergedCycles.slice(-2000)" in source


def test_dashboard_closed_table_reads_operator_ledger_and_does_not_assign_strategy():
    app=(ROOT/"operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "recovered_closed_cycles(recovered_closed_rows)" in app
    assert "owner_controlled_closed_cycles(control_entry_rows, control_exit_rows)" in app
    assert "merge_evidenced_closed_trades(" in app
    assert "strategy_id" not in app[app.index("recovered_closed_rows = connection.execute"):app.index("control_entry_rows = connection.execute")]
