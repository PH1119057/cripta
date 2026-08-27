from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle, Execution, InstrumentRules, Order
from bybit_workbench.domain.types import OrderSide
from bybit_workbench.historical.market_data import FundingEvent, HistoricalMarketData
from bybit_workbench.historical.validation import HistoricalDataset

from .errors import BybitApiError, BybitProtocolError
from .mappers import (
    _optional_decimal,
    map_account,
    map_instrument,
    map_order,
    map_position,
    map_rest_klines,
)
from .models import (
    AccountSnapshot,
    ApiKeyInfo,
    ApiKeyPermissionAudit,
    BybitPositionSnapshot,
    BybitReadSnapshot,
    ClosedPnlRecord,
    MainnetAccountWideSnapshot,
)
from .transport import BybitReadTransport


class BybitReadOnlyAdapter:
    def __init__(
        self,
        transport: BybitReadTransport,
        *,
        category: str = "linear",
        account_type: str = "UNIFIED",
    ) -> None:
        if category != "linear":
            raise ValueError("the first read-only adapter slice supports category=linear")
        self._transport = transport
        self.category = category
        self.account_type = account_type

    async def server_time(self) -> datetime:
        response = await self._get("/v5/market/time", {}, authenticated=False)
        result = _result(response)
        nanoseconds = result.get("timeNano")
        if nanoseconds not in (None, "") and int(str(nanoseconds)) > 0:
            return datetime.fromtimestamp(int(str(nanoseconds)) / 1_000_000_000, tz=UTC)
        response_ms = response.get("time")
        if response_ms not in (None, ""):
            return datetime.fromtimestamp(int(str(response_ms)) / 1_000, tz=UTC)
        seconds = result.get("timeSecond")
        if seconds in (None, ""):
            return _response_time(response)
        return datetime.fromtimestamp(int(str(seconds)), tz=UTC)

    async def api_key_info(self) -> ApiKeyInfo:
        response = await self._get("/v5/user/query-api", {}, authenticated=True)
        result = _result(response)
        ip_bindings = tuple(str(value) for value in _array(result.get("ips"), "ips"))
        deadline_day = _optional_int(result.get("deadlineDay"))
        if deadline_day is not None and deadline_day < 0:
            deadline_day = None
        expired_at = _optional_datetime(result.get("expiredAt"))
        if (
            expired_at is not None
            and deadline_day is None
            and expired_at <= datetime(1971, 1, 1, tzinfo=UTC)
        ):
            # Bybit uses Unix epoch as a sentinel on API keys where expiry is not active.
            expired_at = None
        raw_parent_uid = str(result.get("parentUid") or "").strip()
        parent_uid = None if raw_parent_uid in {"", "0"} else raw_parent_uid
        return ApiKeyInfo(
            note=str(result.get("note") or ""),
            read_only=int(str(result.get("readOnly", 1))) == 1,
            ip_bindings=ip_bindings,
            deadline_day=deadline_day,
            expired_at=expired_at,
            created_at=_optional_datetime(result.get("createdAt")),
            is_master=bool(result.get("isMaster", False)),
            parent_uid=parent_uid,
            unified_account=int(str(result.get("uta", 0))) == 1,
            key_type=_optional_int(result.get("type")),
            permissions=_permission_audit(result),
            key_id=str(result.get("id") or "").strip() or None,
        )

    async def instrument_rules(self, symbol: str) -> InstrumentRules:
        response = await self._get(
            "/v5/market/instruments-info",
            {"category": self.category, "symbol": symbol},
            authenticated=False,
        )
        items = _result_list(response)
        matches = [item for item in items if str(item.get("symbol")) == symbol]
        if len(matches) != 1:
            raise BybitProtocolError(
                f"expected one instrument for {symbol}, received {len(matches)}"
            )
        return map_instrument(matches[0])

    async def wallet_snapshot(self) -> AccountSnapshot:
        response = await self._get(
            "/v5/account/wallet-balance",
            {"accountType": self.account_type},
            authenticated=True,
        )
        items = _result_list(response)
        if len(items) != 1:
            raise BybitProtocolError(f"expected one wallet account, received {len(items)}")
        return map_account(items[0], _response_time(response))

    async def account_snapshot(self, symbol: str) -> AccountSnapshot:
        wallet = await self.wallet_snapshot()
        account_response = await self._get("/v5/account/info", {}, authenticated=True)
        account_info = _result(account_response)
        fee_response = await self._get(
            "/v5/account/fee-rate",
            {"category": self.category, "symbol": symbol},
            authenticated=True,
        )
        fee_items = _result_list(fee_response)
        if len(fee_items) != 1:
            raise BybitProtocolError(
                f"expected one fee-rate row for {symbol}, received {len(fee_items)}"
            )
        fee = fee_items[0]
        status = account_info.get("unifiedMarginStatus")
        daily_realized_pnl = await self.daily_realized_pnl(symbol)
        return AccountSnapshot(
            account_type=wallet.account_type,
            equity=wallet.equity,
            available_balance=wallet.available_balance,
            wallet_balance=wallet.wallet_balance,
            unrealized_pnl=wallet.unrealized_pnl,
            observed_at=max(
                wallet.observed_at,
                _response_time(account_response),
                _response_time(fee_response),
            ),
            margin_mode=str(account_info.get("marginMode") or "") or None,
            unified_margin_status=None if status in (None, "") else int(str(status)),
            maker_fee_rate=_optional_decimal(fee.get("makerFeeRate")),
            taker_fee_rate=_optional_decimal(fee.get("takerFeeRate")),
            daily_realized_pnl=daily_realized_pnl,
        )

    async def daily_realized_pnl(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> Decimal:
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        utc_now = observed_at.astimezone(UTC)
        start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
        params: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "startTime": _timestamp_ms(start),
            "endTime": _timestamp_ms(utc_now),
            "limit": 100,
        }
        total = Decimal("0")
        seen_cursors: set[str] = set()
        while True:
            response = await self._get(
                "/v5/position/closed-pnl",
                params,
                authenticated=True,
            )
            total += sum(
                (
                    _optional_decimal(item.get("closedPnl")) or Decimal("0")
                    for item in _result_list(response)
                ),
                Decimal("0"),
            )
            cursor = str(_result(response).get("nextPageCursor") or "")
            if not cursor:
                return total
            if cursor in seen_cursors:
                raise BybitProtocolError("closed PnL pagination cursor repeated")
            seen_cursors.add(cursor)
            params["cursor"] = cursor

    async def position_rows(
        self,
        symbol: str,
        *,
        require_one_way: bool = True,
    ) -> tuple[BybitPositionSnapshot, ...]:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        response = await self._get(
            "/v5/position/list",
            {"category": self.category, "symbol": selected},
            authenticated=True,
        )
        items = _result_list(response)
        matches = [item for item in items if str(item.get("symbol")) == selected]
        if not matches:
            raise BybitProtocolError(f"expected position rows for {selected}, received none")
        observed_at = _response_time(response)
        return tuple(
            map_position(item, observed_at, require_one_way=require_one_way)
            for item in matches
        )

    async def position_snapshot(self, symbol: str) -> BybitPositionSnapshot:
        rows = await self.position_rows(symbol, require_one_way=True)
        if len(rows) != 1:
            raise BybitProtocolError(
                f"expected one one-way position row for {symbol}, received {len(rows)}"
            )
        return rows[0]

    async def contract_positions(
        self,
        *,
        require_one_way: bool = True,
    ) -> tuple[BybitPositionSnapshot, ...]:
        settle_coins = await self._linear_settle_coins()
        rows: list[BybitPositionSnapshot] = []
        for settle_coin in settle_coins:
            rows.extend(
                await self._position_rows(
                    "linear",
                    {"settleCoin": settle_coin},
                    require_one_way=require_one_way,
                )
            )
        rows.extend(
            await self._position_rows(
                "inverse",
                {},
                require_one_way=require_one_way,
            )
        )
        return tuple(rows)

    async def contract_open_orders(self) -> tuple[Order, ...]:
        settle_coins = await self._linear_settle_coins()
        orders: dict[str, Order] = {}
        for settle_coin in settle_coins:
            for item in await self._open_order_rows("linear", {"settleCoin": settle_coin}):
                orders[item.order_id] = item
        for item in await self._open_order_rows("inverse", {}):
            orders[item.order_id] = item
        return tuple(sorted(orders.values(), key=lambda item: item.order_id))

    async def open_orders(self, symbol: str) -> list[Order]:
        orders: list[Order] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "category": self.category,
                "symbol": symbol,
                "openOnly": 0,
                "limit": 50,
            }
            if cursor:
                params["cursor"] = cursor
            response = await self._get(
                "/v5/order/realtime",
                params,
                authenticated=True,
            )
            orders.extend(map_order(item) for item in _result_list(response))
            result = _result(response)
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise BybitProtocolError("open-order cursor did not advance")
            cursor = next_cursor
        return orders

    async def mainnet_account_wide_snapshot(
        self,
        symbol: str,
    ) -> MainnetAccountWideSnapshot:
        """Read every contract surface that this key can mutate.

        Linear positions/orders are enumerated for every settle coin currently
        advertised by the public instrument catalogue; inverse contracts are
        enumerated through their documented category-wide query.  Spot and
        Options are intentionally not queried because Micro-Live requires those
        API permissions to be absent.
        """

        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        instrument = await self.instrument_rules(selected)
        account = await self.account_snapshot(selected)
        position = await self.position_snapshot(selected)
        positions = {
            (item.position.symbol, item.position_idx): item
            for item in await self.contract_positions(require_one_way=True)
        }
        orders = {item.order_id: item for item in await self.contract_open_orders()}
        other_positions = tuple(
            sorted(
                (
                    item
                    for item in positions.values()
                    if (
                        item.position.symbol != selected
                        or item.position_idx != position.position_idx
                    )
                ),
                key=lambda item: (item.position.symbol, item.position_idx),
            )
        )
        return MainnetAccountWideSnapshot(
            instrument=instrument,
            account=account,
            position=position,
            other_positions=other_positions,
            open_orders=tuple(sorted(orders.values(), key=lambda item: item.order_id)),
            observed_at=datetime.now(UTC),
        )

    async def _linear_settle_coins(self) -> tuple[str, ...]:
        coins: set[str] = set()
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = await self._get(
                "/v5/market/instruments-info",
                params,
                authenticated=False,
            )
            for item in _result_list(response):
                settle_coin = str(item.get("settleCoin") or "").strip().upper()
                if settle_coin:
                    coins.add(settle_coin)
            next_cursor = str(_result(response).get("nextPageCursor") or "")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise BybitProtocolError("instrument pagination cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if not coins:
            raise BybitProtocolError("linear instrument catalogue has no settle coins")
        return tuple(sorted(coins))

    async def _position_rows(
        self,
        category: str,
        filters: Mapping[str, Any],
        *,
        require_one_way: bool = True,
    ) -> tuple[BybitPositionSnapshot, ...]:
        rows: list[BybitPositionSnapshot] = []
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {"category": category, "limit": 200, **filters}
            if cursor:
                params["cursor"] = cursor
            response = await self._get(
                "/v5/position/list",
                params,
                authenticated=True,
            )
            observed_at = _response_time(response)
            rows.extend(
                map_position(item, observed_at, require_one_way=require_one_way)
                for item in _result_list(response)
            )
            next_cursor = str(_result(response).get("nextPageCursor") or "")
            if not next_cursor:
                return tuple(rows)
            if next_cursor in seen_cursors:
                raise BybitProtocolError("account-wide position cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def _open_order_rows(
        self,
        category: str,
        filters: Mapping[str, Any],
    ) -> tuple[Order, ...]:
        rows: list[Order] = []
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {
                "category": category,
                "openOnly": 0,
                "limit": 50,
                **filters,
            }
            if cursor:
                params["cursor"] = cursor
            response = await self._get(
                "/v5/order/realtime",
                params,
                authenticated=True,
            )
            rows.extend(map_order(item) for item in _result_list(response))
            next_cursor = str(_result(response).get("nextPageCursor") or "")
            if not next_cursor:
                return tuple(rows)
            if next_cursor in seen_cursors:
                raise BybitProtocolError("account-wide open-order cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Order | None:
        if not client_order_id:
            raise ValueError("client_order_id is required")
        response = await self._get(
            "/v5/order/realtime",
            {
                "category": self.category,
                "symbol": symbol,
                "orderLinkId": client_order_id,
                "limit": 1,
            },
            authenticated=True,
        )
        items = _result_list(response)
        if not items:
            history = await self._get(
                "/v5/order/history",
                {
                    "category": self.category,
                    "symbol": symbol,
                    "orderLinkId": client_order_id,
                    "limit": 1,
                },
                authenticated=True,
            )
            items = _result_list(history)
        if not items:
            return None
        if len(items) != 1:
            raise BybitProtocolError(
                f"expected at most one order for {client_order_id}, received {len(items)}"
            )
        order = map_order(items[0])
        if order.request.client_order_id != client_order_id:
            raise BybitProtocolError("Bybit returned a mismatched client order id")
        return order

    async def recent_executions(
        self,
        symbol: str,
        *,
        limit: int = 50,
    ) -> tuple[Execution, ...]:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        if not 1 <= limit <= 100:
            raise ValueError("execution history limit must be between 1 and 100")
        response = await self._get(
            "/v5/execution/list",
            {
                "category": self.category,
                "symbol": selected,
                "execType": "Trade",
                "limit": limit,
            },
            authenticated=True,
        )
        executions: dict[str, Execution] = {}
        for raw in _result_list(response):
            if str(raw.get("symbol") or "").upper() != selected:
                continue
            execution_id = str(raw.get("execId") or "").strip()
            order_id = str(raw.get("orderId") or "").strip()
            if not execution_id or not order_id:
                raise BybitProtocolError("execution history row is missing execId/orderId")
            execution = Execution(
                execution_id=execution_id,
                order_id=order_id,
                client_order_id=str(raw.get("orderLinkId") or ""),
                symbol=selected,
                side=OrderSide(str(raw.get("side"))),
                quantity=_required_positive_decimal(raw.get("execQty"), "execQty"),
                price=_required_positive_decimal(raw.get("execPrice"), "execPrice"),
                executed_at=_timestamp_datetime(raw.get("execTime"), "execTime"),
            )
            previous = executions.get(execution_id)
            if previous is not None and previous != execution:
                raise BybitProtocolError("execution history reused execId with different data")
            executions[execution_id] = execution
        return tuple(
            sorted(
                executions.values(),
                key=lambda item: (item.executed_at, item.execution_id, item.order_id),
                reverse=True,
            )
        )

    async def recent_closed_pnl(
        self,
        symbol: str,
        *,
        limit: int = 50,
    ) -> tuple[ClosedPnlRecord, ...]:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        if not 1 <= limit <= 100:
            raise ValueError("closed PnL history limit must be between 1 and 100")
        response = await self._get(
            "/v5/position/closed-pnl",
            {
                "category": self.category,
                "symbol": selected,
                "limit": limit,
            },
            authenticated=True,
        )
        records: dict[str, ClosedPnlRecord] = {}
        for raw in _result_list(response):
            if str(raw.get("symbol") or "").upper() != selected:
                continue
            order_id = str(raw.get("orderId") or "").strip()
            if not order_id:
                raise BybitProtocolError("closed PnL row is missing orderId")
            record = ClosedPnlRecord(
                symbol=selected,
                order_id=order_id,
                side=str(raw.get("side") or ""),
                quantity=_required_positive_decimal(raw.get("qty"), "qty"),
                closed_size=_required_positive_decimal(raw.get("closedSize"), "closedSize"),
                average_entry_price=_required_positive_decimal(
                    raw.get("avgEntryPrice"), "avgEntryPrice"
                ),
                average_exit_price=_required_positive_decimal(
                    raw.get("avgExitPrice"), "avgExitPrice"
                ),
                closed_pnl=_required_decimal_value(raw.get("closedPnl"), "closedPnl"),
                open_fee=_optional_decimal(raw.get("openFee")),
                close_fee=_optional_decimal(raw.get("closeFee")),
                order_type=str(raw.get("orderType") or ""),
                leverage=_optional_decimal(raw.get("leverage")),
                created_at=_timestamp_datetime(raw.get("createdTime"), "createdTime"),
                updated_at=_timestamp_datetime(raw.get("updatedTime"), "updatedTime"),
            )
            previous = records.get(order_id)
            if previous is not None and previous != record:
                raise BybitProtocolError("closed PnL history reused orderId with different data")
            records[order_id] = record
        return tuple(
            sorted(
                records.values(),
                key=lambda item: (item.updated_at, item.order_id),
                reverse=True,
            )
        )

    async def historical_candles(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 200,
        observed_at: datetime | None = None,
    ) -> list[Candle]:
        if limit < 1 or limit > 1000:
            raise ValueError("kline limit must be between 1 and 1000")
        response = await self._get(
            "/v5/market/kline",
            {
                "category": self.category,
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
            authenticated=False,
        )
        rows = _result(response).get("list")
        if not isinstance(rows, list):
            raise BybitProtocolError("kline result.list must be an array")
        return map_rest_klines(
            rows,
            symbol=symbol,
            interval=interval,
            observed_at=observed_at or _response_time(response),
        )

    async def historical_candles_range(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime,
        end: datetime,
        page_limit: int = 1000,
        max_pages: int = 10_000,
    ) -> list[Candle]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("historical range timestamps must be timezone-aware")
        if end <= start:
            raise ValueError("historical range end must be after start")
        if page_limit < 1 or page_limit > 1000:
            raise ValueError("kline page_limit must be between 1 and 1000")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        start_ms = _timestamp_ms(start)
        requested_end_ms = _timestamp_ms(end)
        cursor_end_ms = requested_end_ms
        observed_at = min(datetime.now(UTC), end)
        by_opened_at: dict[datetime, Candle] = {}
        for _ in range(max_pages):
            response = await self._get(
                "/v5/market/kline",
                {
                    "category": self.category,
                    "symbol": symbol,
                    "interval": interval,
                    "start": start_ms,
                    "end": cursor_end_ms,
                    "limit": page_limit,
                },
                authenticated=False,
            )
            rows = _result(response).get("list")
            if not isinstance(rows, list):
                raise BybitProtocolError("kline result.list must be an array")
            if not rows:
                break
            candles = map_rest_klines(
                rows,
                symbol=symbol,
                interval=interval,
                observed_at=observed_at,
            )
            for candle in candles:
                if start <= candle.opened_at < end and candle.is_closed:
                    existing = by_opened_at.get(candle.opened_at)
                    if existing is not None and existing != candle:
                        raise BybitProtocolError(
                            "Bybit returned conflicting duplicate historical candles"
                        )
                    by_opened_at[candle.opened_at] = candle
            earliest_ms = min(int(row[0]) for row in rows)
            if earliest_ms <= start_ms:
                break
            next_end_ms = earliest_ms - 1
            if next_end_ms >= cursor_end_ms:
                raise BybitProtocolError("historical kline cursor did not move backwards")
            cursor_end_ms = next_end_ms
        else:
            raise BybitProtocolError("historical kline download exceeded max_pages")
        return sorted(by_opened_at.values(), key=lambda candle: candle.opened_at)

    async def historical_mark_candles_range(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime,
        end: datetime,
        page_limit: int = 1000,
        max_pages: int = 10_000,
    ) -> list[Candle]:
        return await self._historical_price_range(
            "/v5/market/mark-price-kline",
            symbol,
            interval,
            start=start,
            end=end,
            page_limit=page_limit,
            max_pages=max_pages,
            mark_price=True,
        )

    async def historical_funding_range(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
        mark_candles: list[Candle],
        page_limit: int = 200,
        max_pages: int = 10_000,
    ) -> list[FundingEvent]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("invalid funding history range")
        if page_limit < 1 or page_limit > 200 or max_pages < 1:
            raise ValueError("invalid funding pagination policy")
        cursor_end_ms = _timestamp_ms(end)
        start_ms = _timestamp_ms(start)
        raw_by_time: dict[datetime, Decimal] = {}
        for _ in range(max_pages):
            response = await self._get(
                "/v5/market/funding/history",
                {
                    "category": self.category,
                    "symbol": symbol,
                    "startTime": start_ms,
                    "endTime": cursor_end_ms,
                    "limit": page_limit,
                },
                authenticated=False,
            )
            items = _result_list(response)
            if not items:
                break
            times: list[int] = []
            for item in items:
                timestamp_ms = int(str(item.get("fundingRateTimestamp")))
                times.append(timestamp_ms)
                occurred_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
                if start <= occurred_at < end:
                    raw_by_time[occurred_at] = Decimal(str(item.get("fundingRate")))
            earliest_ms = min(times)
            if earliest_ms <= start_ms:
                break
            next_end_ms = earliest_ms - 1
            if next_end_ms >= cursor_end_ms:
                raise BybitProtocolError("funding history cursor did not move backwards")
            cursor_end_ms = next_end_ms
        else:
            raise BybitProtocolError("funding history download exceeded max_pages")
        ordered_marks = sorted(mark_candles, key=lambda candle: candle.closed_at)
        events: list[FundingEvent] = []
        for occurred_at, rate in sorted(raw_by_time.items()):
            candidates = [candle for candle in ordered_marks if candle.closed_at <= occurred_at]
            if not candidates:
                raise BybitProtocolError("funding event has no causal Mark Price candle")
            events.append(FundingEvent(symbol, occurred_at, rate, candidates[-1].close))
        return events

    async def historical_market_data_range(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime,
        end: datetime,
    ) -> HistoricalMarketData:
        trade = tuple(
            await self.historical_candles_range(symbol, interval, start=start, end=end)
        )
        mark = tuple(
            await self.historical_mark_candles_range(symbol, interval, start=start, end=end)
        )
        funding = tuple(
            await self.historical_funding_range(
                symbol,
                start=start,
                end=end,
                mark_candles=list(mark),
            )
        )
        trade_dataset = HistoricalDataset(trade)
        marks_by_time = {candle.opened_at: candle for candle in mark}
        aligned_mark = (
            tuple(marks_by_time[candle.opened_at] for candle in trade)
            if all(candle.opened_at in marks_by_time for candle in trade)
            else ()
        )
        return HistoricalMarketData(
            trade_dataset,
            aligned_mark,
            funding,
            mark_price_complete=len(aligned_mark) == len(trade) and bool(aligned_mark),
            funding_complete=bool(funding),
        )

    async def _historical_price_range(
        self,
        endpoint: str,
        symbol: str,
        interval: str,
        *,
        start: datetime,
        end: datetime,
        page_limit: int,
        max_pages: int,
        mark_price: bool,
    ) -> list[Candle]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("invalid historical range")
        if page_limit < 1 or page_limit > 1000 or max_pages < 1:
            raise ValueError("invalid historical pagination policy")
        start_ms = _timestamp_ms(start)
        cursor_end_ms = _timestamp_ms(end)
        observed_at = min(datetime.now(UTC), end)
        by_opened_at: dict[datetime, Candle] = {}
        for _ in range(max_pages):
            response = await self._get(
                endpoint,
                {
                    "category": self.category,
                    "symbol": symbol,
                    "interval": interval,
                    "start": start_ms,
                    "end": cursor_end_ms,
                    "limit": page_limit,
                },
                authenticated=False,
            )
            rows = _result(response).get("list")
            if not isinstance(rows, list):
                raise BybitProtocolError("historical result.list must be an array")
            if not rows:
                break
            normalized = [list(row) + (["0", "0"] if mark_price else []) for row in rows]
            candles = map_rest_klines(
                normalized,
                symbol=symbol,
                interval=interval,
                observed_at=observed_at,
            )
            for candle in candles:
                if start <= candle.opened_at < end and candle.is_closed:
                    existing = by_opened_at.get(candle.opened_at)
                    if existing is not None and existing != candle:
                        raise BybitProtocolError("conflicting duplicate historical candles")
                    by_opened_at[candle.opened_at] = candle
            earliest_ms = min(int(row[0]) for row in rows)
            if earliest_ms <= start_ms:
                break
            next_end_ms = earliest_ms - 1
            if next_end_ms >= cursor_end_ms:
                raise BybitProtocolError("historical cursor did not move backwards")
            cursor_end_ms = next_end_ms
        else:
            raise BybitProtocolError("historical download exceeded max_pages")
        return sorted(by_opened_at.values(), key=lambda candle: candle.opened_at)

    async def read_snapshot(self, symbol: str) -> BybitReadSnapshot:
        observed_at = datetime.now(UTC)
        instrument = await self.instrument_rules(symbol)
        account = await self.account_snapshot(symbol)
        position = await self.position_snapshot(symbol)
        orders = tuple(await self.open_orders(symbol))
        return BybitReadSnapshot(instrument, account, position, orders, observed_at)

    async def _get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        response = await self._transport.get(
            endpoint,
            params,
            authenticated=authenticated,
        )
        ret_code = response.get("retCode")
        if ret_code != 0:
            raise BybitApiError(endpoint, ret_code, response.get("retMsg"))
        _result(response)
        return response


def _required_positive_decimal(value: Any, field: str) -> Decimal:
    parsed = _optional_decimal(value)
    if parsed is None or parsed <= 0:
        raise BybitProtocolError(f"history field {field} must be positive")
    return parsed


def _required_decimal_value(value: Any, field: str) -> Decimal:
    parsed = _optional_decimal(value)
    if parsed is None or not parsed.is_finite():
        raise BybitProtocolError(f"history field {field} must be a finite decimal")
    return parsed


def _timestamp_datetime(value: Any, field: str) -> datetime:
    if value in (None, ""):
        raise BybitProtocolError(f"history field {field} is required")
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BybitProtocolError(f"invalid history field {field}: {value!r}") from exc


def _result(response: Mapping[str, Any]) -> Mapping[str, Any]:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise BybitProtocolError("Bybit response.result must be an object")
    return result


def _result_list(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = _result(response).get("list")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise BybitProtocolError("Bybit response.result.list must be an object array")
    return list(items)


def _response_time(response: Mapping[str, Any]) -> datetime:
    value = response.get("time")
    if value in (None, ""):
        return datetime.now(UTC)
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _array(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BybitProtocolError(f"Bybit {field} must be an array")
    return value


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(str(value))


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _permission_audit(result: Mapping[str, Any]) -> ApiKeyPermissionAudit:
    raw = result.get("permissions")
    if not isinstance(raw, Mapping):
        raise BybitProtocolError("Bybit permissions must be an object")

    def selected(name: str) -> tuple[str, ...]:
        return tuple(str(item) for item in _array(raw.get(name), f"permissions.{name}"))

    contract = selected("ContractTrade")
    spot = selected("Spot")
    wallet = selected("Wallet")
    options = selected("Options")
    missing = sorted({"Order", "Position"}.difference(contract))
    known = {"ContractTrade", "Spot", "Wallet", "Options"}
    other = tuple(
        sorted(
            (
                str(name),
                tuple(str(item) for item in _array(value, f"permissions.{name}")),
            )
            for name, value in raw.items()
            if name not in known and value
        )
    )
    forbidden_other = tuple(
        (name, values)
        for name, values in other
        if name != "Derivatives" or any(value != "DerivativesTrade" for value in values)
    )
    blockers: list[str] = []
    if missing:
        blockers.append(f"ContractTrade missing: {', '.join(missing)}")
    if spot:
        blockers.append(f"Spot permissions are forbidden: {', '.join(spot)}")
    if wallet:
        blockers.append(f"Wallet permissions are forbidden: {', '.join(wallet)}")
    if options:
        blockers.append(f"Options/USDC permissions are forbidden: {', '.join(options)}")
    if forbidden_other:
        blockers.append(
            "other permissions are forbidden: "
            + ", ".join(name for name, _ in forbidden_other)
        )
    if int(str(result.get("uta", 0))) != 1:
        blockers.append("Unified Account permission is required")
    return ApiKeyPermissionAudit(
        contract,
        spot,
        wallet,
        options,
        other,
        tuple(blockers),
        (),
    )
