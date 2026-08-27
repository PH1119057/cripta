import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain import Order, OrderRequest, Position
from bybit_workbench.domain.types import AppState, OrderSide, OrderStatus, OrderType, PositionSide
from bybit_workbench.exchange.bybit import (
    BybitReadOnlyAdapter,
    BybitReadSnapshot,
    BybitStreamProcessor,
    HealthMonitor,
    PybitReadOnlyTransport,
    PybitWebSocketBridge,
    ReadOnlySynchronizer,
    ReconnectBackoff,
    StreamRecoveryCoordinator,
)
from bybit_workbench.exchange.bybit.errors import (
    BybitApiError,
    BybitErrorCategory,
    BybitModeMismatch,
    BybitProtocolError,
    classify_bybit_error,
)
from bybit_workbench.exchange.bybit.mappers import map_account, map_position, timestamp_ms
from bybit_workbench.persistence import TradingJournal

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


def response(items: list[Any], **result_extra: Any) -> dict[str, Any]:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "time": NOW_MS,
        "result": {"list": items, **result_extra},
    }


def instrument_item() -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "priceFilter": {"tickSize": "0.10"},
        "lotSizeFilter": {
            "qtyStep": "0.001",
            "minOrderQty": "0.001",
            "minNotionalValue": "5",
            "maxOrderQty": "100",
            "maxMktOrderQty": "25",
        },
    }


def wallet_item() -> dict[str, Any]:
    return {
        "accountType": "UNIFIED",
        "totalEquity": "10000.5",
        "totalAvailableBalance": "8000.25",
        "totalWalletBalance": "9900",
        "totalPerpUPL": "100.5",
    }


def isolated_wallet_zero_totals_item() -> dict[str, Any]:
    return {
        "accountType": "UNIFIED",
        "totalEquity": "0",
        "totalAvailableBalance": "0",
        "totalWalletBalance": "0",
        "totalPerpUPL": "0",
        "coin": [
            {
                "coin": "USDT",
                "equity": "20.5",
                "usdValue": "20.5",
                "walletBalance": "20.5",
                "unrealisedPnl": "0",
                "totalPositionIM": "0",
                "totalOrderIM": "0",
                "locked": "0",
                "bonus": "0",
            }
        ],
    }


def position_item(
    *,
    side: str = "",
    size: str = "0",
    avg_price: str = "",
    position_idx: int = 0,
    seq: int = 10,
    break_even_price: str = "",
) -> dict[str, Any]:
    return {
        "positionIdx": position_idx,
        "symbol": "BTCUSDT",
        "side": side,
        "size": size,
        "avgPrice": avg_price,
        "breakEvenPrice": break_even_price,
        "leverage": "2",
        "markPrice": "50100",
        "liqPrice": "25000" if size != "0" else "",
        "stopLoss": "49000" if size != "0" else "0",
        "takeProfit": "52000" if size != "0" else "0",
        "trailingStop": "0",
        "unrealisedPnl": "12.5" if size != "0" else "0",
        "seq": seq,
        "updatedTime": str(NOW_MS),
    }


def order_item(
    *,
    order_id: str = "order-1",
    client_id: str = "client-1",
    status: str = "New",
    cumulative: str = "0",
    updated_ms: int = NOW_MS,
) -> dict[str, Any]:
    return {
        "orderId": order_id,
        "orderLinkId": client_id,
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Limit",
        "qty": "1",
        "price": "50000",
        "reduceOnly": False,
        "stopOrderType": "",
        "orderStatus": status,
        "cumExecQty": cumulative,
        "avgPrice": "50000" if Decimal(cumulative) > 0 else "",
        "createdTime": str(NOW_MS - 1000),
        "updatedTime": str(updated_ms),
    }


def execution_item(
    *,
    execution_id: str = "exec-1",
    order_id: str = "order-1",
    client_id: str = "client-1",
    side: str = "Buy",
    qty: str = "0.1",
    price: str = "50000",
    executed_ms: int = NOW_MS,
) -> dict[str, Any]:
    return {
        "execId": execution_id,
        "orderId": order_id,
        "orderLinkId": client_id,
        "symbol": "BTCUSDT",
        "side": side,
        "execQty": qty,
        "execPrice": price,
        "execTime": str(executed_ms),
    }


def closed_pnl_item(
    *,
    order_id: str = "closed-order-1",
    side: str = "Buy",
    qty: str = "0.1",
    closed_size: str = "0.1",
    entry: str = "50000",
    exit_price: str = "50100",
    pnl: str = "9.88",
    updated_ms: int = NOW_MS,
) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "orderId": order_id,
        "side": side,
        "qty": qty,
        "closedSize": closed_size,
        "avgEntryPrice": entry,
        "avgExitPrice": exit_price,
        "closedPnl": pnl,
        "openFee": "0.06",
        "closeFee": "0.06",
        "orderType": "Market",
        "leverage": "1",
        "createdTime": str(updated_ms - 2_000),
        "updatedTime": str(updated_ms),
    }


class FixtureTransport:
    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        copied = dict(params)
        self.calls.append((endpoint, copied, authenticated))
        return self.handler(endpoint, copied)


def complete_handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    if endpoint == "/v5/market/instruments-info":
        return response([instrument_item()])
    if endpoint == "/v5/account/wallet-balance":
        return response([wallet_item()])
    if endpoint == "/v5/account/info":
        return {
            "retCode": 0,
            "retMsg": "OK",
            "time": NOW_MS,
            "result": {"marginMode": "REGULAR_MARGIN", "unifiedMarginStatus": 5},
        }
    if endpoint == "/v5/account/fee-rate":
        return response(
            [
                {
                    "symbol": "BTCUSDT",
                    "makerFeeRate": "0.0001",
                    "takerFeeRate": "0.0006",
                }
            ]
        )
    if endpoint == "/v5/position/closed-pnl":
        return response(
            [{"symbol": "BTCUSDT", "closedPnl": "12.5"}],
            nextPageCursor="",
        )
    if endpoint == "/v5/position/list":
        return response([position_item()])
    if endpoint == "/v5/order/realtime":
        return response([], nextPageCursor="")
    if endpoint == "/v5/market/kline":
        start = NOW_MS - 120_000
        return response(
            [
                [str(start + 60_000), "101", "103", "100", "102", "2", "0"],
                [str(start), "100", "102", "99", "101", "1", "0"],
            ],
            symbol="BTCUSDT",
            category="linear",
        )
    raise AssertionError(endpoint)


class BybitRestAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_isolated_zero_account_totals_fall_back_to_nonzero_coin_rows(self) -> None:
        account = map_account(isolated_wallet_zero_totals_item(), NOW)

        self.assertEqual(account.equity, Decimal("20.5"))
        self.assertEqual(account.available_balance, Decimal("20.5"))
        self.assertEqual(account.wallet_balance, Decimal("20.5"))

    async def test_mainnet_historical_bundle_uses_trade_mark_and_funding_gets(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        opens = [int((start + timedelta(hours=index)).timestamp() * 1000) for index in range(3)]

        def handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == "/v5/market/kline":
                return response(
                    [
                        [str(value), "100", "102", "99", "101", "10", "0"]
                        for value in reversed(opens)
                    ]
                )
            if endpoint == "/v5/market/mark-price-kline":
                return response(
                    [
                        [str(value), "100", "101", "99", "100.5"]
                        for value in reversed(opens)
                    ]
                )
            if endpoint == "/v5/market/funding/history":
                if int(params["endTime"]) < opens[2]:
                    return response([])
                return response(
                    [
                        {
                            "symbol": "BTCUSDT",
                            "fundingRate": "0.0001",
                            "fundingRateTimestamp": str(opens[2]),
                        }
                    ]
                )
            raise AssertionError(endpoint)

        transport = FixtureTransport(handler)
        market = await BybitReadOnlyAdapter(transport).historical_market_data_range(
            "BTCUSDT",
            "60",
            start=start,
            end=start + timedelta(hours=3),
        )
        self.assertEqual(len(market.trade.candles), 3)
        self.assertEqual(len(market.mark_candles), 3)
        self.assertEqual(len(market.funding_events), 1)
        self.assertEqual(market.funding_events[0].mark_price, Decimal("100.5"))
        self.assertTrue(market.quality.production_equivalent)
        self.assertTrue(all(not authenticated for _, _, authenticated in transport.calls))

    async def test_complete_read_snapshot_maps_decimals_and_auth_boundaries(self) -> None:
        transport = FixtureTransport(complete_handler)
        adapter = BybitReadOnlyAdapter(transport)
        snapshot = await adapter.read_snapshot("BTCUSDT")
        self.assertEqual(snapshot.instrument.tick_size, Decimal("0.10"))
        self.assertEqual(snapshot.instrument.max_market_order_qty, Decimal("25"))
        self.assertEqual(snapshot.account.equity, Decimal("10000.5"))
        self.assertEqual(snapshot.account.margin_mode, "REGULAR_MARGIN")
        self.assertEqual(snapshot.account.unified_margin_status, 5)
        self.assertEqual(snapshot.account.maker_fee_rate, Decimal("0.0001"))
        self.assertEqual(snapshot.account.daily_realized_pnl, Decimal("12.5"))
        self.assertEqual(snapshot.position.position.side, PositionSide.FLAT)
        public_calls = {endpoint: authenticated for endpoint, _, authenticated in transport.calls}
        self.assertFalse(public_calls["/v5/market/instruments-info"])
        self.assertTrue(public_calls["/v5/account/wallet-balance"])
        self.assertTrue(public_calls["/v5/position/list"])
        self.assertTrue(public_calls["/v5/order/realtime"])

    async def test_server_time_prefers_millisecond_response_time_over_whole_seconds(self) -> None:
        response_ms = NOW_MS + 789

        def handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(endpoint, "/v5/market/time")
            self.assertEqual(params, {})
            return {
                "retCode": 0,
                "retMsg": "OK",
                "time": response_ms,
                "result": {"timeSecond": str(response_ms // 1000)},
            }

        server = await BybitReadOnlyAdapter(FixtureTransport(handler)).server_time()

        self.assertEqual(server, datetime.fromtimestamp(response_ms / 1000, tz=UTC))

    async def test_recent_closed_pnl_maps_trade_ledger_newest_first(self) -> None:
        def handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(endpoint, "/v5/position/closed-pnl")
            self.assertEqual(params["category"], "linear")
            self.assertEqual(params["symbol"], "BTCUSDT")
            self.assertEqual(params["limit"], 50)
            return response(
                [
                    closed_pnl_item(order_id="older", updated_ms=NOW_MS - 1_000),
                    closed_pnl_item(
                        order_id="newer",
                        side="Sell",
                        qty="0.2",
                        closed_size="0.2",
                        entry="50100",
                        exit_price="50000",
                        pnl="19.76",
                        updated_ms=NOW_MS,
                    ),
                ]
            )

        transport = FixtureTransport(handler)
        records = await BybitReadOnlyAdapter(transport).recent_closed_pnl("btcusdt")

        self.assertEqual([item.order_id for item in records], ["newer", "older"])
        self.assertEqual(records[0].side, "Sell")
        self.assertEqual(records[0].closed_pnl, Decimal("19.76"))
        self.assertEqual(records[0].open_fee, Decimal("0.06"))
        self.assertEqual(records[0].close_fee, Decimal("0.06"))
        self.assertTrue(transport.calls[0][2])

    async def test_recent_executions_backfills_and_sorts_newest_first(self) -> None:
        def handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(endpoint, "/v5/execution/list")
            self.assertEqual(params["category"], "linear")
            self.assertEqual(params["symbol"], "BTCUSDT")
            self.assertEqual(params["execType"], "Trade")
            self.assertEqual(params["limit"], 50)
            return response(
                [
                    execution_item(execution_id="old", executed_ms=NOW_MS - 1_000),
                    execution_item(
                        execution_id="new",
                        order_id="order-2",
                        client_id="client-2",
                        side="Sell",
                        qty="0.2",
                        price="50100",
                        executed_ms=NOW_MS,
                    ),
                ]
            )

        transport = FixtureTransport(handler)
        executions = await BybitReadOnlyAdapter(transport).recent_executions("btcusdt")

        self.assertEqual([item.execution_id for item in executions], ["new", "old"])
        self.assertEqual(executions[0].side, OrderSide.SELL)
        self.assertEqual(executions[0].quantity, Decimal("0.2"))
        self.assertEqual(executions[0].price, Decimal("50100"))
        self.assertTrue(transport.calls[0][2])

    async def test_order_lookup_can_fall_back_to_pybit_order_history(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def get_open_orders(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(("get_open_orders", dict(kwargs)))
                return response([], nextPageCursor="")

            def get_order_history(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(("get_order_history", dict(kwargs)))
                return response(
                    [
                        order_item(
                            order_id="history-order",
                            client_id="client-history",
                            status="Filled",
                            cumulative="1",
                        )
                    ],
                    nextPageCursor="",
                )

        session = Session()
        adapter = BybitReadOnlyAdapter(PybitReadOnlyTransport(session))
        order = await adapter.order_by_client_id("BTCUSDT", "client-history")

        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order.order_id, "history-order")
        self.assertEqual(order.request.client_order_id, "client-history")
        self.assertEqual(
            [name for name, _ in session.calls],
            ["get_open_orders", "get_order_history"],
        )
        self.assertEqual(session.calls[1][1]["orderLinkId"], "client-history")

    async def test_open_orders_follow_cursor_pagination(self) -> None:
        def handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(endpoint, "/v5/order/realtime")
            if "cursor" not in params:
                return response(
                    [order_item(order_id="one", client_id="client-one")],
                    nextPageCursor="next",
                )
            return response([order_item(order_id="two", client_id="client-two")], nextPageCursor="")

        adapter = BybitReadOnlyAdapter(FixtureTransport(handler))
        orders = await adapter.open_orders("BTCUSDT")
        self.assertEqual([item.order_id for item in orders], ["one", "two"])

    async def test_rest_klines_are_reordered_oldest_first(self) -> None:
        adapter = BybitReadOnlyAdapter(FixtureTransport(complete_handler))
        candles = await adapter.historical_candles("BTCUSDT", "1", observed_at=NOW)
        self.assertLess(candles[0].opened_at, candles[1].opened_at)
        self.assertTrue(all(item.is_closed for item in candles))

    async def test_nonzero_ret_code_is_a_protocol_error(self) -> None:
        transport = FixtureTransport(
            lambda endpoint, params: {"retCode": 10001, "retMsg": "bad", "result": {}}
        )
        with self.assertRaises(BybitProtocolError):
            await BybitReadOnlyAdapter(transport).instrument_rules("BTCUSDT")

    async def test_historical_range_paginates_backwards_and_deduplicates(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        rows = [
            [
                str(int((start + timedelta(minutes=index)).timestamp() * 1000)),
                str(100 + index),
                str(102 + index),
                str(99 + index),
                str(101 + index),
                "1",
                "0",
            ]
            for index in range(5)
        ]

        def handler(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(endpoint, "/v5/market/kline")
            selected = [
                row for row in rows if int(params["start"]) <= int(row[0]) <= int(params["end"])
            ]
            selected.sort(key=lambda row: int(row[0]), reverse=True)
            return response(selected[: int(params["limit"])])

        transport = FixtureTransport(handler)
        candles = await BybitReadOnlyAdapter(transport).historical_candles_range(
            "BTCUSDT",
            "1",
            start=start,
            end=start + timedelta(minutes=5),
            page_limit=2,
        )
        self.assertEqual(len(candles), 5)
        self.assertEqual(candles[0].opened_at, start)
        self.assertEqual(candles[-1].opened_at, start + timedelta(minutes=4))
        self.assertEqual(len(transport.calls), 3)

    async def test_error_codes_are_actionably_classified(self) -> None:
        transport = FixtureTransport(
            lambda endpoint, params: {
                "retCode": 10006,
                "retMsg": "Too many visits!",
                "result": {},
            }
        )
        with self.assertRaises(BybitApiError) as caught:
            await BybitReadOnlyAdapter(transport).instrument_rules("BTCUSDT")
        self.assertEqual(caught.exception.category, BybitErrorCategory.RATE_LIMIT)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(classify_bybit_error(110007), BybitErrorCategory.INSUFFICIENT_MARGIN)
        self.assertEqual(classify_bybit_error(10002), BybitErrorCategory.CLOCK_SKEW)

    def test_hedge_mode_position_is_rejected(self) -> None:
        with self.assertRaises(BybitModeMismatch):
            map_position(position_item(position_idx=1), NOW)

    def test_exchange_break_even_price_is_preserved(self) -> None:
        snapshot = map_position(
            position_item(
                side="Buy",
                size="1",
                avg_price="50000",
                break_even_price="50047.5",
            ),
            NOW,
        )
        self.assertEqual(snapshot.break_even_price, Decimal("50047.5"))


class BybitStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.health = HealthMonitor()
        self.processor = BybitStreamProcessor("BTCUSDT", self.health)

    def test_only_confirmed_kline_is_emitted_once(self) -> None:
        message = {
            "topic": "kline.1.BTCUSDT",
            "ts": NOW_MS,
            "data": [
                {
                    "start": NOW_MS - 60_000,
                    "end": NOW_MS - 1,
                    "interval": "1",
                    "open": "100",
                    "high": "102",
                    "low": "99",
                    "close": "101",
                    "volume": "1",
                    "confirm": False,
                }
            ],
        }
        self.assertIsNone(self.processor.on_public(message, received_at=NOW))
        message["data"][0]["confirm"] = True
        self.assertIsNotNone(self.processor.on_public(message, received_at=NOW))
        self.assertIsNone(self.processor.on_public(message, received_at=NOW))

    def test_batched_kline_payload_is_processed_without_transport_failure(self) -> None:
        first_start = NOW_MS - 120_000
        second_start = NOW_MS - 60_000
        message = {
            "topic": "kline.1.BTCUSDT",
            "ts": NOW_MS,
            "data": [
                {
                    "start": first_start,
                    "end": second_start - 1,
                    "interval": "1",
                    "open": "100",
                    "high": "102",
                    "low": "99",
                    "close": "101",
                    "volume": "1",
                    "confirm": True,
                },
                {
                    "start": second_start,
                    "end": NOW_MS - 1,
                    "interval": "1",
                    "open": "101",
                    "high": "103",
                    "low": "100",
                    "close": "102",
                    "volume": "2",
                    "confirm": False,
                },
            ],
        }
        emitted = self.processor.on_public(message, received_at=NOW)
        self.assertIsNotNone(emitted)
        assert emitted is not None
        self.assertEqual(emitted.opened_at, timestamp_ms(first_start))
        latest = self.processor.snapshot().latest_candle
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.opened_at, timestamp_ms(second_start))
        self.assertFalse(latest.is_closed)

    def test_ticker_delta_merges_with_snapshot(self) -> None:
        snapshot = {
            "topic": "tickers.BTCUSDT",
            "type": "snapshot",
            "ts": NOW_MS,
            "data": {
                "symbol": "BTCUSDT",
                "lastPrice": "50000",
                "markPrice": "50001",
                "indexPrice": "49999",
                "fundingRate": "0.0001",
                "nextFundingTime": str(NOW_MS + 3_600_000),
            },
        }
        self.processor.on_public(snapshot, received_at=NOW)
        delta = {
            "topic": "tickers.BTCUSDT",
            "type": "delta",
            "ts": NOW_MS + 1000,
            "data": {"markPrice": "50002"},
        }
        ticker = self.processor.on_public(delta, received_at=NOW + timedelta(seconds=1))
        self.assertEqual(ticker.last_price, Decimal("50000"))
        self.assertEqual(ticker.mark_price, Decimal("50002"))

    def test_private_order_and_execution_events_are_deduplicated(self) -> None:
        order_message = {
            "id": "message-1",
            "topic": "order",
            "creationTime": NOW_MS,
            "data": [order_item()],
        }
        self.assertEqual(len(self.processor.on_private(order_message, received_at=NOW)), 1)
        self.assertEqual(self.processor.on_private(order_message, received_at=NOW), ())
        execution_message = {
            "id": "exec-message",
            "topic": "execution",
            "creationTime": NOW_MS,
            "data": [
                {
                    "execId": "exec-1",
                    "orderId": "order-1",
                    "orderLinkId": "client-1",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "execQty": "0.5",
                    "execPrice": "50000",
                    "execTime": str(NOW_MS),
                },
                {
                    "execId": "exec-2",
                    "orderId": "order-1",
                    "orderLinkId": "client-1",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "execQty": "0.5",
                    "execPrice": "50001",
                    "execTime": str(NOW_MS + 1),
                },
            ],
        }
        self.assertEqual(len(self.processor.on_private(execution_message, received_at=NOW)), 2)
        self.assertEqual(self.processor.on_private(execution_message, received_at=NOW), ())

    def test_private_position_duplicate_is_ignored(self) -> None:
        message = {
            "id": "position-message",
            "topic": "position.linear",
            "creationTime": NOW_MS,
            "data": [position_item(side="Buy", size="1", avg_price="50000")],
        }
        self.assertEqual(len(self.processor.on_private(message, received_at=NOW)), 1)
        self.assertEqual(self.processor.on_private(message, received_at=NOW), ())

    def test_private_wallet_zero_totals_do_not_erase_nonzero_coin_balance(self) -> None:
        message = {
            "topic": "wallet",
            "creationTime": NOW_MS,
            "data": [isolated_wallet_zero_totals_item()],
        }

        changed = self.processor.on_private(message, received_at=NOW)

        self.assertEqual(len(changed), 1)
        account = self.processor.snapshot().account
        self.assertIsNotNone(account)
        assert account is not None
        self.assertEqual(account.equity, Decimal("20.5"))
        self.assertEqual(account.available_balance, Decimal("20.5"))

    def test_malformed_message_makes_channel_unhealthy(self) -> None:
        self.health.mark_message("public", NOW)
        with self.assertRaises(BybitProtocolError):
            self.processor.on_public(
                {"topic": "unsupported.topic", "data": {}},
                received_at=NOW + timedelta(seconds=1),
            )
        state = self.health.snapshot(NOW + timedelta(seconds=1)).public
        self.assertFalse(state.connected)
        self.assertFalse(state.fresh)


class HealthAndReconnectTests(unittest.TestCase):
    def test_health_requires_all_three_fresh_channels(self) -> None:
        monitor = HealthMonitor(
            max_public_age_seconds=5,
            max_private_age_seconds=5,
            max_rest_age_seconds=5,
        )
        for channel in ("public", "private", "rest"):
            monitor.mark_message(channel, NOW)
        self.assertTrue(monitor.snapshot(NOW + timedelta(seconds=5)).can_create_entry)
        self.assertFalse(monitor.snapshot(NOW + timedelta(seconds=6)).can_create_entry)
        self.assertFalse(monitor.snapshot(NOW - timedelta(seconds=1)).can_create_entry)

    def test_reconnect_backoff_is_deterministic_capped_and_resettable(self) -> None:
        first = ReconnectBackoff(base_seconds=1, maximum_seconds=4, seed=7)
        second = ReconnectBackoff(base_seconds=1, maximum_seconds=4, seed=7)
        delays = [first.next_delay() for _ in range(6)]
        self.assertEqual(delays, [second.next_delay() for _ in range(6)])
        self.assertTrue(all(0 <= delay <= 4 for delay in delays))
        first.reset()
        second.reset()
        self.assertEqual(first.next_delay(), second.next_delay())

    def test_recovery_restores_subscriptions_and_requires_private_reconciliation(self) -> None:
        health = HealthMonitor()
        recovery = StreamRecoveryCoordinator("BTCUSDT", "5", health, seed=3)
        directive = recovery.disconnected("private", "socket closed")
        self.assertEqual(
            directive.subscriptions,
            ("order", "execution", "position", "wallet"),
        )
        self.assertTrue(directive.require_rest_reconciliation)
        restored = recovery.connected("private")
        self.assertEqual(restored, directive.subscriptions)
        self.assertTrue(recovery.heartbeat_due("private", NOW))
        payload = recovery.heartbeat_payload("private", NOW)
        self.assertEqual(payload["op"], "ping")
        self.assertFalse(recovery.heartbeat_due("private", NOW + timedelta(seconds=19)))
        self.assertTrue(recovery.heartbeat_due("private", NOW + timedelta(seconds=20)))


class FakeWsSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.exited = False
        self.connected = True

    def __getattr__(self, name: str) -> Any:
        def record(**kwargs: Any) -> None:
            self.calls.append((name, kwargs))

        return record

    def exit(self) -> None:
        self.exited = True

    def is_connected(self) -> bool:
        return self.connected and not self.exited


class PybitWebSocketBridgeTests(unittest.TestCase):
    def test_all_read_only_topics_are_registered(self) -> None:
        public = FakeWsSession()
        private = FakeWsSession()
        health = HealthMonitor()
        processor = BybitStreamProcessor("BTCUSDT", health)
        bridge = PybitWebSocketBridge(public, private, processor, health)
        bridge.subscribe("BTCUSDT", "5")
        self.assertEqual(
            [name for name, _ in public.calls],
            ["kline_stream", "ticker_stream"],
        )
        self.assertEqual(
            [name for name, _ in private.calls],
            ["order_stream", "execution_stream", "position_stream", "wallet_stream"],
        )
        bridge.close()
        self.assertTrue(public.exited)
        self.assertTrue(private.exited)

    def test_transport_status_keeps_idle_private_channel_fresh(self) -> None:
        public = FakeWsSession()
        private = FakeWsSession()
        health = HealthMonitor(max_private_age_seconds=1)
        processor = BybitStreamProcessor("BTCUSDT", health)
        bridge = PybitWebSocketBridge(public, private, processor, health)
        bridge.subscribe("BTCUSDT", "5")

        public_ok, private_ok = bridge.transport_status(NOW)

        self.assertTrue(public_ok)
        self.assertTrue(private_ok)
        self.assertTrue(health.snapshot(NOW).private.fresh)
        private.connected = False
        _public_ok, private_ok = bridge.transport_status(NOW + timedelta(seconds=1))
        self.assertFalse(private_ok)
        self.assertFalse(health.snapshot(NOW + timedelta(seconds=1)).private.connected)


class StubSnapshotAdapter:
    def __init__(self, snapshot: BybitReadSnapshot | Exception) -> None:
        self.snapshot = snapshot

    async def read_snapshot(self, symbol: str) -> BybitReadSnapshot:
        del symbol
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


class ReadOnlySynchronizerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = TradingJournal(Path(self.temp.name) / "sync.db")

    async def asyncTearDown(self) -> None:
        self.journal.close()
        self.temp.cleanup()

    async def test_exchange_truth_replaces_stale_local_projection_then_verifies(self) -> None:
        stale_order = Order(
            "stale-local",
            OrderRequest(
                "stale-client",
                "BTCUSDT",
                OrderSide.BUY,
                OrderType.LIMIT,
                Decimal("1"),
                Decimal("49000"),
            ),
            status=OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        self.journal.upsert_order(stale_order, event_id="local-event")
        self.journal.record_position_snapshot(
            Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
            source="local",
            observed_at=NOW,
        )
        remote_order_raw = order_item(order_id="remote-order", client_id="remote-client")
        transport = FixtureTransport(
            lambda endpoint, params: (
                response([instrument_item()])
                if endpoint == "/v5/market/instruments-info"
                else response([wallet_item()])
                if endpoint == "/v5/account/wallet-balance"
                else response([position_item(side="Buy", size="1", avg_price="50000")])
                if endpoint == "/v5/position/list"
                else response([remote_order_raw], nextPageCursor="")
            )
        )
        adapter = BybitReadOnlyAdapter(transport)
        machine = AppStateMachine()
        health = HealthMonitor()
        outcome = await ReadOnlySynchronizer(adapter, self.journal, machine, health).synchronize(
            "BTCUSDT", "sync-1", occurred_at=NOW
        )
        self.assertFalse(outcome.initial_reconciliation.synchronized)
        self.assertTrue(outcome.verification.synchronized)
        self.assertEqual(machine.state, AppState.READY)
        projection = self.journal.load_projection("BTCUSDT")
        self.assertEqual(projection.position.side, PositionSide.LONG)
        self.assertEqual([item.order_id for item in projection.active_orders], ["remote-order"])
        self.assertTrue(health.snapshot(NOW).rest.fresh)
        self.assertEqual(self.journal.table_count("reconciliation_runs"), 2)

    async def test_periodic_sync_keeps_armed_state_when_reconciliation_matches(self) -> None:
        adapter = BybitReadOnlyAdapter(FixtureTransport(complete_handler))
        machine = AppStateMachine()
        machine.transition(AppState.SYNCING, "fixture startup")
        machine.transition(AppState.READY, "fixture ready")
        machine.transition(AppState.ARMED, "fixture armed")
        health = HealthMonitor()

        outcome = await ReadOnlySynchronizer(
            adapter, self.journal, machine, health
        ).synchronize(
            "BTCUSDT",
            "periodic-sync",
            occurred_at=NOW,
            update_state=False,
        )

        self.assertTrue(outcome.verification.synchronized)
        self.assertEqual(machine.state, AppState.ARMED)

    async def test_sync_failure_moves_to_degraded(self) -> None:
        machine = AppStateMachine()
        health = HealthMonitor()
        synchronizer = ReadOnlySynchronizer(
            StubSnapshotAdapter(BybitProtocolError("fixture failure")),  # type: ignore[arg-type]
            self.journal,
            machine,
            health,
        )
        with self.assertRaises(BybitProtocolError):
            await synchronizer.synchronize("BTCUSDT", "sync-fail", occurred_at=NOW)
        self.assertEqual(machine.state, AppState.DEGRADED)
        self.assertFalse(health.snapshot(NOW).rest.connected)

    def test_transient_rest_error_preserves_fresh_last_snapshot(self) -> None:
        monitor = HealthMonitor(max_rest_age_seconds=60)
        observed = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        monitor.mark_message("rest", observed)
        monitor.mark_transient_error("rest", "temporary read timeout")
        snapshot = monitor.snapshot(observed + timedelta(seconds=5)).rest
        self.assertTrue(snapshot.connected)
        self.assertTrue(snapshot.fresh)
        self.assertEqual(snapshot.last_error, "temporary read timeout")


if __name__ == "__main__":
    unittest.main()
