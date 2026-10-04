from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.market_watch import compute_r1_l53_stable_zone


def _candles(count: int = 205) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    for index in range(count):
        opened = start + timedelta(minutes=5 * index)
        high = Decimal("110") if index % 10 == 0 else Decimal("101")
        low = Decimal("90") if index % 10 == 5 else Decimal("99")
        rows.append(
            Candle(
                symbol="APTUSDT",
                timeframe="5",
                opened_at=opened,
                closed_at=opened + timedelta(minutes=5),
                open=Decimal("100"),
                high=high,
                low=low,
                close=Decimal("100"),
                volume=Decimal("1"),
            )
        )
    return rows


def test_r1_l53_requires_six_strict_stable_states() -> None:
    rows = _candles()
    zone = compute_r1_l53_stable_zone(rows)
    assert zone is not None
    assert zone.range_low == Decimal("90")
    assert zone.range_high == Decimal("110")


def test_r1_l53_rejects_boundary_change_inside_six_state_window() -> None:
    rows = _candles()
    last = rows[-1]
    rows[-1] = Candle(
        symbol=last.symbol,
        timeframe=last.timeframe,
        opened_at=last.opened_at,
        closed_at=last.closed_at,
        open=last.open,
        high=Decimal("111"),
        low=last.low,
        close=last.close,
        volume=last.volume,
    )
    assert compute_r1_l53_stable_zone(rows) is None
