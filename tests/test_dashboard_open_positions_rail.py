from pathlib import Path

UI = Path("operations/dashboard/index.html")


def test_open_positions_render_in_left_workspace_rail():
    source = UI.read_text(encoding="utf-8")
    assert 'id="marketWorkspaceRail"' in source
    assert 'aria-label="Открытые сделки Bybit"' in source
    assert 'id="openRailPositions"' in source
    assert "renderOpenRailPositions(positions);" in source
    assert "const expandedRailPositions=new Set();" in source
    assert "toggleRailPosition(symbol)" in source
    assert 'aria-controls="rail-open-' in source


def test_live_position_actions_preserved_in_compact_rail():
    source = UI.read_text(encoding="utf-8")
    rail = source.split("function renderOpenRailPositions(positions){", 1)[1]
    rail = rail.split("let livePollTimer=null;", 1)[0]
    for command in ("break_even", "current_stop", "close"):
        assert command in rail
    assert "rememberTrailing(" in rail
    assert "setTrailing(" in rail
    assert "trailingOptions(p.symbol)" in rail
    assert "bybit.kz/trade/usdt/" in rail
    assert "p.net_pnl_to_close" in rail
    assert "rail-long-arrow" in rail
    assert "rail-short-arrow" in rail
    assert "expandedRailPositions.has(p.symbol)" in rail


def test_market_monitor_is_under_central_tables_without_inner_vertical_scroll():
    source = UI.read_text(encoding="utf-8")
    assert 'id="marketIndicatorSection"' in source
    assert "open:[openTradesSection,coinMonitorSection,marketIndicatorSection]" in source
    assert (
        "#coinMonitorSection .signal-scroll{max-height:none!important;overflow:visible!important}"
        in source
    )
    assert "#openTradesSection{display:none!important}" in source
    assert "@media(max-width:1249px){.workspace-rail{display:none!important}}" in source


def test_no_new_exchange_mutation_endpoint_or_gate_controls():
    source = UI.read_text(encoding="utf-8")
    rail = source.split("function renderOpenRailPositions(positions){", 1)[1]
    rail = rail.split("let livePollTimer=null;", 1)[0]
    assert "fetch(" not in rail
    assert "livePost(" not in rail
    assert "tradeCommand(" in rail
