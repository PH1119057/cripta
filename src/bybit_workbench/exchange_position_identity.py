"""Canonical account-scoped Bybit LINEAR physical position key.

Pure identity helper; not an exchange admission, order or hedge permission.
"""
from __future__ import annotations


def exchange_position_key(
    trading_account_id: str,
    symbol: str,
    position_idx: int,
) -> str:
    if position_idx < 0:
        raise ValueError("position_idx cannot be negative")
    account = trading_account_id.strip()
    if not account:
        raise ValueError("trading_account_id is required")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return f"BYBIT:{account}:LINEAR:USDT:{normalized}:{position_idx}"
