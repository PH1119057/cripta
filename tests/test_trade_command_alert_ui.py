from pathlib import Path

UI = Path("operations/dashboard/index.html").read_text(encoding="utf-8")


def test_trade_command_status_has_visible_severity() -> None:
    assert "trade-command-alert error" in UI
    assert "trade-command-alert warn" in UI
    assert "trade-command-alert success" in UI
    assert "КРИТИЧЕСКИЙ ОТКАЗ КОМАНДЫ" in UI


def test_bybit_not_modified_is_not_presented_as_trade_rejection() -> None:
    assert "retCode=34040" in UI
    assert "Это не отказ сделки." in UI
    assert "Bybit: изменение не требовалось" in UI


def test_last_command_includes_timestamp() -> None:
    assert "new Date(when).toLocaleString('ru-RU')" in UI
