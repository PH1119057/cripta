from pathlib import Path


DASHBOARD = Path("operations/dashboard/index.html")


def test_strategy_monitor_has_readiness_traffic_light() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert 'id="strategyMonitorGreen"' in html
    assert 'Зелёный ≤' in html
    assert "strategy-monitor-row-green" in html
    assert "strategy-monitor-row-yellow" in html
    assert "strategy-monitor-row-red" in html
    assert "label:'НЕ ГОТОВО'" in html
    assert "label:'ГОТОВО'" in html
    assert "LOW_CHANGED" in html
    assert "working_width_pct" in html
    assert "strict_stable_states" in html


def test_strategy_monitor_traffic_light_is_ui_only() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "Светофор — только визуализация readiness; EntryPlan не меняется." in html
    assert "onStrategyMonitorGreenChange()" in html
    assert "localStorage.setItem('cripta-strategy-monitor-green'" in html
