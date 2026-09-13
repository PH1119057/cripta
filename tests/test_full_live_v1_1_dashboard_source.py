from pathlib import Path

APP = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
HTML = Path("operations/dashboard/index.html").read_text(encoding="utf-8")


def test_closed_trade_rows_have_strategy_and_exchange_reasons() -> None:
    assert "Причина стратегии" in HTML
    assert "Причина биржи" in HTML
    assert "strategy_reason" in APP


def test_closed_trade_card_remains_but_legacy_entry_funnel_is_not_on_trade_page() -> None:
    assert "closed-trade-row" in HTML
    assert "cardHtml" in HTML
    assert "m3EntryFunnel" not in HTML
    assert "Воронка M3 Entry" not in HTML
    # Historical read-model may remain server-side; it is no longer operator UI.
    assert "entry_funnel" in APP


def test_strategy_controls_show_installed_and_loaded_values() -> None:
    assert "installed_version" in APP
    assert "loaded_version" in APP
    assert "DISABLED/FALLBACK_SAFE" in APP
