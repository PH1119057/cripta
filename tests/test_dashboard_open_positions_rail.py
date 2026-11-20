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
    assert "open:[openTradesSection,marketIndicatorSection]" in source
    assert 'id="coinMonitorSection"' not in source
    assert 'data-trade-page="monitor"' not in source
    assert "#openTradesSection{display:none!important}" in source
    assert "@media(max-width:1249px){.workspace-rail{display:none!important}}" in source


def test_no_new_exchange_mutation_endpoint_or_gate_controls():
    source = UI.read_text(encoding="utf-8")
    rail = source.split("function renderOpenRailPositions(positions){", 1)[1]
    rail = rail.split("let livePollTimer=null;", 1)[0]
    assert "fetch(" not in rail
    assert "livePost(" not in rail
    assert "tradeCommand(" in rail


def test_p90_rail_stably_pins_open_rows_without_geometry_jitter():
    source = UI.read_text(encoding="utf-8")
    block = source.split("function p90RailStableRows(items){", 1)[1].split(
        "function patchP90RailDom(", 1
    )[0]
    assert "p90RailKnownOpen.add(key)" in block
    assert "p90RailConfirmedGone.set(key,seen)" in block
    assert "seen>=3" in block
    assert "p90RailKnownOpen.has(key)" in block
    assert "position_attribution" in block
    assert "p90RailLastH9Distance" in block
    assert (
        "if((!Number.isFinite(liveA)||!Number.isFinite(liveB))"
        "&&previous!==null)return previous;"
    ) in block
    assert "Math.abs(da-db)>0.05" in block
    assert "p90RailPreviousOrder.clear()" in block
    assert "patchP90RailDom(rail,rows.map(item=>" in source
    assert "if(Array.isArray(lt.p90_rail?.items)" in source
    assert "else renderOpenRailPositions(positions);" in source
    assert "rail.dataset.p90Rendered==='true'" in source
    assert "function patchP90RailNode(node,next){" in source
    assert "node===document.activeElement" in source
    assert "node.innerHTML=next.innerHTML" not in source
    assert "data-p90-symbol" in source
    assert "fetch(" not in block
    assert "tradeCommand(" not in block
