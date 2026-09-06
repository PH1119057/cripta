from pathlib import Path

ROOT = Path(__file__).parents[1]
CORRELATOR = ROOT / "operations/monitoring/dispatcher_v2_context_correlator.py"
SQL = ROOT / "operations/sql/20260906_dispatcher_v2_event_links.sql"
UNIT = ROOT / "operations/dispatcher_v2/cripta-dispatcher-v2-context-correlator.service"
DASHBOARD = ROOT / "operations/dashboard/app.py"
ARCHIVE = ROOT / "operations/dashboard/archive_v2.py"


def test_d8_correlator_is_clean_observation_only() -> None:
    body = CORRELATOR.read_text(encoding="utf-8")
    assert "strategy_dispatcher" not in body
    assert "GOOD_MATCH" not in body
    assert "INCOMPATIBLE" not in body
    assert "dispatcher_v2.global_market_contexts" in body
    assert "dispatcher_v2.coin_market_contexts" in body
    assert "dispatcher_v2.trading_capacity_snapshots" in body
    assert "'OBSERVED_CONTEXT','NOT_CONSUMED'" in body
    assert "'NONE'" in body
    assert "trading_command',false" in body


def test_d8_coin_context_is_bound_to_same_global_context() -> None:
    body = CORRELATOR.read_text(encoding="utf-8")
    assert "global_context_id=g.global_context_id" in body
    assert "observed_at <= e.occurred_at" in body
    assert "symbol=e.symbol" in body


def test_d8_uses_exact_position_lineage_not_symbol_time_guessing() -> None:
    body = CORRELATOR.read_text(encoding="utf-8")
    assert "runtime.position_ownership" in body
    assert "p.position_id=l.position_id" in body
    assert "p.position_id=s.position_id" in body
    assert "p.position_id=d.position_id" in body


def test_d8_sql_is_append_only_and_non_trading() -> None:
    body = SQL.read_text(encoding="utf-8")
    assert "research_context.dispatcher_v2_event_links" in body
    assert "BEFORE UPDATE OR DELETE" in body
    assert "GRANT SELECT, INSERT" in body
    assert "UPDATE, DELETE" not in body
    assert "trading_effect = 'NONE'" in body
    assert "observed_context_mode = 'OBSERVED_CONTEXT'" in body
    assert "consumed_context_mode = 'NOT_CONSUMED'" in body


def test_d8_unit_has_persistent_provenance_and_no_execution_dependency() -> None:
    body = UNIT.read_text(encoding="utf-8")
    assert "EnvironmentFile=/var/lib/cripta/dispatcher_v2/correlator.env" in body
    assert "cripta-dispatcher-v2.service" in body
    assert "Execution" not in body
    assert "private-runtime" not in body


def test_d8_archive_and_signal_export_are_registered() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    archive = ARCHIVE.read_text(encoding="utf-8")
    assert "research_context.dispatcher_v2_event_links" in dashboard
    assert "03_DISPATCHER_V2_OBSERVED_CONTEXT.csv" in dashboard
    assert "dispatcher_v2_causal_ok" in dashboard
    assert "research_context.dispatcher_v2_event_links" in archive
    assert "dispatcher_v2_event_links.jsonl" in archive
    assert "cripta-dispatcher-v2-context-correlator.service" in archive
