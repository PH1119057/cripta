from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_uses_wide_desktop_layout() -> None:
    html=(ROOT/"operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "width:min(1920px,calc(100vw - 24px));max-width:none" in html
    assert "main{width:100%;padding:14px}" in html
    assert "main{max-width:1280px" not in html
