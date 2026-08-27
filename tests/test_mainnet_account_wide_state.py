import unittest
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import InstrumentRules, Position
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.exchange.bybit.errors import BybitModeMismatch
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot, ChannelHealth
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    ApiKeyInfo,
    ApiKeyPermissionAudit,
    BybitPositionSnapshot,
    MainnetAccountWideSnapshot,
)
from bybit_workbench.exchange.bybit.rest import BybitReadOnlyAdapter
from bybit_workbench.execution.mainnet_safety import MutationBlocked
from bybit_workbench.execution.mainnet_state import (
    MainnetReadinessContext,
    RestBackedMainnetSafetyStateProvider,
)

NOW = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


def ok(result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"retCode": 0, "retMsg": "OK", "time": NOW_MS, "result": result}


def raw_position(
    symbol: str,
    side: str = "",
    size: str = "0",
    *,
    position_idx: int = 0,
) -> dict[str, Any]:
    return {
        "positionIdx": position_idx,
        "symbol": symbol,
        "side": side,
        "size": size,
        "avgPrice": "3" if size != "0" else "",
        "leverage": "1",
        "markPrice": "3.1",
        "liqPrice": "",
        "stopLoss": "2.8" if size != "0" else "0",
        "takeProfit": "0",
        "trailingStop": "0",
        "unrealisedPnl": "0",
        "seq": 1,
        "updatedTime": str(NOW_MS),
    }


def raw_order(
    order_id: str,
    symbol: str,
    *,
    client_id: str | None = None,
    status: str = "New",
    filled: str = "0",
) -> dict[str, Any]:
    return {
        "orderId": order_id,
        "orderLinkId": client_id or f"link-{order_id}",
        "symbol": symbol,
        "side": "Buy",
        "orderType": "Limit",
        "qty": "1",
        "price": "3",
        "reduceOnly": False,
        "stopOrderType": "",
        "orderStatus": status,
        "cumExecQty": filled,
        "avgPrice": "",
        "createdTime": str(NOW_MS),
        "updatedTime": str(NOW_MS),
    }


class AccountWideTransport:
    def __init__(self, *, include_hedge_position: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.include_hedge_position = include_hedge_position

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        selected = dict(params)
        self.calls.append((endpoint, selected, authenticated))
        if endpoint == "/v5/market/instruments-info":
            if "symbol" in selected:
                return ok(
                    {
                        "list": [
                            {
                                "symbol": selected["symbol"],
                                "settleCoin": "USDT",
                                "priceFilter": {"tickSize": "0.001"},
                                "lotSizeFilter": {
                                    "qtyStep": "0.1",
                                    "minOrderQty": "0.1",
                                    "minNotionalValue": "5",
                                    "maxOrderQty": "1000",
                                    "maxMktOrderQty": "1000",
                                },
                            }
                        ],
                        "nextPageCursor": "",
                    }
                )
            return ok(
                {
                    "list": [
                        {"symbol": "UNIUSDT", "settleCoin": "USDT"},
                        {"symbol": "ETHUSDC", "settleCoin": "USDC"},
                    ],
                    "nextPageCursor": "",
                }
            )
        if endpoint == "/v5/account/wallet-balance":
            return ok(
                {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalEquity": "20",
                            "totalAvailableBalance": "20",
                            "totalWalletBalance": "20",
                            "totalPerpUPL": "0",
                        }
                    ]
                }
            )
        if endpoint == "/v5/account/info":
            return ok({"marginMode": "ISOLATED_MARGIN", "unifiedMarginStatus": 5})
        if endpoint == "/v5/account/fee-rate":
            return ok(
                {"list": [{"makerFeeRate": "0.0002", "takerFeeRate": "0.00055"}]}
            )
        if endpoint == "/v5/position/closed-pnl":
            return ok({"list": [], "nextPageCursor": ""})
        if endpoint == "/v5/position/list":
            category = selected["category"]
            if "symbol" in selected:
                return ok({"list": [raw_position(str(selected["symbol"]))]})
            if category == "linear" and selected.get("settleCoin") == "USDT":
                rows = [raw_position("UNIUSDT", "Buy", "1")]
                if self.include_hedge_position:
                    rows.append(
                        raw_position(
                            "UNIUSDT",
                            "Sell",
                            "1",
                            position_idx=2,
                        )
                    )
                return ok(
                    {
                        "list": rows
                    }
                )
            if category == "linear" and selected.get("settleCoin") == "USDC":
                return ok({"list": [raw_position("ETHUSDC", "Buy", "1")]})
            if category == "inverse":
                return ok({"list": [raw_position("BTCUSD", "Sell", "1")]})
        if endpoint == "/v5/order/realtime":
            category = selected["category"]
            if category == "linear" and selected.get("settleCoin") == "USDT":
                return ok({"list": [raw_order("one", "UNIUSDT")], "nextPageCursor": ""})
            if category == "linear" and selected.get("settleCoin") == "USDC":
                return ok({"list": [], "nextPageCursor": ""})
            if category == "inverse":
                return ok({"list": [raw_order("two", "BTCUSD")], "nextPageCursor": ""})
        raise AssertionError((endpoint, selected, authenticated))


class OrderLookupTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        if not authenticated or params.get("orderLinkId") != "lost-close-link":
            raise AssertionError((endpoint, params, authenticated))
        self.calls.append(endpoint)
        if endpoint == "/v5/order/realtime":
            return ok({"list": [], "nextPageCursor": ""})
        if endpoint == "/v5/order/history":
            return ok(
                {
                    "list": [
                        raw_order(
                            "closed-order",
                            "UNIUSDT",
                            client_id="lost-close-link",
                            status="Filled",
                            filled="1",
                        )
                    ],
                    "nextPageCursor": "",
                }
            )
        raise AssertionError(endpoint)


def key_info() -> ApiKeyInfo:
    return ApiKeyInfo(
        "BotW-Mainnet",
        False,
        (),
        80,
        NOW + timedelta(days=80),
        NOW - timedelta(days=1),
        True,
        None,
        True,
        1,
        ApiKeyPermissionAudit(("Order", "Position"), (), (), (), (), (), ()),
    )


def selected_position() -> BybitPositionSnapshot:
    return BybitPositionSnapshot(
        Position("UNIUSDT", PositionSide.FLAT, Decimal("0"), None),
        0,
        Decimal("1"),
        Decimal("3.1"),
        None,
        None,
        None,
        None,
        Decimal("0"),
        1,
        NOW,
    )


class FixtureAccountWideReader:
    async def mainnet_account_wide_snapshot(
        self,
        symbol: str,
    ) -> MainnetAccountWideSnapshot:
        if symbol != "UNIUSDT":
            raise AssertionError(symbol)
        return MainnetAccountWideSnapshot(
            InstrumentRules(
                "UNIUSDT",
                Decimal("0.001"),
                Decimal("0.1"),
                Decimal("0.1"),
                Decimal("5"),
                Decimal("1000"),
            ),
            AccountSnapshot(
                "UNIFIED",
                Decimal("20"),
                Decimal("20"),
                Decimal("20"),
                Decimal("0"),
                NOW,
                "ISOLATED_MARGIN",
                5,
                Decimal("0.0002"),
                Decimal("0.00055"),
                Decimal("0"),
            ),
            selected_position(),
            (),
            (),
            NOW,
        )




class AdvancingAccountWideReader(FixtureAccountWideReader):
    def __init__(self, on_read: Callable[[], None]) -> None:
        self.on_read = on_read

    async def mainnet_account_wide_snapshot(
        self,
        symbol: str,
    ) -> MainnetAccountWideSnapshot:
        self.on_read()
        return await super().mainnet_account_wide_snapshot(symbol)


class MainnetAccountWideStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_lookup_falls_back_to_history_after_realtime_miss(self) -> None:
        transport = OrderLookupTransport()
        found = await BybitReadOnlyAdapter(transport).order_by_client_id(
            "UNIUSDT",
            "lost-close-link",
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.order_id, "closed-order")
        self.assertEqual(
            transport.calls,
            ["/v5/order/realtime", "/v5/order/history"],
        )

    async def test_adapter_enumerates_all_linear_settle_coins_and_inverse(self) -> None:
        transport = AccountWideTransport()
        snapshot = await BybitReadOnlyAdapter(transport).mainnet_account_wide_snapshot(
            "UNIUSDT"
        )
        self.assertEqual(snapshot.instrument.symbol, "UNIUSDT")
        self.assertEqual(
            {(item.position.symbol, item.position_idx) for item in snapshot.other_positions},
            {("ETHUSDC", 0), ("BTCUSD", 0)},
        )
        self.assertEqual(
            {(item.request.symbol, item.order_id) for item in snapshot.open_orders},
            {("UNIUSDT", "one"), ("BTCUSD", "two")},
        )
        position_queries = [
            params
            for endpoint, params, _authenticated in transport.calls
            if endpoint == "/v5/position/list" and "symbol" not in params
        ]
        self.assertIn(
            {"category": "linear", "limit": 200, "settleCoin": "USDT"},
            position_queries,
        )
        self.assertIn(
            {"category": "linear", "limit": 200, "settleCoin": "USDC"},
            position_queries,
        )
        self.assertIn({"category": "inverse", "limit": 200}, position_queries)

    async def test_adapter_fails_closed_on_any_hedge_mode_position(self) -> None:
        transport = AccountWideTransport(include_hedge_position=True)
        with self.assertRaisesRegex(BybitModeMismatch, "positionIdx=2"):
            await BybitReadOnlyAdapter(transport).mainnet_account_wide_snapshot(
                "UNIUSDT"
            )

    async def test_provider_combines_fresh_ws_identity_and_account_wide_rest(self) -> None:
        channel = ChannelHealth(True, True, NOW, None)
        context = MainnetReadinessContext(
            "https://api.bybit.com",
            key_info(),
            BybitHealthSnapshot(channel, channel, channel),
            True,
        )
        provider = RestBackedMainnetSafetyStateProvider(
            FixtureAccountWideReader(),
            "https://api.bybit.com",
            lambda: context,
        )
        snapshot = await provider.snapshot("UNIUSDT")
        self.assertEqual(snapshot.api_key.note, "BotW-Mainnet")
        self.assertTrue(snapshot.positions_complete)
        self.assertTrue(snapshot.open_orders_complete)
        self.assertTrue(snapshot.reconciliation_complete)

    async def test_provider_refreshes_ws_health_after_slow_account_wide_read(self) -> None:
        old = ChannelHealth(True, True, NOW - timedelta(seconds=9), None)
        fresh = ChannelHealth(True, True, NOW + timedelta(seconds=3), None)
        contexts = [
            MainnetReadinessContext(
                "https://api.bybit.com",
                key_info(),
                BybitHealthSnapshot(old, old, old),
                True,
            )
        ]

        def advance_health() -> None:
            contexts.append(
                MainnetReadinessContext(
                    "https://api.bybit.com",
                    key_info(),
                    BybitHealthSnapshot(fresh, fresh, fresh),
                    True,
                )
            )

        provider = RestBackedMainnetSafetyStateProvider(
            AdvancingAccountWideReader(advance_health),
            "https://api.bybit.com",
            lambda: contexts[-1],
        )
        snapshot = await provider.snapshot("UNIUSDT")
        self.assertEqual(snapshot.public_observed_at, fresh.last_message_at)
        self.assertEqual(snapshot.private_observed_at, fresh.last_message_at)

    async def test_provider_blocks_if_connection_identity_changes_during_rest(self) -> None:
        channel = ChannelHealth(True, True, NOW, None)
        contexts = [
            MainnetReadinessContext(
                "https://api.bybit.com",
                key_info(),
                BybitHealthSnapshot(channel, channel, channel),
                True,
            )
        ]

        def change_identity() -> None:
            changed_key = ApiKeyInfo(
                "Different-Key",
                False,
                (),
                80,
                NOW + timedelta(days=80),
                NOW - timedelta(days=1),
                True,
                None,
                True,
                1,
                ApiKeyPermissionAudit(("Order", "Position"), (), (), (), (), (), ()),
            )
            contexts.append(
                MainnetReadinessContext(
                    "https://api.bybit.com",
                    changed_key,
                    BybitHealthSnapshot(channel, channel, channel),
                    True,
                )
            )

        provider = RestBackedMainnetSafetyStateProvider(
            AdvancingAccountWideReader(change_identity),
            "https://api.bybit.com",
            lambda: contexts[-1],
        )
        with self.assertRaisesRegex(MutationBlocked, "identity changed"):
            await provider.snapshot("UNIUSDT")

    async def test_provider_blocks_before_rest_when_read_context_is_missing(self) -> None:
        reader = FixtureAccountWideReader()
        provider = RestBackedMainnetSafetyStateProvider(
            reader,
            "https://api.bybit.com",
            lambda: None,
        )
        with self.assertRaisesRegex(MutationBlocked, "GET-only"):
            await provider.snapshot("UNIUSDT")


if __name__ == "__main__":
    unittest.main()
