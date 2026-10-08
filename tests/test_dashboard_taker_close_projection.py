from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_net_close_estimated_for_unowned_positions_as_well():
    source = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    block = source[source.index("        # Conservative immediate taker-exit estimate"):source.index("        positions.append(", source.index("        # Conservative immediate taker-exit estimate"))]
    assert 'if gross_to_close is not None:' in block
    assert 'if gross_to_close is not None and ownership is not None:' not in block
    assert "entry_price * position_size * REAL_IMMEDIATE_CLOSE_FEE_RATE" in block
    assert "executable_value * position_size * REAL_IMMEDIATE_CLOSE_FEE_RATE" in block
    assert '"entry_fee_basis": "ACTUAL" if ownership is not None else "ESTIMATED_TAKER"' in source


def test_current_price_and_taker_net_display_without_exchange_unrealised_pnl():
    app = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert '"mark_price": ticker.get("mark_price") or ticker.get("last_price") or raw.get("markPrice")' in app
    assert 'после комиссий · закрытие тейкером (оценка)' in html
    assert "pnl=p.net_pnl_to_close==null?null:Number(p.net_pnl_to_close)" in html
    assert "p.bybit_unrealised_pnl" not in html
    assert "pnl/actualMargin*100" in html
