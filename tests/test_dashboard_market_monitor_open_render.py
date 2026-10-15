from pathlib import Path


def test_market_monitor_refreshes_on_open_trades_page():
    html = Path('operations/dashboard/index.html').read_text(encoding='utf-8')
    assert "if(page==='monitor'||page==='open')renderStrategyMonitor(d.strategy_monitor);" in html
    assert 'open:[openTradesSection,coinMonitorSection,marketIndicatorSection]' in html
