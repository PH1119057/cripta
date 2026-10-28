"""Strict REST closed-bar warmup plan for an explicitly offline P90 seed.

Read-only pure validation. This is NOT an activation, a source-cursor ACK,
or a substitute for ordered WS publicTrade/bar boundary proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Mapping

from bybit_workbench.domain.models import Candle

from .p90l90_causal import P90L90EntryState, causal_entry_state

_SYMBOLS = frozenset({"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"})


@dataclass(frozen=True, slots=True)
class P90L90OfflineSeedPlan:
    symbol: str
    closed_5m_at: datetime
    closed_15m_at: datetime
    fives: tuple[Candle, ...]
    fifteens: tuple[Candle, ...]
    initial_geometry: P90L90EntryState


def plan_p90l90_offline_seed(
    symbol: str,
    history: Mapping[str, tuple[Candle, ...]],
) -> P90L90OfflineSeedPlan:
    """Reject incomplete causal PREV7D+Wilder ATR200 instead of a fake seed."""
    if symbol not in _SYMBOLS:
        raise ValueError("P90L90_BOOTSTRAP_UNKNOWN_SYMBOL")
    five, fifteen = tuple(history.get("5", ())), tuple(history.get("15", ()))
    if not (2216 <= len(five) <= 2800 and 200 <= len(fifteen) <= 1000):
        raise RuntimeError("P90L90_BOOTSTRAP_SOURCE_WARMUP_NOT_READY")
    at = five[-1].closed_at
    if at.tzinfo is None or at.utcoffset() is None:
        raise RuntimeError("P90L90_BOOTSTRAP_NON_UTC_CANDLE")
    at = at.astimezone(UTC)
    boundary15 = at.replace(minute=at.minute // 15 * 15, second=0, microsecond=0)
    if (
        fifteen[-1].closed_at.astimezone(UTC) != boundary15
        or five[-1].closed_at.astimezone(UTC) != at
        or any(candle.symbol != symbol for candle in five + fifteen)
    ):
        raise RuntimeError("P90L90_BOOTSTRAP_BAR_WATERMARK_OR_SYMBOL_MISMATCH")
    geometry = causal_entry_state(five, fifteen, effective_at=at)
    if geometry is None or geometry.effective_at != at:
        raise RuntimeError("P90L90_BOOTSTRAP_CAUSAL_GEOMETRY_NOT_READY")
    return P90L90OfflineSeedPlan(
        symbol=symbol, closed_5m_at=at, closed_15m_at=boundary15,
        fives=five, fifteens=fifteen, initial_geometry=geometry,
    )
