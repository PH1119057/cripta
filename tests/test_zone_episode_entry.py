from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.research.zone_episode_entry import (
    Episode,
    _days,
    _intersection,
    entry_price,
)


def test_days_treats_end_as_exclusive() -> None:
    start = datetime(2026, 8, 15, tzinfo=UTC)
    end = datetime(2026, 8, 16, tzinfo=UTC)
    assert _days(start, end) == ("2026-08-15",)


def test_exact_intersection_rejects_gap() -> None:
    assert _intersection(Decimal("1"), Decimal("2"), Decimal("2.1"), Decimal("3")) is None
    assert _intersection(Decimal("1"), Decimal("2"), Decimal("1.5"), Decimal("3")) == (
        Decimal("1.5"),
        Decimal("2"),
    )


def test_depth_runs_from_near_to_far_edge_by_direction() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    long = Episode("X", "Long", now, now + timedelta(minutes=5), Decimal("90"), Decimal("100"))
    short = Episode("X", "Short", now, now + timedelta(minutes=5), Decimal("90"), Decimal("100"))
    assert entry_price(long, Decimal("0")) == Decimal("100")
    assert entry_price(long, Decimal("1")) == Decimal("90")
    assert entry_price(short, Decimal("0")) == Decimal("90")
    assert entry_price(short, Decimal("1")) == Decimal("100")
