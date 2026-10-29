"""Exact P90 REAL dynamic opposite RAW L5-3 from closed 36×5m candles.

The market source must independently guarantee immutable source identity,
committed candle delivery and gap-free as-of visibility. This projection
rejects an incomplete, misordered or future candle window; it cannot
reconstruct absent OHLC from publicTrade or PAPER geometry.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import ALLOWED_SYMBOLS

_SPAN = timedelta(minutes=5)
_REQUIRED = 36


class P90RealClosedGeometryUnverified(ValueError):
    """The causal closed RAW 5m geometry does not support a REAL Exit."""


def opposite_raw_l53_from_closed_fives(
    *,
    symbol: str,
    direction: TradeDirection,
    closed_at: datetime,
    candles: Sequence[Candle],
) -> Decimal:
    """Return original opposite RAW 36-bar boundary, never a heuristic TP."""
    if (
        symbol not in frozenset.union(*ALLOWED_SYMBOLS.values())
        or closed_at.tzinfo is None
        or len(candles) != _REQUIRED
        or direction not in {TradeDirection.LONG, TradeDirection.SHORT}
    ):
        raise P90RealClosedGeometryUnverified("P90_REAL_RAW36_SCOPE_OR_WINDOW_INVALID")
    latest = closed_at.astimezone(UTC)
    if latest.second != 0 or latest.microsecond != 0 or latest.minute % 5 != 0:
        raise P90RealClosedGeometryUnverified("P90_REAL_RAW36_BOUNDARY_INVALID")
    for index, candle in enumerate(candles):
        boundary = latest - _SPAN * (_REQUIRED - 1 - index)
        if (
            candle.symbol != symbol
            or candle.timeframe != "5"
            or not candle.is_closed
            or candle.opened_at.astimezone(UTC) != boundary - _SPAN
            or candle.closed_at.astimezone(UTC) != boundary
            or any(
                not value.is_finite() or value <= 0
                for value in (candle.open, candle.high, candle.low, candle.close)
            )
            or candle.high < max(candle.open, candle.close)
            or candle.low > min(candle.open, candle.close)
            or candle.low > candle.high
        ):
            raise P90RealClosedGeometryUnverified(
                "P90_REAL_RAW36_GAPPED_OR_UNVERIFIED_CANDLES"
            )
    if direction is TradeDirection.LONG:
        return max(c.high for c in candles)
    return min(c.low for c in candles)
