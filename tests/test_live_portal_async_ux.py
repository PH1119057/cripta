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
    assert "fetchWithTimeout(path,{cache:'no-store'},8000)" in html
    assert "const deadline=Date.now()+20000" in html
    assert "Шлюз открыт, но 0 монет разрешено" in html
    assert html.count("fetch('/api/live/state'") == 0


def test_live_polling_adapts_to_visible_trade_page_and_skips_hidden_dom() -> None:
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "function livePollDelay()" in html
    assert "open:1000,monitor:2000,closed:10000,signals:5000" in html
    assert "if(document.hidden)return 30000" in html
    assert "if(activePanelName()!=='liveDesk')return 15000" in html
    assert "scheduleLivePoll(0)" in html
    assert "setInterval(()=>refreshEntryShadow" not in html
    assert "if(activeTradePage()==='open')positionRows.innerHTML" in html
    assert "if(activeTradePage()==='closed')realClosedRows.innerHTML" in html
    assert "if(activeTradePage()==='signals')renderSignalObservation" in html
    assert (
        "if(page==='monitor'){renderStrategyPaper(d.paper_strategy);renderEntryShadow(d.entry_shadow)}"
        in html
    )


def test_live_state_requests_only_data_for_visible_trade_view() -> None:
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    app = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "`/api/live/state?view=${encodeURIComponent(view)}`" in html
    assert "def live_trading_state() -> dict[str, object]" in app
    assert "def _live_trading_state(*, include_history: bool)" in app
    assert '_live_trading_state(include_history=view == "closed")' in app
    assert 'opportunity_state()\n                    if view == "signals"' in app
    assert 'entry_shadow_state() if view == "monitor" else None' in app
    assert 'mayak_v2_state() if view == "open" else None' in app
    assert "if include_history and not has_exact_exit_table" in app
    assert "1000 if include_history else 20" in app
