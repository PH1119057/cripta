from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from bybit_workbench.mayak.core.live import LiveMayakEngine

EventKind = Literal["TRADE", "TICKER", "ORDERBOOK", "LIQUIDATION", "TRANSPORT"]


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_at: float
    kind: EventKind
    symbol: str | None = None
    market: str | None = None
    payload: dict[str, Any] | None = None


class CausalMayakReplay:
    """Chronological adapter over the production MAYAK feature engine.

    It deliberately contains no feature formulas. The only responsibility here is
    event ordering and routing into ``LiveMayakEngine``.
    """

    def __init__(self, symbols: tuple[str, ...], *, exact_liquidations: bool = False) -> None:
        self.engine = LiveMayakEngine(symbols, exact_liquidations=exact_liquidations)
        self.last_event_at: float | None = None
        self.last_snapshot_at: float | None = None

    def set_supported(self, market: str, symbols: set[str]) -> None:
        self.engine.set_instrument_support(market, symbols)

    def _check_event_time(self, event_at: float) -> None:
        if self.last_event_at is not None and event_at < self.last_event_at:
            raise ValueError(
                f"MAYAK_REPLAY_OUT_OF_ORDER event_at={event_at} "
                f"last_event_at={self.last_event_at}"
            )
        if self.last_snapshot_at is not None and event_at < self.last_snapshot_at:
            raise ValueError(
                f"MAYAK_REPLAY_EVENT_BEFORE_SNAPSHOT event_at={event_at} "
                f"last_snapshot_at={self.last_snapshot_at}"
            )

    def feed_trade(
        self,
        event_at: float,
        symbol: str,
        market: str,
        side: str,
        price: float,
        size: float,
    ) -> None:
        """Fast chronological route for large exact public-trade archives."""
        self._check_event_time(event_at)
        self.engine.on_trade(market, symbol, event_at, side, price, size)
        self.last_event_at = event_at

    def feed(self, event: MarketEvent) -> None:
        if event.kind == "TRADE":
            if event.market is None or event.symbol is None:
                raise ValueError("TRADE event requires market and symbol")
            payload = event.payload or {}
            self.feed_trade(
                event.event_at,
                event.symbol,
                event.market,
                str(payload["side"]),
                float(payload["price"]),
                float(payload["size"]),
            )
            return
        self._check_event_time(event.event_at)
        payload = event.payload or {}
        if event.kind == "TRANSPORT":
            if event.market is None:
                raise ValueError("TRANSPORT event requires market")
            self.engine.on_transport(
                event.market,
                connected=bool(payload.get("connected", True)),
                timestamp=event.event_at,
                error=str(payload["error"]) if payload.get("error") is not None else None,
            )
        elif event.kind == "TICKER":
            if event.symbol is None:
                raise ValueError("TICKER event requires symbol")
            numeric = {key: float(value) for key, value in payload.items() if value is not None}
            self.engine.on_ticker(event.symbol, event.event_at, **numeric)
        elif event.kind == "ORDERBOOK":
            if event.market is None or event.symbol is None:
                raise ValueError("ORDERBOOK event requires market and symbol")
            self.engine.on_book(
                event.market,
                event.symbol,
                event.event_at,
                [(float(p), float(q)) for p, q in payload.get("bids", [])],
                [(float(p), float(q)) for p, q in payload.get("asks", [])],
            )
        elif event.kind == "LIQUIDATION":
            if event.symbol is None:
                raise ValueError("LIQUIDATION event requires symbol")
            self.engine.on_liquidation(
                event.symbol,
                event.event_at,
                str(payload["side"]),
                float(payload["price"]),
                float(payload["size"]),
            )
        else:  # pragma: no cover - Literal makes this unreachable for typed callers.
            raise ValueError(f"unknown MAYAK replay event kind: {event.kind}")
        self.last_event_at = event.event_at

    def snapshot(self, at: float) -> dict[str, Any]:
        if self.last_event_at is not None and at < self.last_event_at:
            raise ValueError(
                f"MAYAK_REPLAY_SNAPSHOT_BEFORE_EVENT snapshot_at={at} "
                f"last_event_at={self.last_event_at}"
            )
        if self.last_snapshot_at is not None and at < self.last_snapshot_at:
            raise ValueError(
                f"MAYAK_REPLAY_SNAPSHOT_OUT_OF_ORDER snapshot_at={at} "
                f"last_snapshot_at={self.last_snapshot_at}"
            )
        self.last_snapshot_at = at
        return self.engine.snapshot(datetime.fromtimestamp(at, UTC))
