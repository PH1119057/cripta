from pathlib import Path


SOURCE = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
APP_SOURCE = Path("operations/dashboard/app.py").read_text(encoding="utf-8")


def test_primary_trading_tables_are_promoted_in_requested_order() -> None:
    assert (
        "tradeSubnav.after(tradePlatformSection,tradeSettingsSection,openTradesSection,closedTradesSection,"
        "coinMonitorSection,signalObservationSection)"
    ) in SOURCE
    assert 'id="tradeSubnav" class="trade-subnav"' in SOURCE
    for section_id in (
        "openTradesSection",
        "closedTradesSection",
        "coinMonitorSection",
        "signalObservationSection",
    ):
        assert f'id="{section_id}"' in SOURCE


def test_open_position_card_is_compact_and_expands_from_left_triangle() -> None:
    assert 'class="row-toggle"' in SOURCE
    assert "expandedPositions=new Set()" in SOURCE
    assert "function togglePositionCard(symbol)" in SOURCE
    assert 'cardRow.hidden=!open' in SOURCE
    assert 'colspan="10" class="position-card-cell"' in SOURCE


def test_closed_trades_have_internal_scroll_and_exports_remain() -> None:
    assert 'class="closed-scroll"' in SOURCE
    assert "installExportControl('Завершённые сделки Bybit','closed','closed')" in SOURCE
    assert "installExportControl('Независимое наблюдение за сигналами','signals','signals')" in SOURCE


def test_live_refresh_does_not_destroy_text_selection() -> None:
    assert "positionRows.contains(selected)||realClosedRows.contains(selected)||liveRows.contains(selected)" in SOURCE
    assert "if(!tableSelected){renderLiveState(d);installPositionCards()}" in SOURCE
    assert "function tradingViewport()" in SOURCE
    assert "restoreTradingViewport(viewport)" in SOURCE
    restore_body = SOURCE.split("function restoreTradingViewport", 1)[1].split(
        "async function refreshEntryShadow", 1
    )[0]
    assert "window.scrollTo" not in restore_body
    assert "expandedClosedTrades=new Set()" in SOURCE
    assert "function toggleClosedTradeCard(key)" in SOURCE
    assert SOURCE.count("positionRows.innerHTML=") == 1
    assert SOURCE.count("renderLiveState(d);installPositionCards()") == 1


def test_supervisor_explanation_is_only_in_expanded_position_card() -> None:
    compact_row = SOURCE.split("positionRows.innerHTML=", 1)[1].split(
        "const safe=", 1
    )[0]
    assert "supervisorBlock(p.supervisor)" not in compact_row
    assert "Положение и обоснование" in SOURCE


def test_trading_uses_real_subpages_and_open_settings_precede_positions() -> None:
    assert "function selectTradeSubpage(name)" in SOURCE
    assert "trade-subpage-hidden" in SOURCE
    assert "tradeSubnav.after(tradePlatformSection,tradeSettingsSection,openTradesSection" in SOURCE


def test_master_symbol_checkbox_has_unambiguous_toggle() -> None:
    assert "autoAll.onchange=null" in SOURCE
    assert "const enable=enabledSymbols.size===0" in SOURCE


def test_legacy_market_guard_is_not_selectable_and_advisory_wording_is_current() -> None:
    assert "entryPolicy.querySelector('option[value=\"market_guard_v1\"]')?.remove()" in SOURCE
    assert '"base_entry_v1", "m3_full_live_v1"' in APP_SOURCE
    assert "Они сами не создают, не запрещают и не закрывают сделки" in SOURCE


def test_closed_trade_table_uses_exact_postgresql_attribution() -> None:
    assert "runtime.position_exit_attribution" in APP_SOURCE
    assert "WHERE a.link_status='EXACT'" in APP_SOURCE
    assert "if has_exact_exit_table:" in APP_SOURCE
    assert '"UNKNOWN": "точный механизм не доказан"' in APP_SOURCE
