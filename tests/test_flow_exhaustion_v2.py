from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bybit_workbench.research.flow_exhaustion_v2 import (
    ExhaustionResearchConfig,
    MicroTape,
    P31SourceSignal,
    _analyse_micro_features,
    _combine_tapes,
    _directional_delta_pct,
    _transition_state,
    _window_notional,
)


def _signal(direction: str = "Long", flow_state: str = "pressure_continues") -> P31SourceSignal:
    typed_direction = "Long" if direction == "Long" else "Short"
    return P31SourceSignal(
        symbol="UNIUSDT",
        direction=typed_direction,
        candidate_bar_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        entry_price=Decimal("100"),
        touch_at=datetime(2026, 8, 16, 12, 5, tzinfo=UTC),
        hourly_alignment="neutral",
        zone_gap_percent=Decimal("0"),
        exact_first_0_5_vs_0_5="favorable_first",
        exact_first_0_5_vs_1_0="favorable_first",
        exact_mfe_30m_pct=Decimal("0.8"),
        exact_mae_30m_pct=Decimal("-0.2"),
        p31_flow_state=flow_state,
        p31_pressure_delta_pct=Decimal("-40"),
        p31_reversal_delta_pct=Decimal("-5"),
    )


def _tape() -> MicroTape:
    start = datetime(2026, 8, 16, 12, 0, tzinfo=UTC).timestamp()
    timestamps = tuple(start + offset for offset in range(0, 420, 10))
    prices = tuple(101.0 - offset * 0.001 for offset in range(len(timestamps)))
    buys: list[float] = [0.0]
    sells: list[float] = [0.0]
    for index in range(len(timestamps)):
        # Heavy selling before touch; buys take over after 12:05.
        after_touch = timestamps[index] >= start + 300
        buys.append(buys[-1] + (80.0 if after_touch else 20.0))
        sells.append(sells[-1] + (20.0 if after_touch else 80.0))
    return MicroTape(timestamps, prices, tuple(buys), tuple(sells))


def test_directional_delta_mirrors_short() -> None:
    assert _directional_delta_pct("Long", 80.0, 20.0) == 60.0
    assert _directional_delta_pct("Short", 80.0, 20.0) == -60.0


def test_window_notional_uses_prefix_ranges() -> None:
    tape = _tape()
    start = tape.timestamps[0]
    buy, sell = _window_notional(tape, start, start + 20)
    assert buy == 40.0
    assert sell == 160.0


def test_combine_tapes_preserves_prefix_totals() -> None:
    tape = _tape()
    split = 20
    first = MicroTape(
        tape.timestamps[:split],
        tape.prices[:split],
        tape.buy_prefix[: split + 1],
        tape.sell_prefix[: split + 1],
    )
    second_buys = tuple(value - tape.buy_prefix[split] for value in tape.buy_prefix[split:])
    second_sells = tuple(value - tape.sell_prefix[split] for value in tape.sell_prefix[split:])
    second = MicroTape(
        tape.timestamps[split:],
        tape.prices[split:],
        second_buys,
        second_sells,
    )
    combined = _combine_tapes(first, second)
    assert combined.timestamps == tape.timestamps
    assert combined.buy_prefix[-1] == tape.buy_prefix[-1]
    assert combined.sell_prefix[-1] == tape.sell_prefix[-1]


def test_late_flip_after_touch_is_separated_from_continued_pressure() -> None:
    assert _transition_state("pressure_continues", 20.0) == "late_flip_after_touch"
    assert _transition_state("pressure_continues", -20.0) == "pressure_continues"


def test_preflip_can_hold_or_fail_after_touch() -> None:
    assert _transition_state("pressure_then_reversal", 1.0) == "preflip_holds"
    assert _transition_state("pressure_then_reversal", -1.0) == "preflip_fails"


def test_micro_features_detect_post_touch_takeover() -> None:
    signal = _signal("Long", "pressure_continues")
    features = _analyse_micro_features(
        signal,
        _tape(),
        config=ExhaustionResearchConfig(),
    )
    assert features.pre30_delta_pct < 0
    assert features.post30_delta_pct > 0
    assert features.transition_30s == "late_flip_after_touch"
