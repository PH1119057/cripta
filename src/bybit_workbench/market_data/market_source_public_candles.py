"""Validated, closed-only Bybit WS kline decoder for the ONE public MarketSource.

A caller must be the already approved producer. This module starts no socket,
does no REST fetch, and has no Entry, Exit, account or Exchange mutation rights.
Unconfirmed candles never become canonical closed OHLCV.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bybit_workbench.domain.models import Candle

_SUPPORTED_MINUTES = {"1": 1, "5": 5, "15": 15, "60": 60}


class ClosedKlineSourceInvalid(RuntimeError):
    """A provider payload conflicts with its authenticated stream topic."""


def decode_confirmed_bybit_klines(
    message: Mapping[str, object], *,
    symbols: frozenset[str],
    intervals: frozenset[str],
    received_at: datetime,
) -> tuple[Candle, ...]:
    """Accept exact provider-closed candles. Return no row for forming bars.

    Bybit kline.end is *inclusive* in milliseconds; canonical closed_at is
    the *exclusive* UTC end. Provider ts and local received_at must confirm
    that boundary. We never infer a close from a forming kline's last price.
    """
    topic = message.get("topic")
    if not isinstance(topic, str) or not topic.startswith("kline."):
        return ()
    parts = topic.split(".")
    if len(parts) != 3:
        raise ClosedKlineSourceInvalid("KLINE_TOPIC_INVALID")
    _, interval, symbol = parts
    if symbol not in symbols or interval not in intervals:
        raise ClosedKlineSourceInvalid("KLINE_SOURCE_TOPIC_UNAUTHORIZED")
    if interval not in _SUPPORTED_MINUTES:
        raise ClosedKlineSourceInvalid("KLINE_INTERVAL_UNSUPPORTED")
    if received_at.tzinfo is None or received_at.utcoffset() != timedelta(0):
        raise ClosedKlineSourceInvalid("KLINE_RECEIVER_CLOCK_NOT_UTC")
    if message.get("type") != "snapshot":
        raise ClosedKlineSourceInvalid("KLINE_UNVERIFIED_FRAME_TYPE")
    source_ms = message.get("ts")
    if type(source_ms) is not int:
        raise ClosedKlineSourceInvalid("KLINE_SOURCE_CLOCK_INVALID")
    observed_ms = int(received_at.timestamp() * 1000)
    if source_ms > observed_ms + 5000 or source_ms < 946684800000:
        raise ClosedKlineSourceInvalid("KLINE_SOURCE_CLOCK_INVALID")
    rows = message.get("data")
    if not isinstance(rows, list):
        raise ClosedKlineSourceInvalid("KLINE_PROVIDER_DATA_INVALID")
    step_ms = _SUPPORTED_MINUTES[interval] * 60000
    result: list[Candle] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ClosedKlineSourceInvalid("KLINE_PROVIDER_ROW_INVALID")
        if type(row.get("confirm")) is not bool:
            raise ClosedKlineSourceInvalid("KLINE_PROVIDER_CLOSE_FLAG_INVALID")
        if not row["confirm"]:
            continue
        try:
            start = row["start"]
            end = row["end"]
            if type(start) is not int or type(end) is not int:
                raise ValueError("start/end must be integer milliseconds")
            if row["interval"] != interval or start % step_ms != 0:
                raise ValueError("interval or UTC boundary differs")
            if end != start + step_ms - 1:
                raise ValueError("provider end is not canonical close-1ms")
            if not 946684800000 <= start < 4102444800000:
                raise ValueError("source UTC timestamp invalid")
            close_ms = start + step_ms
            if source_ms < close_ms or observed_ms < close_ms:
                raise ValueError("closed event arrives before its UTC boundary")
            if start in seen:
                raise ValueError("duplicate close in a single frame")
            seen.add(start)
            values = [
                Decimal(str(row[name]))
                for name in ("open", "high", "low", "close", "volume")
            ]
            bar = Candle(
                symbol=symbol,
                timeframe=interval,
                opened_at=datetime.fromtimestamp(start / 1000, UTC),
                closed_at=datetime.fromtimestamp(close_ms / 1000, UTC),
                open=values[0],
                high=values[1],
                low=values[2],
                close=values[3],
                volume=values[4],
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ClosedKlineSourceInvalid(
                "KLINE_PROVIDER_CLOSED_BAR_INVALID"
            ) from exc
        result.append(bar)
    return tuple(result)
