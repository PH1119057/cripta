from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "production/src/bybit_workbench/dispatcher_v2"
RUNTIME = ROOT / "operations/monitoring/dispatcher_v2.py"
SQL = ROOT / "operations/sql/20260906_dispatcher_v2_contexts.sql"
UNIT = ROOT / "operations/dispatcher_v2/cripta-dispatcher-v2.service"
ARCHIVE = ROOT / "operations/dashboard/archive_v2.py"
DASHBOARD = ROOT / "operations/dashboard/app.py"


def test_clean_v2_has_no_legacy_or_strategy_dependency() -> None:
    body = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(PACKAGE.glob("*.py"))
    )
    runtime = RUNTIME.read_text(encoding="utf-8")
    combined = body + "\n" + runtime
    assert "bybit_workbench.strategy_dispatcher" not in combined
    assert "config/strategy_dispatcher" not in combined
    for forbidden in (
        "GOOD_MATCH",
        "INCOMPATIBLE",
        "StrategyMarketProfile",
        "SuitabilityStatus",
    ):
        assert forbidden not in combined
    assert "runtime.trade_commands" not in runtime
    assert "Execution" not in runtime


def test_v2_reads_only_canonical_mayak_and_account_sources() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "mayak_v2.shared_market_contexts" in runtime
    assert "mayak_v2.coin_market_contexts" in runtime
    assert "runtime.wallet_latest" in runtime
    assert "runtime.hot_positions" in runtime
    assert "runtime.hot_orders" in runtime
    assert "strategy_dispatcher.assessments" not in runtime
    assert "strategy_dispatcher.runs" not in runtime


def test_v2_persists_append_only_with_minimal_runtime_privileges() -> None:
    sql = SQL.read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS dispatcher_v2" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "GRANT SELECT, INSERT ON dispatcher_v2.global_market_contexts TO cripta" in sql
    assert "GRANT SELECT, INSERT ON dispatcher_v2.coin_market_contexts TO cripta" in sql
    assert "GRANT SELECT, INSERT ON dispatcher_v2.trading_capacity_snapshots TO cripta" in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql


def test_v2_service_is_passive_and_independent() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "User=cripta" in unit
    assert "dispatcher_v2.py --poll-seconds 2" in unit
    assert "cripta-mayak-v2.service" in unit
    assert "cripta-private-runtime.service" in unit
    assert "strategy-dispatcher" not in unit


def test_v2_tables_are_in_archive_and_signal_analysis_contracts() -> None:
    archive = ARCHIVE.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    for table in (
        "dispatcher_v2.global_market_contexts",
        "dispatcher_v2.coin_market_contexts",
        "dispatcher_v2.trading_capacity_snapshots",
    ):
        assert table in archive
        assert table in dashboard


def test_v2_live_bootstrap_starts_from_latest_mayak_and_then_moves_forward() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "max(source_mayak_snapshot_id)" in runtime
    assert "max(mayak_snapshot_id)" in runtime
    assert "source.mayak_snapshot_id>watermark.source_mayak_snapshot_id" in runtime
    assert "Bootstrap from latest source only" in runtime


def test_v2_service_loads_persistent_deploy_provenance() -> None:
    unit_path = ROOT / "operations/dispatcher_v2/cripta-dispatcher-v2.service"
    unit = unit_path.read_text(encoding="utf-8")
    assert "EnvironmentFile=/var/lib/cripta/dispatcher_v2/runtime.env" in unit
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert 'SOURCE_COMMIT = os.environ.get("DISPATCHER_V2_SOURCE_COMMIT", "UNSPECIFIED")' in runtime
