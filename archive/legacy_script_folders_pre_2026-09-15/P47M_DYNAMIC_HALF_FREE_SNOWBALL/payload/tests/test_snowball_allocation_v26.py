from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.research.portfolio_replay_v25 import SignalEvent
from bybit_workbench.research.snowball_allocation_v26 import SnowballConfig, replay_snowball


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


def test_overlapping_budgets_follow_half_of_free_deposit() -> None:
    result = replay_snowball(
        [
            signal("A", 0, 60),
            signal("B", 1, 60),
            signal("C", 2, 60),
            signal("D", 3, 60),
            signal("E", 4, 60),
        ],
        SnowballConfig(),
        policy_id="P",
    )
    allocations = [trade.allocation_budget_usd for trade in result.executed]
    assert allocations == [
        Decimal("50.000000"),
        Decimal("25.000000"),
        Decimal("12.500000"),
        Decimal("6.250000"),
        Decimal("3.125000"),
    ]
    assert result.max_open_positions == 5


def test_minimum_six_accepts_first_four_and_skips_fifth_overlap() -> None:
    result = replay_snowball(
        [
            signal("A", 0, 60),
            signal("B", 1, 60),
            signal("C", 2, 60),
            signal("D", 3, 60),
            signal("E", 4, 60),
        ],
        SnowballConfig(minimum_allocation_usd=Decimal("6")),
        policy_id="P",
    )
    assert len(result.executed) == 4
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "below_minimum_allocation"
    assert result.skipped[0].proposed_allocation_usd == Decimal("3.125000")


def test_same_symbol_overlap_is_allowed_in_virtual_research() -> None:
    result = replay_snowball(
        [signal("A", 0, 60), signal("A", 1, 30)],
        SnowballConfig(),
        policy_id="P",
    )
    assert len(result.executed) == 2
    assert result.max_open_positions == 2


def test_closed_trade_releases_deposit_before_next_signal() -> None:
    result = replay_snowball(
        [
            signal("A", 0, 10, "reached_1p10"),
            signal("B", 1, 60, "reached_1p10"),
            signal("C", 11, 20, "reached_1p10"),
        ],
        SnowballConfig(),
        policy_id="P",
    )
    by_symbol = {trade.symbol: trade for trade in result.executed}
    assert by_symbol["B"].allocation_budget_usd == Decimal("25.000000")
    assert by_symbol["C"].allocation_budget_usd > Decimal("25.000000")


def test_profitable_close_compounds_next_available_slice() -> None:
    result = replay_snowball(
        [
            signal("A", 0, 10, "reached_1p10"),
            signal("B", 11, 20, "reached_1p10"),
        ],
        SnowballConfig(),
        policy_id="P",
    )
    by_symbol = {trade.symbol: trade for trade in result.executed}
    assert by_symbol["B"].allocation_budget_usd > Decimal("50.000000")


def test_first_trade_fee_and_pnl_math() -> None:
    result = replay_snowball(
        [signal("A", 0, 10, "reached_1p10")],
        SnowballConfig(),
        policy_id="P",
    )
    trade = result.executed[0]
    assert trade.allocation_budget_usd == Decimal("50.000000")
    assert trade.margin_usd == Decimal("49.900200")
    assert trade.entry_fee_usd == Decimal("0.099800")
    assert trade.margin_usd + trade.entry_fee_usd == Decimal("50.000000")
    assert trade.net_pnl_usd == Decimal("5.289422")
    assert result.ending_wallet_usd == Decimal("105.289422")


def test_stop_uses_taker_exit_fee() -> None:
    result = replay_snowball(
        [signal("A", 0, 10, "floor_minus_0p50")],
        SnowballConfig(),
        policy_id="P",
    )
    trade = result.executed[0]
    assert trade.gross_pnl_usd == Decimal("-2.495010")
    assert trade.exit_fee_usd == Decimal("0.274451")
    assert trade.net_pnl_usd == Decimal("-2.869261")


def test_exit_at_same_timestamp_is_freed_before_new_entry() -> None:
    result = replay_snowball(
        [
            signal("A", 0, 10, "reached_1p10"),
            signal("B", 1, 50, "reached_1p10"),
            signal("C", 10, 20, "reached_1p10"),
        ],
        SnowballConfig(),
        policy_id="P",
    )
    by_symbol = {trade.symbol: trade for trade in result.executed}
    assert by_symbol["C"].available_before_usd > Decimal("50")
    assert by_symbol["C"].allocation_budget_usd > Decimal("25")
