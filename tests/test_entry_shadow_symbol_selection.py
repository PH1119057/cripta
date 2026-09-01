from pathlib import Path


SCANNER = Path("operations/monitoring/entry_shadow_scanner.py").read_text(encoding="utf-8")
DASHBOARD = Path("operations/dashboard/app.py").read_text(encoding="utf-8")


def test_scanner_publishes_and_persists_only_owner_enabled_symbols() -> None:
    assert "def enabled_symbols(" in SCANNER
    assert "if item.symbol in monitored_symbols" in SCANNER
    assert "if item.symbol not in monitored_symbols:" in SCANNER
    assert "selected_symbols = enabled_symbols(connection)" in SCANNER
    assert '"available_symbols": sorted(item.symbol for item in snapshot.assets)' in SCANNER


def test_closed_entry_gate_does_not_block_management_of_open_positions() -> None:
    command_branch = DASHBOARD.split('elif path == "/api/live/gate":', 1)[1].split(
        'if path != "/api/bots/action":', 1
    )[0]
    assert 'kind not in {' in command_branch
    assert '"break_even"' in command_branch
    assert '"close"' in command_branch
    assert '"Торговый шлюз закрыт"' not in command_branch
    assert 'request.get("confirmed") is not True' in command_branch
