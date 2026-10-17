from __future__ import annotations

BYBIT_VENUE_ID = "venue-bybit"
BYBIT_PUBLIC_MARKET_SOURCE_ID = "ms-bybit-mainnet-public-v1"


def bybit_instrument_id(market: str, symbol: str) -> str:
    normalized_market = market.strip().lower()
    normalized_symbol = symbol.strip().upper()
    if normalized_market not in {"spot", "linear"}:
        raise ValueError(f"unsupported Bybit market category: {market}")
    if not normalized_symbol or not normalized_symbol.endswith("USDT"):
        raise ValueError(f"unsupported Bybit instrument symbol: {symbol}")
    return f"inst-bybit-{normalized_market}-{normalized_symbol.lower()}"


def bybit_instrument_lineage(symbol: str) -> dict[str, str]:
    return {
        "spot": bybit_instrument_id("spot", symbol),
        "linear": bybit_instrument_id("linear", symbol),
    }
