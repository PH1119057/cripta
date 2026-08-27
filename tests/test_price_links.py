import unittest
from decimal import Decimal

from bybit_workbench.ui.main_window import _percent_from_reference, _price_from_percent


class PriceLinkTests(unittest.TestCase):
    def test_entry_percent_round_trip_uses_tick_size(self) -> None:
        mark = Decimal("3.226")
        entry = _price_from_percent(mark, Decimal("0.31"), Decimal("0.001"))
        self.assertEqual(entry, Decimal("3.236"))
        percent = _percent_from_reference(entry, mark)
        self.assertGreater(percent, Decimal("0.30"))
        self.assertLess(percent, Decimal("0.32"))

    def test_stop_and_take_profit_support_signed_percentages(self) -> None:
        entry = Decimal("3.236")
        self.assertEqual(
            _price_from_percent(entry, Decimal("1.89"), Decimal("0.001")),
            Decimal("3.297"),
        )
        self.assertEqual(
            _price_from_percent(entry, Decimal("-3.37"), Decimal("0.001")),
            Decimal("3.127"),
        )


if __name__ == "__main__":
    unittest.main()
