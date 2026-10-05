from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
UI = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")


def test_operator_money_surfaces_are_after_commission_only() -> None:
    forbidden = (
        "До комиссий",
        "до комиссий",
        "Gross PnL",
        "Комиссия входа",
        "Комиссия выхода",
        "gross:",
        "entry fee:",
        "exit fee:",
    )
    for token in forbidden:
        assert token not in UI
    assert "После комиссий" in UI
    assert "net_pnl_to_close" in UI
    assert "net_pnl_usdt" in UI


def test_negative_money_and_mae_are_red() -> None:
    assert ".loss{color:#ff6b6b}" in UI
    assert "Number(x.pnl_usdt)<0?'loss'" in UI
    assert "Number(x.mae_pct)<0?'loss'" in UI
    assert "pnl<0?'loss'" in UI


def test_dashboard_read_model_computes_net_from_fee_facts() -> None:
    assert "PAPER_MAKER_FEE_RATE = 0.00020" in APP
    assert "PAPER_TAKER_FEE_RATE = 0.00055" in APP
    assert "REAL_IMMEDIATE_CLOSE_FEE_RATE = 0.00055" in APP
    assert "def _paper_net_pnl_usdt(" in APP
    assert 'JOIN runtime.executions e ON e.exec_id=xid.exec_id' in APP
    assert '"net_pnl_to_close": net_to_close' in APP
    assert '"pnl_basis": "AFTER_COMMISSIONS"' in APP
