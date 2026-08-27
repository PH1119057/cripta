from datetime import UTC, datetime

import numpy as np

from bybit_workbench.research.exit_state_machine_eo4 import Result, Trade, ZoneEvent, chronological_replay, classify, simulate


def _series(closes: list[float]):
    ts = np.arange(len(closes), dtype=np.float64) * 60 + datetime(2026, 5, 18, tzinfo=UTC).timestamp()
    close = np.asarray(closes, dtype=np.float64)
    return ts, close + 0.01, close - 0.01, close


def test_structure_is_mirrored() -> None:
    assert classify("Long", "support", "clean_break") == "protective_clean_break_against"
    assert classify("Short", "resistance", "clean_break") == "protective_clean_break_against"
    assert classify("Long", "resistance", "clean_break") == "obstacle_clean_break_with"


def test_structural_exit_uses_first_causal_close() -> None:
    start = datetime(2026, 5, 18, tzinfo=UTC)
    trade = Trade("X", "Long", start, start, 100.0)
    event = ZoneEvent(datetime.fromtimestamp(start.timestamp() + 90, tz=UTC), "support|clean_break")
    result = simulate(trade, "structural_exit", _series([100.2, 100.1, 99.8]), [event])
    assert result.exit_reason == "protective_break"
    assert result.exit_price == 100.1


def test_runner_is_not_closed_at_baseline_target() -> None:
    start = datetime(2026, 5, 18, tzinfo=UTC)
    trade = Trade("X", "Long", start, start, 100.0)
    event = ZoneEvent(datetime.fromtimestamp(start.timestamp() + 30, tz=UTC), "resistance|clean_break")
    result = simulate(trade, "structural_runner", _series([100.2, 101.2, 102.0]), [event])
    assert result.exit_reason == "data_end"
    assert result.final_state == "RUNNER"


def test_chronology_blocks_overlapping_fill() -> None:
    def row(touch: str, fill: str, exit_at: str) -> Result:
        return Result("baseline", "X", "Long", touch, fill, exit_at, 101.0, 1.0, 0.9, 9.0, "baseline_target", "PROVEN", 1.0, 1.1, -0.1, 0)
    rows = [
        row("2026-05-18T00:00:00+00:00", "2026-05-18T00:00:00+00:00", "2026-05-18T02:00:00+00:00"),
        row("2026-05-18T01:00:00+00:00", "2026-05-18T01:00:00+00:00", "2026-05-18T03:00:00+00:00"),
    ]
    audit, summary = chronological_replay(rows)
    assert [x["accepted"] for x in audit] == [True, False]
    assert summary[0]["signals_blocked"] == 1
