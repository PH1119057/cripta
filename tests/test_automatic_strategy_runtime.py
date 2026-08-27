import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain import Candle, EnterIntent, Position
from bybit_workbench.domain.types import AppMode, AppState, ExecutionMode, PositionSide
from bybit_workbench.historical import HistoricalGateDecision
from bybit_workbench.strategies import (
    ArmedStrategy,
    AutomaticStrategyRuntime,
    IntentOutcome,
    IntentOutcomeStatus,
    ReadOnlyStrategyContext,
    TrendBreakoutRetest,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def parameters() -> dict[str, object]:
    return {
        "entry_lookback": 5,
        "atr_period": 3,
        "initial_stop_atr": Decimal("2"),
        "trailing_stop_atr": Decimal("3"),
        "entry_valid_bars": 2,
        "cooldown_bars": 1,
        "requested_leverage": Decimal("1"),
        "direction_mode": "both",
        "take_profit_r": Decimal("0"),
        "exit_on_opposite_breakout": True,
    }


def context() -> ReadOnlyStrategyContext:
    return ReadOnlyStrategyContext(
        "BTCUSDT",
        Decimal("100"),
        Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
        parameters(),
    )


def bar(index: int, close: str, high: str | None = None) -> Candle:
    opened = START + timedelta(hours=index)
    selected_high = Decimal(high or str(Decimal(close) + 1))
    return Candle(
        "BTCUSDT",
        "60",
        opened,
        opened + timedelta(hours=1),
        Decimal(close),
        selected_high,
        Decimal(close) - 2,
        Decimal(close),
        Decimal("10"),
    )


def machine() -> AppStateMachine:
    result = AppStateMachine()
    result.transition(AppState.SYNCING, "test")
    result.transition(AppState.READY, "test")
    result.transition(AppState.ARMED, "historical gate passed")
    return result


def armed() -> ArmedStrategy:
    return ArmedStrategy(
        "user_algorithm_1",
        "0.2.0",
        parameters(),
        HistoricalGateDecision(True, "test eligible", "fingerprint"),
    )


class AutomaticStrategyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mainnet_shadow_journals_virtual_intent_but_never_calls_write_sink(self) -> None:
        recorded: list[str] = []

        async def forbidden_sink(intent, selected_context):
            del intent, selected_context
            raise AssertionError("SHADOW called a write-capable sink")

        runtime = AutomaticStrategyRuntime(
            AppMode.LIVE,
            armed(),
            TrendBreakoutRetest(),
            machine(),
            intent_sink=forbidden_sink,
            execution_mode=ExecutionMode.SHADOW,
            shadow_intent_recorder=lambda intent, selected_context, observed_at: recorded.append(
                intent.intent_id
            ),
        )
        await runtime.start(context())
        for index in range(5):
            await runtime.process_closed_bar(context(), bar(index, str(100 + index)))
        decision = await runtime.process_closed_bar(context(), bar(5, "107", "108"))
        self.assertEqual(recorded[-1], decision.intents[0].intent_id)
        self.assertEqual(len(recorded), 6)
        self.assertIn("Mainnet Shadow", decision.outcomes[0].detail)

    async def test_replay_shadow_records_intents_without_writes_and_persists_state(self) -> None:
        snapshots: list[dict[str, object]] = []
        state = machine()
        runtime = AutomaticStrategyRuntime(
            AppMode.REPLAY,
            armed(),
            TrendBreakoutRetest(),
            state,
            state_sink=lambda value: snapshots.append(dict(value)),
        )
        await runtime.start(context())
        bars = [bar(index, str(100 + index)) for index in range(5)]
        for item in bars:
            await runtime.process_closed_bar(context(), item)
        signal = bar(5, "107", "108")
        decision = await runtime.process_closed_bar(context(), signal)
        self.assertEqual(len(decision.intents), 1)
        self.assertEqual(decision.outcomes[0].status.value, "approved")
        self.assertIn("Replay shadow", decision.outcomes[0].detail)
        self.assertTrue(snapshots)
        duplicate = await runtime.process_closed_bar(context(), signal)
        self.assertEqual(duplicate.intents, ())
        await runtime.stop("test complete")
        self.assertEqual(state.state, AppState.PAUSED)

    async def test_unknown_execution_outcome_blocks_candles_until_reconciled(self) -> None:
        async def uncertain_sink(intent, selected_context):
            del selected_context
            status = (
                IntentOutcomeStatus.UNKNOWN
                if isinstance(intent, EnterIntent)
                else IntentOutcomeStatus.APPROVED
            )
            return IntentOutcome(intent.intent_id, status, START + timedelta(hours=20), "fixture")

        snapshots: list[dict[str, object]] = []
        runtime = AutomaticStrategyRuntime(
            AppMode.LIVE,
            armed(),
            TrendBreakoutRetest(),
            machine(),
            intent_sink=uncertain_sink,
            state_sink=lambda value: snapshots.append(dict(value)),
            execution_mode=ExecutionMode.MICRO_LIVE,
        )
        await runtime.start(context())
        for index in range(5):
            await runtime.process_closed_bar(context(), bar(index, str(100 + index)))
        decision = await runtime.process_closed_bar(context(), bar(5, "107", "108"))
        self.assertEqual(decision.outcomes[0].status, IntentOutcomeStatus.UNKNOWN)
        self.assertTrue(runtime.reconciliation_required)
        self.assertEqual(snapshots[-1]["state"], "PENDING_UNKNOWN")
        self.assertTrue(snapshots[-1]["reconciliation_required"])

        with self.assertRaisesRegex(PermissionError, "reconciliation"):
            await runtime.process_closed_bar(context(), bar(6, "108", "109"))
        await runtime.reconcile(context())
        self.assertFalse(runtime.reconciliation_required)
        self.assertEqual(snapshots[-1]["state"], "FLAT")

    async def test_restored_unknown_state_remains_fail_closed(self) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context()
        await strategy.on_start(ctx)
        state = dict(strategy.snapshot_state())
        state["state"] = "PENDING_UNKNOWN"
        state["reconciliation_required"] = True
        runtime = AutomaticStrategyRuntime(
            AppMode.LIVE,
            armed(),
            TrendBreakoutRetest(),
            machine(),
            restored_state=state,
        )
        await runtime.start(ctx)
        self.assertTrue(runtime.reconciliation_required)
        with self.assertRaisesRegex(PermissionError, "reconciliation"):
            await runtime.process_closed_bar(ctx, bar(0, "100"))
        await runtime.reconcile(ctx)
        self.assertFalse(runtime.reconciliation_required)

    async def test_out_of_order_candle_is_rejected_without_advancing_state(self) -> None:
        runtime = AutomaticStrategyRuntime(
            AppMode.REPLAY,
            armed(),
            TrendBreakoutRetest(),
            machine(),
        )
        ctx = context()
        await runtime.start(ctx)
        await runtime.process_closed_bar(ctx, bar(1, "101"))
        before = dict(runtime.strategy.snapshot_state())
        with self.assertRaisesRegex(ValueError, "out-of-order candle"):
            await runtime.process_closed_bar(ctx, bar(0, "100"))
        self.assertEqual(runtime.strategy.snapshot_state(), before)

    def test_demo_is_blocked_and_mainnet_shadow_is_allowed(self) -> None:
        with self.assertRaises(PermissionError):
            AutomaticStrategyRuntime(
                AppMode.DEMO,
                armed(),
                TrendBreakoutRetest(),
                machine(),
            )
        runtime = AutomaticStrategyRuntime(
            AppMode.LIVE,
            armed(),
            TrendBreakoutRetest(),
            machine(),
        )
        self.assertEqual(runtime.mode, AppMode.LIVE)

    def test_testnet_requires_an_execution_sink(self) -> None:
        with self.assertRaises(PermissionError):
            AutomaticStrategyRuntime(
                AppMode.TESTNET,
                armed(),
                TrendBreakoutRetest(),
                machine(),
            )


if __name__ == "__main__":
    unittest.main()
