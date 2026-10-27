"""Pure causal P90L90 Entry/Exit regression and fail-closed negative cases."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalEntry,
    P90L90CausalExit,
    causal_entry_state,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(step_minutes: int, count: int) -> tuple[Candle, ...]:
    length = timedelta(minutes=step_minutes)
    return tuple(
        Candle(
            symbol="LTCUSDT",
            timeframe=str(step_minutes),
            opened_at=START + length * i,
            closed_at=START + length * (i + 1),
            open=D("100"),
            high=D("110"),
            low=D("90"),
            close=D("100"),
            volume=D("1"),
        )
        for i in range(count)
    )


def _ready():
    # State January 10, 00:00 UTC: Jan 3-9 are seven FULL completed
    # UTC calendar days, with ATR200 and H9 windows already warmed up.
    end = START + timedelta(days=9)
    five = _bars(5, 9 * 288)
    fifteen = _bars(15, 9 * 96)
    state = causal_entry_state(five, fifteen, effective_at=end)
    assert state is not None
    return five, fifteen, state


def test_exact_raw_h9_both_sides_zero_depth_and_prev7d():
    _, _, s = _ready()
    assert s.l53_low_raw == D("90")
    assert s.l53_high_raw == D("110")
    assert s.raw_h9_union_span == D("20")
    assert s.long_depth_pct == s.short_depth_pct == D("0")
    assert s.working_width_pct == s.previous_seven_day_median == D("0")
    assert s.entry_edge("LONG") == D("90")
    assert s.entry_edge("SHORT") == D("110")
    assert s.opposite_tp("LONG") == D("110")
    assert s.opposite_tp("SHORT") == D("90")


def test_no_future_or_gap_or_missing_full_utc_day():
    f, q, s = _ready()
    assert causal_entry_state(f[:-1], q, effective_at=s.effective_at) is None
    assert causal_entry_state(f, q[:-1] + q[-1:], effective_at=s.effective_at) == s
    assert causal_entry_state(f[:100] + f[101:], q, effective_at=s.effective_at) is None
    assert causal_entry_state(f[2 * 288:], q, effective_at=s.effective_at) is None
    assert causal_entry_state(f, q[:-1], effective_at=s.effective_at) is None
    with pytest.raises(ValueError, match="five-minute"):
        causal_entry_state(f, q, effective_at=s.effective_at + timedelta(seconds=1))


def test_h9_exact_side_equality_no_gap_tolerance():
    f, q, s = _ready()
    last = q[-1]
    # A strict RAW side change, not the old 0.25 gap allowance.
    changed = Candle(
        symbol=last.symbol, timeframe=last.timeframe,
        opened_at=last.opened_at, closed_at=last.closed_at,
        open=last.open, high=last.high,
        low=D("89.99"), close=last.close, volume=last.volume,
    )
    result = causal_entry_state(f, q[:-1] + (changed,), effective_at=s.effective_at)
    assert result is not None
    assert result.long_depth_pct is None
    assert result.short_depth_pct == D("0")


def test_first_adjacent_crossing_and_no_retroactive_reentry():
    _, _, state = _ready()
    e = P90L90CausalEntry()
    e.update_geometry(state)
    at = state.effective_at + timedelta(seconds=1)
    assert e.on_trade(price=D("91"), event_at=at) == ()
    found = e.on_trade(price=D("90"), event_at=at + timedelta(microseconds=1))
    assert len(found) == 1
    assert found[0].side == "LONG"
    assert found[0].current_price == D("90")
    assert found[0].modeled_exchange_fill is False
    e.on_trade(price=D("91"), event_at=at + timedelta(seconds=2))
    assert e.on_trade(price=D("89"), event_at=at + timedelta(seconds=3)) == ()
    with pytest.raises(ValueError, match="non-monotone"):
        e.on_trade(price=D("91"), event_at=at)
    with pytest.raises(ValueError, match="non-monotone"):
        e.update_geometry(state)


def test_hard_stop_and_long_short_trailing_next_tick_only():
    pos = P90L90CausalExit(
        side="LONG", entry_price=D("100"), entry_sequence=7,
        state_bucket=0,
    )
    assert pos.stop == D("90")
    assert pos.on_trade(
        price=D("100.5"), sequence=8, bucket=0,
        candidate_opposite_raw_tp=None,
    ) is None
    assert pos.protected
    assert pos.stop == D("100.15")
    done = pos.on_trade(
        price=D("100.10"), sequence=9, bucket=0,
        candidate_opposite_raw_tp=None,
    )
    assert done is not None and done.reason == "PROTECTED_STOP"
    assert done.modeled_price == D("100.10")
    assert not done.modeled_exchange_fill
    with pytest.raises(ValueError, match="closed"):
        pos.on_trade(
            price=D("100"), sequence=10, bucket=0,
            candidate_opposite_raw_tp=None,
        )
    short = P90L90CausalExit(
        side="SHORT", entry_price=D("100"), entry_sequence=0,
        state_bucket=0,
    )
    assert short.stop == D("110")
    assert short.on_trade(
        price=D("99.5"), sequence=1, bucket=0,
        candidate_opposite_raw_tp=None,
    ) is None
    assert short.stop == D("99.85")
    event = short.on_trade(
        price=D("99.9"), sequence=2, bucket=0,
        candidate_opposite_raw_tp=None,
    )
    assert event is not None and event.reason == "PROTECTED_STOP"


def test_hard_stop_priority_and_dynamic_raw_tp_resting_vs_marketable():
    pos = P90L90CausalExit(
        side="LONG", entry_price=D("100"), entry_sequence=0,
        state_bucket=0,
    )
    # Newly computed TP at 102 is not immediately filled on this tick.
    assert pos.on_trade(
        price=D("100.2"), sequence=1, bucket=300,
        candidate_opposite_raw_tp=D("102"),
    ) is None
    assert pos.target == D("102")
    close = pos.on_trade(
        price=D("102.2"), sequence=2, bucket=300,
        candidate_opposite_raw_tp=None,
    )
    assert close is not None
    assert close.reason == "RESTING_TP"
    assert close.modeled_price == D("102")
    assert close.resting_maker_proxy
    marketable = P90L90CausalExit(
        side="LONG", entry_price=D("100"), entry_sequence=0,
        state_bucket=0,
    ).on_trade(
        price=D("102"), sequence=1, bucket=300,
        candidate_opposite_raw_tp=D("101"),
    )
    assert marketable is not None and marketable.reason == "MARKETABLE_TP"
    assert marketable.modeled_price == D("102")
    hard = P90L90CausalExit(
        side="LONG", entry_price=D("100"), entry_sequence=0,
        state_bucket=0,
    ).on_trade(
        price=D("88"), sequence=1, bucket=300,
        candidate_opposite_raw_tp=D("103"),
    )
    assert hard is not None and hard.reason == "HARD_STOP"
    assert hard.modeled_price == D("88")
