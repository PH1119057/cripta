import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import (
    CancelEntryIntent,
    Candle,
    EnterIntent,
    Execution,
    NoOpIntent,
    Position,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.types import OrderSide, OrderStatus, PositionSide
from bybit_workbench.historical import parameters_fingerprint
from bybit_workbench.strategies import (
    IntentOutcome,
    IntentOutcomeStatus,
    PendingEntrySnapshot,
    PowerChannelRejection,
    ProtectionSnapshot,
    ReadOnlyStrategyContext,
    StrategyHealthSnapshot,
    TrendBreakoutRetest,
    default_strategy_registry,
)
from bybit_workbench.strategies.indicators import causal_channel, true_ranges, wilder_atr
from bybit_workbench.strategies.state import strategy_parameters_fingerprint

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(
    index: int,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    opened_at = START + timedelta(hours=index)
    return Candle(
        "BTCUSDT",
        "60",
        opened_at,
        opened_at + timedelta(hours=1),
        Decimal(open_),
        Decimal(high),
        Decimal(low),
        Decimal(close),
        Decimal("10"),
    )


def context(parameters: dict[str, object], price: str = "100") -> ReadOnlyStrategyContext:
    return ReadOnlyStrategyContext(
        "BTCUSDT",
        Decimal(price),
        Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
        parameters,
        tick_size=Decimal("0.1"),
    )


class IndicatorTests(unittest.TestCase):
    def test_wilder_atr_and_causal_channel_are_exact(self) -> None:
        bars = (
            candle(0, "100", "102", "99", "101"),
            candle(1, "101", "104", "100", "103"),
            candle(2, "103", "105", "101", "102"),
        )
        self.assertEqual(true_ranges(bars), (Decimal("3"), Decimal("4"), Decimal("4")))
        self.assertEqual(
            wilder_atr(bars, 2),
            (None, Decimal("3.5"), Decimal("3.75")),
        )
        self.assertEqual(causal_channel(bars[:2], 2), (Decimal("104"), Decimal("99")))

    def test_registry_rejects_lossy_or_out_of_range_parameters(self) -> None:
        trend = default_strategy_registry().get("user_algorithm_1")
        with self.assertRaisesRegex(ValueError, "entry_lookback"):
            trend.resolve_parameters({"entry_lookback": 19})
        with self.assertRaisesRegex(ValueError, "exact Decimal"):
            trend.resolve_parameters({"initial_stop_atr": 2.0})
        with self.assertRaisesRegex(ValueError, "direction_mode"):
            trend.resolve_parameters({"direction_mode": "up"})

    def test_runtime_parameter_fingerprint_matches_historical_gate_fingerprint(self) -> None:
        selected = {
            "entry_lookback": 55,
            "initial_stop_atr": Decimal("2.0"),
            "direction_mode": "both",
        }
        self.assertEqual(
            strategy_parameters_fingerprint(selected),
            parameters_fingerprint(selected),
        )


class TrendBreakoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.parameters: dict[str, object] = {
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
        self.history = (
            candle(0, "100", "102", "99", "101"),
            candle(1, "101", "103", "100", "102"),
            candle(2, "102", "104", "101", "103"),
            candle(3, "103", "105", "102", "104"),
            candle(4, "104", "106", "103", "105"),
        )

    async def test_golden_long_breakout_uses_frozen_prior_boundary(self) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context(self.parameters)
        for bar in self.history:
            await strategy.on_bar_closed(ctx, bar)
        signal = candle(5, "105", "108", "104", "107")
        intents = await strategy.on_bar_closed(replace(ctx, latest_price=signal.close), signal)
        self.assertEqual(len(intents), 1)
        self.assertIsInstance(intents[0], EnterIntent)
        entry = intents[0]
        assert isinstance(entry, EnterIntent)
        self.assertEqual(entry.direction, PositionSide.LONG)
        self.assertEqual(entry.entry_price, Decimal("106"))
        self.assertLess(entry.stop_price, entry.entry_price)
        snapshot = strategy.snapshot_state()["snapshot"]
        assert isinstance(snapshot, dict)
        self.assertEqual(snapshot["upper"], "106")

    async def test_wick_and_boundary_equality_do_not_break_out(self) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context(self.parameters)
        for bar in self.history:
            await strategy.on_bar_closed(ctx, bar)
        wick = candle(5, "105", "108", "104", "105.5")
        self.assertEqual(await strategy.on_bar_closed(ctx, wick), ())
        equal = candle(6, "105.5", "108", "105", "108")
        self.assertEqual(await strategy.on_bar_closed(ctx, equal), ())

    async def test_duplicate_bar_is_idempotent_and_future_data_cannot_rewrite_signal(self) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context(self.parameters)
        for bar in self.history:
            await strategy.on_bar_closed(ctx, bar)
        signal = candle(5, "105", "108", "104", "107")
        first = await strategy.on_bar_closed(ctx, signal)
        frozen = strategy.snapshot_state()["snapshot"]
        self.assertEqual(await strategy.on_bar_closed(ctx, signal), ())
        self.assertEqual(strategy.snapshot_state()["snapshot"], frozen)
        self.assertIsInstance(first[0], EnterIntent)

    async def test_restart_preserves_history_and_reproduces_same_signal(self) -> None:
        original = TrendBreakoutRetest()
        ctx = context(self.parameters)
        for item in self.history:
            await original.on_bar_closed(ctx, item)
        restored = TrendBreakoutRetest()
        restored.restore_state(original.snapshot_state())
        signal = candle(5, "105", "108", "104", "107")
        self.assertEqual(
            await restored.on_bar_closed(ctx, signal),
            await original.on_bar_closed(ctx, signal),
        )

    async def test_short_breakout_expiry_partial_fill_and_monotonic_trailing(self) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context(self.parameters)
        falling = tuple(
            candle(index, str(100 - index), str(101 - index), str(98 - index), str(99 - index))
            for index in range(5)
        )
        for item in falling:
            await strategy.on_bar_closed(ctx, item)
        signal = candle(5, "95", "96", "90", "91")
        result = await strategy.on_bar_closed(ctx, signal)
        self.assertIsInstance(result[0], EnterIntent)
        entry = result[0]
        assert isinstance(entry, EnterIntent)
        self.assertEqual(entry.direction, PositionSide.SHORT)
        self.assertGreater(entry.stop_price, entry.entry_price)
        pending_short = replace(
            ctx,
            pending_entry=PendingEntrySnapshot(
                entry.intent_id,
                OrderSide.SELL,
                entry.entry_price,
                Decimal("1"),
                Decimal("1"),
                OrderStatus.ACCEPTED,
                0,
            ),
        )
        self.assertEqual(
            await strategy.on_bar_closed(pending_short, candle(6, "92", "94", "91", "93")),
            (),
        )
        expired = await strategy.on_bar_closed(pending_short, candle(7, "93", "95", "92", "94"))
        self.assertIsInstance(expired[0], CancelEntryIntent)

        trailing_parameters = dict(self.parameters)
        trailing_parameters["trailing_stop_atr"] = Decimal("0.5")
        trailing_ctx = context(trailing_parameters)
        long_strategy = TrendBreakoutRetest()
        for item in self.history:
            await long_strategy.on_bar_closed(trailing_ctx, item)
        long_signal = candle(5, "105", "108", "104", "107")
        long_entry = (await long_strategy.on_bar_closed(trailing_ctx, long_signal))[0]
        assert isinstance(long_entry, EnterIntent)
        execution = Execution(
            "exec-1",
            "order-1",
            long_entry.intent_id,
            "BTCUSDT",
            OrderSide.BUY,
            Decimal("0.5"),
            long_entry.entry_price,
            long_signal.closed_at,
        )
        partial_context = replace(
            trailing_ctx,
            position=Position("BTCUSDT", PositionSide.LONG, Decimal("0.5"), long_entry.entry_price),
            pending_entry=PendingEntrySnapshot(
                long_entry.intent_id,
                OrderSide.BUY,
                long_entry.entry_price,
                Decimal("1"),
                Decimal("0.5"),
                OrderStatus.PARTIALLY_FILLED,
                0,
            ),
        )
        partial = await long_strategy.on_execution(partial_context, execution)
        self.assertIsInstance(partial[0], CancelEntryIntent)
        open_context = replace(
            partial_context,
            pending_entry=None,
            latest_price=Decimal("119"),
            mark_price=Decimal("119"),
            protection=ProtectionSnapshot(confirmed_stop=Decimal("90")),
        )
        trail = await long_strategy.on_bar_closed(
            open_context,
            candle(6, "107", "120", "106", "119"),
        )
        self.assertIsInstance(trail[0], UpdateProtectionIntent)
        proposed = trail[0]
        assert isinstance(proposed, UpdateProtectionIntent)
        unchanged = await long_strategy.on_bar_closed(
            replace(
                open_context,
                protection=ProtectionSnapshot(confirmed_stop=proposed.stop_price),
            ),
            candle(7, "118", "119", "110", "115"),
        )
        self.assertEqual(unchanged, ())

    async def test_unknown_entry_is_persisted_until_explicit_reconciliation(self) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context(self.parameters)
        for item in self.history:
            await strategy.on_bar_closed(ctx, item)
        signal = candle(5, "105", "108", "104", "107")
        entry = (await strategy.on_bar_closed(ctx, signal))[0]
        assert isinstance(entry, EnterIntent)
        await strategy.on_intent_outcome(
            ctx,
            IntentOutcome(
                entry.intent_id,
                IntentOutcomeStatus.UNKNOWN,
                signal.closed_at,
                "lost response",
            ),
        )
        frozen = strategy.snapshot_state()
        self.assertEqual(frozen["state"], "PENDING_UNKNOWN")
        self.assertTrue(frozen["reconciliation_required"])

        restored = TrendBreakoutRetest()
        restored.restore_state(frozen)
        with self.assertRaisesRegex(PermissionError, "reconciliation"):
            await restored.on_bar_closed(ctx, candle(6, "107", "109", "105", "108"))

        pending = replace(
            ctx,
            pending_entry=PendingEntrySnapshot(
                entry.intent_id,
                OrderSide.BUY,
                entry.entry_price,
                Decimal("1"),
                Decimal("1"),
                OrderStatus.ACCEPTED,
                0,
            ),
        )
        await restored.on_reconcile(pending)
        self.assertEqual(restored.snapshot_state()["state"], "ENTRY_PENDING")
        self.assertFalse(restored.snapshot_state()["reconciliation_required"])

    async def test_parameter_fingerprint_is_in_state_and_intent_identity(self) -> None:
        first = TrendBreakoutRetest()
        first_ctx = context(self.parameters)
        for item in self.history:
            await first.on_bar_closed(first_ctx, item)
        signal = candle(5, "105", "108", "104", "107")
        first_entry = (await first.on_bar_closed(first_ctx, signal))[0]
        assert isinstance(first_entry, EnterIntent)
        first_state = first.snapshot_state()
        self.assertEqual(len(str(first_state["parameters_fingerprint"])), 64)

        changed = dict(self.parameters)
        changed["cooldown_bars"] = 2
        second = TrendBreakoutRetest()
        second_ctx = context(changed)
        for item in self.history:
            await second.on_bar_closed(second_ctx, item)
        second_entry = (await second.on_bar_closed(second_ctx, signal))[0]
        assert isinstance(second_entry, EnterIntent)
        self.assertNotEqual(first_entry.intent_id, second_entry.intent_id)

        restored = TrendBreakoutRetest()
        restored.restore_state(first_state)
        with self.assertRaisesRegex(ValueError, "parameters differ"):
            await restored.on_reconcile(second_ctx)

    async def test_duplicate_execution_is_ignored_and_out_of_order_execution_is_rejected(
        self,
    ) -> None:
        strategy = TrendBreakoutRetest()
        ctx = context(self.parameters)
        for item in self.history:
            await strategy.on_bar_closed(ctx, item)
        signal = candle(5, "105", "108", "104", "107")
        entry = (await strategy.on_bar_closed(ctx, signal))[0]
        assert isinstance(entry, EnterIntent)
        partial_context = replace(
            ctx,
            position=Position("BTCUSDT", PositionSide.LONG, Decimal("0.5"), entry.entry_price),
            pending_entry=PendingEntrySnapshot(
                entry.intent_id,
                OrderSide.BUY,
                entry.entry_price,
                Decimal("1"),
                Decimal("0.5"),
                OrderStatus.PARTIALLY_FILLED,
                0,
            ),
        )
        execution = Execution(
            "exec-dedup",
            "order-1",
            entry.intent_id,
            "BTCUSDT",
            OrderSide.BUY,
            Decimal("0.5"),
            entry.entry_price,
            signal.closed_at,
        )
        first = await strategy.on_execution(partial_context, execution)
        self.assertEqual(len(first), 1)
        self.assertEqual(await strategy.on_execution(partial_context, execution), ())
        older = Execution(
            "exec-older",
            "order-1",
            entry.intent_id,
            "BTCUSDT",
            OrderSide.BUY,
            Decimal("0.1"),
            entry.entry_price,
            signal.closed_at - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            await strategy.on_execution(partial_context, older)


class PowerChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.parameters: dict[str, object] = {
            "range_lookback": 5,
            "atr_period": 3,
            "zone_half_width_atr": Decimal("0.5"),
            "min_center_range_atr": Decimal("1"),
            "confirmation_bars": 1,
            "entry_valid_bars": 2,
            "stop_buffer_atr": Decimal("0.1"),
            "minimum_reward_risk": Decimal("0"),
            "trailing_activation_r": Decimal("1"),
            "cooldown_bars": 1,
            "requested_leverage": Decimal("1"),
            "direction_mode": "both",
            "take_profit_mode": "midline",
            "use_candle_power_filter": False,
            "minimum_power_share": Decimal("0.55"),
        }
        self.history = (
            candle(0, "100", "102", "98", "101"),
            candle(1, "101", "103", "99", "102"),
            candle(2, "102", "104", "100", "103"),
            candle(3, "103", "105", "101", "104"),
            candle(4, "104", "106", "102", "105"),
            candle(5, "105", "106", "102", "103"),
            candle(6, "103", "105", "100", "102"),
        )

    async def _warmed(self) -> tuple[PowerChannelRejection, ReadOnlyStrategyContext]:
        strategy = PowerChannelRejection()
        ctx = context(self.parameters)
        disabled = replace(ctx, health=StrategyHealthSnapshot(new_entries_allowed=False))
        for bar in self.history:
            await strategy.on_bar_closed(disabled, bar)
        return strategy, ctx

    async def test_golden_long_rejection_freezes_channel_then_confirms(self) -> None:
        strategy, ctx = await self._warmed()
        touch = candle(7, "102", "103", "97", "99")
        touch_result = await strategy.on_bar_closed(ctx, touch)
        self.assertIsInstance(touch_result[0], NoOpIntent)
        frozen = strategy.snapshot_state()["snapshot"]
        assert isinstance(frozen, dict)
        support_top = Decimal(frozen["support_top"])
        confirmation = candle(8, "103", "104", str(support_top + Decimal("0.1")), "103")
        result = await strategy.on_bar_closed(ctx, confirmation)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], EnterIntent)
        entry = result[0]
        assert isinstance(entry, EnterIntent)
        self.assertEqual(entry.direction, PositionSide.LONG)
        self.assertEqual(entry.entry_price, support_top)
        self.assertEqual(entry.take_profit, Decimal(frozen["midline"]))

    async def test_confirmation_boundary_equality_is_rejected(self) -> None:
        strategy, ctx = await self._warmed()
        touch = candle(7, "102", "103", "97", "99")
        await strategy.on_bar_closed(ctx, touch)
        frozen = strategy.snapshot_state()["snapshot"]
        assert isinstance(frozen, dict)
        boundary = Decimal(frozen["support_top"])
        equal = candle(8, "103", "104", str(boundary), "103")
        result = await strategy.on_bar_closed(ctx, equal)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], NoOpIntent)

    async def test_restart_of_frozen_touch_and_short_rejection(self) -> None:
        strategy, ctx = await self._warmed()
        touch = candle(7, "102", "103", "97", "99")
        await strategy.on_bar_closed(ctx, touch)
        restored = PowerChannelRejection()
        restored.restore_state(strategy.snapshot_state())
        frozen = restored.snapshot_state()["snapshot"]
        assert isinstance(frozen, dict)
        support_top = Decimal(frozen["support_top"])
        confirmation = candle(8, "103", "104", str(support_top + Decimal("0.1")), "103")
        self.assertEqual(
            await restored.on_bar_closed(ctx, confirmation),
            await strategy.on_bar_closed(ctx, confirmation),
        )

        short_strategy, short_ctx = await self._warmed()
        short_touch = candle(7, "105", "107", "104", "106")
        await short_strategy.on_bar_closed(short_ctx, short_touch)
        short_frozen = short_strategy.snapshot_state()["snapshot"]
        assert isinstance(short_frozen, dict)
        resistance_bottom = Decimal(short_frozen["resistance_bottom"])
        short_confirmation = candle(
            8,
            "102",
            str(resistance_bottom - Decimal("0.1")),
            "100",
            "101",
        )
        short_result = await short_strategy.on_bar_closed(short_ctx, short_confirmation)
        self.assertIsInstance(short_result[0], EnterIntent)
        short_entry = short_result[0]
        assert isinstance(short_entry, EnterIntent)
        self.assertEqual(short_entry.direction, PositionSide.SHORT)
        self.assertGreater(short_entry.stop_price, short_entry.entry_price)

    async def test_ambiguous_candle_touching_both_zones_is_discarded(self) -> None:
        strategy, ctx = await self._warmed()
        ambiguous = candle(7, "102", "120", "80", "102")
        self.assertEqual(await strategy.on_bar_closed(ctx, ambiguous), ())
        self.assertEqual(strategy.snapshot_state()["state"], "FLAT")
        self.assertIsNone(strategy.snapshot_state()["snapshot"])

    async def test_unknown_power_entry_requires_reconciliation(self) -> None:
        strategy, ctx = await self._warmed()
        touch = candle(7, "102", "103", "97", "99")
        await strategy.on_bar_closed(ctx, touch)
        frozen = strategy.snapshot_state()["snapshot"]
        assert isinstance(frozen, dict)
        support_top = Decimal(frozen["support_top"])
        confirmation = candle(8, "103", "104", str(support_top + Decimal("0.1")), "103")
        entry = (await strategy.on_bar_closed(ctx, confirmation))[0]
        assert isinstance(entry, EnterIntent)
        await strategy.on_intent_outcome(
            ctx,
            IntentOutcome(
                entry.intent_id,
                IntentOutcomeStatus.UNKNOWN,
                confirmation.closed_at,
                "lost response",
            ),
        )
        self.assertEqual(strategy.snapshot_state()["state"], "PENDING_UNKNOWN")
        with self.assertRaisesRegex(PermissionError, "reconciliation"):
            await strategy.on_bar_closed(ctx, candle(9, "103", "104", "101", "103"))
        await strategy.on_reconcile(ctx)
        self.assertEqual(strategy.snapshot_state()["state"], "FLAT")


if __name__ == "__main__":
    unittest.main()
