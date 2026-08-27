import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain import (
    CancelEntryIntent,
    Candle,
    EnterIntent,
    ExitIntent,
    InstrumentRules,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.types import OrderType, PositionSide
from bybit_workbench.historical import (
    HistoricalAcceptancePolicy,
    HistoricalDataset,
    HistoricalEligibilityQuery,
    HistoricalRunConfig,
    StressScenario,
    chronological_split,
    evaluate_stress_scenarios,
    evaluate_temporal_validation,
    evaluate_walk_forward,
    run_strategy,
    walk_forward_splits,
)
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.replay import ReplayConfig
from bybit_workbench.risk import RiskProfile
from bybit_workbench.strategies.base import (
    DataRequirements,
    ReadOnlyStrategyContext,
    StrategyMetadata,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


def dataset(count: int = 16) -> HistoricalDataset:
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
                price + Decimal("3"),
                price - Decimal("1"),
                price + Decimal("1"),
                Decimal("10"),
            )
        )
    return HistoricalDataset(tuple(candles))


def run_config() -> HistoricalRunConfig:
    return HistoricalRunConfig(
        initial_equity=Decimal("10000"),
        available_balance=Decimal("1000"),
        risk_profile=RiskProfile(
            max_risk_amount=Decimal("10"),
            max_risk_percent=Decimal("0"),
            max_position_notional=Decimal("1000"),
            max_leverage=Decimal("2"),
            max_daily_loss=Decimal("100"),
            max_consecutive_losses=3,
            max_open_positions=1,
            max_pending_entries=1,
            max_slippage_percent=Decimal("0.1"),
            estimated_fee_rate=Decimal("0.0006"),
            max_market_data_age_seconds=Decimal("1"),
            max_private_stream_age_seconds=Decimal("1"),
            allowed_symbols=frozenset({"BTCUSDT"}),
            allowed_directions=frozenset({PositionSide.LONG}),
        ),
        instrument_rules=InstrumentRules(
            "BTCUSDT",
            Decimal("0.1"),
            Decimal("0.001"),
            Decimal("0.001"),
            Decimal("1"),
            Decimal("100"),
        ),
        replay=ReplayConfig(
            fee_rate=Decimal("0.0006"),
            slippage_percent=Decimal("0.1"),
            seed=17,
        ),
    )


class FixtureStrategy:
    """Technical test fixture; it is deliberately not a user trading algorithm."""

    def __init__(self) -> None:
        self.sequence = 0
        self.stopped = False

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata("fixture-next-bar", "1", "Fixture")

    def required_data(self) -> DataRequirements:
        return DataRequirements(("1",), 2)

    def default_parameters(self):
        return {"take_distance": Decimal("2")}

    async def on_start(self, context: ReadOnlyStrategyContext) -> None:
        self.started_price = context.latest_price

    async def on_bar_closed(self, context: ReadOnlyStrategyContext, bar: Candle):
        if context.position.side is not PositionSide.FLAT:
            return ()
        self.sequence += 1
        return (
            EnterIntent(
                f"fixture-{self.sequence}",
                context.symbol,
                PositionSide.LONG,
                OrderType.LIMIT,
                context.latest_price,
                context.latest_price - Decimal("2"),
                Decimal("2"),
                "test no-lookahead timing",
                context.latest_price + Decimal(str(context.parameters["take_distance"])),
            ),
        )

    async def on_execution(self, context: ReadOnlyStrategyContext, execution_id: str):
        return ()

    async def on_stop(self, reason: str) -> None:
        self.stopped = True


class IntentLifecycleStrategy(FixtureStrategy):
    async def on_bar_closed(self, context: ReadOnlyStrategyContext, bar: Candle):
        self.sequence += 1
        if self.sequence == 1:
            return (
                EnterIntent(
                    "lifecycle-enter",
                    context.symbol,
                    PositionSide.LONG,
                    OrderType.LIMIT,
                    context.latest_price,
                    context.latest_price - Decimal("2"),
                    Decimal("2"),
                    "fixture entry",
                ),
            )
        if self.sequence == 2:
            return (
                UpdateProtectionIntent(
                    "lifecycle-protect",
                    context.symbol,
                    "fixture trail",
                    stop_price=Decimal("100"),
                ),
            )
        if self.sequence == 3:
            return (ExitIntent("lifecycle-exit", context.symbol, "fixture exit"),)
        return (NoOpIntent(f"noop-{self.sequence}", context.symbol, "fixture noop"),)


class CancelPendingStrategy(FixtureStrategy):
    async def on_bar_closed(self, context: ReadOnlyStrategyContext, bar: Candle):
        self.sequence += 1
        if self.sequence == 1:
            return (
                EnterIntent(
                    "cancel-enter",
                    context.symbol,
                    PositionSide.LONG,
                    OrderType.LIMIT,
                    Decimal("90"),
                    Decimal("80"),
                    Decimal("2"),
                    "fixture pending entry",
                ),
            )
        if self.sequence == 2:
            return (CancelEntryIntent("cancel-pending", context.symbol, "fixture cancel"),)
        return ()


class HistoricalRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_executes_protection_exit_cancel_and_noop_intents(self) -> None:
        lifecycle = await run_strategy(
            IntentLifecycleStrategy(), dataset(5), parameters=None, config=run_config()
        )
        by_type = {type(item.intent): item for item in lifecycle.outcomes}
        self.assertTrue(by_type[UpdateProtectionIntent].submitted)
        self.assertTrue(by_type[ExitIntent].submitted)
        self.assertFalse(by_type[NoOpIntent].submitted)
        self.assertEqual(lifecycle.trades[0].exit_reason.value, "StrategyExit")

        cancelled = await run_strategy(
            CancelPendingStrategy(), dataset(4), parameters=None, config=run_config()
        )
        cancel_outcome = next(
            item for item in cancelled.outcomes if isinstance(item.intent, CancelEntryIntent)
        )
        self.assertTrue(cancel_outcome.submitted)
        self.assertFalse(cancelled.trades)

    async def test_strategy_decision_cannot_fill_before_next_bar(self) -> None:
        strategy = FixtureStrategy()
        result = await run_strategy(strategy, dataset(6), parameters=None, config=run_config())
        submitted = next(item for item in result.outcomes if item.submitted)
        entry = next(fill for fill in result.fills if fill.reason.value == "Entry")
        self.assertGreaterEqual(entry.occurred_at, submitted.observed_at)
        self.assertEqual(entry.occurred_at, dataset(6).candles[1].opened_at)
        self.assertTrue(strategy.stopped)
        self.assertGreater(result.metrics.fees, 0)
        self.assertGreater(result.net_realized_pnl, 0)
        self.assertEqual(result.ending_position.side, PositionSide.FLAT)

    async def test_risk_engine_rejections_are_retained_in_result(self) -> None:
        config = run_config()
        blocked_profile = replace(
            config.risk_profile,
            allowed_utc_hours=frozenset({12}),
        )
        blocked = HistoricalRunConfig(
            config.initial_equity,
            config.available_balance,
            blocked_profile,
            config.instrument_rules,
            config.replay,
        )
        result = await run_strategy(FixtureStrategy(), dataset(4), parameters=None, config=blocked)
        self.assertTrue(result.outcomes)
        self.assertTrue(all(not item.submitted for item in result.outcomes))
        self.assertIn("trading_hour_allowed", result.outcomes[0].risk_decision.rejection_codes)

    async def test_walk_forward_runs_fresh_strategy_on_every_window(self) -> None:
        folds = walk_forward_splits(dataset(16), training_bars=6, test_bars=4)
        results = await evaluate_walk_forward(
            FixtureStrategy, folds, parameters=None, config=run_config()
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.train.trades for item in results))
        self.assertTrue(all(item.test.trades for item in results))

    async def test_temporal_validation_builds_and_persists_report(self) -> None:
        parts = chronological_split(dataset(20))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        journal = TradingJournal(Path(directory.name) / "journal.sqlite3")
        self.addCleanup(journal.close)
        result = await evaluate_temporal_validation(
            FixtureStrategy,
            train_dataset=parts.train,
            out_of_sample_dataset=parts.test,
            parameters={"take_distance": Decimal("2")},
            config=run_config(),
            policy=HistoricalAcceptancePolicy(
                minimum_out_of_sample_trades=1,
                maximum_out_of_sample_drawdown=Decimal("10"),
                maximum_ambiguous_fraction=Decimal("0"),
            ),
            code_version="test-code-version",
            report_store=journal,
            report_id="fixture-report",
        )
        self.assertEqual(result.report_id, "fixture-report")
        self.assertTrue(result.report.eligible_for_testnet)
        query = HistoricalEligibilityQuery.from_instrument(
            symbol=result.report.symbol,
            timeframe=result.report.timeframe,
            code_version=result.report.code_version,
            instrument_rules=run_config().instrument_rules,
            maker_fee_rate=run_config().replay.effective_maker_fee_rate,
            taker_fee_rate=run_config().replay.effective_taker_fee_rate,
            slippage_percent=run_config().replay.slippage_percent,
            price_trigger=result.report.price_trigger,
        )
        stored = journal.latest_historical_eligibility(
            result.report.strategy_id,
            result.report.strategy_version,
            result.report.parameters_fingerprint,
            query,
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertTrue(stored.eligible)
        self.assertFalse(stored.production_equivalent)

    async def test_stress_matrix_varies_cost_delay_and_missing_bars(self) -> None:
        results = await evaluate_stress_scenarios(
            FixtureStrategy,
            dataset(12),
            parameters=None,
            config=run_config(),
            scenarios=(
                StressScenario("base", Decimal("0.0006"), Decimal("0.1")),
                StressScenario(
                    "adverse",
                    Decimal("0.005"),
                    Decimal("0.5"),
                    execution_delay_bars=1,
                    gap_every_n_bars=4,
                ),
            ),
        )
        self.assertEqual([item.scenario.name for item in results], ["base", "adverse"])
        self.assertLess(
            results[1].run.net_realized_pnl,
            results[0].run.net_realized_pnl,
        )


if __name__ == "__main__":
    unittest.main()
