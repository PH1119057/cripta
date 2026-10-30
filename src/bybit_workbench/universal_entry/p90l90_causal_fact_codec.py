"""Lossless normalized fact envelope for the P90L90 durable causal queue.

This codec does not dispatch, activate Strategies, connect to Bybit, or make
orders. It permits an Observer to journal the exact normalized fact and native
trade cursor BEFORE any delayed Entry/Exit processing. The source's raw Bybit
WS JSON is a separate provenance requirement, not invented by this codec.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .contracts import FrozenPolicy, MarketFactEnvelope, TradeDirection
from .fingerprint import canonical_json
from .p90l90_causal_transport_join import P90TransportEvent
from .transport_continuity import TradeCursor

_SCHEMA = "P90L90_NORMALIZED_FACT_V1"
_SYMBOLS = frozenset({"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"})
_KEYS = frozenset({
    "schema", "fact_id", "event_kind", "symbol", "observed_at", "event_at",
    "received_at", "source_refs", "attributes", "direction",
})


def _utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("P90L90 normalized fact time must be aware")
    return at.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("P90L90 normalized fact timestamp must be string")
    at = datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() != UTC.utcoffset(at):
        raise ValueError("P90L90 normalized fact timestamp must be UTC")
    return at


def _positive_price(raw: object) -> None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("P90L90 normalized fact invalid native price") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("P90L90 normalized fact invalid native price")


def encode_p90l90_fact(
    fact: MarketFactEnvelope,
    *,
    cursor: TradeCursor | None = None,
) -> P90TransportEvent:
    """Encode a five-symbol native trade or completed 5m/15m fact verbatim."""
    if fact.symbol not in _SYMBOLS:
        raise ValueError("P90L90 normalized fact outside exact Strategy symbols")
    if not fact.fact_id or not fact.source_refs or any(
        not isinstance(item, str) or not item for item in fact.source_refs
    ):
        raise ValueError("P90L90 normalized fact provenance missing")
    if _utc(fact.event_at) != _utc(fact.observed_at):
        raise ValueError("P90L90 fact event/observation times differ")
    if _utc(fact.received_at) < _utc(fact.event_at):
        raise ValueError("P90L90 fact received before exchange event")
    attrs = fact.attributes.to_dict()
    kind: Literal["PUBLIC_TRADE", "CANDLE_CLOSED"]
    if fact.event_kind == "PUBLIC_TRADE":
        kind = "PUBLIC_TRADE"
        if (
            cursor is None or cursor.symbol != fact.symbol
            or _utc(cursor.traded_at) != _utc(fact.event_at)
            or not cursor.exec_id or type(cursor.seq) is not int
            or cursor.seq < 0
        ):
            raise ValueError("P90L90 normalized fact native trade cursor mismatch")
        _positive_price(attrs.get("price"))
        tf = None
        seq, exec_id = cursor.seq, cursor.exec_id
    elif fact.event_kind == "CANDLE_CLOSED":
        kind = "CANDLE_CLOSED"
        if cursor is not None:
            raise ValueError("P90L90 closed kline must not contain trade cursor")
        tf = str(attrs.get("timeframe") or "")
        if tf not in {"5", "15"}:
            raise ValueError("P90L90 closed kline timeframe unsupported")
        if _parse_timestamp(attrs.get("closed_at")) != _utc(fact.event_at):
            raise ValueError("P90L90 closed kline boundary inconsistent")
        for key in ("open", "high", "low", "close"):
            _positive_price(attrs.get(key))
        seq, exec_id = None, None
    else:
        raise ValueError("P90L90 queue handles native trades/closed candles only")
    payload = {
        "schema": _SCHEMA,
        "fact_id": fact.fact_id,
        "event_kind": fact.event_kind,
        "symbol": fact.symbol,
        "observed_at": _utc(fact.observed_at).isoformat(),
        "event_at": _utc(fact.event_at).isoformat(),
        "received_at": _utc(fact.received_at).isoformat(),
        "source_refs": list(fact.source_refs),
        "attributes": attrs,
        "direction": None if fact.direction is None else fact.direction.value,
    }
    return P90TransportEvent(
        event_id=fact.fact_id, symbol=fact.symbol, kind=kind,
        event_at=_utc(fact.event_at), source_json=canonical_json(payload),
        timeframe=tf, exchange_sequence=seq, exec_id=exec_id,
    )


def restore_p90l90_fact(
    event: P90TransportEvent,
) -> tuple[MarketFactEnvelope, TradeCursor | None]:
    """Verify whole persisted envelope before delivering any PAPER side effect."""
    try:
        raw: Any = json.loads(event.source_json)
    except (ValueError, TypeError) as exc:
        raise ValueError("P90L90 normalized fact JSON corrupt") from exc
    if not isinstance(raw, dict) or frozenset(raw) != _KEYS or raw["schema"] != _SCHEMA:
        raise ValueError("P90L90 normalized fact payload schema mismatch")
    attrs = raw["attributes"]
    refs = raw["source_refs"]
    if (
        not isinstance(attrs, dict) or not isinstance(refs, list)
        or not refs or any(not isinstance(x, str) or not x for x in refs)
        or raw["event_kind"] != event.kind
        or raw["fact_id"] != event.event_id
        or raw["symbol"] != event.symbol
    ):
        raise ValueError("P90L90 normalized fact source identity mismatch")
    direction = raw["direction"]
    if direction is not None and direction not in {"LONG", "SHORT"}:
        raise ValueError("P90L90 normalized fact direction invalid")
    fact = MarketFactEnvelope(
        fact_id=raw["fact_id"], event_kind=raw["event_kind"],
        symbol=raw["symbol"],
        observed_at=_parse_timestamp(raw["observed_at"]),
        event_at=_parse_timestamp(raw["event_at"]),
        received_at=_parse_timestamp(raw["received_at"]),
        source_refs=tuple(refs),
        attributes=FrozenPolicy.from_mapping(attrs),
        direction=None if direction is None else TradeDirection(direction),
    )
    seq: TradeCursor | None = None
    if event.kind == "PUBLIC_TRADE":
        if event.exchange_sequence is None or not event.exec_id:
            raise ValueError("P90L90 normalized fact native cursor absent")
        seq = TradeCursor(
            event.symbol, event.exec_id, event.exchange_sequence, event.event_at
        )
    elif (
        event.timeframe != str(attrs.get("timeframe"))
        or event.exchange_sequence is not None
        or event.exec_id is not None
    ):
        raise ValueError("P90L90 normalized fact candle cursor mismatch")
    # Re-encoding proves timestamp, price, symbol, timeframes, native cursor,
    # and original canonical source payload all agree before dispatch.
    rebuilt = encode_p90l90_fact(fact, cursor=seq)
    if rebuilt != event:
        raise ValueError("P90L90 normalized fact roundtrip identity mismatch")
    return fact, seq
