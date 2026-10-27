"""Account-scoped position observation, not Exit policy or trade execution.

MFE/MAE are explicitly sampled extrema, never exact intrabar or full-life proof.
A partial historical observation cannot become an executable Exit decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class PositionSample:
    trading_account_id: str
    position_id: str
    symbol: str
    position_idx: int
    side: str
    entry_price: Decimal
    mark_price: Decimal
    return_pct: Decimal
    sampled_mfe_pct: Decimal
    sampled_mae_pct: Decimal
    completeness: str


def build_sample(
    *,
    trading_account_id: str,
    position_id: str,
    symbol: str,
    position_idx: int,
    side: str,
    entry_price: object,
    mark_price: object,
    previous_mfe: object | None,
    previous_mae: object | None,
) -> PositionSample:
    if not trading_account_id or not position_id or not symbol:
        raise ValueError("ACCOUNT_POSITION_IDENTITY_MISSING")
    if type(position_idx) is not int or position_idx not in (0, 1, 2):
        raise ValueError("ACCOUNT_POSITION_SLOT_INVALID")
    if side not in ("Buy", "Sell"):
        raise ValueError("ACCOUNT_POSITION_SIDE_INVALID")
    try:
        entry = Decimal(str(entry_price))
        mark = Decimal(str(mark_price))
        old_mfe = None if previous_mfe is None else Decimal(str(previous_mfe))
        old_mae = None if previous_mae is None else Decimal(str(previous_mae))
    except InvalidOperation as exc:
        raise ValueError("ACCOUNT_POSITION_PRICE_INVALID") from exc
    if not entry.is_finite() or entry <= 0 or not mark.is_finite() or mark <= 0:
        raise ValueError("ACCOUNT_POSITION_PRICE_INVALID")
    if old_mfe is not None and (not old_mfe.is_finite() or old_mfe < 0):
        raise ValueError("ACCOUNT_POSITION_MFE_INVALID")
    if old_mae is not None and (not old_mae.is_finite() or old_mae > 0):
        raise ValueError("ACCOUNT_POSITION_MAE_INVALID")
    directional = (
        (mark - entry) / entry * Decimal(100)
        if side == "Buy" else (entry - mark) / entry * Decimal(100)
    )
    mfe = max(Decimal(0), directional, old_mfe or Decimal(0))
    mae = min(Decimal(0), directional, old_mae or Decimal(0))
    return PositionSample(
        trading_account_id, position_id, symbol, position_idx, side,
        entry, mark, directional, mfe, mae, "SAMPLED_PARTIAL",
    )
