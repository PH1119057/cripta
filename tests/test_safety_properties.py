import unittest
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from bybit_workbench.domain import EnterIntent, OrderRequest
from bybit_workbench.domain.types import OrderRole, OrderSide, OrderType, PositionSide
from bybit_workbench.exchange.fake import FakeExchange
from bybit_workbench.risk import RiskEngine
from bybit_workbench.stops import RiskExpansionError, validate_stop_update
from tests.test_risk_engine import context, profile, rules


class SafetyPropertyTests(unittest.IsolatedAsyncioTestCase):
    @given(
        current=st.integers(min_value=1, max_value=1_000_000),
        distance=st.integers(min_value=1, max_value=1_000_000),
    )
    @settings(max_examples=300, deadline=None)
    def test_long_and_short_stops_never_expand(self, current: int, distance: int) -> None:
        current_stop = Decimal(current + distance)
        lower_stop = Decimal(current)
        with self.assertRaises(RiskExpansionError):
            validate_stop_update(current_stop, lower_stop, PositionSide.LONG)
        with self.assertRaises(RiskExpansionError):
            validate_stop_update(lower_stop, current_stop, PositionSide.SHORT)

    @given(
        entry_minor=st.integers(min_value=1, max_value=100_000),
        distance_minor=st.integers(min_value=1, max_value=10_000),
    )
    @settings(max_examples=300, deadline=None)
    def test_sizing_never_exceeds_risk_budget(self, entry_minor: int, distance_minor: int) -> None:
        engine = RiskEngine()
        entry = Decimal("100") + Decimal(entry_minor) / Decimal("100")
        distance = min(
            entry - Decimal("0.01"),
            Decimal("0.01") + Decimal(distance_minor) / Decimal("100"),
        )
        intent = EnterIntent(
            f"property-{entry_minor}-{distance_minor}",
            "BTCUSDT",
            PositionSide.LONG,
            OrderType.LIMIT,
            entry,
            entry - distance,
            Decimal("2"),
            "property fixture",
        )
        decision = engine.evaluate_entry(intent, profile(), context(), rules())
        if decision.approved:
            assert decision.estimated_loss_at_stop is not None
            assert decision.risk_budget is not None
            self.assertLessEqual(
                decision.estimated_loss_at_stop,
                decision.risk_budget,
            )

    async def test_reduce_only_never_increases_absolute_position(self) -> None:
        for close_quantity in ("0.001", "0.005", "0.010", "0.011", "0.020"):
            exchange = FakeExchange()
            await exchange.connect()
            await exchange.place_order(
                OrderRequest(
                    "property-open",
                    "BTCUSDT",
                    OrderSide.BUY,
                    OrderType.MARKET,
                    Decimal("0.010"),
                )
            )
            before = (await exchange.positions())[0].quantity
            order = await exchange.place_order(
                OrderRequest(
                    f"property-close-{close_quantity}",
                    "BTCUSDT",
                    OrderSide.SELL,
                    OrderType.MARKET,
                    Decimal(close_quantity),
                    reduce_only=True,
                    role=OrderRole.EXIT,
                )
            )
            after = (await exchange.positions())[0].quantity
            if order.status.value == "Filled":
                self.assertLessEqual(after, before)
            else:
                self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
