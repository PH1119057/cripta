from pathlib import Path


def test_dashboard_live_actions_have_visible_pending_state_and_empty_symbol_gate() -> None:
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    app = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    assert 'id="tradeOperationStatus"' in html
    assert 'id="autoSaveStatus"' in html
    assert "beginTimedStatus" in html
    assert "gateChanging" in html
    assert "if(!settingsSaving)enabledSymbols=new Set" in html
    assert 'data-symbol="${x.symbol}"' in html
    assert "livePostTimed('/api/live/gate'" in html
    assert "livePostTimed('/api/live/settings'" in html
    assert "Нельзя открыть шлюз: не выбрана ни одна торговая монета" in html
    assert "select at least one trading symbol before re-arm" in app
    assert "нельзя открыть шлюз: не выбрана ни одна торговая монета" in app
