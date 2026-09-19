from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_dispatcher_observed_context_contract_is_current() -> None:
    observation = (ROOT / "docs/OBSERVATION_ANALYTICS_RU.md").read_text(encoding="utf-8")
    sql = (ROOT / "operations/sql/20260906_dispatcher_v2_event_links.sql").read_text(
        encoding="utf-8"
    )

    assert "`OBSERVED_CONTEXT` — существовавший причинный контекст;" in observation
    assert (
        "`CONSUMED_CONTEXT` — контекст, реально использованный EntryPlan/ExitPlan."
        in observation
    )
    assert "research_context.dispatcher_v2_event_links" in sql
    assert "observed_context_mode TEXT NOT NULL DEFAULT 'OBSERVED_CONTEXT'" in sql
    assert "consumed_context_mode TEXT NOT NULL DEFAULT 'NOT_CONSUMED'" in sql
    assert "trading_effect TEXT NOT NULL DEFAULT 'NONE'" in sql

def test_dispatcher_v2_runtime_evidence_uses_current_contract() -> None:
    index = (ROOT / "docs/DOCUMENTATION_INDEX_RU.md").read_text(encoding="utf-8")
    current_map = (ROOT / "docs/CURRENT_PROJECT_MAP_RU.md").read_text(encoding="utf-8")
    correlator = (
        ROOT / "operations/monitoring/dispatcher_v2_context_correlator.py"
    ).read_text(encoding="utf-8")

    assert "DISPATCHER_V2_D8_OBSERVED_CONTEXT_RU.md" not in index
    assert "DISPATCHER_V2_D8_STAGE_RESULTS_RU.md" not in index
    assert "`cripta-dispatcher-v2.service` active/enabled;" in current_map
    assert "legacy `cripta-strategy-dispatcher.service` inactive/disabled;" in current_map
    assert "research_context.dispatcher_v2_event_links" in correlator
    assert "'OBSERVED_CONTEXT','NOT_CONSUMED'" in correlator
