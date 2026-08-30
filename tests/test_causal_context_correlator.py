from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
CORRELATOR = ROOT / "operations" / "monitoring" / "causal_context_correlator.py"


def test_correlator_is_observation_only_and_has_no_trading_mutations() -> None:
    source = CORRELATOR.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in ("place_order", "cancel_order", "set_stop", "close_position"):
        assert forbidden not in source
    assert "'mode','OBSERVED_CONTEXT'" in source
    assert "consumed_context" in source
    assert "'consumed_context','NOT_RECORDED'" in source


def test_correlator_uses_only_context_not_after_event() -> None:
    source = CORRELATOR.read_text(encoding="utf-8")
    assert source.count("observed_at <= to_timestamp(e.event_ms/1000.0)") == 2
    assert "latest_snapshot_not_after_event" in source
    assert "MAYAK_AND_DISPATCHER_CAUSAL_PRIOR" in source
    assert "ON CONFLICT(event_type,reference_id) DO NOTHING" in source


def test_correlator_covers_signal_decision_fill_command_and_position_transition() -> None:
    source = CORRELATOR.read_text(encoding="utf-8")
    for event_type in (
        "SIGNAL",
        "ENTRY_DECISION",
        "TRADE_COMMAND",
        "FILL",
        "POSITION_TRANSITION",
    ):
        assert f"'{event_type}'" in source
