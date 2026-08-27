import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain.models import Candle
from bybit_workbench.domain.types import FillReason
from bybit_workbench.historical import (
    EquityPoint,
    HistoricalAcceptancePolicy,
    HistoricalDataset,
    build_validation_report,
    calculate_metrics,
    chronological_split,
    walk_forward_splits,
)
from bybit_workbench.replay import ReplayTradeResult

START = datetime(2025, 1, 1, tzinfo=UTC)


def dataset(count=20):
    candles = []
    for index in range(count):
        opened = START + timedelta(minutes=index)
        price = Decimal("100") + Decimal(index)
        candles.append(
            Candle(
                "BTCUSDT",
                "1",
                opened,
                opened + timedelta(minutes=1),
                price,
                price + Decimal("2"),
                price - Decimal("1"),
                price + Decimal("1"),
                Decimal("10"),
            )
        )
    return HistoricalDataset(tuple(candles))


def trade(opened, closed, pnl, *, ambiguous=False):
    return ReplayTradeResult(
        "BTCUSDT",
        "Long",
        Decimal("1"),
        Decimal("100"),
        Decimal("101"),
        Decimal(pnl) + Decimal("0.2"),
        Decimal("0.1"),
        Decimal("0.1"),
        Decimal(pnl),
        FillReason.TAKE_PROFIT if Decimal(pnl) > 0 else FillReason.STOP_LOSS,
        opened,
        closed,
        ambiguous,
    )


class HistoricalValidationTests(unittest.TestCase):
    def test_dataset_fingerprint_is_reproducible_and_sensitive(self):
        first = dataset()
        second = dataset()
        changed = HistoricalDataset(
            (
                *second.candles[:-1],
                Candle(
                    "BTCUSDT",
                    "1",
                    second.candles[-1].opened_at,
                    second.candles[-1].closed_at,
                    Decimal("999"),
                    Decimal("1001"),
                    Decimal("998"),
                    Decimal("1000"),
                    Decimal("10"),
                ),
            )
        )
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, changed.fingerprint)

    def test_temporal_split_never_shuffles_or_overlaps(self):
        parts = chronological_split(dataset(20))
        self.assertEqual(len(parts.train.candles), 12)
        self.assertEqual(len(parts.validation.candles), 4)
        self.assertEqual(len(parts.test.candles), 4)
        self.assertLessEqual(parts.train.ended_at, parts.validation.started_at)
        self.assertLessEqual(parts.validation.ended_at, parts.test.started_at)
        self.assertEqual(parts.test.candles[-1].closed_at, dataset(20).ended_at)

    def test_walk_forward_folds_keep_test_strictly_after_training(self):
        folds = walk_forward_splits(
            dataset(20),
            training_bars=8,
            test_bars=4,
            step_bars=4,
        )
        self.assertEqual(len(folds), 3)
        for fold in folds:
            self.assertLessEqual(fold.train.ended_at, fold.test.started_at)
            self.assertEqual(len(fold.train.candles), 8)
            self.assertEqual(len(fold.test.candles), 4)

    def test_metrics_include_costs_drawdown_and_ambiguity(self):
        trades = (
            trade(START, START + timedelta(minutes=1), "5"),
            trade(START + timedelta(minutes=2), START + timedelta(minutes=3), "-2"),
            trade(
                START + timedelta(minutes=4),
                START + timedelta(minutes=5),
                "3",
                ambiguous=True,
            ),
        )
        metrics = calculate_metrics(trades)
        self.assertEqual(metrics.net_pnl, Decimal("6"))
        self.assertEqual(metrics.fees, Decimal("0.3"))
        self.assertEqual(metrics.funding, Decimal("0.3"))
        self.assertEqual(metrics.max_drawdown, Decimal("2"))
        self.assertEqual(metrics.ambiguous_fraction, Decimal("1") / Decimal("3"))

    def test_equity_curve_drawdown_captures_intratrade_adverse_excursion(self):
        data = dataset(3)
        curve = (
            EquityPoint(data.candles[0].closed_at, Decimal("100"), Decimal("100")),
            EquityPoint(data.candles[1].closed_at, Decimal("105"), Decimal("80")),
            EquityPoint(data.candles[2].closed_at, Decimal("110"), Decimal("108")),
        )
        metrics = calculate_metrics((), data, Decimal("100"), curve)
        self.assertEqual(metrics.max_drawdown, Decimal("25"))
        self.assertEqual(
            metrics.max_drawdown_percent, Decimal("25") / Decimal("105") * Decimal("100")
        )

    def test_metrics_include_exposure_and_buy_hold_benchmark(self):
        data = dataset(10)
        trades = (
            trade(data.started_at, data.started_at + timedelta(minutes=2), "1"),
            trade(
                data.started_at + timedelta(minutes=1),
                data.started_at + timedelta(minutes=3),
                "1",
            ),
        )
        metrics = calculate_metrics(trades, data)
        self.assertEqual(metrics.exposure_seconds, Decimal("180.0"))
        self.assertEqual(metrics.time_in_market_percent, Decimal("30.0"))
        self.assertGreater(metrics.buy_and_hold_return_percent, 0)

    def test_report_is_an_explicit_out_of_sample_gate(self):
        parts = chronological_split(dataset(20))
        train_trades = (
            trade(parts.train.started_at, parts.train.started_at + timedelta(minutes=1), "1"),
        )
        test_trades = (
            trade(parts.test.started_at, parts.test.started_at + timedelta(minutes=1), "5"),
            trade(
                parts.test.started_at + timedelta(minutes=1),
                parts.test.started_at + timedelta(minutes=2),
                "-2",
            ),
        )
        report = build_validation_report(
            strategy_id="algorithm-1",
            strategy_version="1.0",
            code_version="test-commit",
            parameters={"period": 20, "risk": Decimal("0.5")},
            train_dataset=parts.train,
            out_of_sample_dataset=parts.test,
            in_sample_trades=train_trades,
            out_of_sample_trades=test_trades,
            policy=HistoricalAcceptancePolicy(
                minimum_out_of_sample_trades=2,
                maximum_out_of_sample_drawdown=Decimal("5"),
                maximum_ambiguous_fraction=Decimal("0"),
            ),
            fee_rate=Decimal("0.0006"),
            slippage_percent=Decimal("0.1"),
            seed=7,
            generated_at=START,
        )
        self.assertTrue(report.eligible_for_testnet)
        self.assertEqual(report.out_of_sample.net_pnl, Decimal("3"))
        self.assertIn("not evidence", report.limitation)
        self.assertEqual(len(report.dataset_fingerprint), 64)

    def test_report_fails_when_costs_or_sample_size_are_missing(self):
        parts = chronological_split(dataset(20))
        report = build_validation_report(
            strategy_id="algorithm-1",
            strategy_version="1.0",
            code_version="test-commit",
            parameters={},
            train_dataset=parts.train,
            out_of_sample_dataset=parts.test,
            in_sample_trades=(),
            out_of_sample_trades=(),
            policy=HistoricalAcceptancePolicy(minimum_out_of_sample_trades=2),
            fee_rate=Decimal("0"),
            slippage_percent=Decimal("0"),
            seed=0,
        )
        self.assertFalse(report.eligible_for_testnet)
        failed = {check.code for check in report.checks if not check.passed}
        self.assertIn("minimum_oos_trades", failed)
        self.assertIn("execution_costs_modelled", failed)
        self.assertIn("positive_oos_pnl", failed)


if __name__ == "__main__":
    unittest.main()
