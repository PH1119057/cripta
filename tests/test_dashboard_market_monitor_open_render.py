from pathlib import Path


def test_owner_removed_unused_entryplan_monitor_from_open_trades():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="coinMonitorSection"' not in html
    assert 'data-trade-page="monitor"' not in html
    assert "renderStrategyMonitor(d.strategy_monitor);" not in html
    assert "open:[openTradesSection,marketIndicatorSection]" in html
    assert 'id="marketIndicatorSection"' in html
    assert 'id="marketWorkspaceRail"' in html
