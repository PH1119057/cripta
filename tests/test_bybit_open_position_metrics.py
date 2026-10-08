from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_exchange_mark_and_unrealised_pnl_are_present_in_live_read_model():
    s=(ROOT/"operations/dashboard/app.py").read_text(encoding="utf-8")
    assert '"mark_price": ticker.get("mark_price") or raw.get("markPrice")' in s
    assert '"bybit_unrealised_pnl": raw.get("unrealisedPnl")' in s
    assert '"bybit_position_im": raw.get("positionIM")' in s


def test_unowned_pnl_is_not_falsely_labeled_after_fees():
    s=(ROOT/"operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "bybitPnl=p.bybit_unrealised_pnl==null?null:Number(p.bybit_unrealised_pnl)" in s
    assert "pnl=netPnl==null?bybitPnl:netPnl" in s
    assert "Bybit unrealised PnL · без комиссий" in s
    assert "marginBasis=bybitMargin>0?bybitMargin:actualMargin" in s
