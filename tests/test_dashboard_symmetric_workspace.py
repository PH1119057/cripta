"""Dashboard two-column P90 workspace with retained hidden history rail."""
from pathlib import Path


def test_p90_rail_and_trade_desk_replace_visible_closed_history_rail():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert html.count('id="marketWorkspaceRail"') == 1
    assert html.count('id="closedWorkspaceRail"') == 1
    assert html.count('id="liveDesk"') == 1
    assert html.count('id="realClosedRows"') == 1
    assert "minmax(0,18fr) minmax(0,82fr)" in html
    assert 'aria-label="Завершённые сделки и результат" hidden' in html
    assert "#closedWorkspaceRail{display:none!important}" in html
    assert "position:sticky" in html
    assert "overflow-y:auto;overflow-x:hidden" in html
    assert "@media(max-width:1249px)" in html


def test_hidden_history_rail_keeps_rollback_code_but_does_not_poll():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert 'data-rail-period="day"' in html
    assert 'data-rail-period="week"' in html
    assert 'data-rail-period="month"' in html
    assert "day:{ms:86400000" in html
    assert "week:{ms:7*86400000" in html
    assert "month:{ms:30*86400000" in html
    assert "/api/live/state?view=closed" in html
    assert "closedRail.hidden||railRefreshBusy" in html
    assert "x.actual_net_pnl??x.net_pnl" in html
    assert "railHardStopTrade(x)" in html
    assert "История ограничена 1000 строками" in html


def test_retained_history_rail_is_presentation_only():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    widget = html.split("/* Right rail is a presentation-only view", 1)[1]
    assert "fetch('/api/live/state?view=closed',{cache:'no-store'})" in widget
    assert "method:'POST'" not in widget
    assert "tradeCommand(" not in widget
