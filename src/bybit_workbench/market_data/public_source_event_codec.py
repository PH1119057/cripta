"""Source-owned Bybit public WS event codec: market identity, not Strategy signals.

All observed public event identities are tagged with exact SPOT/LINEAR
instrument, original venue timestamp and immutable source type. No private
Bybit API, no account field, and no consumer permission is inferred.
A reconnect does NOT prove source continuity; exact gap epoch is mandatory.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

Product = Literal["SPOT", "LINEAR"]
Kind = Literal["TRADE", "BOOK", "TICKER", "LIQUIDATION", "FORMING_KLINE"]
_ALLOWED_FIELDS = frozenset({
    "lastPrice", "markPrice", "indexPrice", "bid1Price", "ask1Price",
    "bid1Size", "ask1Size", "openInterest", "openInterestValue",
    "fundingRate", "nextFundingTime",
})
_SENSITIVE_TIME = {"nextFundingTime"}
_OPTIONAL_SIGNED = {"fundingRate"}


class PublicSourceFrameInvalid(RuntimeError):
    """Untrusted public market-data frame or identity mismatch."""


@dataclass(frozen=True, slots=True)
class PublicSourceFact:
    market_source_id: str
    instrument_id: str
    product: Product
    symbol: str
    kind: Kind
    source_time: datetime
    frame_type: str
    payload: Mapping[str, object]
    exchange_sequence: int | None = None
    exec_id: str | None = None

    @property
    def source_topic(self) -> str:
        if self.kind == "TRADE":
            return f"publicTrade.{self.symbol}"
        if self.kind == "TICKER":
            return f"tickers.{self.symbol}"
        if self.kind == "LIQUIDATION":
            return f"allLiquidation.{self.symbol}"
        if self.kind == "FORMING_KLINE":
            return f"kline.{self.payload['timeframe']}.{self.symbol}"
        return f"orderbook.50.{self.symbol}"


def _milliseconds(value: object) -> datetime:
    if type(value) is not int or not 946684800000 <= value < 4102444800000:
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TIME_INVALID")
    return datetime.fromtimestamp(value / 1000, UTC)


def _decimal(value: object, *, zero: bool = False, signed: bool = False) -> str:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_NUMBER_INVALID")
    try:
        dec = Decimal(str(value))
        if not dec.is_finite() or (dec < 0 and not signed) or (
            dec == 0 and not zero and not signed
        ):
            raise ValueError("invalid value")
    except (InvalidOperation, ValueError) as exc:
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_NUMBER_INVALID") from exc
    return str(dec)


def _levels(value: object, *, snapshot: bool) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or len(value) > 100:
        raise PublicSourceFrameInvalid("PUBLIC_BOOK_LEVELS_INVALID")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_LEVEL_INVALID")
        price = _decimal(item[0])
        size = _decimal(item[1], zero=not snapshot)
        if price in seen:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_DUPLICATE_LEVEL")
        seen.add(price)
        result.append((price, size))
    return tuple(result)


def decode_bybit_public_frame(
    message: Mapping[str, object], *, product: Product,
    allowed_symbols: frozenset[str], received_at: datetime,
) -> tuple[PublicSourceFact, ...]:
    """Strict original provider facts; never infer spot from an instrument name.

    Ticker delta may contain only changed fields; absent fields remain absent.
    Snapshot book vs delta and original update/sequence are preserved.
    Liquidation side denotes the LIQUIDATED position, not our trade side.
    """
    if product not in ("SPOT", "LINEAR"):
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_CATEGORY_INVALID")
    if received_at.tzinfo is None or received_at.utcoffset() != timedelta(0):
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_RECEIVER_TIME_INVALID")
    topic = message.get("topic")
    if not isinstance(topic, str) or not topic:
        return ()
    parts = topic.split(".")
    if len(parts) == 2 and parts[0] in (
        "publicTrade", "tickers", "allLiquidation",
    ):
        kind, symbol = parts
    elif len(parts) == 3 and parts[:2] == ["orderbook", "50"]:
        kind, symbol = "orderbook.50", parts[2]
    elif len(parts) == 3 and parts[:2] == ["kline", "1"]:
        kind, symbol = "kline.1", parts[2]
    else:
        return ()
    if symbol not in allowed_symbols:
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_SYMBOL_UNAUTHORIZED")
    if product == "SPOT" and kind in ("tickers", "allLiquidation"):
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TOPIC_CATEGORY_INVALID")
    observed = _milliseconds(message.get("ts"))
    if observed > received_at + timedelta(seconds=5):
        raise PublicSourceFrameInvalid("PUBLIC_SOURCE_FUTURE_VENUE_TIME")
    data = message.get("data")
    frame_type = str(message.get("type") or "")
    facts: list[PublicSourceFact] = []
    category = bybit_instrument_id(product.lower(), symbol)

    def fact(
        k: Kind, row: Mapping[str, object],
        *, when: datetime = observed, seq: int | None = None,
        eid: str | None = None,
    ) -> PublicSourceFact:
        return PublicSourceFact(
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=category,
            product=product, symbol=symbol,
            kind=k, source_time=when,
            frame_type=frame_type, payload=row,
            exchange_sequence=seq, exec_id=eid,
        )

    if kind == "publicTrade":
        if not isinstance(data, list) or len(data) > 2048:
            raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TRADES_INVALID")
        seen: set[tuple[int, str]] = set()
        for row in data:
            if not isinstance(row, dict) or row.get("s") != symbol:
                raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TRADE_SYMBOL")
            seq = row.get("seq")
            eid = row.get("i")
            if type(seq) is not int or seq < 0 or not isinstance(eid, str) or not eid:
                raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TRADE_ID_INVALID")
            if (seq, eid) in seen:
                raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TRADE_DUPLICATE")
            seen.add((seq, eid))
            side = row.get("S")
            if side not in ("Buy", "Sell"):
                raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TRADE_SIDE")
            facts.append(fact(
                "TRADE", {
                    "price": _decimal(row.get("p")),
                    "qty": _decimal(row.get("v")),
                    "side": side,
                }, when=_milliseconds(row.get("T")), seq=seq, eid=eid,
            ))
    elif kind == "kline.1":
        # NEVER insert forming data into the immutable closed candle journal.
        # Only provider-confirmed bars follow _persist_closed_kline_frame().
        if product != "LINEAR":
            # SPOT kline.1 carries provider-confirmed closed candles
            # through the canonical candle adapter. SPOT forming-minute
            # observations are NOT an approved capability in this codec.
            return ()
        if frame_type != "snapshot" or not isinstance(data, list):
            raise PublicSourceFrameInvalid("PUBLIC_FORMING_FRAME_INVALID")
        if len(data) != 1 or not isinstance(data[0], dict):
            raise PublicSourceFrameInvalid("PUBLIC_FORMING_ROW_INVALID")
        row = data[0]
        if type(row.get("confirm")) is not bool:
            raise PublicSourceFrameInvalid(
                "PUBLIC_FORMING_CONFIRM_FLAG_INVALID"
            )
        if row["confirm"]:
            return ()  # only confirmed closed-candle owner persists this
        start, end = row.get("start"), row.get("end")
        if (
            type(start) is not int or type(end) is not int
            or start % 60000 != 0 or end != start + 59999
            or row.get("interval") != "1"
        ):
            raise PublicSourceFrameInvalid("PUBLIC_FORMING_BOUNDARY_INVALID")
        opened_at = _milliseconds(start)
        closed_at = _milliseconds(start + 60000)
        if not opened_at <= observed < closed_at:
            raise PublicSourceFrameInvalid(
                "PUBLIC_FORMING_EXPIRED_OR_FUTURE_SOURCE"
            )
        match_ms = row.get("timestamp")
        if (
            type(match_ms) is not int or not start <= match_ms <= end
            or match_ms > int(observed.timestamp() * 1000) + 5000
        ):
            raise PublicSourceFrameInvalid(
                "PUBLIC_FORMING_LAST_MATCH_TIMESTAMP_INVALID"
            )
        values = {
            name: _decimal(
                row.get(name), zero=name in ("volume", "turnover"),
            )
            for name in (
                "open", "high", "low", "close", "volume", "turnover",
            )
        }
        if (
            Decimal(values["low"]) > Decimal(values["high"])
            or not (
                Decimal(values["low"]) <= Decimal(values["open"])
                <= Decimal(values["high"])
            )
            or not (
                Decimal(values["low"]) <= Decimal(values["close"])
                <= Decimal(values["high"])
            )
        ):
            raise PublicSourceFrameInvalid("PUBLIC_FORMING_OHLC_INVALID")
        facts.append(fact("FORMING_KLINE", {
            "timeframe": "1", "confirmed": False,
            "opened_at": opened_at.isoformat(),
            "closed_at": closed_at.isoformat(),
            "observed_at": observed.isoformat(),
            "provider_last_match_at": _milliseconds(match_ms).isoformat(),
            **values,
        }))
    elif kind == "tickers":
        if not isinstance(data, dict) or data.get("symbol") != symbol:
            raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TICKER_SYMBOL")
        if frame_type not in ("snapshot", "delta"):
            raise PublicSourceFrameInvalid("PUBLIC_SOURCE_TICKER_TYPE")
        updates: dict[str, str] = {}
        for name, value in data.items():
            if name not in _ALLOWED_FIELDS or value in (None, ""):
                continue
            if name in _SENSITIVE_TIME:
                if not isinstance(value, (int, str)) or not str(value).isdigit():
                    raise PublicSourceFrameInvalid("PUBLIC_SOURCE_FUNDING_TIME")
                updates[name] = str(value)
            else:
                updates[name] = _decimal(
                    value,
                    zero=name not in ("lastPrice", "markPrice", "indexPrice",
                                      "bid1Price", "ask1Price"),
                    signed=name in _OPTIONAL_SIGNED,
                )
        if not updates:
            return ()
        facts.append(fact("TICKER", updates))
    elif kind == "allLiquidation":
        if not isinstance(data, list) or len(data) > 4096:
            raise PublicSourceFrameInvalid("PUBLIC_SOURCE_LIQUIDATIONS_INVALID")
        for row in data:
            if not isinstance(row, dict) or row.get("s") != symbol:
                raise PublicSourceFrameInvalid("PUBLIC_SOURCE_LIQUIDATION_SYMBOL")
            side = row.get("S")
            if side not in ("Buy", "Sell"):
                raise PublicSourceFrameInvalid("PUBLIC_SOURCE_LIQUIDATION_SIDE")
            facts.append(fact(
                "LIQUIDATION", {
                    "price": _decimal(row.get("p")),
                    "qty": _decimal(row.get("v")),
                    "side": side,
                }, when=_milliseconds(row.get("T")),
            ))
    else:
        if frame_type not in ("snapshot", "delta") or not isinstance(data, dict):
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_TYPE_INVALID")
        if data.get("s") != symbol:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_SYMBOL_INVALID")
        seq, update = data.get("seq"), data.get("u")
        if type(seq) is not int or seq < 0 or type(update) is not int or update < 0:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_SEQUENCE_INVALID")
        bids = _levels(data.get("b"), snapshot=frame_type == "snapshot")
        asks = _levels(data.get("a"), snapshot=frame_type == "snapshot")
        if frame_type == "snapshot" and (not bids or not asks):
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_SNAPSHOT_EMPTY")
        facts.append(fact("BOOK", {
            "bids": bids, "asks": asks, "update_id": update,
            "source_sequence": seq,
        }, seq=seq))
    return tuple(facts)


class OrderbookContinuity:
    """Exact in-memory ladder, no false continuity across reconnects."""

    def __init__(self) -> None:
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._sequence: int | None = None
        self._update_id: int | None = None
        self._ready = False

    def invalidate(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._sequence = None
        self._update_id = None
        self._ready = False

    def apply(self, event: PublicSourceFact) -> Mapping[str, object]:
        if event.kind != "BOOK" or event.exchange_sequence is None:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_NOT_AN_EVENT")
        snap = event.frame_type == "snapshot"
        update = event.payload.get("update_id")
        if type(update) is not int:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_UPDATE_ID_INVALID")
        if not snap and not self._ready:
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_DELTA_WITHOUT_SNAPSHOT")
        # Bybit explicitly uses update_id=1 for orderbook source restart.
        # The old ladder must never be combined with this new epoch.
        if not snap and update == 1:
            self.invalidate()
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_RESET_REQUIRES_SNAPSHOT")
        if not snap and self._update_id is not None and update <= self._update_id:
            self.invalidate()
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_UPDATE_REGRESSION")
        if not snap and self._sequence is not None and event.exchange_sequence <= self._sequence:
            self.invalidate()
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_SEQUENCE_REGRESSION")
        if snap:
            self.invalidate()
        for side, key in ((self._bids, "bids"), (self._asks, "asks")):
            rows = event.payload[key]
            if not isinstance(rows, tuple):
                raise PublicSourceFrameInvalid("PUBLIC_BOOK_LEVELS_INVALID")
            for p, q in rows:
                price, qty = Decimal(p), Decimal(q)
                if qty == 0:
                    side.pop(price, None)
                else:
                    side[price] = qty
        self._sequence = event.exchange_sequence
        self._update_id = update
        if not self._bids or not self._asks:
            self.invalidate()
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_SIDE_EMPTY")
        bid = max(self._bids)
        ask = min(self._asks)
        if bid >= ask:
            self.invalidate()
            raise PublicSourceFrameInvalid("PUBLIC_BOOK_CROSSED")
        self._ready = True
        middle = (bid + ask) / 2
        bands: dict[str, str] = {}
        for bps in (5, 10, 25, 50):
            ratio = Decimal(bps) / 10000
            bands[f"bid_{bps}bps_usd"] = str(sum(
                (p * q for p, q in self._bids.items()
                 if p >= middle * (1 - ratio)), Decimal(0),
            ))
            bands[f"ask_{bps}bps_usd"] = str(sum(
                (p * q for p, q in self._asks.items()
                 if p <= middle * (1 + ratio)), Decimal(0),
            ))
        return {
            "best_bid": str(bid), "best_ask": str(ask),
            "bid_size": str(self._bids[bid]),
            "ask_size": str(self._asks[ask]),
            "source_sequence": event.exchange_sequence,
            "source_time": event.source_time.isoformat(),
            **bands,
        }
