"""Exact deduplicated public acquisition manifest for one CRIPTA MarketSource.

No implicit default symbols, Strategy version, or permissions. Inputs come
from separately reviewed Entry/Exit/MAYAK/Position required-data manifests.
The same upstream topic is subscribed ONCE per venue/category, no matter how
many internal consumers reference it. REST-only facts cannot masquerade as
WebSocket facts. This pure module performs no network, DB or Exchange writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

Product = Literal["SPOT", "LINEAR"]
Dataset = Literal[
    "PUBLIC_TRADE", "CLOSED_KLINE", "FORMING_KLINE",
    "TICKER", "ORDERBOOK_50", "LIQUIDATION",
    "ACCOUNT_RATIO", "INSTRUMENT_INFO",
]
_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
_CONSUMER = re.compile(r"^[A-Z0-9_:-]{2,90}$")
_VALID = frozenset({
    "PUBLIC_TRADE", "CLOSED_KLINE", "FORMING_KLINE",
    "TICKER", "ORDERBOOK_50", "LIQUIDATION",
    "ACCOUNT_RATIO", "INSTRUMENT_INFO",
})
_REST_ONLY = frozenset({"ACCOUNT_RATIO", "INSTRUMENT_INFO"})
_ALLOWED_INTERVALS = frozenset({"1", "5", "15", "60"})


class SourceManifestInvalid(RuntimeError):
    """Exact source, topic, consumer or capture scope was not established."""


@dataclass(frozen=True, slots=True)
class RequiredPublicFeed:
    consumer_id: str
    market_source_id: str
    instrument_id: str
    product: Product
    symbol: str
    dataset: Dataset
    freshness_seconds: int
    required: bool = True
    timeframe: str = ""
    lookback_bars: int = 0

    def __post_init__(self) -> None:
        if (
            self.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
            or not _CONSUMER.fullmatch(self.consumer_id)
            or not _SYMBOL.fullmatch(self.symbol)
            or self.product not in ("SPOT", "LINEAR")
            or self.dataset not in _VALID
            or self.instrument_id != bybit_instrument_id(
                self.product.lower(), self.symbol,
            )
            or type(self.freshness_seconds) is not int
            or not 1 <= self.freshness_seconds <= 3600
            or type(self.required) is not bool
            or type(self.lookback_bars) is not int
            or not 0 <= self.lookback_bars <= 30000
        ):
            raise SourceManifestInvalid("SOURCE_INPUT_MANIFEST_INVALID")
        if self.dataset in ("CLOSED_KLINE", "FORMING_KLINE"):
            if self.timeframe not in _ALLOWED_INTERVALS:
                raise SourceManifestInvalid("SOURCE_KLINE_TIMEFRAME_INVALID")
        elif self.timeframe or self.lookback_bars:
            raise SourceManifestInvalid("SOURCE_NONKLINE_HORIZON_INVALID")
        if self.dataset == "LIQUIDATION" and self.product != "LINEAR":
            raise SourceManifestInvalid("SOURCE_SPOT_LIQUIDATION_INVALID")


@dataclass(frozen=True, slots=True)
class PhysicalFeedTopic:
    product: Product
    dataset_topic: str
    consumers: tuple[str, ...]
    required: bool
    freshness_seconds: int


@dataclass(frozen=True, slots=True)
class PublicFeedUnion:
    websocket_topics: tuple[PhysicalFeedTopic, ...]
    rest_inputs: tuple[PhysicalFeedTopic, ...]
    consumer_requirements: int

    def websocket_batches(self, product: Product) -> tuple[tuple[str, ...], ...]:
        """Limits are those already proven in MAYAK: spot=10, linear=30."""
        if product not in ("SPOT", "LINEAR"):
            raise SourceManifestInvalid("SOURCE_PRODUCT_INVALID")
        limit = 10 if product == "SPOT" else 30
        topics = [
            x.dataset_topic for x in self.websocket_topics
            if x.product == product
        ]
        return tuple(
            tuple(topics[i:i + limit]) for i in range(0, len(topics), limit)
        )


def _topic(item: RequiredPublicFeed) -> str:
    if item.dataset == "PUBLIC_TRADE":
        return f"publicTrade.{item.symbol}"
    if item.dataset in ("CLOSED_KLINE", "FORMING_KLINE"):
        return f"kline.{item.timeframe}.{item.symbol}"
    if item.dataset == "TICKER":
        return f"tickers.{item.symbol}"
    if item.dataset == "ORDERBOOK_50":
        return f"orderbook.50.{item.symbol}"
    if item.dataset == "LIQUIDATION":
        return f"allLiquidation.{item.symbol}"
    if item.dataset == "ACCOUNT_RATIO":
        return f"REST:market/account-ratio:{item.symbol}"
    if item.dataset == "INSTRUMENT_INFO":
        return f"REST:market/instruments-info:{item.symbol}"
    raise SourceManifestInvalid("SOURCE_FEED_TOPIC_UNKNOWN")


def union_public_feed_requirements(
    requirements: tuple[RequiredPublicFeed, ...],
) -> PublicFeedUnion:
    """Physical subscription union; duplicated consumers do NOT create feeds.

    Different consumers of the SAME kline WS topic (forming vs closed) share
    one transport but keep *different* downstream quality/retention rules.
    Approval/readiness remains per-consumer and is never inferred from topic
    availability.
    """
    if not requirements or len(requirements) > 5000:
        raise SourceManifestInvalid("SOURCE_MANIFEST_SCOPE_INVALID")

    grouped: dict[tuple[Product, str], list[RequiredPublicFeed]] = {}
    for item in requirements:
        key = (item.product, _topic(item))
        grouped.setdefault(key, []).append(item)
    if len(grouped) > 400:
        raise SourceManifestInvalid("SOURCE_UPSTREAM_TOPIC_BUDGET_EXCEEDED")
    ws: list[PhysicalFeedTopic] = []
    rest: list[PhysicalFeedTopic] = []
    for (product, topic), items in sorted(grouped.items()):
        entry = PhysicalFeedTopic(
            product=product,
            dataset_topic=topic,
            consumers=tuple(sorted(set(x.consumer_id for x in items))),
            required=any(x.required for x in items),
            freshness_seconds=min(x.freshness_seconds for x in items),
        )
        if topic.startswith("REST:"):
            rest.append(entry)
        else:
            ws.append(entry)
    return PublicFeedUnion(
        websocket_topics=tuple(ws),
        rest_inputs=tuple(rest),
        consumer_requirements=len(requirements),
    )
