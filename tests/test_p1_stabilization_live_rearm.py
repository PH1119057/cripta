from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_position_cycle():
    path = ROOT / "operations" / "connectivity" / "position_cycle.py"
    spec = importlib.util.spec_from_file_location("p1_position_cycle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_position_cycle_id_is_stable_across_partial_fills() -> None:
    module = load_position_cycle()
    first = module.stable_cycle_ids(
        entry_command_id="auto-entry-1",
        first_execution_id="exec-1",
        symbol="linkusdt",
        side="Buy",
        position_idx=0,
    )
    after_second_partial = module.stable_cycle_ids(
        entry_command_id="auto-entry-1",
        first_execution_id="exec-1",
        symbol="LINKUSDT",
        side="Buy",
        position_idx=0,
    )
    assert first == after_second_partial


def test_partial_fill_summary_updates_qty_and_weighted_average() -> None:
    module = load_position_cycle()
    decimal = __import__("decimal").Decimal
    first = module.summarize_entry_fills(
        [("exec-1", decimal("2"), decimal("10"))]
    )
    second = module.summarize_entry_fills(
        [
            ("exec-1", decimal("2"), decimal("10")),
            ("exec-2", decimal("1"), decimal("13")),
        ]
    )
    assert first.first_execution_id == second.first_execution_id == "exec-1"
    assert first.actual_qty == decimal("2")
    assert second.actual_qty == decimal("3")
    assert second.actual_avg_fill == decimal("11")
    assert second.execution_ids == ("exec-1", "exec-2")


def test_position_cycle_changes_for_new_first_execution() -> None:
    module = load_position_cycle()
    first = module.stable_cycle_ids(
        entry_command_id="auto-entry-1",
        first_execution_id="exec-1",
        symbol="LINKUSDT",
        side="Buy",
        position_idx=0,
    )
    another = module.stable_cycle_ids(
        entry_command_id="auto-entry-1",
        first_execution_id="exec-2",
        symbol="LINKUSDT",
        side="Buy",
        position_idx=0,
    )
    assert first != another


def test_source_contract_partial_fill_and_owner_matching() -> None:
    source = (ROOT / "operations" / "connectivity" / "private_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "stable_cycle_ids(" in source
    assert "ON CONFLICT(entry_command_id) DO UPDATE SET" in source
    assert "actual_avg_fill=excluded.actual_avg_fill" in source
    assert "actual_qty=excluded.actual_qty" in source
    assert "AND p.size=o.actual_qty" not in source
    assert "AND p.entry_price=o.actual_avg_fill" not in source
    assert 'Decimal(str(current.get("size") or 0)) == Decimal(str(row[5]))' not in source
    assert 'Decimal(str(current.get("avgPrice") or 0)) == Decimal(str(row[4]))' not in source


def test_source_contract_restart_and_entry_barrier() -> None:
    source = (ROOT / "operations" / "connectivity" / "private_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "startup_live_safety(" in source
    assert "disarm_new_entries(" in source
    assert "cancel_bot_owned_pending_entry_orders(" in source
    assert "entry_runtime_readiness(" in source
    assert "ENTRY_BLOCKED:" in source
    assert 'reason != "periodic"' in source
    assert "missing_owned_position" in source


def test_scanner_observes_full_universe_and_shutdown_signature() -> None:
    source = (ROOT / "operations" / "monitoring" / "entry_shadow_scanner.py").read_text(
        encoding="utf-8"
    )
    assert "monitored_symbols = set(monitored)" in source
    assert "selected_symbols = enabled_symbols(connection)" not in source
    assert "write_state(runtime, monitored_symbols)" in source


def test_dashboard_rearm_is_separate_from_settings_save() -> None:
    html = (ROOT / "operations" / "dashboard" / "index.html").read_text(encoding="utf-8")
    handler_start = html.index("tradeGateButton.addEventListener")
    handler_end = html.index("function processAudio", handler_start)
    handler = html[handler_start:handler_end]
    assert "saveSettings()" not in handler
    assert "settings_version:serverSettingsVersion" in handler
    assert "confirmed:true" in handler
    assert "rearmReady" in html


def test_dashboard_server_rechecks_rearm_readiness() -> None:
    source = (ROOT / "operations" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "def live_rearm_readiness(" in source
    assert "settings_version mismatch" in source
    assert "rearm_ready" in source
    assert "current real position has no exchange-confirmed protection" in source


def test_operations_file_exchange_contract_is_persisted() -> None:
    doc = (ROOT / "docs" / "OPERATIONS_FILE_EXCHANGE_RU.md").read_text(encoding="utf-8")
    assert "/srv/cripta-share/incoming" in doc
    assert "/srv/cripta-share/operations" in doc
    assert "/srv/cripta-share/reports" in doc
    assert "НЕ source of truth" in doc
