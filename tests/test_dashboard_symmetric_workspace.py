"""Dashboard-only symmetric market / closed-trades workspace."""
from pathlib import Path


def test_symmetric_rails_do_not_replace_existing_trade_desk():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert html.count('id="marketWorkspaceRail"') == 1
    assert html.count('id="closedWorkspaceRail"') == 1
    assert html.count('id="liveDesk"') == 1
    assert html.count('id="realClosedRows"') == 1
    assert "minmax(0,18fr) minmax(0,64fr) minmax(0,18fr)" in html
    assert "position:sticky" in html
    assert "overflow-y:auto;overflow-x:hidden" in html
    assert "@media(max-width:1249px)" in html


def test_rolling_periods_saved_and_real_history_reused():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert 'data-rail-period="day"' in html
    assert 'data-rail-period="week"' in html
    assert 'data-rail-period="month"' in html
    assert "day:{ms:86400000" in html
    assert "week:{ms:7*86400000" in html
    assert "month:{ms:30*86400000" in html
    assert "localStorage.setItem(railPeriodKey,railPeriod)" in html
    assert "localStorage.getItem(railPeriodKey)" in html
    assert "/api/live/state?view=closed" in html
    assert "x.actual_net_pnl??x.net_pnl" in html
    assert "railHardStopTrade(x)" in html
    assert "История ограничена 1000 строками" in html


def test_workspace_is_presentation_only():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    widget = html.split('/* Right rail is a presentation-only view', 1)[1]
    assert "fetch('/api/live/state?view=closed',{cache:'no-store'})" in widget
    assert "method:'POST'" not in widget
    assert "tradeCommand(" not in widget
