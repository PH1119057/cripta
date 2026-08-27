from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from bybit_workbench.research.early_protection_plus05_minus05_v24 import (
    Config,
    Result,
    scan,
    summarize,
)
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


def test_initial_stop_remains_minus_one_until_activation() -> None:
    result = scan(
        _signal(),
        "early_be",
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.2, -0.4, -1.01, 0.6])),
        Config(),
    )
    assert result.outcome == "initial_stop_before_0p50"
    assert result.activation_at is None


def test_minus_05_floor_stops_after_plus_05_activation() -> None:
    result = scan(
        _signal(),
        "early_be",
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.2, 0.51, 0.1, -0.51, 1.2])),
        Config(),
    )
    assert result.outcome == "floor_minus_0p50"
    assert result.seconds_activation_to_event == 2.0


def test_continuation_wins_before_minus_05_floor() -> None:
    result = scan(
        _signal(),
        "core_take",
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.51, 0.2, 0.8, 1.11, -0.6])),
        Config(),
    )
    assert result.outcome == "reached_1p10"
    assert result.seconds_activation_to_event == 3.0


def test_jump_over_activation_and_continuation_counts_same_tick() -> None:
    result = scan(
        _signal(),
        "core_take",
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 1.2, -0.6])),
        Config(),
    )
    assert result.outcome == "reached_1p10"
    assert result.activation_at == result.event_at
    assert result.same_timestamp_event is True


def test_no_activation_is_censored() -> None:
    result = scan(
        _signal(),
        "data_end",
        {"2026-06-01": Path("day")},
        FakeCache(_tape([0.0, 0.2, -0.2, 0.49])),
        Config(),
    )
    assert result.outcome == "data_end_no_activation"
    assert result.activation_at is None


def test_old_early_be_summary_isolated() -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    results = [
        Result("BTCUSDT", now, "early_be", "floor_minus_0p50", now, now, 2.0, False),
        Result("BTCUSDT", now, "early_be", "reached_1p10", now, now, 3.0, False),
        Result("BTCUSDT", now, "core_take", "reached_1p10", now, now, 1.0, False),
    ]
    row = summarize(results, "ALL9", "OLD_EARLY_BE")
    assert row["signals"] == 2
    assert row["stopped_minus_0p50"] == 1
    assert row["reached_plus_1p10_first"] == 1
    assert row["remain_in_battle"] == 1
