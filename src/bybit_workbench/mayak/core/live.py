from __future__ import annotations

import hashlib
import json
import math
import statistics
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SourceQuality(StrEnum):
    WARMUP = "прогрев"
    FRESH = "актуально"
    STALE = "данные устарели"
    MISSING = "нет данных"
    UNAVAILABLE = "источник не подключён"


class InstrumentAvailability(StrEnum):
    WARMUP = "WARMUP"
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


class MarketState(StrEnum):
    CALM = "спокойный рынок"
    DIRECTIONAL = "направленное движение"
    SYNCHRONOUS_DROP = "синхронный пролив"
    SYNCHRONOUS_RISE = "синхронный вынос вверх"
    TRANSITION = "переходный рынок"
    MONEY_DIVERGENCE = "денежное расхождение"


@dataclass(slots=True)
class TradeWindow:
    rows: deque[tuple[float, float, float]] = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, timestamp: float, signed_usd: float, price: float) -> None:
        with self.lock:
            self.rows.append((timestamp, signed_usd, price))
            self.trim(timestamp - 3600)

    def trim(self, cutoff: float) -> None:
        while self.rows and self.rows[0][0] < cutoff:
            self.rows.popleft()

    def metrics(self, now: float, seconds: int) -> dict[str, float | None]:
        with self.lock:
            all_rows = tuple(self.rows)
        rows = [row for row in all_rows if row[0] >= now - seconds]
        if not rows:
            return {
                "buy_usd": None,
                "sell_usd": None,
                "net_usd": None,
                "turnover_usd": None,
                "return_pct": None,
                "large_buy_usd": None,
                "large_sell_usd": None,
            }
        positive = [v for _, v, _ in rows if v > 0]
        negative = [-v for _, v, _ in rows if v < 0]
        sizes = sorted(abs(v) for _, v, _ in all_rows)
        threshold = (
            sizes[max(0, math.ceil(len(sizes) * 0.95) - 1)] if len(sizes) >= 20 else math.inf
        )
        return {
            "buy_usd": sum(positive),
            "sell_usd": sum(negative),
            "net_usd": sum(v for _, v, _ in rows),
            "turnover_usd": sum(abs(v) for _, v, _ in rows),
            "return_pct": (rows[-1][2] / rows[0][2] - 1) * 100 if rows[0][2] else None,
            "large_buy_usd": sum(v for _, v, _ in rows if v >= threshold),
            "large_sell_usd": sum(-v for _, v, _ in rows if v <= -threshold),
        }


@dataclass(slots=True)
class SourceStamp:
    observed_at: float | None = None
    expected_seconds: int = 2
    stale_seconds: int = 10

    def describe(self, now: float) -> dict[str, Any]:
        if self.observed_at is None:
            return {"quality": SourceQuality.WARMUP, "age_seconds": None}
        age = max(0, now - self.observed_at)
        quality = SourceQuality.FRESH if age <= self.stale_seconds else SourceQuality.STALE
        return {"quality": quality, "age_seconds": round(age, 1)}


class LiveMayakEngine:
    ARCHITECTURE_VERSION = "1.0"
    FEATURE_VERSION = "external-market-observer-v1"
    """Pure, causal, read-only market observer. It has no execution dependency."""

    VERSION = "mayak-v2.1"

    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.symbols = symbols
        self.trades = {
            (market, symbol): TradeWindow() for market in ("spot", "linear") for symbol in symbols
        }
        self.stamps: dict[tuple[str, str, str], SourceStamp] = defaultdict(SourceStamp)
        self.transport: dict[str, dict[str, Any]] = {
            market: {"connected": False, "observed_at": None, "error": None}
            for market in ("spot", "linear")
        }
        self.instrument_support: dict[str, set[str] | None] = {
            "spot": None,
            "linear": None,
        }
        self.tickers: dict[str, dict[str, Any]] = {}
        self.ticker_history: dict[str, deque[tuple[float, float]]] = defaultdict(deque)
        self.books: dict[tuple[str, str], dict[str, Any]] = {}
        # One compact sample per second preserves the complete 15-minute causal
        # horizon even on very active books, while keeping memory bounded by time
        # instead of by an exchange-dependent number of websocket updates.
        self.book_history: dict[
            tuple[str, str], deque[tuple[float, float, float, float]]
        ] = defaultdict(deque)
        self.previous_books: dict[tuple[str, str], dict[str, float]] = {}
        self.history: deque[tuple[float, dict[str, float]]] = deque(maxlen=60)

    def set_instrument_support(self, market: str, symbols: set[str] | None) -> None:
        if market in self.instrument_support:
            self.instrument_support[market] = set(symbols) if symbols is not None else None

    def on_transport(
        self, market: str, *, connected: bool, timestamp: float, error: str | None = None
    ) -> None:
        if market not in self.transport:
            return
        self.transport[market] = {
            "connected": connected,
            "observed_at": timestamp,
            "error": error,
        }

    def on_trade(
        self, market: str, symbol: str, timestamp: float, side: str, price: float, size: float
    ) -> None:
        if (market, symbol) not in self.trades or price <= 0 or size <= 0:
            return
        signed = price * size * (1 if side.lower() == "buy" else -1)
        self.trades[(market, symbol)].add(timestamp, signed, price)
        self.stamps[(market, symbol, "trades")].observed_at = timestamp

    def on_ticker(self, symbol: str, timestamp: float, **values: float) -> None:
        if symbol not in self.symbols:
            return
        prior = self.tickers.get(symbol, {})
        current: dict[str, Any] = {
            **prior,
            **{key: value for key, value in values.items() if value is not None},
        }
        if "open_interest" in values and current.get("open_interest", 0) > 0:
            history = self.ticker_history[symbol]
            oi_row = (timestamp, current["open_interest"])
            if history and int(timestamp) == int(history[-1][0]):
                history[-1] = oi_row
            else:
                history.append(oi_row)
            while history and history[0][0] < timestamp - 3900:
                history.popleft()
            for minutes in (5, 15, 30, 60):
                baseline = next(
                    (value for at, value in reversed(history) if at <= timestamp - minutes * 60),
                    None,
                )
                current[f"open_interest_change_{minutes}m_pct"] = (
                    (current["open_interest"] / baseline - 1) * 100
                    if baseline and baseline > 0
                    else None
                )
            current["open_interest_change_pct"] = current["open_interest_change_5m_pct"]
        self.tickers[symbol] = current
        stamp = self.stamps[("linear", symbol, "ticker")]
        stamp.observed_at, stamp.stale_seconds = timestamp, 45

    def on_book(
        self,
        market: str,
        symbol: str,
        timestamp: float,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
    ) -> None:
        if symbol not in self.symbols:
            return
        key = (market, symbol)
        old = self.books.get(key)
        bid = sum(price * size for price, size in bids if price > 0 and size > 0)
        ask = sum(price * size for price, size in asks if price > 0 and size > 0)
        current: dict[str, Any] = {
            "bid_usd": bid,
            "ask_usd": ask,
            "imbalance": (bid - ask) / (bid + ask) if bid + ask else 0.0,
        }
        if old:
            current["bid_change_pct"] = (bid / old["bid_usd"] - 1) * 100 if old["bid_usd"] else 0.0
            current["ask_change_pct"] = (ask / old["ask_usd"] - 1) * 100 if old["ask_usd"] else 0.0
            self.previous_books[key] = old
        self.books[key] = current
        history = self.book_history[key]
        row = (timestamp, bid, ask, current["imbalance"])
        if history and int(timestamp) == int(history[-1][0]):
            history[-1] = row
        else:
            history.append(row)
        while history and history[0][0] < timestamp - 1000:
            history.popleft()
        for minutes in (1, 5, 15):
            baseline = next(
                (row for row in reversed(history) if row[0] <= timestamp - minutes * 60), None
            )
            for name, value, index in (("bid", bid, 1), ("ask", ask, 2)):
                current[f"{name}_change_{minutes}m_pct"] = (
                    (value / baseline[index] - 1) * 100
                    if baseline and baseline[index] > 0
                    else None
                )
            current[f"imbalance_change_{minutes}m"] = (
                current["imbalance"] - baseline[3] if baseline else None
            )
        stamp = self.stamps[(market, symbol, "book")]
        stamp.observed_at, stamp.stale_seconds = timestamp, 8

    def snapshot(self, now: datetime) -> dict[str, Any]:
        now_ts = now.timestamp()
        coins: dict[str, Any] = {}
        returns: dict[str, float] = {}
        spot_sells = future_sells = oi_up = bid_withdrawal = 0
        source_rows: list[dict[str, Any]] = []
        transport = {
            market: self._transport_description(market, now_ts)
            for market in ("spot", "linear")
        }
        for symbol in self.symbols:
            spot = self.trades[("spot", symbol)].metrics(now_ts, 300)
            linear = self.trades[("linear", symbol)].metrics(now_ts, 300)
            ticker = self.tickers.get(symbol)
            books = {market: self.books.get((market, symbol)) for market in ("spot", "linear")}
            ret = linear["return_pct"] if linear["return_pct"] is not None else spot["return_pct"]
            if ret is not None:
                returns[symbol] = float(ret)
            spot_sells += spot["net_usd"] is not None and spot["net_usd"] < 0
            future_sells += linear["net_usd"] is not None and linear["net_usd"] < 0
            oi_up += bool(ticker and (ticker.get("open_interest_change_5m_pct") or 0) > 0)
            bid_withdrawal += any(
                bool(book and (book.get("bid_change_5m_pct") or 0) < -5)
                for book in books.values()
            )
            availability = {
                market: self._instrument_availability(market, symbol, transport[market])
                for market in ("spot", "linear")
            }
            quality = {}
            for market, source in (
                ("spot", "trades"),
                ("linear", "trades"),
                ("spot", "book"),
                ("linear", "book"),
                ("linear", "ticker"),
            ):
                row = self.stamps[(market, symbol, source)].describe(now_ts)
                row["activity_quality"] = row["quality"]
                row["transport_quality"] = transport[market]["quality"]
                if (
                    source == "trades"
                    and availability[market] == InstrumentAvailability.SUPPORTED
                    and transport[market]["quality"] == SourceQuality.FRESH
                ):
                    row["quality"] = SourceQuality.FRESH
                quality[f"{market}_{source}"] = row
                source_rows.append(row)
            coins[symbol] = {
                "spot": spot,
                "linear": linear,
                "ticker": ticker,
                "books": books,
                "quality": quality,
                "availability": availability,
            }
        valid_spot = sum(
            coin["spot"]["net_usd"] is not None for coin in coins.values()
        )
        valid_linear = sum(
            coin["linear"]["net_usd"] is not None for coin in coins.values()
        )
        valid_oi = sum(
            bool(coin["ticker"] and coin["ticker"].get("open_interest_change_5m_pct") is not None)
            for coin in coins.values()
        )
        valid_books = sum(
            any(
                book and book.get("bid_change_5m_pct") is not None
                for book in coin["books"].values()
            )
            for coin in coins.values()
        )
        valid = max(1, len(returns))
        up = sum(value > 0 for value in returns.values())
        down = sum(value < 0 for value in returns.values())
        money_breadth = {
            "spot_sales_share": spot_sells / valid_spot if valid_spot else None,
            "spot_coverage": {"valid": valid_spot, "total": len(self.symbols)},
            "derivatives_sales_share": future_sells / valid_linear if valid_linear else None,
            "derivatives_coverage": {"valid": valid_linear, "total": len(self.symbols)},
            "open_interest_growth_share": oi_up / valid_oi if valid_oi else None,
            "open_interest_coverage": {"valid": valid_oi, "total": len(self.symbols)},
            "buyer_liquidity_withdrawal_share": (
                bid_withdrawal / valid_books if valid_books else None
            ),
            "book_coverage": {"valid": valid_books, "total": len(self.symbols)},
        }
        agreement = max(up, down) / valid
        median = statistics.median(returns.values()) if returns else None
        synchronization = self._synchronization(returns)
        state, reasons = self._classify(median, agreement, synchronization, money_breadth)
        fresh = sum(row["quality"] == SourceQuality.FRESH for row in source_rows)
        confidence = fresh / max(1, len(source_rows))
        snapshot = {
            "observed_at": now.isoformat(),
            "architecture_version": self.ARCHITECTURE_VERSION,
            "engine_version": self.VERSION,
            "feature_version": self.FEATURE_VERSION,
            "config_fingerprint": hashlib.sha256(
                json.dumps(
                    {
                        "symbols": self.symbols,
                        "architecture": self.ARCHITECTURE_VERSION,
                        "engine": self.VERSION,
                        "features": self.FEATURE_VERSION,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "state": state,
            "confidence": round(confidence, 3),
            "reasons": reasons,
            "btc": coins.get("BTCUSDT"),
            "eth": coins.get("ETHUSDT"),
            "price_breadth": {
                "up": up,
                "down": down,
                "up_share": up / valid,
                "down_share": down / valid,
                "median_return_pct": median,
            },
            "money_breadth": money_breadth,
            "direction_synchronization": synchronization,
            "coins": coins,
            "transport": transport,
            "external_exchange_flows": {
                "quality": SourceQuality.UNAVAILABLE,
                "reason": "Bybit не публикует совокупные вводы и выводы клиентов",
            },
        }
        snapshot["dispatcher_handoff"] = self._dispatcher_handoff(snapshot)
        return snapshot

    def _transport_description(self, market: str, now: float) -> dict[str, Any]:
        state = self.transport[market]
        observed_at = state.get("observed_at")
        age = max(0.0, now - float(observed_at)) if observed_at is not None else None
        connected = bool(state.get("connected")) and age is not None and age <= 45
        quality = (
            SourceQuality.WARMUP
            if observed_at is None
            else SourceQuality.FRESH
            if connected
            else SourceQuality.STALE
        )
        return {
            "connected": connected,
            "quality": quality,
            "age_seconds": round(age, 1) if age is not None else None,
            "error": state.get("error"),
        }

    def _instrument_availability(
        self, market: str, symbol: str, transport: dict[str, Any]
    ) -> InstrumentAvailability:
        supported = self.instrument_support[market]
        if supported is None:
            return InstrumentAvailability.WARMUP
        if symbol not in supported:
            return InstrumentAvailability.UNSUPPORTED
        if not transport["connected"]:
            return InstrumentAvailability.TEMPORARILY_UNAVAILABLE
        return InstrumentAvailability.SUPPORTED

    @staticmethod
    def _dispatcher_handoff(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Build the objective read-only Mayak -> Dispatcher contract."""
        observed_at = str(snapshot["observed_at"])
        breadth = snapshot["price_breadth"]
        money = snapshot["money_breadth"]
        synchronization = snapshot["direction_synchronization"]
        median = breadth.get("median_return_pct")
        confidence = float(snapshot["confidence"])

        coins = snapshot["coins"]

        def source_confidence(market: str, source: str) -> float:
            eligible = [
                coin
                for coin in coins.values()
                if coin["availability"][market] == InstrumentAvailability.SUPPORTED
            ]
            fresh = sum(
                coin["quality"][f"{market}_{source}"]["quality"]
                == SourceQuality.FRESH
                for coin in eligible
            )
            return fresh / max(1, len(eligible))

        spot_confidence = source_confidence("spot", "trades")
        derivatives_confidence = source_confidence("linear", "trades")
        price_confidence = sum(
            (
                coin["quality"]["linear_trades"]["quality"] == SourceQuality.FRESH
                or coin["quality"]["spot_trades"]["quality"] == SourceQuality.FRESH
            )
            for coin in coins.values()
        ) / max(1, len(coins))

        def feature(
            value: str | None,
            *,
            available: bool = True,
            feature_confidence: float | None = None,
        ) -> dict[str, Any]:
            if not available:
                return {
                    "status": "NO_DATA",
                    "confidence": 0.0,
                    "observed_at": None,
                }
            return {
                "value": value,
                "status": "VALID",
                "confidence": confidence if feature_confidence is None else feature_confidence,
                "observed_at": observed_at,
            }

        def direction(value: float | None) -> str | None:
            if value is None:
                return None
            if value >= 0.35:
                return "STRONG_UP"
            if value >= 0.10:
                return "UP"
            if value <= -0.35:
                return "STRONG_DOWN"
            if value <= -0.10:
                return "DOWN"
            return "NEUTRAL"

        def pressure(sales_share: float | None) -> str | None:
            if sales_share is None:
                return None
            if sales_share >= 0.75:
                return "STRONG_SELL"
            if sales_share >= 0.55:
                return "SELL"
            if sales_share <= 0.25:
                return "STRONG_BUY"
            if sales_share <= 0.45:
                return "BUY"
            return "BALANCED"

        up_share = breadth.get("up_share")
        market_breadth = None
        if up_share is not None:
            market_breadth = (
                "STRONGLY_BULLISH"
                if up_share >= 0.75
                else "BULLISH"
                if up_share >= 0.55
                else "STRONGLY_BEARISH"
                if up_share <= 0.25
                else "BEARISH"
                if up_share <= 0.45
                else "BALANCED"
            )
        agreement = synchronization.get("agreement")
        sync_value = None
        if agreement is not None:
            sync_value = (
                "EXTREME"
                if agreement >= 0.85
                else "HIGH"
                if agreement >= 0.65
                else "NORMAL"
                if agreement >= 0.35
                else "LOW"
            )
        spot_pressure = pressure(money.get("spot_sales_share"))
        derivatives_pressure = pressure(money.get("derivatives_sales_share"))
        data_quality = (
            "HIGH"
            if confidence >= 0.90
            else "MEDIUM"
            if confidence >= 0.65
            else "LOW"
            if confidence >= 0.25
            else "INSUFFICIENT"
        )
        features = {
            "market.direction": feature(
                direction(median),
                available=median is not None,
                feature_confidence=price_confidence,
            ),
            "market.breadth": feature(
                market_breadth,
                available=market_breadth is not None,
                feature_confidence=price_confidence,
            ),
            "market.synchronization": feature(
                sync_value,
                available=sync_value is not None,
                feature_confidence=price_confidence,
            ),
            "money.spot_pressure": feature(
                spot_pressure,
                available=spot_pressure is not None,
                feature_confidence=spot_confidence,
            ),
            "money.derivatives_pressure": feature(
                derivatives_pressure,
                available=derivatives_pressure is not None,
                feature_confidence=derivatives_confidence,
            ),
            "liquidation.intensity": feature(None, available=False),
            "liquidation.acceleration": feature(None, available=False),
            "liquidation.breadth": feature(None, available=False),
            "liquidation.phase": feature(None, available=False),
            "event.context": feature(None, available=False),
            "event.importance": feature(None, available=False),
        }
        identity = json.dumps(
            {
                "observed_at": observed_at,
                "engine_version": snapshot["engine_version"],
                "architecture_version": snapshot["architecture_version"],
                "dispatcher_features": features,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "snapshot_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
            "observed_at": observed_at,
            "engine_version": snapshot["engine_version"],
            "architecture_version": snapshot["architecture_version"],
            "data_quality": data_quality,
            "dispatcher_features": features,
        }

    def _synchronization(self, returns: dict[str, float]) -> dict[str, float | None]:
        if len(returns) < 3:
            return {"agreement": None, "change": None}
        signs = [1 if value > 0 else -1 if value < 0 else 0 for value in returns.values()]
        agreement = abs(sum(signs)) / len(signs)
        previous = self.history[-1][1] if self.history else {}
        previous_signs = [1 if value > 0 else -1 if value < 0 else 0 for value in previous.values()]
        previous_agreement = (
            abs(sum(previous_signs)) / len(previous_signs) if previous_signs else agreement
        )
        self.history.append((datetime.now(UTC).timestamp(), dict(returns)))
        return {"agreement": agreement, "change": agreement - previous_agreement}

    @staticmethod
    def _classify(
        median: float | None,
        agreement: float,
        correlation: dict[str, float | None],
        money: dict[str, Any],
    ) -> tuple[MarketState, list[str]]:
        if median is None:
            return MarketState.TRANSITION, ["идёт прогрев ценовых потоков"]
        corr = float(correlation.get("agreement") or 0)
        if median <= -0.35 and agreement >= 0.7:
            return MarketState.SYNCHRONOUS_DROP, [
                "большинство монет падает одновременно",
                "медианное падение ускорилось",
            ]
        if median >= 0.35 and agreement >= 0.7:
            return MarketState.SYNCHRONOUS_RISE, [
                "большинство монет растёт одновременно",
                "медианный рост ускорился",
            ]
        if (
            abs(median) < 0.10
            and max(
                [
                    value
                    for value in (money["spot_sales_share"], money["derivatives_sales_share"])
                    if value is not None
                ]
                or [0.0]
            ) >= 0.7
        ):
            return MarketState.MONEY_DIVERGENCE, [
                "цена почти стоит",
                "агрессивные продажи преобладают раньше движения цены",
            ]
        if agreement >= 0.7 and abs(median) >= 0.1:
            return MarketState.DIRECTIONAL, ["широкий рынок движется в одном направлении"]
        if corr >= 0.7:
            return MarketState.TRANSITION, [
                "общий рыночный фактор усилился",
                "направление ещё не подтверждено",
            ]
        return MarketState.CALM, ["выраженного общего движения не обнаружено"]
