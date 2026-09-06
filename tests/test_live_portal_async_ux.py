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


def test_live_state_polling_is_single_flight_and_operations_are_bounded() -> None:
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "liveStateInFlight=null" in html
    assert "async function fetchLiveStateData()" in html
    assert "if(liveStateInFlight)return liveStateInFlight" in html
    assert "if(gateChanging||settingsSaving)return" in html
    assert "fetchWithTimeout('/api/live/state'" in html
    assert "const deadline=Date.now()+20000" in html
    assert "Шлюз открыт, но 0 монет разрешено" in html
    assert html.count("fetch('/api/live/state'") == 0
