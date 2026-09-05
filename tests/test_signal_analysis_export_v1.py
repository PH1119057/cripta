from pathlib import Path

APP = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
UI = Path("operations/dashboard/index.html").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = APP.index(f"def {name}(")
    tail = APP[start:]
    next_def = tail.find("\n\ndef ", 5)
    return tail if next_def < 0 else tail[:next_def]


def test_signal_export_has_requested_periods_and_richer_zip_button() -> None:
    assert '"72h": (72 * 3600, "72_часа")' in APP
    assert '<option value="72h">72 часа</option>' in UI
    assert 'Скачать аналитический ZIP' in UI


def test_signal_context_joins_mayak_dispatcher_coin_and_entry_causally() -> None:
    body = _function("_write_signal_context_csv")
    assert "research_context.event_links" in body
    assert "event_type='SIGNAL'" in body
    assert "mayak_v2.snapshots" in body
    assert "strategy_dispatcher.assessments" in body
    assert "mayak_v2.coin_minutes" in body
    assert "runtime.entry_decisions" in body
    assert "runtime.m3_consumed_context" in body
    assert "monitoring.entry_geometry_handoffs" in body
    assert "monitoring.entry_dispatcher_shadow_decisions" in body
    assert "cm.observed_at <= to_timestamp(o.signal_at_epoch_ms / 1000.0)" in body
    assert "causal_context_ok" in body


def test_export_preserves_observed_vs_consumed_and_exact_first_hits() -> None:
    assert "observed_context" in APP
    assert "consumed_context" in APP
    assert '"+0.1"' in APP
    assert '"+1.1"' in APP
    assert '"-1.0"' in APP
    assert "plus_0_10_vs_minus_1" in APP
    assert "plus_1_10_vs_minus_1" in APP


def test_entry_audit_and_period_postgres_are_exported_without_trade_mutation() -> None:
    assert "/var/lib/cripta/entry_shadow/workbench.db" in APP
    assert '"CANDIDATE_ARMED"' in APP
    assert '"TOUCH_VETO"' in APP
    assert '"TOUCH_BLOCKED"' in APP
    assert '"CORE_SIGNAL"' in APP
    body = _function("export_signal_analysis_bundle")
    assert "INSERT INTO" not in body
    assert "UPDATE " not in body
    assert "DELETE FROM" not in body
    assert '"trading_effect": "NONE: export/Analyst observation only"' in body


def test_raw_period_tables_cover_required_observability_layers() -> None:
    for table in (
        "monitoring.opportunities",
        "monitoring.opportunity_events",
        "research_context.event_links",
        "strategy_dispatcher.runs",
        "strategy_dispatcher.assessments",
        "mayak_v2.snapshots",
        "mayak_v2.coin_minutes",
        "mayak_v2.observation_journal",
        "runtime.entry_decisions",
        "runtime.m3_consumed_context",
    ):
        assert table in APP


def test_signal_zip_is_background_job_to_survive_http_proxy_timeout() -> None:
    assert "def start_signal_export_job(period: str)" in APP
    assert "threading.Thread(" in APP
    assert 'path.startswith("/api/trading/export-jobs/")' in APP
    assert "pollTradingExportJob" in UI
    assert "if(table==='signals'&&out.job_id)" in UI
