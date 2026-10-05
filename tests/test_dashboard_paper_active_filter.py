from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_open_paper_summary_only_aggregates_enabled_strategies() -> None:
    source=(ROOT/"operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "active_aggregate_rows = connection.execute(" in source
    assert "JOIN strategy_entry.strategy_activations a" in source
    assert "AND a.enabled=true" in source
    assert '"history_summary": history_summary' in source


def test_closed_paper_history_keeps_historical_strategy_aggregate() -> None:
    source=(ROOT/"operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "historySummary=p.history_summary||summary" in source
    assert "Strategy ${historySummary.length}" in source
    assert "strategyPaperByStrategy.innerHTML=summary.length?summary.map" in source
