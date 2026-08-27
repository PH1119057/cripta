from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle


def strategy_parameters_fingerprint(parameters: Mapping[str, object]) -> str:
    """Stable strategy parameter identity used by persisted state and intent IDs."""

    payload = json.dumps(
        _json_safe(parameters),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_parameters_fingerprint(value: object) -> str:
    selected = str(value)
    if len(selected) != 64 or any(character not in "0123456789abcdef" for character in selected):
        raise ValueError("strategy state parameter fingerprint must be lowercase sha256")
    return selected


def candles_to_state(candles: Sequence[Candle]) -> list[dict[str, object]]:
    return [
        {
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "opened_at": candle.opened_at.isoformat(),
            "closed_at": candle.closed_at.isoformat(),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
            "is_closed": candle.is_closed,
        }
        for candle in candles
    ]


def candles_from_state(raw: object) -> list[Candle]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("strategy candle state must be a sequence")
    candles: list[Candle] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("strategy candle state item must be an object")
        candles.append(_candle_from_mapping(item))
    return candles


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=str)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _candle_from_mapping(item: Mapping[str, Any]) -> Candle:
    return Candle(
        symbol=str(item["symbol"]),
        timeframe=str(item["timeframe"]),
        opened_at=datetime.fromisoformat(str(item["opened_at"])),
        closed_at=datetime.fromisoformat(str(item["closed_at"])),
        open=Decimal(str(item["open"])),
        high=Decimal(str(item["high"])),
        low=Decimal(str(item["low"])),
        close=Decimal(str(item["close"])),
        volume=Decimal(str(item["volume"])),
        is_closed=bool(item.get("is_closed", True)),
    )
