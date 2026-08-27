from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.research.portfolio_replay_v25 import (
    ReplayConfig,
    SignalEvent,
    replay_policy,
)


def stamp(minutes: int) -> datetime:
    return datetime(2026, 5, 18, tzinfo=UTC) + timedelta(minutes=minutes)


def signal(
    symbol: str,
    entry_minute: int,
    exit_minute: int,
    outcome: str = "reached_1p10",
) -> SignalEvent:
    move = {
        "reached_1p10": Decimal("1.10"),
        "floor_minus_0p50": Decimal("-0.50"),
        "initial_stop_before_0p50": Decimal("-1.00"),
        "baseline_initial_stop": Decimal("-1.00"),
    }[outcome]
    return SignalEvent(
        symbol=symbol,
        entry_at=stamp(entry_minute),
        exit_at=stamp(exit_minute),
        outcome=outcome,  # type: ignore[arg-type]
        move_pct=move,
        old_exit_reason="fixture",
    )


def test_first_three_overlaps_use_50_30_20_slots() -> None:
    result = replay_policy(
        [signal("A", 0, 60), signal("B", 1, 60), signal("C", 2, 60)],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    assert [trade.slot_fraction for trade in result.executed] == [
        Decimal("0.50"),
        Decimal("0.30"),
        Decimal("0.20"),
    ]
    assert result.max_open_positions == 3


def test_fourth_overlap_is_rejected_for_capacity() -> None:
    result = replay_policy(
        [
            signal("A", 0, 60),
            signal("B", 1, 60),
            signal("C", 2, 60),
            signal("D", 3, 60),
        ],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    assert len(result.executed) == 3
    assert [item.reason for item in result.skipped] == ["no_capacity"]


def test_same_symbol_signal_is_rejected_until_exit() -> None:
    result = replay_policy(
        [signal("A", 0, 60), signal("A", 30, 40), signal("B", 31, 45)],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    assert len(result.executed) == 2
    assert result.skipped[0].reason == "same_symbol_open"


def test_freed_high_priority_slot_is_reused() -> None:
    result = replay_policy(
        [signal("A", 0, 10), signal("B", 1, 50), signal("C", 11, 20)],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    by_symbol = {trade.symbol: trade for trade in result.executed}
    assert by_symbol["A"].slot_fraction == Decimal("0.50")
    assert by_symbol["B"].slot_fraction == Decimal("0.30")
    assert by_symbol["C"].slot_fraction == Decimal("0.50")


def test_burst_cap_blocks_third_entry_inside_15_minutes() -> None:
    result = replay_policy(
        [signal("A", 0, 60), signal("B", 5, 60), signal("C", 10, 60)],
        ReplayConfig(burst_window_minutes=15, burst_max_entries=2),
        policy_id="P",
        use_burst_cap=True,
    )
    assert len(result.executed) == 2
    assert result.skipped[0].reason == "burst_cap"


def test_exit_at_same_timestamp_frees_slot_before_new_entry() -> None:
    result = replay_policy(
        [
            signal("A", 0, 10),
            signal("B", 1, 50),
            signal("C", 2, 50),
            signal("D", 10, 20),
        ],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    by_symbol = {trade.symbol: trade for trade in result.executed}
    assert by_symbol["D"].slot_fraction == Decimal("0.50")


def test_fee_and_pnl_math_for_50_percent_slot() -> None:
    result = replay_policy(
        [signal("A", 0, 10, "reached_1p10")],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    trade = result.executed[0]
    assert trade.margin_usd == Decimal("49.900200")
    assert trade.notional_usd == Decimal("499.001996")
    assert trade.entry_fee_usd == Decimal("0.099800")
    assert trade.margin_usd + trade.entry_fee_usd == Decimal("50.000000")
    assert trade.gross_pnl_usd == Decimal("5.489022")
    assert trade.exit_fee_usd == Decimal("0.099800")
    assert trade.net_pnl_usd == Decimal("5.289422")


def test_stop_uses_taker_exit_fee() -> None:
    result = replay_policy(
        [signal("A", 0, 10, "floor_minus_0p50")],
        ReplayConfig(),
        policy_id="P",
        use_burst_cap=False,
    )
    trade = result.executed[0]
    assert trade.gross_pnl_usd == Decimal("-2.495010")
    assert trade.entry_fee_usd == Decimal("0.099800")
    assert trade.exit_fee_usd == Decimal("0.274451")
    assert trade.net_pnl_usd == Decimal("-2.869261")
