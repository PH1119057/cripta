import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain.models import Candle, Execution, InstrumentRules, Position
from bybit_workbench.domain.types import AppMode, AppState, OrderSide, PositionSide
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot, ChannelHealth
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    BybitPositionSnapshot,
    BybitReadSnapshot,
    TickerSnapshot,
)
from bybit_workbench.ui.view_model import UserFacingError, WorkbenchViewModel


class WorkbenchViewModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.model = WorkbenchViewModel(AppMode.TESTNET)

    def test_default_market_timeframe_is_one_hour(self) -> None:
        self.assertEqual(self.model.state.timeframe, "60")

    def test_health_requires_all_three_fresh_channels(self) -> None:
        healthy = ChannelHealth(True, True, self.now, None)
        stale = ChannelHealth(True, False, self.now - timedelta(minutes=2), None)
        self.model.apply_health(BybitHealthSnapshot(healthy, stale, healthy))

        self.assertFalse(self.model.state.connection_safe_for_entries)
        self.assertEqual(self.model.state.private.detail, "Подключено, данные устарели")

    def test_read_snapshot_populates_account_and_position(self) -> None:
        snapshot = BybitReadSnapshot(
            InstrumentRules(
                "BTCUSDT",
                Decimal("0.1"),
                Decimal("0.001"),
                Decimal("0.001"),
                Decimal("5"),
                Decimal("100"),
            ),
            AccountSnapshot(
                "UNIFIED",
                Decimal("1000"),
                Decimal("800"),
                Decimal("950"),
                Decimal("50"),
                self.now,
            ),
            BybitPositionSnapshot(
                Position("BTCUSDT", PositionSide.LONG, Decimal("0.01"), Decimal("60000")),
                0,
                Decimal("2"),
                Decimal("61000"),
                Decimal("30000"),
                Decimal("59000"),
                Decimal("63000"),
                None,
                Decimal("10"),
                1,
                self.now,
            ),
            (),
            self.now,
        )

        self.model.apply_read_snapshot(snapshot)

        state = self.model.state
        self.assertEqual(state.equity, Decimal("1000"))
        self.assertEqual(state.position_side, "Long")
        self.assertEqual(state.protection.confirmed_stop, Decimal("59000"))

    def test_execution_history_merges_and_deduplicates_across_sources(self) -> None:
        old = Execution(
            "exec-old",
            "order-old",
            "client-old",
            "BTCUSDT",
            OrderSide.BUY,
            Decimal("0.1"),
            Decimal("50000"),
            self.now,
        )
        new = Execution(
            "exec-new",
            "order-new",
            "client-new",
            "BTCUSDT",
            OrderSide.SELL,
            Decimal("0.1"),
            Decimal("50100"),
            self.now + timedelta(seconds=1),
        )

        self.model.merge_executions((old,))
        self.model.merge_executions((new, old))

        self.assertEqual(
            [item.execution_id for item in self.model.state.executions],
            ["exec-new", "exec-old"],
        )

    def test_candle_is_deduplicated_and_history_is_bounded(self) -> None:
        model = WorkbenchViewModel(AppMode.REPLAY, max_candles=2)
        for minute in range(3):
            opened = self.now + timedelta(minutes=minute)
            model.apply_candle(
                Candle(
                    "BTCUSDT",
                    "1",
                    opened,
                    opened + timedelta(minutes=1),
                    Decimal("100"),
                    Decimal("102"),
                    Decimal("99"),
                    Decimal(str(100 + minute)),
                    Decimal("1"),
                )
            )

        self.assertEqual(len(model.state.candles), 2)
        self.assertEqual(model.state.last_price, Decimal("102"))

    def test_ticker_for_other_symbol_is_ignored(self) -> None:
        self.model.apply_ticker(
            TickerSnapshot("ETHUSDT", Decimal("1"), None, None, None, None, self.now)
        )
        self.assertIsNone(self.model.state.last_price)

    def test_error_contract_has_three_actionable_parts(self) -> None:
        error = UserFacingError("REST недоступен", "Новые входы запрещены", "Проверьте сеть")
        self.model.set_error(error)

        self.assertIn("Что произошло", self.model.state.error.text)
        self.assertIn("Что сделала система", self.model.state.error.text)
        self.assertIn("Что сделать вам", self.model.state.error.text)
        self.assertEqual(self.model.state.engine_state, AppState.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()
