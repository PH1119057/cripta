from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_paper_pnl_is_explicitly_labeled_after_fees() -> None:
    html = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert html.count("После комиссий") >= 2
    assert "Gross PnL (до комиссий)" not in html
    assert "Комиссии в paper PnL не включены." not in html
