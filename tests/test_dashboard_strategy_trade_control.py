from pathlib import Path


HTML = Path("operations/dashboard/index.html").read_text(encoding="utf-8")


def test_open_trades_page_has_strategy_execution_table() -> None:
    assert 'id="strategyTradeControlSection"' in HTML
    assert 'id="tradeStrategyRows"' in HTML
    assert "Стратегии реальной торговли" in HTML
    assert "Фактически торгует" in HTML
    assert "Разрешить торги" in HTML
    assert "Остановить мониторинг" in HTML


def test_strategy_table_shows_capital_leverage_and_actual_gate_state() -> None:
    assert "capital.requested_amount" in HTML
    assert "capital.leverage" in HTML
    assert "actuallyTrading=Boolean(liveGateOpen&&active&&execution)" in HTML
    assert "global gate:" in HTML


def test_open_subpage_contains_only_real_open_positions() -> None:
    assert "open:[openTradesSection]" in HTML
    assert (
        "control:[strategyTradeControlSection,ownerTestEntrySection]"
        in HTML
    )
    assert "monitor:[coinMonitorSection]" in HTML


def test_operational_strategy_table_excludes_inactive_historical_versions() -> None:
    block = HTML.split("function tradeStrategyRowsVisible()", 1)[1].split(
        "function tradeStrategyMonitorHtml", 1
    )[0]
    assert "return active||permission;" in block
    assert "startsWith('r1_')" not in block


def test_strategy_page_no_longer_duplicates_r1_batch_button() -> None:
    assert 'id="r1MicroLiveButton"' not in HTML
    assert "R1 MICRO_LIVE · 5×10 USDT · 1x" not in HTML
