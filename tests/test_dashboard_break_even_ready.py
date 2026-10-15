from pathlib import Path


def test_break_even_highlight_requires_fresh_fee_aware_profit_buffer():
    html = Path('operations/dashboard/index.html').read_text(encoding='utf-8')
    block = html.split('function breakEvenVisualReady(p){', 1)[1].split('function renderOpenRailPositions', 1)[0]
    for token in ('p.executable_close_price', 'p.net_pnl_to_close', 'p.refreshed_at_epoch_ms', 'p.stop_loss', '0.002', 'fresh>10000'):
        assert token in block
    assert '.action.break-even-ready' in html
    assert "breakEvenVisualReady(p)?'break-even-ready':''" in html
    assert "tradeCommand(\\'break_even\\'" in html


def test_be_button_keeps_same_trade_command_and_never_auto_submits():
    html = Path('operations/dashboard/index.html').read_text(encoding='utf-8')
    block = html.split('function breakEvenVisualReady(p){', 1)[1].split('function renderOpenRailPositions', 1)[0]
    assert 'fetch(' not in block
    assert 'tradeCommand(' not in block
    assert 'livePost(' not in block
