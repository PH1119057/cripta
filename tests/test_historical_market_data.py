import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import Candle
from bybit_workbench.historical import (
    FundingEvent,
    HistoricalAcceptancePolicy,
    HistoricalDataset,
    HistoricalMarketData,
    build_validation_report,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def dataset() -> HistoricalDataset:
    return HistoricalDataset(
        tuple(
            Candle(
                "BTCUSDT",
                "60",
                START + timedelta(hours=index),
                START + timedelta(hours=index + 1),
                Decimal("100"),
                Decimal("102"),
                Decimal("98"),
                Decimal("101"),
                Decimal("10"),
            )
            for index in range(3)
        )
    )


class HistoricalMarketDataTests(unittest.TestCase):
    def test_complete_series_have_independent_reproducible_fingerprints(self) -> None:
        trade = dataset()
        marks = tuple(replace(item, volume=Decimal("0")) for item in trade.candles)
        funding = (
            FundingEvent("BTCUSDT", START + timedelta(hours=2), Decimal("0.0001"), Decimal("101")),
        )
        market = HistoricalMarketData(
            trade,
            marks,
            funding,
            mark_price_complete=True,
            funding_complete=True,
        )
        self.assertTrue(market.quality.production_equivalent)
        self.assertEqual(market.quality.flags, ())
        self.assertEqual(len(market.fingerprint), 64)
        self.assertNotEqual(market.quality.trade_fingerprint, market.quality.mark_fingerprint)

    def test_missing_mark_and_funding_are_explicit_quality_failures(self) -> None:
        market = HistoricalMarketData(dataset())
        self.assertFalse(market.quality.production_equivalent)
        self.assertEqual(
            set(market.quality.flags),
            {"mark_price_missing_or_incomplete", "funding_missing_or_incomplete"},
        )

    def test_strict_gate_blocks_missing_mark_and_funding(self) -> None:
        data = dataset()
        report = build_validation_report(
            strategy_id="user_algorithm_1",
            strategy_version="0.1.0",
            code_version="test",
            parameters={},
            train_dataset=data,
            out_of_sample_dataset=HistoricalDataset(
                tuple(
                    replace(
                        item,
                        opened_at=item.opened_at + timedelta(hours=3),
                        closed_at=item.closed_at + timedelta(hours=3),
                    )
                    for item in data.candles
                )
            ),
            in_sample_trades=(),
            out_of_sample_trades=(),
            policy=HistoricalAcceptancePolicy(
                minimum_out_of_sample_trades=1,
                require_positive_out_of_sample_pnl=False,
                require_production_data=True,
            ),
            fee_rate=Decimal("0.0006"),
            slippage_percent=Decimal("0.1"),
            seed=1,
        )
        failed = {check.code for check in report.checks if not check.passed}
        self.assertIn("mark_price_complete", failed)
        self.assertIn("funding_complete", failed)
        self.assertIn("production_equivalent", failed)
        self.assertFalse(report.eligible_for_testnet)


if __name__ == "__main__":
    unittest.main()
