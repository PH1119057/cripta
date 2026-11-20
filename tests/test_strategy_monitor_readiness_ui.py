from pathlib import Path

DASHBOARD = Path("operations/dashboard/index.html")


def test_operator_p90_rail_keeps_read_only_proximity_indicators() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="marketWorkspaceRail"' in html
    assert "p90-open" in html
    assert "p90-near" in html
    assert "p90-far" in html
    assert "h9Proximity<0.5" in html
    assert "const h9NearestDistance=p90RailH9Proximity(item)" in html
    assert "ENTRY BLOCKED (геометрия)" in html
    assert "Entry требует overlap с L5-3" in html


def test_retired_entryplan_widget_is_not_exposed_or_used_to_trade() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="coinMonitorSection"' not in html
    assert 'id="strategyMonitorGreen"' not in html
    assert 'data-trade-page="monitor"' not in html
    assert "renderStrategyMonitor(d.strategy_monitor)" not in html
    assert "EntryPlan не меняется" not in html
    assert "Пresentation-only" not in html
    assert "function p90RailStableRows(items){" in html
