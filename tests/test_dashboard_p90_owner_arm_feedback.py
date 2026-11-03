from pathlib import Path

HTML = Path("operations/dashboard/index.html")


def test_owner_arm_latest_state_visible_without_forcing_technical_details_open() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "function showAuthoritativeP90ArmState(exactArmed,operation)" in html
    assert "panel.open=true" not in html
    assert "P90 REAL v3 ARMED · mainnet gate OPEN" in html
    assert "showAuthoritativeP90ArmState(exactArmed,operation)" in html
    assert "Технические детали последней операции OWNER ARM" in html


def test_runtime_not_ready_banner_is_not_reported_as_release_mismatch() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "REAL_ENTRY_RUNTIME_NOT_READY" in html
    assert "REAL Entry временно не готов." in html
    assert "Несовпадение LIVE-arm release не обнаружено" in html
    assert "Observer/runtime ещё завершает обязательную подготовку" in html
    assert "Это сообщение само по себе не означает несовпадение release." in html
