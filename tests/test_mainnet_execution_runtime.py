import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.app.windows_time import WindowsTimeSyncResult
from bybit_workbench.domain.intents import EnterIntent
from bybit_workbench.domain.models import InstrumentRules, OrderRequest, Position
from bybit_workbench.domain.types import (
    AppMode,
    AppState,
    ExecutionMode,
    OrderSide,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.connection import MainnetExecutionConnection
from bybit_workbench.exchange.bybit.errors import BybitApiError
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    ApiKeyInfo,
    ApiKeyPermissionAudit,
    BybitPositionSnapshot,
)
from bybit_workbench.execution.mainnet_safety import MainnetSafetySnapshot
from bybit_workbench.historical import (
    HistoricalEligibilityQuery,
    HistoricalGateDecision,
    eligibility_binding_fingerprint,
)
from bybit_workbench.risk import RiskCheck, RiskDecision, RiskProfile
from bybit_workbench.strategies import ArmedStrategy
from bybit_workbench.ui.mainnet_execution_runtime import (
    MainnetExecutionRuntime,
    MainnetRuntimePhase,
    default_micro_live_limits,
    micro_live_entry_plan,
)
from bybit_workbench.ui.manual_workflow import PreparedManualTrade
from bybit_workbench.ui.view_model import WorkbenchViewModel


def safety_snapshot() -> MainnetSafetySnapshot:
    now = datetime.now(UTC)
    return MainnetSafetySnapshot(
        "https://api.bybit.com",
        ApiKeyInfo(
            "BotW-Mainnet",
            False,
            (),
            80,
            now + timedelta(days=80),
            now - timedelta(days=1),
            True,
            None,
            True,
            1,
            ApiKeyPermissionAudit(("Order", "Position"), (), (), (), (), (), ()),
        ),
        InstrumentRules(
            "UNIUSDT",
            Decimal("0.001"),
            Decimal("0.1"),
            Decimal("0.1"),
            Decimal("1"),
            Decimal("1000"),
        ),
        AccountSnapshot(
            "UNIFIED",
            Decimal("20"),
            Decimal("20"),
            Decimal("20"),
            Decimal("0"),
            now,
            "ISOLATED_MARGIN",
            5,
            Decimal("0.0002"),
            Decimal("0.00055"),
            Decimal("0"),
        ),
        BybitPositionSnapshot(
            Position("UNIUSDT", PositionSide.FLAT, Decimal("0"), None),
            0,
            Decimal("1"),
            Decimal("3"),
            None,
            None,
            None,
            None,
            Decimal("0"),
            1,
            now,
        ),
        (),
        (),
        now,
        now,
        now,
        True,
        True,
        True,
    )


def prepared_trade() -> PreparedManualTrade:
    now = datetime.now(UTC)
    request = OrderRequest(
        "runtime-entry-1",
        "UNIUSDT",
        OrderSide.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        Decimal("3"),
    )
    intent = EnterIntent(
        "runtime-intent-1",
        "UNIUSDT",
        PositionSide.LONG,
        OrderType.LIMIT,
        Decimal("3"),
        Decimal("2.8"),
        Decimal("1"),
        "fixture",
        Decimal("3.5"),
    )
    decision = RiskDecision(
        True,
        (RiskCheck("fixture", True, "approved"),),
        request,
        Decimal("2.8"),
        Decimal("1"),
        Decimal("0.2"),
        Decimal("0.01"),
        Decimal("0"),
    )
    return PreparedManualTrade(
        "runtime-run-1",
        "runtime-decision-1",
        "runtime-risk-1",
        intent,
        decision,
        RiskProfile(
            max_risk_amount=Decimal("0"),
            max_risk_percent=Decimal("1"),
            max_position_notional=Decimal("1000"),
            max_leverage=Decimal("1"),
            max_daily_loss=Decimal("5"),
            max_consecutive_losses=3,
            max_open_positions=1,
            max_pending_entries=1,
            max_slippage_percent=Decimal("0.1"),
            estimated_fee_rate=Decimal("0.00055"),
            max_market_data_age_seconds=Decimal("10"),
            max_private_stream_age_seconds=Decimal("30"),
            allowed_symbols=frozenset({"UNIUSDT"}),
            allowed_directions=frozenset({PositionSide.LONG, PositionSide.SHORT}),
        ),
        now,
    )


def armed_strategy() -> ArmedStrategy:
    snapshot = safety_snapshot()
    query = HistoricalEligibilityQuery.from_instrument(
        symbol="UNIUSDT",
        timeframe="60",
        code_version="0.8.5",
        instrument_rules=snapshot.instrument,
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.00055"),
        slippage_percent=Decimal("0.1"),
    )
    binding = eligibility_binding_fingerprint(
        strategy_id="user_algorithm_1",
        strategy_version="0.2.0",
        parameters_fingerprint="fixture-fingerprint",
        query=query,
        dataset_fingerprint="d" * 64,
    )
    return ArmedStrategy(
        "user_algorithm_1",
        "0.2.0",
        {"timeframe": "60"},
        HistoricalGateDecision(
            True,
            "fixture eligible",
            "fixture-fingerprint",
            "fixture-report",
            "d" * 64,
            binding,
            query,
        ),
    )


class StubCredentials:
    def load(self, profile: AppMode, *, name: str | None = None) -> BybitCredentials:
        return BybitCredentials(profile, "public-key", "private-secret", name)


class FakeStateProvider:
    async def snapshot(self, symbol: str) -> MainnetSafetySnapshot:
        if symbol != "UNIUSDT":
            raise AssertionError(symbol)
        return safety_snapshot()


class FakeGateway:
    async def submit(self, mutation):  # pragma: no cover - preflight must not write
        raise AssertionError(f"unexpected mutation during preflight: {mutation}")


class FakeReader:
    async def position_snapshot(self, symbol):  # pragma: no cover
        raise AssertionError(symbol)

    async def open_orders(self, symbol):  # pragma: no cover
        raise AssertionError(symbol)

    async def order_by_client_id(self, symbol, client_order_id):  # pragma: no cover
        raise AssertionError((symbol, client_order_id))


class FakeConnectionFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        settings,
        credentials,
        arming,
        idempotency,
        context_provider,
    ) -> MainnetExecutionConnection:
        del settings, credentials, arming, idempotency, context_provider
        self.calls += 1
        return MainnetExecutionConnection(
            FakeGateway(),  # type: ignore[arg-type]
            FakeReader(),  # type: ignore[arg-type]
            FakeStateProvider(),
        )


class MainnetExecutionRuntimeTests(unittest.TestCase):
    def test_micro_live_uses_requested_leverage_as_required_exchange_configuration(self) -> None:
        base = prepared_trade()
        selected = replace(
            base,
            intent=replace(base.intent, leverage=Decimal("10")),
            risk_profile=replace(base.risk_profile, max_leverage=Decimal("10")),
        )

        plan = micro_live_entry_plan(selected)
        limits = default_micro_live_limits(selected)

        self.assertEqual(plan.symbol, "UNIUSDT")
        self.assertEqual(limits.required_leverage, Decimal("10"))

    def test_reconfigure_switches_endpoint_and_disarms(self) -> None:
        runtime = MainnetExecutionRuntime(
            AppSettings(
                mode=AppMode.LIVE,
                allow_live_trading=True,
                rest_url_override="https://api.bybit.kz",
            ),
            AppStateMachine(),
            context_provider=lambda: None,
            private_snapshot_provider=lambda: None,
        )
        runtime.reconfigure(
            AppSettings(
                mode=AppMode.LIVE,
                allow_live_trading=True,
                rest_url_override="https://api.bybit.com",
            )
        )
        self.assertEqual(
            runtime.settings.endpoint_profile.rest_url,
            "https://api.bybit.com",
        )
        self.assertEqual(runtime.status.phase, MainnetRuntimePhase.DISARMED)
        self.assertIn("api.bybit.com", runtime.status.detail)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(
            mode=AppMode.LIVE,
            allow_live_trading=True,
            database_path=Path(self.temp.name) / "runtime.db",
        )
        self.machine = AppStateMachine()
        self.machine.transition(AppState.SYNCING, "fixture sync")
        self.machine.transition(AppState.READY, "fixture ready")
        self.factory = FakeConnectionFactory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preflight_stays_shadow_until_exact_memory_only_arm(self) -> None:
        runtime = MainnetExecutionRuntime(
            self.settings,
            self.machine,
            context_provider=lambda: None,
            armed_strategy_provider=lambda _prepared: armed_strategy(),
            credential_store=StubCredentials(),  # type: ignore[arg-type]
            connection_factory=self.factory,
            poll_seconds=0.01,
        )
        runtime.prepare(prepared_trade())
        self._wait(runtime)
        self.assertEqual(runtime.status.phase, MainnetRuntimePhase.CHECKED)
        self.assertEqual(runtime.status.mode, ExecutionMode.SHADOW)
        with self.assertRaisesRegex(PermissionError, "exact confirmation"):
            runtime.arm("ARM")
        runtime.arm("ARM MICRO_LIVE")
        self.assertEqual(runtime.status.phase, MainnetRuntimePhase.ARMED)
        self.assertEqual(runtime.status.mode, ExecutionMode.MICRO_LIVE)
        runtime.invalidate("fixture reset")
        self.assertEqual(runtime.status.phase, MainnetRuntimePhase.DISARMED)
        self.assertEqual(runtime.status.mode, ExecutionMode.SHADOW)
        runtime.stop()

    def test_missing_historical_binding_blocks_before_connection_factory(self) -> None:
        runtime = MainnetExecutionRuntime(
            self.settings,
            self.machine,
            context_provider=lambda: None,
            credential_store=StubCredentials(),  # type: ignore[arg-type]
            connection_factory=self.factory,
        )
        with self.assertRaisesRegex(PermissionError, "fail-closed"):
            runtime.prepare(prepared_trade())
        self.assertEqual(self.factory.calls, 0)
        self.assertEqual(runtime.status.mode, ExecutionMode.SHADOW)
        runtime.stop()

    def test_bybit_clock_error_triggers_windows_resync_without_blind_retry(self) -> None:
        runtime = MainnetExecutionRuntime(
            self.settings,
            self.machine,
            context_provider=lambda: None,
        )
        result = WindowsTimeSyncResult(
            attempted=True,
            succeeded=True,
            command=("w32tm", "/resync"),
            detail="sync ok",
        )
        with patch(
            "bybit_workbench.ui.mainnet_execution_runtime.resync_windows_time",
            return_value=result,
        ) as sync:
            runtime._maybe_resync_after_clock_error(  # noqa: SLF001 - safety behavior test
                BybitApiError("/v5/order/create", 10002, "request expired")
            )

        sync.assert_called_once_with()
        model = WorkbenchViewModel(AppMode.LIVE)
        runtime.drain_into(model)
        self.assertTrue(
            any("Mutation is not retried automatically" in line for line in model.state.system_log)
        )
        runtime.stop()

    def test_new_runtime_instance_never_restores_armed_ticket(self) -> None:
        first = MainnetExecutionRuntime(
            self.settings,
            self.machine,
            context_provider=lambda: None,
        )
        second = MainnetExecutionRuntime(
            self.settings,
            self.machine,
            context_provider=lambda: None,
        )
        self.assertEqual(first.status.phase, MainnetRuntimePhase.DISARMED)
        self.assertEqual(second.status.mode, ExecutionMode.SHADOW)
        first.stop()
        second.stop()

    @staticmethod
    def _wait(runtime: MainnetExecutionRuntime) -> None:
        deadline = time.monotonic() + 2
        while runtime.running and time.monotonic() < deadline:
            time.sleep(0.01)
        if runtime.running:
            runtime.stop()
            raise AssertionError("Mainnet preflight did not finish")


if __name__ == "__main__":
    unittest.main()
