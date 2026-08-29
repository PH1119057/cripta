from __future__ import annotations

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


class MarketState(StrEnum):
    CALM = "спокойный рынок"
    DIRECTIONAL = "направленное движение"
    COUNTER_SPIKE = "встречный вынос"
    FALSE_REVERSAL = "ложный разворот"
    SYNCHRONOUS_DROP = "синхронный пролив"
    SYNCHRONOUS_RISE = "синхронный вынос вверх"
    TRANSITION = "переходный рынок"
    WHIPSAW = "двусторонняя пила"
    CORRELATED_RISK = "высокая корреляция риска"
    POSITION_BUILDUP = "накопление позиций"
    POSITION_REDUCTION = "сокращение позиций"
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
    """Pure, causal, read-only market observer. It has no execution dependency."""

    VERSION = "mayak-v2.1"

    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.symbols = symbols
        self.trades = {
            (market, symbol): TradeWindow() for market in ("spot", "linear") for symbol in symbols
        }
        self.stamps: dict[tuple[str, str, str], SourceStamp] = defaultdict(SourceStamp)
        self.tickers: dict[str, dict[str, Any]] = {}
        self.ticker_history: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=4000)
        )
        self.books: dict[tuple[str, str], dict[str, Any]] = {}
        self.book_history: dict[
            tuple[str, str], deque[tuple[float, float, float, float]]
        ] = defaultdict(lambda: deque(maxlen=4000))
        self.previous_books: dict[tuple[str, str], dict[str, float]] = {}
        self.history: deque[tuple[float, dict[str, float]]] = deque(maxlen=60)

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
            history.append((timestamp, current["open_interest"]))
            while history and history[0][0] < timestamp - 3700:
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
        history.append((timestamp, bid, ask, current["imbalance"]))
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

    def snapshot(
        self,
        now: datetime,
        *,
        signals: dict[str, Any] | None = None,
        positions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now_ts = now.timestamp()
        coins: dict[str, Any] = {}
        returns: dict[str, float] = {}
        spot_sells = future_sells = oi_up = bid_withdrawal = 0
        source_rows: list[dict[str, Any]] = []
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
            quality = {}
            for market, source in (
                ("spot", "trades"),
                ("linear", "trades"),
                ("spot", "book"),
                ("linear", "book"),
                ("linear", "ticker"),
            ):
                row = self.stamps[(market, symbol, source)].describe(now_ts)
                quality[f"{market}_{source}"] = row
                source_rows.append(row)
            coins[symbol] = {
                "spot": spot,
                "linear": linear,
                "ticker": ticker,
                "books": books,
                "quality": quality,
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
        state, reasons = self._classify(
            median, agreement, synchronization, money_breadth, positions or {}
        )
        fresh = sum(row["quality"] == SourceQuality.FRESH for row in source_rows)
        confidence = fresh / max(1, len(source_rows))
        return {
            "observed_at": now.isoformat(),
            "engine_version": self.VERSION,
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
            "signals": signals or {},
            "positions": positions or {},
            "coins": coins,
            "external_exchange_flows": {
                "quality": SourceQuality.UNAVAILABLE,
                "reason": "Bybit не публикует совокупные вводы и выводы клиентов",
            },
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
        positions: dict[str, Any],
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
        if positions.get("correlated_risk") and corr >= 0.7:
            return MarketState.CORRELATED_RISK, [
                "открытые позиции зависят от одного общего движения"
            ]
        if agreement >= 0.7 and abs(median) >= 0.1:
            return MarketState.DIRECTIONAL, ["широкий рынок движется в одном направлении"]
        if corr >= 0.7:
            return MarketState.TRANSITION, [
                "общий рыночный фактор усилился",
                "направление ещё не подтверждено",
            ]
        return MarketState.CALM, ["выраженного общего движения не обнаружено"]
