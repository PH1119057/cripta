from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTIVE_READER_FILES = (
    "operations/connectivity/private_runtime.py",
    "operations/connectivity/universal_entry_consumer.py",
    "operations/connectivity/universal_exit_consumer.py",
    "operations/monitoring/dispatcher_v2_context_correlator.py",
    "operations/monitoring/exit_runtime.py",
    "operations/monitoring/lifecycle_supervisor_runtime.py",
    "operations/monitoring/m3_trade_analyst.py",
    "operations/monitoring/position_supervisor.py",
    "operations/monitoring/universal_entry_shadow.py",
    "operations/monitoring/universal_exit_shadow_runtime.py",
    "src/bybit_workbench/capital_reservation.py",
    "src/bybit_workbench/entry_admission.py",
    "src/bybit_workbench/entry_reservation_lifecycle.py",
    "src/bybit_workbench/exchange/bybit/protection_supervisor_bridge.py",
    "src/bybit_workbench/lifecycle_supervisor.py",
    "src/bybit_workbench/stale_arm_retirement.py",
    "src/bybit_workbench/universal_exit/execution_store.py",
    "src/bybit_workbench/universal_exit/loader.py",
)

PRESERVATION_OR_SCHEMA_FILES = {
    "operations/dashboard/app.py",
    "operations/dashboard/archive_v2.py",
    "operations/monitoring/live_v1_session_audit.py",
    "operations/monitoring/mayak_v2.py",
    "operations/sql/20261016_account_scoped_key_preparation_r5.sql",
    "operations/sql/20261016_binding_lineage_r6b1.sql",
    "operations/sql/20261016_command_position_lineage_r6b2.sql",
    "operations/sql/20261016_execution_evidence_r5b.sql",
    "operations/sql/20261016_exit_account_lineage_r6c.sql",
    "operations/sql/20261016_identity_bridge_r3_r4.sql",
    "operations/sql/20261016_private_current_state_r5a.sql",
    "operations/sql/20261016_report_replay_provenance_r5.sql",
    "operations/sql/20261016_trade_command_account_identity_r6d.sql",
    "operations/sql/20261017_market_instrument_identity_r7a.sql",
    "operations/sql/p1_exact_close_readonly_audit.sql",
    "src/bybit_workbench/strategy_position.py",
    "src/bybit_workbench/universal_entry/storage.py",
    "src/bybit_workbench/universal_exit/contracts.py",
}


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_active_current_readers_have_no_legacy_unknown_or_coin_minutes_fallback() -> None:
    for relative in ACTIVE_READER_FILES:
        source = _text(relative)
        assert "LEGACY_UNKNOWN" not in source, relative
        assert "mayak_v2.coin_minutes" not in source, relative


def test_residual_legacy_references_are_confined_to_explicit_preservation_schema_set() -> None:
    roots = (
        ROOT / "operations",
        ROOT / "src/bybit_workbench",
        ROOT / "research/server",
    )
    actual: set[str] = set()
    for base in roots:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sql", ".service"}:
                continue
            if ".git" in path.parts or "tests" in path.parts or ".hypothesis" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "LEGACY_UNKNOWN" in source or "mayak_v2.coin_minutes" in source:
                actual.add(path.relative_to(ROOT).as_posix())
    assert actual == PRESERVATION_OR_SCHEMA_FILES


def test_coin_minutes_is_confined_to_archive_preservation_and_schema() -> None:
    archive = _text("operations/dashboard/archive_v2.py")
    dashboard = _text("operations/dashboard/app.py")
    report = _text("operations/monitoring/mayak_v2_report.py")
    assert 'TableExport("mayak_v2.coin_minutes"' in archive
    assert "mayak_v2.coin_minutes" not in dashboard
    assert "mayak_v2.coin_minutes" not in report


def test_universal_entry_storage_legacy_marker_is_not_a_private_state_reader() -> None:
    source = _text("src/bybit_workbench/universal_entry/storage.py")
    assert source.count('"LEGACY_UNKNOWN"') == 1
    assert "SELECT" not in source.split('"LEGACY_UNKNOWN"', 1)[1][:200]
    assert "strategy_entry.entry_decisions" in source


def test_historical_legacy_rows_are_not_relabelled_proven_by_r10b() -> None:
    for relative in (
        "operations/dashboard/archive_v2.py",
        "operations/sql/p1_exact_close_readonly_audit.sql",
    ):
        source = _text(relative)
        assert "LEGACY_UNKNOWN" in source


def test_recovered_unowned_history_does_not_require_legacy_identity_reader() -> None:
    source = _text("operations/dashboard/app.py")
    block = source.split("recovered_closed_rows = connection.execute(", 1)[1].split(
        "control_entry_rows = connection.execute(", 1
    )[0]
    assert "runtime.recovered_closed_entry_cycles" in block
    assert "JOIN runtime.capital_reservations" not in block
    assert "identity_status" not in block


def test_dashboard_legacy_unknown_is_confined_to_closed_trade_preservation() -> None:
    source = _text("operations/dashboard/app.py")
    prefix, tail = source.split("        exact_exit_rows = []", 1)
    preservation, suffix = tail.split("        lifecycle_rows = []", 1)
    assert "LEGACY_UNKNOWN" not in prefix
    assert "LEGACY_UNKNOWN" not in suffix
    assert preservation.count("LEGACY_UNKNOWN") == 5
    assert "runtime.position_exit_attribution" in preservation
    assert "owner_controlled_live_test" in preservation
    assert "runtime.recovered_closed_entry_cycles" in preservation
