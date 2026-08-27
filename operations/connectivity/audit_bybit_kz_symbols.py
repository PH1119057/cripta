from __future__ import annotations

import json
import os
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

from private_runtime import api_post
from safety_observer import api_get


SYMBOLS = tuple(
    symbol.strip().upper()
    for symbol in os.environ.get("CRIPTA_SYMBOLS", "").split(",")
    if symbol.strip()
)


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def main() -> None:
    if not SYMBOLS:
        raise SystemExit("CRIPTA_SYMBOLS is empty")
    credential_path = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "bybit-mainnet"
    credentials = json.loads(credential_path.read_text(encoding="utf-8"))
    key, secret = credentials["api_key"], credentials["api_secret"]
    results: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        instrument_payload, _ = api_get(
            "/v5/market/instruments-info", {"category": "linear", "symbol": symbol}
        )
        instruments = (instrument_payload.get("result") or {}).get("list") or []
        if not instruments:
            results.append({"symbol": symbol, "status": "нет в каталоге Bybit KZ"})
            continue
        instrument = instruments[0]
        ticker_payload, _ = api_get(
            "/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )
        ticker = ((ticker_payload.get("result") or {}).get("list") or [{}])[0]
        price = Decimal(str(ticker.get("lastPrice") or 0))
        qty_step = Decimal(str((instrument.get("lotSizeFilter") or {}).get("qtyStep") or 0))
        minimum_qty = Decimal(str((instrument.get("lotSizeFilter") or {}).get("minOrderQty") or 0))
        qty = max(minimum_qty, floor_step(Decimal("10") / price, qty_step))
        order = {
            "category": "linear",
            "symbol": symbol,
            "side": "Buy",
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            "timeInForce": "GTC",
            "positionIdx": 0,
            "reduceOnly": False,
        }
        try:
            payload = api_post("/v5/order/pre-check", order, key, secret)
            results.append({"symbol": symbol, "status": "разрешена", "retCode": payload.get("retCode")})
        except Exception as exc:
            results.append({"symbol": symbol, "status": "не подтверждена", "reason": str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
