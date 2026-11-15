from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bybit_workbench.research.clean_zone_lifecycle_p451 import (  # noqa: I001
    CleanZoneLifecycleDetector,
    LifecycleTouchEvent,
    ZonePhase,
    _touch_metrics,
    build_core_feature,
    classify_touch_outcome,
)
from bybit_workbench.research.multi_touch_sr_p45 import Candle, CoreSignal


def _candles(closes: list[float]) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        opened_at = start + timedelta(minutes=15 * index)
        rows.append(
            Candle(
                opened_at=opened_at,
                closed_at=opened_at + timedelta(minutes=15),
                open=previous,
                high=max(previous, close) + 0.2,
                low=min(previous, close) - 0.2,
                close=close,
                volume=1.0,
            )
        )
        previous = close
    return tuple(rows)


def _phase(candles: tuple[Candle, ...], *, role: str = "support") -> ZonePhase:
    assert role in {"support", "resistance"}
    typed_role = "support" if role == "support" else "resistance"
    return ZonePhase(
        phase_id=1,
        chain_id=1,
        center=10.0,
        half_width=0.5,
        chain_origin_at=candles[0].closed_at,
        phase_started_at=candles[0].closed_at,
        confirmed_at=candles[0].closed_at,
        origin_role=typed_role,
        role=typed_role,
        phase_origin="pivot",
        source_pivots=1,
        support_pivots=1 if typed_role == "support" else 0,
        resistance_pivots=1 if typed_role == "resistance" else 0,
    )


def test_confirmed_break_ends_phase_and_starts_opposite_phase_with_reset() -> None:
    candles = _candles([10.0, 9.3, 9.2, 8.0])
    detector = CleanZoneLifecycleDetector("X", candles, pivot_span=1, atr_period=2)
    phase = _phase(candles)
    phase.retest_count = 3
    detector.phases.append(phase)
    detector._update_phase(phase, candles[1], 1, 1.0)
    assert phase.active is True
    detector._update_phase(phase, candles[2], 2, 1.0)
    assert phase.active is False
    assert phase.end_reason == "confirmed_break"
    reversal = detector.phases[-1]
    assert reversal.role == "resistance"
    assert reversal.phase_origin == "role_reversal"
    assert reversal.retest_count == 0
    assert reversal.prior_phase_retests == 3
    assert reversal.armed_for_retest is False


def test_phase_expiry_is_absolute_and_does_not_extend_on_pivots() -> None:
    candles = _candles([10.0] * 4)
    detector = CleanZoneLifecycleDetector(
        "X", candles, pivot_span=1, atr_period=2, phase_max_age_hours=1.0
    )
    phase = _phase(candles)
    detector.phases.append(phase)
    detector._expire_old_phases(phase.phase_started_at + timedelta(minutes=59))
    assert phase.active is True
    detector._expire_old_phases(phase.phase_started_at + timedelta(hours=1))
    assert phase.active is False
    assert phase.end_reason == "age_expiry"


def test_merge_requires_same_role() -> None:
    candles = _candles([10.0] * 4)
    detector = CleanZoneLifecycleDetector("X", candles, pivot_span=1, atr_period=2)
    support = detector._new_pivot_phase(
        price=10.0,
        half_width=0.5,
        role="support",
        origin_at=candles[0].closed_at,
        confirmed_at=candles[0].closed_at,
    )
    detector._merge_or_create(
        price=10.1,
        half_width=0.5,
        role="resistance",
        origin_at=candles[1].closed_at,
        confirmed_at=candles[2].closed_at,
    )
    assert len(detector.phases) == 2
    assert detector.phases[1].role == "resistance"
    assert detector.phases[1].chain_id != support.chain_id


def test_core_test_ordinal_requires_rearm() -> None:
    candles = _candles([10.0] * 20)
    detector = CleanZoneLifecycleDetector("X", candles, pivot_span=1, atr_period=2)
    detector.processed_index = 4
    phase = _phase(candles)
    phase.retest_count = 2
    phase.armed_for_retest = False
    detector.phases.append(phase)
    signal = CoreSignal(
        "X",
        "Long",
        candles[5].opened_at + timedelta(minutes=1),
        10.0,
        "favorable_first",
        "favorable_first",
    )
    row = build_core_feature(
        signal,
        detector=detector,
        start=candles[0].opened_at,
        calibration_days=30,
        p44_values={},
        p44_q25={},
    )
    assert row.phase_found is True
    assert row.independent_test_ready is False
    assert row.current_test_ordinal is None


def test_touch_outcome_bounce() -> None:
    candles = _candles([10.0, 10.2, 11.7])
    event = LifecycleTouchEvent(
        "X", 1, 1, "support", "pivot", candles[0].closed_at, 0, 1, 1, 0, 0,
        10.0, 9.5, 10.5, 1.0, "unresolved", None, None,
    )
    classified = classify_touch_outcome(candles, event, horizon_bars=2)
    assert classified.outcome == "bounce"
    assert classified.outcome_bars == 2


def test_touch_outcome_clean_break() -> None:
    candles = _candles([10.0, 9.3, 9.2])
    event = LifecycleTouchEvent(
        "X", 1, 1, "support", "pivot", candles[0].closed_at, 0, 1, 1, 0, 0,
        10.0, 9.5, 10.5, 1.0, "unresolved", None, None,
    )
    classified = classify_touch_outcome(candles, event, horizon_bars=2)
    assert classified.outcome == "clean_break"
    assert classified.outcome_bars == 2


def test_touch_outcome_false_break_reclaim() -> None:
    candles = _candles([10.0, 9.3, 10.0])
    event = LifecycleTouchEvent(
        "X", 1, 1, "support", "pivot", candles[0].closed_at, 0, 1, 1, 0, 0,
        10.0, 9.5, 10.5, 1.0, "unresolved", None, None,
    )
    classified = classify_touch_outcome(candles, event, horizon_bars=2)
    assert classified.outcome == "false_break_reclaim"
    assert classified.outcome_bars == 2


def test_touch_metrics_are_mutually_exclusive() -> None:
    candles = _candles([10.0])
    base = LifecycleTouchEvent(
        "X", 1, 1, "support", "pivot", candles[0].closed_at, 0, 1, 1, 0, 0,
        10.0, 9.5, 10.5, 1.0, "bounce", 1, candles[0].closed_at,
    )
    rows = [
        base,
        LifecycleTouchEvent(
            "X", 2, 2, "support", "pivot", candles[0].closed_at, 0, 2, 1, 0, 0,
            10.0, 9.5, 10.5, 1.0, "clean_break", 2, candles[0].closed_at,
        ),
        LifecycleTouchEvent(
            "X", 3, 3, "support", "pivot", candles[0].closed_at, 0, 3, 1, 0, 0,
            10.0, 9.5, 10.5, 1.0, "false_break_reclaim", 2, candles[0].closed_at,
        ),
        LifecycleTouchEvent(
            "X", 4, 4, "support", "pivot", candles[0].closed_at, 0, 4, 1, 0, 0,
            10.0, 9.5, 10.5, 1.0, "unresolved", None, None,
        ),
    ]
    metrics = _touch_metrics(rows)
    assert metrics.sample == 4
    assert metrics.bounce == 1
    assert metrics.clean_break == 1
    assert metrics.false_break_reclaim == 1
    assert metrics.unresolved == 1
    assert metrics.bounce_pct == 25.0


def test_role_reversal_first_retest_is_explicit_core_feature() -> None:
    candles = _candles([10.0] * 20)
    detector = CleanZoneLifecycleDetector("X", candles, pivot_span=1, atr_period=2)
    detector.processed_index = 4
    phase = ZonePhase(
        phase_id=2,
        chain_id=1,
        center=10.0,
        half_width=0.5,
        chain_origin_at=candles[0].closed_at,
        phase_started_at=candles[2].closed_at,
        confirmed_at=candles[2].closed_at,
        origin_role="support",
        role="resistance",
        phase_origin="role_reversal",
        source_pivots=0,
        support_pivots=0,
        resistance_pivots=0,
        prior_phase_id=1,
        prior_phase_retests=3,
        armed_for_retest=True,
    )
    detector.phases.append(phase)
    signal = CoreSignal(
        "X",
        "Short",
        candles[5].opened_at + timedelta(minutes=1),
        10.0,
        "favorable_first",
        "favorable_first",
    )
    row = build_core_feature(
        signal,
        detector=detector,
        start=candles[0].opened_at,
        calibration_days=30,
        p44_values={},
        p44_q25={},
    )
    assert row.independent_test_ready is True
    assert row.current_test_ordinal == 1
    assert row.role_reversal_phase is True
    assert row.first_retest_after_break is True
    assert row.prior_phase_retests == 3


def test_touch_outcome_resistance_is_mirrored() -> None:
    candles = _candles([10.0, 9.8, 8.3])
    event = LifecycleTouchEvent(
        "X", 1, 1, "resistance", "pivot", candles[0].closed_at, 0, 1, 1, 0, 0,
        10.0, 9.5, 10.5, 1.0, "unresolved", None, None,
    )
    classified = classify_touch_outcome(candles, event, horizon_bars=2)
    assert classified.outcome == "bounce"
