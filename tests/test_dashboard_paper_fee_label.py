from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_paper_pnl_is_explicitly_labeled_before_fees() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert html.count("Gross PnL (до комиссий)") >= 2
    assert "Средний результат (до комиссий)" in html
    assert "Комиссии в paper PnL не включены." in html
    assert "Движение цены:" in html
