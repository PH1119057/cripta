import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.app.redaction import REDACTED, redact_mapping, redact_text
from bybit_workbench.domain import InstrumentRules, Order, OrderRequest, Position
from bybit_workbench.domain.types import (
    AppMode,
    ExecutionMode,
    OrderRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    ApiKeyInfo,
    ApiKeyPermissionAudit,
    BybitPositionSnapshot,
)
from bybit_workbench.execution.mainnet_safety import (
    ExecutionArmingController,
    MainnetMutation,
    MainnetMutationGateway,
    MainnetSafetySnapshot,
    MemoryIdempotencyStore,
    MicroLiveEntryPlan,
    MicroLiveLimits,
    MutationBlocked,
    MutationKind,
    UnprotectedPositionEmergency,
    issue_micro_live_ticket,
    require_confirmed_server_stop,
)
from bybit_workbench.historical import (
    HistoricalEligibilityQuery,
    HistoricalGateDecision,
    eligibility_binding_fingerprint,
)
from bybit_workbench.persistence.mainnet_idempotency import SqliteIdempotencyStore
from bybit_workbench.strategies.arming import ArmedStrategy

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
ENDPOINT = "https://api.bybit.com"


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class SpyWriteTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, endpoint: str, params: Any) -> dict[str, Any]:
        self.calls.append((endpoint, dict(params)))
        return {"retCode": 0, "result": {"orderId": "one"}}


class YieldingSpyWriteTransport(SpyWriteTransport):
    async def post(self, endpoint: str, params: Any) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return await super().post(endpoint, params)


class SnapshotProvider:
    def __init__(
        self,
        state: MainnetSafetySnapshot,
        clock: MutableClock,
        *,
        refresh: bool = True,
    ) -> None:
        self.state = state
        self.clock = clock
        self.refresh = refresh
        self.calls = 0

    async def snapshot(self, symbol: str) -> MainnetSafetySnapshot:
        self.calls += 1
        if symbol != self.state.instrument.symbol:
            raise AssertionError("gateway requested an unexpected symbol")
        if not self.refresh:
            return self.state
        return replace(
            self.state,
            public_observed_at=self.clock(),
            private_observed_at=self.clock(),
            rest_observed_at=self.clock(),
        )


def key_info(
    *,
    wallet: tuple[str, ...] = (),
    spot: tuple[str, ...] = (),
    options: tuple[str, ...] = (),
    other: tuple[tuple[str, tuple[str, ...]], ...] = (),
    read_only: bool = False,
    is_master: bool = True,
    parent_uid: str | None = None,
    key_id: str | None = "key-id-1",
) -> ApiKeyInfo:
    blockers = () if not wallet else (f"Wallet permissions are forbidden: {', '.join(wallet)}",)
    return ApiKeyInfo(
        "BotW-Mainnet",
        read_only,
        (),
        80,
        NOW + timedelta(days=80),
        NOW - timedelta(days=1),
        is_master,
        parent_uid,
        True,
        1,
        ApiKeyPermissionAudit(
            ("Order", "Position"),
            spot,
            wallet,
            options,
            other,
            blockers,
            (),
        ),
        key_id,
    )


def limits(**changes: Any) -> MicroLiveLimits:
    values: dict[str, Any] = {
        "allowed_symbols": frozenset({"BTCUSDT"}),
        "max_order_notional": Decimal("10"),
        "max_total_exposure": Decimal("20"),
        "max_daily_loss": Decimal("5"),
        "max_orders_per_interval": 2,
        "order_interval": timedelta(minutes=1),
        "cooldown": timedelta(seconds=10),
    }
    values.update(changes)
    return MicroLiveLimits(**values)


def position(
    *,
    side: PositionSide = PositionSide.FLAT,
    quantity: Decimal = Decimal("0"),
    leverage: Decimal = Decimal("1"),
    stop_loss: Decimal | None = None,
    symbol: str = "BTCUSDT",
    mark_price: Decimal = Decimal("50000"),
) -> BybitPositionSnapshot:
    average_price = None if side is PositionSide.FLAT else Decimal("50000")
    return BybitPositionSnapshot(
        Position(symbol, side, quantity, average_price),
        0,
        leverage,
        mark_price,
        None,
        stop_loss,
        None,
        None,
        Decimal("0"),
        1,
        NOW,
    )


def account(
    *,
    available_balance: Decimal = Decimal("20"),
    unrealized_pnl: Decimal = Decimal("0"),
    daily_realized_pnl: Decimal | None = Decimal("0"),
    margin_mode: str = "ISOLATED_MARGIN",
    unified_margin_status: int = 5,
) -> AccountSnapshot:
    return AccountSnapshot(
        "UNIFIED",
        Decimal("20"),
        available_balance,
        Decimal("20"),
        unrealized_pnl,
        NOW,
        margin_mode,
        unified_margin_status,
        Decimal("0.0002"),
        Decimal("0.00055"),
        daily_realized_pnl,
    )


def open_entry_order(
    quantity: Decimal,
    *,
    symbol: str = "BTCUSDT",
    price: Decimal = Decimal("50000"),
) -> Order:
    return Order(
        "existing-order",
        OrderRequest(
            "existing-link",
            symbol,
            OrderSide.BUY,
            OrderType.LIMIT,
            quantity,
            price,
        ),
        status=OrderStatus.ACCEPTED,
    )


def safety_snapshot(**changes: Any) -> MainnetSafetySnapshot:
    values: dict[str, Any] = {
        "endpoint": ENDPOINT,
        "api_key": key_info(),
        "instrument": InstrumentRules(
            "BTCUSDT",
            Decimal("0.1"),
            Decimal("0.0001"),
            Decimal("0.0001"),
            Decimal("5"),
            Decimal("100"),
            Decimal("100"),
        ),
        "account": account(),
        "position": position(),
        "other_positions": (),
        "open_orders": (),
        "public_observed_at": NOW,
        "private_observed_at": NOW,
        "rest_observed_at": NOW,
        "reconciliation_complete": True,
        "positions_complete": True,
        "open_orders_complete": True,
    }
    values.update(changes)
    return MainnetSafetySnapshot(**values)


def armed_strategy(*, allowed: bool = True) -> ArmedStrategy:
    rules = safety_snapshot().instrument
    query = HistoricalEligibilityQuery.from_instrument(
        symbol="BTCUSDT",
        timeframe="60",
        code_version="0.8.5",
        instrument_rules=rules,
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.00055"),
        slippage_percent=Decimal("0.1"),
    )
    binding = eligibility_binding_fingerprint(
        strategy_id="trend_breakout",
        strategy_version="0.1.0",
        parameters_fingerprint="parameters-sha256",
        query=query,
        dataset_fingerprint="d" * 64,
    )
    return ArmedStrategy(
        "trend_breakout",
        "0.1.0",
        {"timeframe": "60"},
        HistoricalGateDecision(
            allowed,
            "fixture decision",
            "parameters-sha256",
            "fixture-report",
            "d" * 64,
            binding,
            query,
        ),
    )



def sealed_entry_plan(**changes: Any) -> MicroLiveEntryPlan:
    values: dict[str, Any] = {
        "symbol": "BTCUSDT",
        "client_order_id": "intent-1",
        "side": OrderSide.BUY,
        "quantity": Decimal("0.0001"),
        "limit_price": Decimal("50000"),
        "stop_loss": Decimal("49000"),
        "take_profit": None,
        "risk_percent": Decimal("1"),
        "risk_budget": Decimal("0.20"),
        "estimated_loss_at_stop": Decimal("0.11"),
    }
    values.update(changes)
    return MicroLiveEntryPlan(**values)


def ticket(
    state: MainnetSafetySnapshot,
    clock: MutableClock,
    *,
    selected_limits: MicroLiveLimits | None = None,
    strategy: ArmedStrategy | None = None,
    selected_entry_plan: MicroLiveEntryPlan | None = None,
):
    return issue_micro_live_ticket(
        AppSettings(mode=AppMode.LIVE, allow_live_trading=True),
        state,
        selected_limits or limits(),
        strategy or armed_strategy(),
        selected_entry_plan or sealed_entry_plan(),
        now=clock(),
    )


def entry(key: str = "intent-1", **param_changes: Any) -> MainnetMutation:
    params: dict[str, Any] = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Limit",
        "qty": "0.0001",
        "price": "50000",
        "timeInForce": "GTC",
        "positionIdx": 0,
        "orderLinkId": key,
        "tpslMode": "Full",
        "stopLoss": "49000",
        "slTriggerBy": "MarkPrice",
        "slOrderType": "Market",
    }
    params.update(param_changes)
    return MainnetMutation("/v5/order/create", params, MutationKind.ENTRY, key)


def market_entry(key: str = "intent-market-1", **param_changes: Any) -> MainnetMutation:
    params: dict[str, Any] = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Market",
        "qty": "0.0001",
        "positionIdx": 0,
        "orderLinkId": key,
        "tpslMode": "Full",
        "stopLoss": "49000",
        "slTriggerBy": "MarkPrice",
        "slOrderType": "Market",
    }
    params.update(param_changes)
    return MainnetMutation("/v5/order/create", params, MutationKind.ENTRY, key)


def armed_gateway(
    state: MainnetSafetySnapshot | None = None,
    *,
    clock: MutableClock | None = None,
    selected_limits: MicroLiveLimits | None = None,
    selected_entry_plan: MicroLiveEntryPlan | None = None,
    refresh: bool = True,
    store: MemoryIdempotencyStore | None = None,
):
    selected_clock = clock or MutableClock()
    selected_state = state or safety_snapshot()
    controller = ExecutionArmingController(selected_clock)
    controller.arm_micro_live(
        "ARM MICRO_LIVE",
        ticket(
            selected_state,
            selected_clock,
            selected_limits=selected_limits,
            selected_entry_plan=selected_entry_plan,
        ),
    )
    spy = SpyWriteTransport()
    provider = SnapshotProvider(selected_state, selected_clock, refresh=refresh)
    gateway = MainnetMutationGateway(
        spy,
        controller,
        store or MemoryIdempotencyStore(),
        provider,
        endpoint=ENDPOINT,
        clock=selected_clock,
    )
    return controller, gateway, spy, provider, selected_clock


class MainnetSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_sqlite_idempotency_claim_survives_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.db"
            first = SqliteIdempotencyStore(path)
            self.assertTrue(first.claim_before_send("stable-intent-key"))
            first.close()
            restarted = SqliteIdempotencyStore(path)
            self.assertFalse(restarted.claim_before_send("stable-intent-key"))
            restarted.close()

    async def test_shadow_blocks_before_state_provider_and_delegate(self) -> None:
        clock = MutableClock()
        spy = SpyWriteTransport()
        provider = SnapshotProvider(safety_snapshot(), clock)
        gateway = MainnetMutationGateway(
            spy,
            ExecutionArmingController(clock),
            MemoryIdempotencyStore(),
            provider,
            clock=clock,
        )
        mutations = (
            entry(),
            MainnetMutation(
                "/v5/order/cancel",
                {"category": "linear", "symbol": "BTCUSDT", "orderId": "one"},
                MutationKind.CANCEL,
                "cancel-shadow",
            ),
            MainnetMutation(
                "/v5/position/set-leverage",
                {"category": "linear", "symbol": "BTCUSDT"},
                MutationKind.ACCOUNT_CONFIGURATION,
                "config-shadow",
            ),
        )
        for mutation in mutations:
            with self.subTest(endpoint=mutation.endpoint), self.assertRaises(MutationBlocked):
                await gateway.submit(mutation)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(spy.calls, [])


    async def test_market_entry_is_allowed_without_price_and_uses_fresh_mark_notional(self) -> None:
        state = safety_snapshot(position=position(mark_price=Decimal("50000")))
        plan = sealed_entry_plan(
            client_order_id="intent-market-1",
            order_type=OrderType.MARKET,
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            state,
            selected_entry_plan=plan,
        )

        await gateway.submit(market_entry())

        self.assertEqual(len(spy.calls), 1)
        _endpoint, params = spy.calls[0]
        self.assertEqual(params["orderType"], "Market")
        self.assertNotIn("price", params)
        self.assertEqual(params["tpslMode"], "Full")
        self.assertEqual(params["stopLoss"], "49000")
        self.assertEqual(params["slTriggerBy"], "MarkPrice")
        self.assertEqual(params["slOrderType"], "Market")

    async def test_market_entry_is_bounded_by_fresh_mark_notional(self) -> None:
        state = safety_snapshot(position=position(mark_price=Decimal("52000")))
        plan = sealed_entry_plan(
            client_order_id="intent-market-1",
            order_type=OrderType.MARKET,
        )
        selected_limits = limits(
            max_order_notional=Decimal("5.1"),
            max_total_exposure=Decimal("5.1"),
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            state,
            selected_limits=selected_limits,
            selected_entry_plan=plan,
        )

        with self.assertRaisesRegex(MutationBlocked, "max order notional exceeded"):
            await gateway.submit(market_entry())
        self.assertEqual(spy.calls, [])

    async def test_market_entry_rejects_caller_price(self) -> None:
        plan = sealed_entry_plan(
            client_order_id="intent-market-1",
            order_type=OrderType.MARKET,
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            selected_entry_plan=plan,
        )

        with self.assertRaisesRegex(MutationBlocked, "must not send price"):
            await gateway.submit(market_entry(price="50000"))
        self.assertEqual(spy.calls, [])

    async def test_market_entry_rejects_wrong_order_type_against_sealed_plan(self) -> None:
        plan = sealed_entry_plan(
            client_order_id="intent-market-1",
            order_type=OrderType.MARKET,
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            selected_entry_plan=plan,
        )

        with self.assertRaisesRegex(MutationBlocked, "order type differs"):
            await gateway.submit(
                entry(
                    key="intent-market-1",
                    orderType="Limit",
                    price="50000",
                    timeInForce="GTC",
                )
            )
        self.assertEqual(spy.calls, [])

    async def test_buy_limit_below_mark_uses_sealed_limit_notional_for_cap(self) -> None:
        state = safety_snapshot(position=position(mark_price=Decimal("51000")))
        selected_limits = limits(
            max_order_notional=Decimal("5"),
            max_total_exposure=Decimal("5"),
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            state,
            selected_limits=selected_limits,
        )

        await gateway.submit(entry())

        self.assertEqual(len(spy.calls), 1)

    async def test_marketable_sell_limit_keeps_conservative_mark_notional_cap(self) -> None:
        state = safety_snapshot(position=position(mark_price=Decimal("51000")))
        selected_limits = limits(
            max_order_notional=Decimal("5"),
            max_total_exposure=Decimal("5"),
        )
        selected_clock = MutableClock()
        controller = ExecutionArmingController(selected_clock)
        sell_plan = sealed_entry_plan(
            side=OrderSide.SELL,
            stop_loss=Decimal("51000"),
        )
        controller.arm_micro_live(
            "ARM MICRO_LIVE",
            issue_micro_live_ticket(
                AppSettings(mode=AppMode.LIVE, allow_live_trading=True),
                state,
                selected_limits,
                armed_strategy(),
                sell_plan,
                now=selected_clock(),
            ),
        )
        spy = SpyWriteTransport()
        provider = SnapshotProvider(state, selected_clock)
        gateway = MainnetMutationGateway(
            spy,
            controller,
            MemoryIdempotencyStore(),
            provider,
            endpoint=ENDPOINT,
            clock=selected_clock,
        )
        sell_entry = entry(side="Sell", stopLoss="51000")

        with self.assertRaisesRegex(MutationBlocked, "max order notional exceeded"):
            await gateway.submit(sell_entry)
        self.assertEqual(spy.calls, [])

    async def test_safe_entry_uses_fresh_exchange_truth(self) -> None:
        _controller, gateway, spy, provider, _clock = armed_gateway()
        response = await gateway.submit(entry())
        self.assertEqual(response["retCode"], 0)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(spy.calls), 1)

    async def test_entry_must_match_exact_sealed_risk_plan(self) -> None:
        cases = (
            {"qty": "0.0002"},
            {"price": "50000.1"},
        )
        for index, changes in enumerate(cases):
            _controller, gateway, spy, _provider, _clock = armed_gateway()
            with self.subTest(changes=changes), self.assertRaisesRegex(
                MutationBlocked, "sealed Micro-Live plan"
            ):
                await gateway.submit(entry(f"sealed-{index}", **changes))
            self.assertEqual(spy.calls, [])

    def test_ticket_requires_external_switch_and_typed_historical_gate(self) -> None:
        state = safety_snapshot()
        clock = MutableClock()
        with self.assertRaisesRegex(MutationBlocked, "external"):
            issue_micro_live_ticket(
                AppSettings(mode=AppMode.LIVE),
                state,
                limits(),
                armed_strategy(),
                sealed_entry_plan(),
                now=clock(),
            )
        with self.assertRaisesRegex(MutationBlocked, "historical"):
            ticket(state, clock, strategy=armed_strategy(allowed=False))

    def test_ticket_rejects_unsafe_account_and_key_contexts(self) -> None:
        cases = (
            ("Spot", {"api_key": key_info(spot=("SpotTrade",))}),
            ("Options", {"api_key": key_info(options=("OptionsTrade",))}),
            ("Wallet", {"api_key": key_info(wallet=("Withdraw",))}),
            (
                "UnknownSurface",
                {"api_key": key_info(other=(("UnknownSurface", ("Write",)),))},
            ),
            (
                "subaccount",
                {"api_key": key_info(is_master=False, parent_uid="parent")},
            ),
            ("UTA 2.0", {"account": account(unified_margin_status=3)}),
            ("ISOLATED", {"account": account(margin_mode="REGULAR_MARGIN")}),
            ("leverage", {"position": position(leverage=Decimal("2"))}),
            ("reconciled", {"reconciliation_complete": False}),
            ("position snapshot", {"positions_complete": False}),
            ("incomplete", {"open_orders_complete": False}),
        )
        for expected, changes in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(
                MutationBlocked, expected
            ):
                ticket(safety_snapshot(**changes), MutableClock())

    def test_derivatives_trade_permission_is_allowed_for_unified_mainnet_key(self) -> None:
        state = safety_snapshot(
            api_key=key_info(other=(("Derivatives", ("DerivativesTrade",)),))
        )
        issued = ticket(state, MutableClock())
        self.assertEqual(issued.symbol, "BTCUSDT")

    def test_ticket_allows_exchange_note_different_from_local_profile_name(self) -> None:
        state = safety_snapshot(api_key=replace(key_info(), note="Bybit_KZ"))
        issued = issue_micro_live_ticket(
            AppSettings(mode=AppMode.LIVE, allow_live_trading=True),
            state,
            limits(),
            armed_strategy(),
            sealed_entry_plan(),
            now=NOW,
        )
        self.assertEqual(issued.credential_profile_name, "BotW-Mainnet")
        self.assertEqual(issued.key_identity.note, "Bybit_KZ")
        self.assertEqual(issued.key_identity.key_id, "key-id-1")

    async def test_strategy_payload_tampering_is_blocked(self) -> None:
        cases = (
            {"symbol": "ETHUSDT"},
            {"category": "spot"},
            {"positionIdx": 1},
            {"orderType": "Market", "price": None},
            {"timeInForce": "IOC"},
            {"qty": "0.00015"},
            {"price": "50000.05"},
            {"qty": "0.0003"},
            {"stopLoss": "0"},
            {"stopLoss": "51000"},
            {"slTriggerBy": "LastPrice"},
            {"slOrderType": "Limit"},
            {"tpslMode": "Partial"},
            {"closeOnTrigger": True},
            {"orderLinkId": "forged-link"},
            {"triggerPrice": "49500"},
        )
        for index, changes in enumerate(cases):
            _controller, gateway, spy, _provider, _clock = armed_gateway()
            with self.subTest(changes=changes), self.assertRaises(MutationBlocked):
                await gateway.submit(entry(f"tamper-{index}", **changes))
            self.assertEqual(spy.calls, [])

    async def test_entry_inline_take_profit_must_match_sealed_plan(self) -> None:
        plan = sealed_entry_plan(take_profit=Decimal("51000"))
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            selected_entry_plan=plan,
        )
        await gateway.submit(
            entry(
                takeProfit="51000",
                tpTriggerBy="MarkPrice",
                tpOrderType="Market",
            )
        )
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(spy.calls[0][1]["takeProfit"], "51000")

        _controller2, gateway2, spy2, _provider2, _clock2 = armed_gateway(
            selected_entry_plan=plan,
        )
        with self.assertRaises(MutationBlocked):
            await gateway2.submit(
                entry(
                    takeProfit="52000",
                    tpTriggerBy="MarkPrice",
                    tpOrderType="Market",
                )
            )
        self.assertEqual(spy2.calls, [])

    async def test_exposure_loss_and_balance_are_derived_not_declared(self) -> None:
        states = (
            safety_snapshot(open_orders=(open_entry_order(Decimal("0.0004")),)),
            safety_snapshot(account=account(daily_realized_pnl=Decimal("-5"))),
            safety_snapshot(account=account(available_balance=Decimal("4"))),
            safety_snapshot(account=account(unrealized_pnl=Decimal("-5"))),
        )
        for index, state in enumerate(states):
            _controller, gateway, spy, _provider, _clock = armed_gateway(state)
            with self.subTest(index=index), self.assertRaises(MutationBlocked):
                await gateway.submit(entry(f"derived-{index}"))
            self.assertEqual(spy.calls, [])

    def test_ticket_blocks_positions_outside_the_single_symbol(self) -> None:
        other = position(
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            stop_loss=Decimal("1000"),
            symbol="ETHUSDT",
        )
        with self.assertRaisesRegex(MutationBlocked, "outside"):
            ticket(safety_snapshot(other_positions=(other,)), MutableClock())

    async def test_open_order_outside_allowlist_blocks_entry_even_if_reduce_only(self) -> None:
        foreign = Order(
            "foreign-reduce",
            OrderRequest(
                "foreign-link",
                "ETHUSDT",
                OrderSide.SELL,
                OrderType.LIMIT,
                Decimal("0.0001"),
                Decimal("50000"),
                reduce_only=True,
                role=OrderRole.EXIT,
            ),
            status=OrderStatus.ACCEPTED,
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            safety_snapshot(open_orders=(foreign,))
        )
        with self.assertRaisesRegex(MutationBlocked, "outside"):
            await gateway.submit(entry())
        self.assertEqual(spy.calls, [])

    async def test_concurrent_entries_cannot_bypass_cooldown(self) -> None:
        state = safety_snapshot()
        clock = MutableClock()
        controller = ExecutionArmingController(clock)
        controller.arm_micro_live("ARM MICRO_LIVE", ticket(state, clock))
        spy = YieldingSpyWriteTransport()
        gateway = MainnetMutationGateway(
            spy,
            controller,
            MemoryIdempotencyStore(),
            SnapshotProvider(state, clock),
            endpoint=ENDPOINT,
            clock=clock,
        )
        results = await asyncio.gather(
            gateway.submit(entry()),
            gateway.submit(entry()),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(item, MutationBlocked) for item in results), 1)
        self.assertEqual(len(spy.calls), 1)

    async def test_server_stop_can_only_move_toward_lower_risk(self) -> None:
        state = safety_snapshot(
            position=position(
                side=PositionSide.LONG,
                quantity=Decimal("0.0002"),
                stop_loss=Decimal("49000"),
            )
        )
        _controller, gateway, spy, _provider, _clock = armed_gateway(state)
        base = {
            "category": "linear",
            "symbol": "BTCUSDT",
            "positionIdx": 0,
            "tpslMode": "Full",
            "stopLoss": "48000",
            "slTriggerBy": "MarkPrice",
            "slOrderType": "Market",
        }
        with self.assertRaisesRegex(MutationBlocked, "risk-increasing"):
            await gateway.submit(
                MainnetMutation(
                    "/v5/position/trading-stop",
                    base,
                    MutationKind.PROTECTION,
                    "wider-stop",
                )
            )
        safer = dict(base)
        safer.update(stopLoss="49500", trailingStop="500", activePrice="50500")
        await gateway.submit(
            MainnetMutation(
                "/v5/position/trading-stop",
                safer,
                MutationKind.PROTECTION,
                "safer-stop",
            )
        )
        self.assertEqual(len(spy.calls), 1)

    def test_mutation_copies_untrusted_parameter_mapping(self) -> None:
        raw = {"category": "linear", "symbol": "BTCUSDT"}
        mutation = MainnetMutation(
            "/v5/order/cancel",
            raw,
            MutationKind.CANCEL,
            "immutable-copy",
        )
        raw["symbol"] = "ETHUSDT"
        self.assertEqual(mutation.params["symbol"], "BTCUSDT")

    async def test_stale_or_changed_state_invalidates_write(self) -> None:
        clock = MutableClock()
        state = safety_snapshot()
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            state,
            clock=clock,
            refresh=False,
        )
        clock.advance(timedelta(seconds=11))
        with self.assertRaisesRegex(MutationBlocked, "stale"):
            await gateway.submit(entry())
        self.assertEqual(spy.calls, [])

        changed_key_state = replace(
            state,
            api_key=replace(state.api_key, note="other-key", key_id="key-id-2"),
        )
        controller, _unused, changed_spy, _unused_provider, _ = armed_gateway(
            state,
            clock=MutableClock(),
        )
        changed_provider = SnapshotProvider(changed_key_state, MutableClock())
        changed_gateway = MainnetMutationGateway(
            changed_spy,
            controller,
            MemoryIdempotencyStore(),
            changed_provider,
            clock=MutableClock(),
        )
        with self.assertRaisesRegex(MutationBlocked, "identity"):
            await changed_gateway.submit(entry("changed-key"))
        self.assertEqual(changed_spy.calls, [])

    async def test_rate_limits_use_internal_clock_and_idempotency_is_durable(self) -> None:
        clock = MutableClock()
        store = MemoryIdempotencyStore()
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            clock=clock,
            store=store,
        )
        await gateway.submit(entry())
        clock.advance(timedelta(seconds=5))
        with self.assertRaisesRegex(MutationBlocked, "cooldown"):
            await gateway.submit(entry())
        self.assertEqual(len(spy.calls), 1)

        _controller2, restarted, _spy2, _provider2, _clock2 = armed_gateway(
            clock=clock,
            store=store,
        )
        with self.assertRaisesRegex(MutationBlocked, "duplicate"):
            await restarted.submit(entry())

    async def test_relaxed_rate_limit_cannot_expand_one_sealed_entry_plan(self) -> None:
        clock = MutableClock()
        selected = limits(cooldown=timedelta(0), max_orders_per_interval=2)
        _controller, gateway, spy, _provider, _clock = armed_gateway(
            clock=clock,
            selected_limits=selected,
        )
        await gateway.submit(entry())
        clock.advance(timedelta(seconds=1))
        with self.assertRaisesRegex(MutationBlocked, "sealed Micro-Live plan"):
            await gateway.submit(entry("rate-2"))
        self.assertEqual(len(spy.calls), 1)

    async def test_kill_switch_allows_only_valid_cancel_and_reduce_only(self) -> None:
        open_position = position(
            side=PositionSide.LONG,
            quantity=Decimal("0.0002"),
            stop_loss=Decimal("49000"),
        )
        controller, gateway, spy, _provider, _clock = armed_gateway(
            safety_snapshot(position=open_position)
        )
        controller.activate_kill_switch()
        with self.assertRaisesRegex(MutationBlocked, "kill switch"):
            await gateway.submit(entry())
        await gateway.submit(
            MainnetMutation(
                "/v5/order/cancel",
                {"category": "linear", "symbol": "BTCUSDT", "orderId": "one"},
                MutationKind.CANCEL,
                "cancel-1",
            )
        )
        await gateway.submit(
            MainnetMutation(
                "/v5/order/create",
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "side": "Sell",
                    "orderType": "Market",
                    "qty": "0.0002",
                    "positionIdx": 0,
                    "orderLinkId": "reduce-1",
                    "reduceOnly": True,
                    "closeOnTrigger": False,
                },
                MutationKind.REDUCE_ONLY,
                "reduce-1",
            )
        )
        self.assertEqual(len(spy.calls), 2)

    async def test_reduce_only_cannot_flip_or_overclose_position(self) -> None:
        state = safety_snapshot(
            position=position(
                side=PositionSide.LONG,
                quantity=Decimal("0.0002"),
                stop_loss=Decimal("49000"),
            )
        )
        for index, changes in enumerate(
            ({"side": "Buy"}, {"qty": "0.0003"}, {"reduceOnly": False})
        ):
            _controller, gateway, spy, _provider, _clock = armed_gateway(state)
            params: dict[str, Any] = {
                "category": "linear",
                "symbol": "BTCUSDT",
                "side": "Sell",
                "orderType": "Market",
                "qty": "0.0002",
                "orderLinkId": f"close-{index}",
                "reduceOnly": True,
            }
            params.update(changes)
            mutation = MainnetMutation(
                "/v5/order/create",
                params,
                MutationKind.REDUCE_ONLY,
                f"close-{index}",
            )
            with self.subTest(changes=changes), self.assertRaises(MutationBlocked):
                await gateway.submit(mutation)
            self.assertEqual(spy.calls, [])

    async def test_expired_ticket_blocks_entry_but_not_emergency_cancel(self) -> None:
        clock = MutableClock()
        controller, gateway, spy, _provider, _clock = armed_gateway(clock=clock)
        clock.advance(timedelta(minutes=3))
        with self.assertRaisesRegex(MutationBlocked, "expired"):
            await gateway.submit(entry())
        self.assertEqual(controller.mode, ExecutionMode.SHADOW)
        self.assertEqual(spy.calls, [])

        clock2 = MutableClock()
        controller2, gateway2, spy2, _provider2, _clock2 = armed_gateway(clock=clock2)
        controller2.activate_kill_switch()
        clock2.advance(timedelta(minutes=3))
        await gateway2.submit(
            MainnetMutation(
                "/v5/order/cancel",
                {"category": "linear", "symbol": "BTCUSDT", "orderLinkId": "old"},
                MutationKind.CANCEL,
                "expired-cancel",
            )
        )
        self.assertEqual(len(spy2.calls), 1)

    async def test_account_configuration_mutations_are_not_available(self) -> None:
        _controller, gateway, spy, _provider, _clock = armed_gateway()
        for index, endpoint in enumerate(
            ("/v5/position/set-leverage", "/v5/position/switch-isolated")
        ):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(
                MutationBlocked, "unsupported"
            ):
                await gateway.submit(
                    MainnetMutation(
                        endpoint,
                        {"category": "linear", "symbol": "BTCUSDT"},
                        MutationKind.ACCOUNT_CONFIGURATION,
                        f"configuration-{index}",
                    )
                )
        self.assertEqual(spy.calls, [])

    def test_restart_is_disarmed_and_full_live_is_unavailable(self) -> None:
        controller = ExecutionArmingController(MutableClock())
        self.assertEqual(controller.mode, ExecutionMode.SHADOW)
        with self.assertRaisesRegex(MutationBlocked, "unavailable"):
            controller.arm_live("ARM LIVE")

    def test_open_position_without_confirmed_stop_is_emergency(self) -> None:
        selected = position(side=PositionSide.LONG, quantity=Decimal("0.0001"))
        with self.assertRaises(UnprotectedPositionEmergency):
            require_confirmed_server_stop(selected)

    def test_credentials_are_redacted_from_text_and_structures(self) -> None:
        credentials = BybitCredentials(
            AppMode.LIVE,
            "mainnet-key-value",
            "mainnet-secret-value",
            "BotW-Mainnet",
        )
        safe = redact_text(
            RuntimeError(
                f"api_key={credentials.api_key} api_secret={credentials.api_secret}"
            ),
            credentials,
        )
        self.assertNotIn(credentials.api_key, safe)
        self.assertNotIn(credentials.api_secret, safe)
        self.assertIn(REDACTED, safe)
        mapped = redact_mapping({"apiKey": credentials.api_key, "nested": {"secret": "x"}})
        self.assertEqual(mapped["apiKey"], REDACTED)
        self.assertEqual(mapped["nested"]["secret"], REDACTED)


if __name__ == "__main__":
    unittest.main()
