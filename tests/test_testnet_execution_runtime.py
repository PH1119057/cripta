import tempfile
import time
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.models import InstrumentRules, Order, Position
from bybit_workbench.domain.types import (
    AppMode,
    AppState,
    OrderStatus,
    PositionSide,
)
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot, ChannelHealth
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    BybitPositionSnapshot,
    BybitReadSnapshot,
)
from bybit_workbench.exchange.bybit.testnet_execution import BybitOrderAcknowledgement
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.ui.manual_workflow import ManualTradeDraft, ManualTradeWorkflow
from bybit_workbench.ui.testnet_execution_runtime import (
    TestnetExecutionRuntime as RuntimeUnderTest,
)
from bybit_workbench.ui.view_model import WorkbenchViewModel

NOW = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)


class CredentialStore:
    def load(self, mode):
        return BybitCredentials(AppMode.TESTNET, "test-key", "test-secret")


class FilledAdapter:
    def __init__(self):
        self.request = None
        self.protection_updates = 0
        self.emergency_requested = False

    async def place_entry(self, request, protection):
        self.request = request
        return BybitOrderAcknowledgement("exchange-1", request.client_order_id)

    async def order_by_client_id(self, symbol, client_order_id):
        return Order(
            "exchange-1",
            self.request,
            OrderStatus.FILLED,
            filled_quantity=self.request.quantity,
            average_price=self.request.price,
            created_at=NOW,
            updated_at=NOW,
        )

    async def position(self, symbol):
        if self.emergency_requested:
            return BybitPositionSnapshot(
                Position(symbol, PositionSide.FLAT, Decimal("0"), None),
                0,
                None,
                Decimal("60100"),
                None,
                None,
                None,
                None,
                Decimal("0"),
                2,
                NOW,
            )
        return BybitPositionSnapshot(
            Position(symbol, PositionSide.LONG, Decimal("0.019"), Decimal("60000")),
            0,
            Decimal("2"),
            Decimal("60100"),
            Decimal("30000"),
            Decimal("59000"),
            Decimal("63000"),
            None,
            Decimal("1"),
            1,
            NOW,
        )

    async def set_full_protection(self, symbol, protection):
        self.protection_updates += 1

    async def emergency_close(self, position, client_order_id):
        self.emergency_requested = True
        return BybitOrderAcknowledgement("emergency-1", client_order_id)

    async def cancel_entry(self, **kwargs):
        raise AssertionError("filled order must not be cancelled")


class PartialFillAdapter(FilledAdapter):
    def __init__(self):
        super().__init__()
        self.lookup_count = 0
        self.current_quantity = Decimal("0")

    async def order_by_client_id(self, symbol, client_order_id):
        self.lookup_count += 1
        self.current_quantity = (
            Decimal("0.005") if self.lookup_count == 1 else self.request.quantity
        )
        status = OrderStatus.PARTIALLY_FILLED if self.lookup_count == 1 else OrderStatus.FILLED
        return Order(
            "exchange-1",
            self.request,
            status,
            filled_quantity=self.current_quantity,
            average_price=self.request.price,
            created_at=NOW,
            updated_at=NOW,
        )

    async def position(self, symbol):
        return BybitPositionSnapshot(
            Position(symbol, PositionSide.LONG, self.current_quantity, Decimal("60000")),
            0,
            Decimal("2"),
            Decimal("60100"),
            Decimal("30000"),
            Decimal("59000"),
            Decimal("63000"),
            None,
            Decimal("1"),
            self.lookup_count,
            NOW,
        )


def prepared_trade():
    machine = AppStateMachine()
    machine.transition(AppState.SYNCING, "sync")
    machine.transition(AppState.READY, "ready")
    model = WorkbenchViewModel(AppMode.TESTNET)
    model.apply_read_snapshot(
        BybitReadSnapshot(
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
                Decimal("10000"),
                Decimal("1000"),
                Decimal("1000"),
                Decimal("0"),
                NOW,
            ),
            BybitPositionSnapshot(
                Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
                0,
                None,
                Decimal("60000"),
                None,
                None,
                None,
                None,
                Decimal("0"),
                1,
                NOW,
            ),
            (),
            NOW,
        )
    )
    channel = ChannelHealth(True, True, NOW, None)
    health = BybitHealthSnapshot(channel, channel, channel)
    model.apply_health(health)
    workflow = ManualTradeWorkflow(machine, model)
    prepared = workflow.check(
        ManualTradeDraft(
            "BTCUSDT",
            PositionSide.LONG,
            Decimal("60000"),
            Decimal("59000"),
            Decimal("63000"),
            Decimal("1"),
        ),
        evaluated_at=NOW,
    )
    workflow.arm()
    workflow.run()
    return machine, model, prepared, health


class TestnetExecutionRuntimeTests(unittest.TestCase):
    def test_locked_settings_cannot_construct_runtime(self):
        with self.assertRaises(PermissionError):
            RuntimeUnderTest(
                AppSettings(mode=AppMode.TESTNET),
                AppStateMachine(),
            )

    def test_approved_manual_trade_reaches_confirmed_exchange_protection(self):
        machine, model, prepared, health = prepared_trade()
        adapter = FilledAdapter()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.db"
            settings = AppSettings(
                mode=AppMode.TESTNET,
                database_path=path,
                enable_testnet_execution=True,
            )
            runtime = RuntimeUnderTest(
                settings,
                machine,
                credential_store=CredentialStore(),
                adapter_factory=lambda settings, credentials: adapter,
                poll_seconds=0.01,
            )
            runtime.submit(prepared, health)
            deadline = time.monotonic() + 2
            while model.state.protection.confirmed_stop is None and time.monotonic() < deadline:
                time.sleep(0.01)
                runtime.drain_into(model)
            runtime.stop()
            runtime.drain_into(model)

            self.assertFalse(runtime.running)
            self.assertEqual(model.state.protection.confirmed_stop, Decimal("59000"))
            self.assertEqual(adapter.protection_updates, 1)
            journal = TradingJournal(path)
            try:
                self.assertEqual(journal.table_count("execution_commands"), 2)
                statuses = {
                    row[0]
                    for row in journal._connection.execute(  # noqa: SLF001
                        "SELECT status FROM execution_commands"
                    ).fetchall()
                }
                self.assertEqual(statuses, {"confirmed"})
            finally:
                journal.close()

    def test_emergency_request_closes_and_confirms_flat(self):
        machine, model, prepared, health = prepared_trade()
        adapter = FilledAdapter()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "emergency.db"
            runtime = RuntimeUnderTest(
                AppSettings(
                    mode=AppMode.TESTNET,
                    database_path=path,
                    enable_testnet_execution=True,
                ),
                machine,
                credential_store=CredentialStore(),
                adapter_factory=lambda settings, credentials: adapter,
                poll_seconds=0.01,
            )
            runtime.submit(prepared, health)
            deadline = time.monotonic() + 2
            while model.state.protection.confirmed_stop is None and time.monotonic() < deadline:
                time.sleep(0.01)
                runtime.drain_into(model)
            runtime.request_emergency()
            while runtime.running and time.monotonic() < deadline:
                time.sleep(0.01)
            runtime.stop()
            runtime.drain_into(model)
            self.assertTrue(adapter.emergency_requested)
            self.assertEqual(machine.state, AppState.EMERGENCY_STOP)
            journal = TradingJournal(path)
            try:
                emergency_status = journal._connection.execute(  # noqa: SLF001
                    "SELECT status FROM execution_commands WHERE kind='emergency_close'"
                ).fetchone()[0]
                self.assertEqual(emergency_status, "confirmed")
            finally:
                journal.close()

    def test_partial_fill_growth_reconfirms_protection_for_new_quantity(self):
        machine, model, prepared, health = prepared_trade()
        adapter = PartialFillAdapter()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.db"
            runtime = RuntimeUnderTest(
                AppSettings(
                    mode=AppMode.TESTNET,
                    database_path=path,
                    enable_testnet_execution=True,
                ),
                machine,
                credential_store=CredentialStore(),
                adapter_factory=lambda settings, credentials: adapter,
                poll_seconds=0.01,
            )
            runtime.submit(prepared, health)
            deadline = time.monotonic() + 2
            while adapter.protection_updates < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
                runtime.drain_into(model)
            runtime.stop()
            runtime.drain_into(model)
            self.assertEqual(
                adapter.protection_updates,
                2,
                None if model.state.error is None else model.state.error.text,
            )
            journal = TradingJournal(path)
            try:
                protection_commands = journal._connection.execute(  # noqa: SLF001
                    "SELECT COUNT(*) FROM execution_commands "
                    "WHERE kind='set_protection' AND status='confirmed'"
                ).fetchone()[0]
                self.assertEqual(protection_commands, 2)
            finally:
                journal.close()

    def test_restart_emergency_can_close_existing_position_without_active_run(self):
        machine, model, prepared, health = prepared_trade()
        del model, prepared, health
        adapter = FilledAdapter()
        machine.transition(AppState.EMERGENCY_STOP, "operator recovery emergency")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart-emergency.db"
            runtime = RuntimeUnderTest(
                AppSettings(
                    mode=AppMode.TESTNET,
                    database_path=path,
                    enable_testnet_execution=True,
                ),
                machine,
                credential_store=CredentialStore(),
                adapter_factory=lambda settings, credentials: adapter,
                poll_seconds=0.01,
            )
            runtime.request_emergency_for_symbol("BTCUSDT")
            deadline = time.monotonic() + 2
            while runtime.running and time.monotonic() < deadline:
                time.sleep(0.01)
            runtime.stop()
            self.assertTrue(adapter.emergency_requested)
            journal = TradingJournal(path)
            try:
                self.assertEqual(journal.table_count("reconciliation_runs"), 1)
                status = journal._connection.execute(  # noqa: SLF001
                    "SELECT status FROM execution_commands WHERE kind='emergency_close'"
                ).fetchone()[0]
                self.assertEqual(status, "confirmed")
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
