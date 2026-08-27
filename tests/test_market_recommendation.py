import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import Candle, InstrumentRules
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.ui.market_recommendation import recommend_market_plan

START = datetime(2026, 1, 1, tzinfo=UTC)


def candles(*, rising: bool = True) -> tuple[Candle, ...]:
    rows: list[Candle] = []
    for index in range(40):
        base = Decimal("3") + Decimal(index if rising else 40 - index) * Decimal("0.01")
        rows.append(
            Candle(
                symbol="UNIUSDT",
                timeframe="60",
                opened_at=START + timedelta(hours=index),
                closed_at=START + timedelta(hours=index + 1),
                open=base,
                high=base + Decimal("0.03"),
                low=base - Decimal("0.02"),
                close=base + (Decimal("0.01") if rising else Decimal("-0.01")),
                volume=Decimal("100"),
                is_closed=True,
            )
        )
    return tuple(rows)


RULES = InstrumentRules(
    symbol="UNIUSDT",
    tick_size=Decimal("0.001"),
    qty_step=Decimal("0.1"),
    min_order_qty=Decimal("0.1"),
    min_notional=Decimal("5"),
    max_order_qty=Decimal("32000"),
    max_market_order_qty=Decimal("11000"),
)


class MarketRecommendationTests(unittest.TestCase):
    def test_rising_market_recommends_long_protected_plan(self) -> None:
        plan = recommend_market_plan(
            symbol="UNIUSDT",
            timeframe="60",
            candles=candles(rising=True),
            instrument=RULES,
            mark_price=Decimal("3.45"),
            last_price=Decimal("3.44"),
        )
        self.assertEqual(plan.direction, PositionSide.LONG)
        self.assertLess(plan.entry_price, Decimal("3.45"))
        self.assertLess(plan.stop_price, plan.entry_price)
        self.assertGreater(plan.take_profit, plan.entry_price)

    def test_falling_market_recommends_short_protected_plan(self) -> None:
        plan = recommend_market_plan(
            symbol="UNIUSDT",
            timeframe="60",
            candles=candles(rising=False),
            instrument=RULES,
            mark_price=Decimal("3.05"),
            last_price=Decimal("3.06"),
        )
        self.assertEqual(plan.direction, PositionSide.SHORT)
        self.assertGreater(plan.entry_price, Decimal("3.05"))
        self.assertGreater(plan.stop_price, plan.entry_price)
        self.assertLess(plan.take_profit, plan.entry_price)

    def test_requires_enough_closed_candles(self) -> None:
        with self.assertRaisesRegex(ValueError, "21 closed candles"):
            recommend_market_plan(
                symbol="UNIUSDT",
                timeframe="60",
                candles=candles()[:20],
                instrument=RULES,
                mark_price=Decimal("3.45"),
                last_price=None,
            )


if __name__ == "__main__":
    unittest.main()
