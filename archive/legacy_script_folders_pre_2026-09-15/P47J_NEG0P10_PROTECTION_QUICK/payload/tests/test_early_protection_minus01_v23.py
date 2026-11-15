from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from bybit_workbench.research.early_protection_minus01_v23 import Config, Result, scan, summarize
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, TradeDayCache


class FakeCache(TradeDayCache):
    def __init__(self, tape: object) -> None:
        self.tape = tape
        self.hits = 0
        self.misses = 0

    def get(self, path: Path) -> object:
        self.hits += 1
        return self.tape


def _signal() -> CoreSignal:
    return CoreSignal(
        symbol="BTCUSDT",
        direction="Long",
        touch_at=datetime(2026, 6, 1, tzinfo=UTC),
        entry_price=100.0,
        source_row={},
    )


def _tape(moves: list[float]) -> object:
    base = datetime(2026, 6, 1, tzinfo=UTC).timestamp()
    return SimpleNamespace(
        timestamps=[base + index for index in range(len(moves))],
        prices=[100.0 * (1.0 + move / 100.0) for move in moves],
    )


def test_minus_01_floor_stops_before_continuation() -> None:
    result = scan(
        _signal(),
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.11, 0.05, -0.11, 1.2])),
        Config(),
    )
    assert result.outcome == "floor_minus_0p10"
    assert result.seconds_activation_to_event == 2.0


def test_continuation_wins_before_floor() -> None:
    result = scan(
        _signal(),
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.11, 0.04, 0.5, 1.11, -0.2])),
        Config(),
    )
    assert result.outcome == "reached_1p10"
    assert result.seconds_activation_to_event == 3.0


def test_no_activation_is_censored() -> None:
    result = scan(
        _signal(),
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.05, -0.05, 0.09])),
        Config(),
    )
    assert result.outcome == "data_end_no_activation"
    assert result.activation_at is None


def test_summary_remain_in_battle_includes_alive() -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    results = [
        Result("BTCUSDT", now, "floor_minus_0p10", now, now, 1.0, False),
        Result("BTCUSDT", now, "reached_1p10", now, now, 2.0, False),
        Result("BTCUSDT", now, "data_end_after_activation", now, None, None, False),
    ]
    row = summarize(results, "ALL9")
    assert row["remain_in_battle"] == 2
    assert row["stopped_minus_0p10"] == 1
    assert row["reached_plus_1p10_first"] == 1
