from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bybit_workbench.domain.models import Candle

from .validation import HistoricalDataset


@dataclass(frozen=True, slots=True)
class FundingEvent:
    symbol: str
    occurred_at: datetime
    rate: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("funding symbol is required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("funding timestamp must be timezone-aware")
        if not self.rate.is_finite():
            raise ValueError("funding rate must be finite")
        if not self.mark_price.is_finite() or self.mark_price <= 0:
            raise ValueError("funding mark price must be finite and positive")


@dataclass(frozen=True, slots=True)
class HistoricalDataQuality:
    trade_ohlcv_complete: bool
    mark_price_complete: bool
    funding_complete: bool
    flags: tuple[str, ...]
    trade_fingerprint: str
    mark_fingerprint: str | None
    funding_fingerprint: str | None

    @property
    def production_equivalent(self) -> bool:
        return self.trade_ohlcv_complete and self.mark_price_complete and self.funding_complete


@dataclass(frozen=True, slots=True)
class HistoricalMarketData:
    trade: HistoricalDataset
    mark_candles: tuple[Candle, ...] = ()
    funding_events: tuple[FundingEvent, ...] = ()
    mark_price_complete: bool = False
    funding_complete: bool = False

    def __post_init__(self) -> None:
        if self.mark_price_complete and not self.mark_candles:
            raise ValueError("complete Mark Price series cannot be empty")
        previous_funding: datetime | None = None
        for event in self.funding_events:
            if event.symbol != self.trade.symbol:
                raise ValueError("funding symbol differs from trade series")
            if previous_funding is not None and event.occurred_at <= previous_funding:
                raise ValueError("funding events must be strictly chronological")
            if not (self.trade.started_at <= event.occurred_at <= self.trade.ended_at):
                raise ValueError("funding event falls outside the trade dataset")
            previous_funding = event.occurred_at
        if self.mark_candles:
            if len(self.mark_candles) != len(self.trade.candles):
                raise ValueError("Mark Price and trade series lengths differ")
            for trade, mark in zip(self.trade.candles, self.mark_candles, strict=True):
                if mark.symbol != trade.symbol or mark.timeframe != trade.timeframe:
                    raise ValueError("Mark Price metadata differs from trade series")
                if mark.opened_at != trade.opened_at or mark.closed_at != trade.closed_at:
                    raise ValueError("Mark Price timeline is not aligned with trade series")

    @property
    def quality(self) -> HistoricalDataQuality:
        contiguous = all(
            current.opened_at == previous.closed_at
            for previous, current in zip(
                self.trade.candles,
                self.trade.candles[1:],
                strict=False,
            )
        )
        flags: list[str] = []
        if not contiguous:
            flags.append("trade_ohlcv_has_gaps")
        if not self.mark_price_complete:
            flags.append("mark_price_missing_or_incomplete")
        if not self.funding_complete:
            flags.append("funding_missing_or_incomplete")
        return HistoricalDataQuality(
            contiguous,
            self.mark_price_complete,
            self.funding_complete,
            tuple(flags),
            self.trade.fingerprint,
            None if not self.mark_candles else _candles_fingerprint(self.mark_candles),
            None if not self.funding_complete else _funding_fingerprint(self.funding_events),
        )

    @property
    def fingerprint(self) -> str:
        quality = self.quality
        payload = ":".join(
            (
                quality.trade_fingerprint,
                quality.mark_fingerprint or "missing-mark",
                quality.funding_fingerprint or "missing-funding",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def mark_for(self, index: int) -> Candle | None:
        return None if not self.mark_candles else self.mark_candles[index]

    def slice_for(self, dataset: HistoricalDataset) -> HistoricalMarketData:
        if dataset.symbol != self.trade.symbol or dataset.timeframe != self.trade.timeframe:
            raise ValueError("market data slice metadata differs from source")
        source_times = {item.opened_at for item in self.trade.candles}
        if any(item.opened_at not in source_times for item in dataset.candles):
            raise ValueError("market data slice is not a subset of the source trade series")
        marks: tuple[Candle, ...] = ()
        if self.mark_candles:
            by_time = {item.opened_at: item for item in self.mark_candles}
            try:
                marks = tuple(by_time[item.opened_at] for item in dataset.candles)
            except KeyError as exc:
                raise ValueError("Mark Price series does not cover requested slice") from exc
        funding = tuple(
            item
            for item in self.funding_events
            if dataset.started_at <= item.occurred_at <= dataset.ended_at
        )
        return HistoricalMarketData(
            dataset,
            marks,
            funding,
            mark_price_complete=self.mark_price_complete,
            funding_complete=self.funding_complete,
        )


def _candles_fingerprint(candles: tuple[Candle, ...]) -> str:
    digest = hashlib.sha256()
    for candle in candles:
        digest.update(
            "|".join(
                (
                    candle.symbol,
                    candle.timeframe,
                    candle.opened_at.isoformat(),
                    candle.closed_at.isoformat(),
                    str(candle.open),
                    str(candle.high),
                    str(candle.low),
                    str(candle.close),
                )
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _funding_fingerprint(events: tuple[FundingEvent, ...]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(
            "|".join(
                (
                    event.symbol,
                    event.occurred_at.isoformat(),
                    str(event.rate),
                    str(event.mark_price),
                )
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()
