import unittest
from decimal import Decimal

from bybit_workbench.domain.models import Position
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.strategies import ReadOnlyStrategyContext


class StrategyContextTests(unittest.TestCase):
    def test_parameters_are_read_only_snapshot(self) -> None:
        source = {"period": 20}
        context = ReadOnlyStrategyContext(
            symbol="BTCUSDT",
            latest_price=Decimal("50000"),
            position=Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
            parameters=source,
        )
        source["period"] = 99
        self.assertEqual(context.parameters["period"], 20)
        with self.assertRaises(TypeError):
            context.parameters["period"] = 30  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
