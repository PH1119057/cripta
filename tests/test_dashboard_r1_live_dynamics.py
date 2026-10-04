from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_trade_table_shows_current_r1_entry_dynamics() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "Сейчас / до Entry" in html
    assert "function tradeStrategyMonitorHtml(item)" in html
    assert "WAITING · геометрия ещё не готова" in html
    assert "Entry '+price(x.entry_price)+' · до " in html
    assert "tradeStrategyMonitorData=d.trade_strategy_monitor" in html


def test_open_live_state_exposes_lightweight_strategy_monitor() -> None:
    source = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "def strategy_trade_monitor_state()" in source
    assert '"trade_strategy_monitor": strategy_trade_monitor_state()' in source
    for field in (
        '"strategy_id"',
        '"strategy_version"',
        '"direction"',
        '"state"',
        '"current_price"',
        '"entry_price"',
        '"distance_pct"',
    ):
        assert field in source
