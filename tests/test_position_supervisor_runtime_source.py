from pathlib import Path


def test_post_fill_supervisor_is_independent_from_entry_runtime() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    assert "entry_shadow" not in source
    assert "ENTRY_STATUS" not in source
    assert "mayak_v2/status.json" in source


def test_supervisor_runtime_contains_no_trading_mutations() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    for forbidden in ("trade_commands", "trailing_stop", "set_trading_stop", "place_order"):
        assert forbidden not in source
