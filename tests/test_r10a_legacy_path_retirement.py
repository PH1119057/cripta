from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(
    encoding="utf-8"
)
CONSUMER = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(
    encoding="utf-8"
)
CONSUMER_UNIT = (
    ROOT / "operations/systemd/cripta-universal-entry-consumer.service"
).read_text(encoding="utf-8")
MAYAK = (ROOT / "operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
REPORT = (ROOT / "operations/monitoring/mayak_v2_report.py").read_text(
    encoding="utf-8"
)
LEGACY_PRIVATE = (
    ROOT / "research/server/connectivity/private_runtime.py"
).read_text(encoding="utf-8")
PRIVATE_UNIT = (
    ROOT / "research/server/connectivity/cripta-private-runtime.service"
).read_text(encoding="utf-8")


def test_new_mayak_minute_writer_is_compact_only() -> None:
    assert "self._persist_compact_market_minutes(snapshot_id, snapshot)" in MAYAK
    assert "self._persist_coin_minutes(snapshot_id, snapshot)" not in MAYAK
    assert "def _persist_coin_minutes" not in MAYAK
    assert "INSERT INTO mayak_v2.coin_minutes" not in MAYAK
    # Historical schema is intentionally retained until the R11 retention stage.
    assert "CREATE TABLE IF NOT EXISTS mayak_v2.coin_minutes" in MAYAK


def test_scheduled_reports_have_no_legacy_coin_minute_fallback() -> None:
    assert "FROM analytics.market_minutes" in REPORT
    assert "FROM mayak_v2.coin_minutes" not in REPORT
    assert '"legacy_fallback_source": None' in REPORT
    assert '"proven_source": "analytics.market_minutes"' in REPORT


def test_legacy_v1_entry_writer_is_retired_from_active_runtime() -> None:
    assert "LEGACY_V1" not in PRIVATE
    assert "LEGACY_V1" not in CONSUMER
    assert "LEGACY_V1" not in CONSUMER_UNIT
    assert '"CRIPTA_ENTRY_COMMAND_SOURCE", "UNIVERSAL_ENTRY"' in PRIVATE
    assert '"CRIPTA_ENTRY_COMMAND_SOURCE", "UNIVERSAL_ENTRY"' in CONSUMER
    assert "CRIPTA_ENTRY_COMMAND_SOURCE=UNIVERSAL_ENTRY" in CONSUMER_UNIT
    assert "R10_LEGACY_ENTRY_COMMAND_SOURCE_RETIRED" in PRIVATE


def test_private_writers_use_account_scoped_conflict_identity() -> None:
    targets = (
        "ON CONFLICT(trading_account_id,singleton) DO UPDATE SET",
        "ON CONFLICT(trading_account_id,symbol,position_idx) DO UPDATE SET",
        "ON CONFLICT(trading_account_id,order_id) DO UPDATE SET",
        "ON CONFLICT(trading_account_id,exec_id) DO UPDATE SET",
    )
    for target in targets:
        assert target in PRIVATE
    for legacy in (
        "ON CONFLICT(singleton) DO UPDATE SET",
        "ON CONFLICT(symbol,position_idx) DO UPDATE SET",
        "ON CONFLICT(order_id) DO UPDATE SET",
        "ON CONFLICT(exec_id) DO UPDATE SET",
    ):
        assert legacy not in PRIVATE


def test_historical_private_writer_is_not_the_installed_service_execstart() -> None:
    assert "def command_loop" in LEGACY_PRIVATE
    assert (
        "ExecStart=/srv/cripta/runtime/current/.venv/bin/python "
        "/srv/cripta/runtime/current/operations/connectivity/private_runtime.py"
        in PRIVATE_UNIT
    )
    assert "research/server/connectivity/private_runtime.py" not in PRIVATE_UNIT


def test_r10a_does_not_claim_legacy_reader_gate_complete() -> None:
    archive = (ROOT / "operations/dashboard/archive_v2.py").read_text(
        encoding="utf-8"
    )
    dashboard = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    # These are explicit remaining R10b/R11 inventory items, not hidden PASS.
    assert "mayak_v2.coin_minutes" in archive
    assert "mayak_v2.coin_minutes" in dashboard
