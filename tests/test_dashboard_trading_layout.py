from pathlib import Path

SOURCE = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
APP_SOURCE = Path("operations/dashboard/app.py").read_text(encoding="utf-8")


def test_primary_trading_tables_are_promoted_in_requested_order() -> None:
    assert (
        "tradeSubnav.after(tradeOperatorBar,openTradesSection,closedTradesSection,"
        "strategyPaperOpenSection,strategyPaperClosedSection,coinMonitorSection,signalObservationSection)"
    ) in SOURCE
    assert 'id="tradeSubnav" class="trade-subnav"' in SOURCE
    for section_id in (
        "openTradesSection",
        "closedTradesSection",
        "strategyPaperOpenSection",
        "strategyPaperClosedSection",
        "coinMonitorSection",
        "signalObservationSection",
    ):
        assert f'id="{section_id}"' in SOURCE


def test_open_position_card_is_compact_and_expands_from_left_triangle() -> None:
    assert 'class="row-toggle"' in SOURCE
    assert "expandedPositions=new Set()" in SOURCE
    assert "function togglePositionCard(symbol)" in SOURCE
    assert "cardRow.hidden=!open" in SOURCE
    assert 'colspan="12" class="position-card-cell"' in SOURCE


def test_closed_trades_have_internal_scroll_and_exports_remain() -> None:
    assert 'class="closed-scroll"' in SOURCE
    assert "installExportControl('Завершённые сделки Bybit','closed','closed')" in SOURCE
    assert (
        "installExportControl('Независимое наблюдение за сигналами','signals','signals')"
    ) in SOURCE


def test_live_refresh_does_not_destroy_text_selection() -> None:
    assert (
        "positionRows.contains(selected)||realClosedRows.contains(selected)||"
        "liveRows.contains(selected)"
    ) in SOURCE
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
    compact_row = SOURCE.split("positionRows.innerHTML=", 1)[1].split("const safe=", 1)[0]
    assert "supervisorBlock(p.supervisor)" not in compact_row
    assert "Положение и обоснование" in SOURCE


def test_trading_uses_sticky_operator_bar_and_real_subpages() -> None:
    assert "function selectTradeSubpage(name)" in SOURCE
    assert "trade-subpage-hidden" in SOURCE
    assert "tradeSubnav.after(tradeOperatorBar,openTradesSection" in SOURCE
    assert 'id="tradeOperatorBar" class="trade-operator-bar"' in SOURCE
    assert ".trade-operator-bar{grid-column:1/-1;position:sticky" in SOURCE
    for legacy in (
        "Общие параметры новых сделок",
        "Площадка live-сделок",
        "Воронка M3 Entry",
        "Управление M3 FULL LIVE V1.1",
    ):
        assert legacy not in SOURCE


def test_strategy_monitor_is_read_only_and_has_no_symbol_permission_checkbox() -> None:
    start = SOURCE.index('id="coinMonitorSection"')
    end = SOURCE.index('id="signalObservationSection"', start)
    monitor = SOURCE[start:end]
    assert 'id="strategyMonitorFilter"' in monitor
    assert 'id="strategyMonitorNear"' in monitor
    assert "Список монет задаётся только StrategyCard" in monitor
    assert "auto-check" not in monitor
    assert "setAuto(" not in monitor
    assert "setAllAuto(" not in monitor


def test_legacy_global_entry_policy_is_not_exposed_on_trade_page() -> None:
    assert 'id="entryPolicy"' not in SOURCE
    assert 'id="tradeStake"' not in SOURCE
    assert 'id="tradeLeverage"' not in SOURCE
    assert 'id="entryOffset"' not in SOURCE
    assert 'id="entryLimitTtl"' not in SOURCE
    assert "StrategyCard" in SOURCE


def test_open_trade_table_has_explicit_strategy_column_and_operator_actions() -> None:
    open_section = SOURCE[
        SOURCE.index('id="openTradesSection"') : SOURCE.index('id="strategyPaperOpenSection"')
    ]
    assert "<th>Стратегия</th>" in open_section
    assert "<th>Состояние</th>" in open_section
    assert "Защитить чистую прибыль" in SOURCE
    assert "Стоп −0,20%" in SOURCE
    assert "Закрыть" in SOURCE


def test_closed_trade_table_uses_exact_postgresql_attribution() -> None:
    assert "runtime.position_exit_attribution" in APP_SOURCE
    assert "WHERE a.link_status='EXACT'" in APP_SOURCE
    assert "if has_exact_exit_table:" in APP_SOURCE
    assert '"UNKNOWN": "точный механизм не доказан"' in APP_SOURCE
