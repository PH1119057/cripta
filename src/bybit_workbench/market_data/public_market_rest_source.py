"""Bybit Kazakhstan PUBLIC market REST evidence for the ONE MarketSource.

Fetchers have no credentials, account controls, Strategy state or DB rights.
They return small typed facts with separate exchange observation time (ratio)
and response receipt time. They never silently substitute the last good
response when an upstream call is incomplete or fails.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

Product = Literal["SPOT", "LINEAR"]
Dataset = Literal["ACCOUNT_RATIO", "INSTRUMENT_INFO"]
_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
_ENDPOINT = {
    "ACCOUNT_RATIO": "account-ratio",
    "INSTRUMENT_INFO": "instruments-info",
}
_REST_URL = "https://api.bybit.kz/v5/market/"
_ALLOWED_STATES = frozenset({
    "Trading", "PreLaunch", "PendingOpen", "Settling", "Delivering", "Closed",
})


class PublicMarketRestInvalid(RuntimeError):
    """Missing, misleading, or noncanonical public REST observation."""


@dataclass(frozen=True, slots=True)
class PublicMarketRestFact:
    market_source_id: str
    instrument_id: str
    product: Product
    symbol: str
    dataset: Dataset
    source_at: datetime
    received_at: datetime
    values: dict[str, str]
    interval: str = ""

    def __post_init__(self) -> None:
        if (
            self.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
            or self.product not in ("SPOT", "LINEAR")
            or _SYMBOL.fullmatch(self.symbol) is None
            or self.instrument_id != bybit_instrument_id(
                self.product.lower(), self.symbol,
            )
            or self.dataset not in _ENDPOINT
            or self.source_at.tzinfo is None
            or self.source_at.utcoffset() != timedelta(0)
            or self.received_at.tzinfo is None
            or self.received_at.utcoffset() != timedelta(0)
            or self.source_at > self.received_at + timedelta(seconds=5)
            or self.interval not in ("", "5min")
        ):
            raise PublicMarketRestInvalid("PUBLIC_REST_FACT_IDENTITY_INVALID")


def _number(value: object, *, allow_zero: bool = True) -> str:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise PublicMarketRestInvalid("PUBLIC_REST_DECIMAL_INVALID")
    try:
        d = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise PublicMarketRestInvalid("PUBLIC_REST_DECIMAL_INVALID") from exc
    if not d.is_finite() or d < 0 or (d == 0 and not allow_zero):
        raise PublicMarketRestInvalid("PUBLIC_REST_DECIMAL_INVALID")
    return str(d)


def _time_ms(value: object, observed_at: datetime) -> datetime:
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise PublicMarketRestInvalid("PUBLIC_REST_PROVIDER_TIME_INVALID")
    s = str(value)
    if not s.isdecimal():
        raise PublicMarketRestInvalid("PUBLIC_REST_PROVIDER_TIME_INVALID")
    n = int(s)
    if not 946684800000 <= n < 4102444800000:
        raise PublicMarketRestInvalid("PUBLIC_REST_PROVIDER_TIME_INVALID")
    at = datetime.fromtimestamp(n / 1000, UTC)
    if at > observed_at + timedelta(seconds=5):
        raise PublicMarketRestInvalid("PUBLIC_REST_PROVIDER_FUTURE_TIME")
    return at


def _result(
    raw: Mapping[str, object], *, category: Product,
) -> Mapping[str, object]:
    if raw.get("retCode") != 0:
        raise PublicMarketRestInvalid("PUBLIC_REST_PROVIDER_NOT_OK")
    data = raw.get("result")
    if not isinstance(data, dict):
        raise PublicMarketRestInvalid("PUBLIC_REST_RESULT_INVALID")
    actual_category = data.get("category")
    if actual_category is not None and actual_category != category.lower():
        raise PublicMarketRestInvalid("PUBLIC_REST_CATEGORY_MISMATCH")
    return data


def decode_public_rest_response(
    payload: Mapping[str, object], *,
    dataset: Dataset,
    product: Product,
    symbol: str,
    received_at: datetime,
) -> PublicMarketRestFact:
    """Validate the *exact symbol query*, never a truncated bulk catalog."""
    if (
        dataset not in _ENDPOINT
        or product not in ("SPOT", "LINEAR")
        or _SYMBOL.fullmatch(symbol) is None
        or received_at.tzinfo is None
        or received_at.utcoffset() != timedelta(0)
    ):
        raise PublicMarketRestInvalid("PUBLIC_REST_REQUEST_IDENTITY_INVALID")
    if dataset == "ACCOUNT_RATIO" and product != "LINEAR":
        raise PublicMarketRestInvalid("PUBLIC_REST_RATIO_CATEGORY_INVALID")
    data = _result(payload, category=product)
    items = data.get("list")
    if not isinstance(items, list) or len(items) > 1:
        raise PublicMarketRestInvalid("PUBLIC_REST_EXACT_SYMBOL_RESULT_INVALID")

    values: dict[str, str]
    if dataset == "ACCOUNT_RATIO":
        if len(items) != 1 or not isinstance(items[0], dict):
            raise PublicMarketRestInvalid("PUBLIC_REST_RATIO_MISSING")
        row = items[0]
        if row.get("symbol") != symbol:
            raise PublicMarketRestInvalid("PUBLIC_REST_RATIO_SYMBOL_INVALID")
        buy = _number(row.get("buyRatio"))
        sell = _number(row.get("sellRatio"))
        if (
            Decimal(buy) > 1 or Decimal(sell) > 1
            or abs(Decimal(buy) + Decimal(sell) - 1) > Decimal("0.02")
        ):
            raise PublicMarketRestInvalid("PUBLIC_REST_RATIO_SUM_INVALID")
        source_at = _time_ms(row.get("timestamp"), received_at)
        values = {"long_account_ratio": buy, "short_account_ratio": sell}
        interval = "5min"
    else:
        source_at = received_at
        interval = ""
        if not items:
            # This is exact query evidence of absence, not permission to
            # synthesize a healthy trading instrument.
            values = {"status": "NOT_LISTED"}
        else:
            row = items[0]
            if not isinstance(row, dict) or row.get("symbol") != symbol:
                raise PublicMarketRestInvalid(
                    "PUBLIC_REST_INSTRUMENT_SYMBOL_INVALID"
                )
            status = row.get("status")
            if status not in _ALLOWED_STATES:
                raise PublicMarketRestInvalid(
                    "PUBLIC_REST_INSTRUMENT_STATUS_INVALID"
                )
            values = {"status": str(status)}
            price_filter = row.get("priceFilter")
            lot_filter = row.get("lotSizeFilter")
            if isinstance(price_filter, dict) and "tickSize" in price_filter:
                values["tick_size"] = _number(
                    price_filter["tickSize"], allow_zero=False,
                )
            if isinstance(lot_filter, dict) and "qtyStep" in lot_filter:
                values["qty_step"] = _number(
                    lot_filter["qtyStep"], allow_zero=False,
                )
    return PublicMarketRestFact(
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id(product.lower(), symbol),
        product=product, symbol=symbol, dataset=dataset,
        source_at=source_at, received_at=received_at,
        values=values, interval=interval,
    )


def fetch_public_market_rest(
    *, dataset: Dataset, product: Product, symbol: str,
    received_at: Callable[[], datetime] = lambda: datetime.now(UTC),
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> PublicMarketRestFact:
    """Fixed host + endpoint, exact symbol and bounded response; no keys/tokens.

    The source owner schedules this in a worker *inside* its process. It
    never blocks the original websocket tape writer and cannot change
    private Exchange state.
    """
    if (
        dataset not in _ENDPOINT
        or product not in ("SPOT", "LINEAR")
        or not _SYMBOL.fullmatch(symbol)
        or (dataset == "ACCOUNT_RATIO" and product != "LINEAR")
    ):
        raise PublicMarketRestInvalid("PUBLIC_REST_REQUEST_UNAUTHORIZED")
    params: dict[str, str | int] = {
        "category": product.lower(), "symbol": symbol,
    }
    if dataset == "ACCOUNT_RATIO":
        params.update(period="5min", limit=1)
    url = _REST_URL + _ENDPOINT[dataset] + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url, headers={"User-Agent": "Cripta-MarketSource-public-only"},
        method="GET",
    )
    try:
        with opener(request, timeout=8.0) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise PublicMarketRestInvalid("PUBLIC_REST_RESPONSE_TOO_LARGE")
        decoded = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PublicMarketRestInvalid(
            "PUBLIC_REST_UNAVAILABLE_OR_UNPARSEABLE"
        ) from exc
    if not isinstance(decoded, dict):
        raise PublicMarketRestInvalid("PUBLIC_REST_JSON_NOT_OBJECT")
    return decode_public_rest_response(
        decoded, dataset=dataset, product=product, symbol=symbol,
        received_at=received_at(),
    )
