import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain import Candle, InstrumentRules, Position
from bybit_workbench.domain.types import AppState, PositionSide
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    BybitPositionSnapshot,
    BybitReadSnapshot,
)
from bybit_workbench.execution.automatic_intents import ShadowIntentJournal
from bybit_workbench.historical import HistoricalGateDecision
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.strategies import ArmedStrategy, TrendBreakoutRetest
from bybit_workbench.strategies.mainnet_shadow import MainnetShadowSession

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


def bar(index: int, close: str, high: str | None = None) -> Candle:
    opened = START + timedelta(hours=index)
    selected = Decimal(high or str(Decimal(close) + 1))
    return Candle(
        "BTCUSDT",
        "60",
        opened,
        opened + timedelta(hours=1),
        Decimal(close),
        selected,
        Decimal(close) - 2,
        Decimal(close),
        Decimal("1"),
    )


def snapshot() -> BybitReadSnapshot:
    observed = START + timedelta(hours=6)
    return BybitReadSnapshot(
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
            Decimal("100"),
            Decimal("90"),
            Decimal("100"),
            Decimal("0"),
            observed,
        ),
        BybitPositionSnapshot(
            Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
            0,
            Decimal("1"),
            Decimal("106"),
            None,
            None,
            None,
            None,
            Decimal("0"),
            1,
            observed,
        ),
        (),
        observed,
    )


class FixtureAdapter:
    def __init__(self) -> None:
        self.snapshot = snapshot()
        self.history = [bar(index, str(100 + index)) for index in range(6)]
        self.read_count = 0

    async def read_snapshot(self, symbol: str) -> BybitReadSnapshot:
        self.assert_symbol(symbol)
        self.read_count += 1
        return self.snapshot

    async def historical_candles(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int,
    ) -> list[Candle]:
        self.assert_symbol(symbol)
        if interval != "60" or limit < 6:
            raise AssertionError("unexpected history request")
        return self.history

    @staticmethod
    def assert_symbol(symbol: str) -> None:
        if symbol != "BTCUSDT":
            raise AssertionError(symbol)


class MainnetShadowSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_data_adapter_warmup_and_live_virtual_intent_are_journalled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "shadow.db")
            params = parameters()
            recorder = ShadowIntentJournal(
                journal,
                run_id="mainnet-shadow-run",
                strategy_id="user_algorithm_1",
                strategy_version="0.2.0",
                symbol="BTCUSDT",
                parameters=params,
            )
            machine = AppStateMachine()
            machine.transition(AppState.SYNCING, "fixture")
            machine.transition(AppState.READY, "fixture")
            machine.transition(AppState.ARMED, "BackTest gate and manual Shadow confirmation")
            armed = ArmedStrategy(
                "user_algorithm_1",
                "0.2.0",
                params,
                HistoricalGateDecision(True, "Eligible for Micro-Live", "fingerprint"),
            )
            adapter = FixtureAdapter()
            session = MainnetShadowSession(
                adapter,  # type: ignore[arg-type]
                armed,
                TrendBreakoutRetest(),
                machine,
                recorder,
            )
            bootstrap = await session.bootstrap("BTCUSDT", "60")
            self.assertEqual(len(bootstrap.warmup_bars), 6)
            self.assertEqual(adapter.read_count, 1)
            self.assertEqual(journal.table_count("trade_intents"), 0)
            decision = await session.process_closed_bar(bar(6, "108", "109"))
            self.assertEqual(adapter.read_count, 2)
            self.assertTrue(decision.intents)
            self.assertEqual(decision.outcomes[0].status.value, "submitted")
            self.assertEqual(decision.state_snapshot["state"], "ENTRY_PENDING")
            self.assertEqual(journal.table_count("trade_intents"), len(decision.intents))
            self.assertTrue(all("Mainnet Shadow" in item.detail for item in decision.outcomes))

            waiting = await session.process_closed_bar(bar(7, "108", "109"))
            self.assertEqual(waiting.intents, ())
            self.assertEqual(waiting.state_snapshot["state"], "ENTRY_PENDING")
            cancelled = await session.process_closed_bar(bar(8, "108", "109"))
            self.assertTrue(cancelled.intents)
            self.assertEqual(cancelled.outcomes[0].status.value, "cancelled")
            self.assertEqual(cancelled.state_snapshot["state"], "FLAT")
            self.assertEqual(adapter.read_count, 4)
            await session.stop("fixture complete")
            journal.close()

    async def test_every_candle_refreshes_snapshot_and_stale_reconnect_snapshot_is_rejected(
        self,
    ) -> None:
        class StaleAdapter(FixtureAdapter):
            async def read_snapshot(self, symbol: str) -> BybitReadSnapshot:
                selected = await super().read_snapshot(symbol)
                if self.read_count == 2:
                    return replace(
                        selected,
                        observed_at=selected.observed_at - timedelta(seconds=1),
                    )
                return selected

        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "shadow.db")
            params = parameters()
            recorder = ShadowIntentJournal(
                journal,
                run_id="mainnet-shadow-stale",
                strategy_id="user_algorithm_1",
                strategy_version="0.2.0",
                symbol="BTCUSDT",
                parameters=params,
            )
            machine = AppStateMachine()
            machine.transition(AppState.SYNCING, "fixture")
            machine.transition(AppState.READY, "fixture")
            machine.transition(AppState.ARMED, "BackTest gate and manual Shadow confirmation")
            armed = ArmedStrategy(
                "user_algorithm_1",
                "0.2.0",
                params,
                HistoricalGateDecision(True, "Eligible for Micro-Live", "fingerprint"),
            )
            adapter = StaleAdapter()
            session = MainnetShadowSession(
                adapter,  # type: ignore[arg-type]
                armed,
                TrendBreakoutRetest(),
                machine,
                recorder,
            )
            await session.bootstrap("BTCUSDT", "60")
            with self.assertRaisesRegex(ValueError, "out-of-order exchange snapshot"):
                await session.process_closed_bar(bar(6, "108", "109"))
            self.assertEqual(adapter.read_count, 2)
            await session.stop("fixture complete")
            journal.close()


if __name__ == "__main__":
    unittest.main()
