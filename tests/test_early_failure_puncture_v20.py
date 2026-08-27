from datetime import UTC, datetime, timedelta

from bybit_workbench.research.early_failure_puncture_v20 import (
    PunctureConfig,
    classify_early_failure,
)
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries


def _path(moves: tuple[float, ...]) -> PathSeries:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",  # type: ignore[arg-type]
        touch_at=start,
        entry_price=10.0,
        source_row={},
    )
    timestamps = tuple(start.timestamp() + index for index in range(len(moves)))
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=moves,
        available_until=start + timedelta(hours=72),
        coverage_until=start + timedelta(hours=72),
    )


def test_puncture_recovers_before_minus_three() -> None:
    result = classify_early_failure(
        _path((-0.2, -1.0, -1.2, -0.8, -0.1, 0.0, 0.3)),
        "development",
        PunctureConfig(),
    )
    assert result.class_name == "puncture_recovered_before_3"
    assert result.recovered_entry_72h
    assert not result.hit_minus_3_72h
    assert result.seconds_to_entry_recovery == 4.0


def test_deep_three_then_recovers() -> None:
    result = classify_early_failure(
        _path((-0.2, -1.0, -2.0, -3.0, -1.0, 0.0)),
        "holdout",
        PunctureConfig(),
    )
    assert result.class_name == "deep_3_then_recovered"
    assert result.hit_minus_3_72h
    assert result.recovered_entry_72h
    assert result.hit_minus_3_before_entry_recovery


def test_deep_three_without_recovery() -> None:
    result = classify_early_failure(
        _path((-0.2, -1.0, -2.0, -3.0, -4.0, -2.0)),
        "holdout",
        PunctureConfig(),
    )
    assert result.class_name == "deep_3_no_recovery"
    assert result.hit_minus_3_72h
    assert not result.recovered_entry_72h


def test_stuck_below_entry_without_three() -> None:
    result = classify_early_failure(
        _path((-0.2, -1.0, -1.4, -1.7, -0.8, -0.2)),
        "holdout",
        PunctureConfig(),
    )
    assert result.class_name == "no_recovery_no_3"
    assert not result.hit_minus_3_72h
    assert not result.recovered_entry_72h
