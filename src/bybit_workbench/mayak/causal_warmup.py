"""R11 #470: exact causal WARMUP contract for the PG MAYAK consumer.

Wall-clock elapsed time is not readiness.  Every required provider scope must
have an unbroken interval long enough for the real LiveMayakEngine horizon,
and stateful features must contain their own causal baseline.  Quiet trade or
liquidation streams may legitimately contain zero events; their zero is usable
only after provider continuity covers the complete window.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.provider_continuity import (
    R11ProviderContinuityProof,
    R11ProviderScope,
    r11_provider_continuity_proof_matches,
)

_SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
_HORIZON_SECONDS = {
    "PUBLIC_TRADE": 7200,
    "ORDERBOOK_50": 900,
    "TICKER": 3600,
    "LIQUIDATION": 3600,
    "ACCOUNT_RATIO": 0,
    "INSTRUMENT_INFO": 0,
}
WarmupState = Literal["READY", "WARMUP", "UNKNOWN"]
ContinuityKey = tuple[str, str, str]


class R11CausalWarmupUnverified(RuntimeError):
    """Provider interval or engine causal state cannot support READY."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11CausalWarmupUnverified("R11_CAUSAL_WARMUP_" + reason)


@dataclass(frozen=True, slots=True)
class R11WarmupScope:
    provider_scope: R11ProviderScope
    required_seconds: int
    continuous_seconds: float
    engine_coverage_verified: bool
    state: WarmupState
    reason: str


@dataclass(frozen=True, slots=True)
class R11CausalWarmupProof:
    publication_id: int
    source_session_id: str
    release_commit: str
    as_of_second: datetime
    config_fingerprint: str
    scopes: tuple[R11WarmupScope, ...]
    dispatcher_coverage_verified: bool
    causal_warmup_verified: bool
    durable_commit_verified: bool = False
    consumer_ready: bool = False

    def as_status_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "product": row.provider_scope.product,
                "dataset": row.provider_scope.dataset,
                "symbol": row.provider_scope.symbol,
                "required_seconds": row.required_seconds,
                "continuous_seconds": round(row.continuous_seconds, 3),
                "engine_coverage_verified": row.engine_coverage_verified,
                "state": row.state,
                "reason": row.reason,
            }
            for row in self.scopes
        ]


def r11_stream_continuity_starts(
    proof: R11ProviderContinuityProof,
) -> dict[ContinuityKey, datetime]:
    starts: dict[ContinuityKey, datetime] = {}
    for row in proof.scopes:
        if row.scope.coverage != "STREAM":
            continue
        _need(
            row.state == "CONTINUOUS"
            and row.continuous_from is not None,
            "STREAM_CONTINUITY_START_MISSING",
        )
        assert row.continuous_from is not None
        starts[
            row.scope.product, row.scope.dataset, row.scope.symbol
        ] = row.continuous_from
    _need(len(starts) == 42, "STREAM_SCOPE_COUNT_INVALID")
    return starts


def reconcile_r11_engine_continuity(
    engine: LiveMayakEngine, *,
    proof: R11ProviderContinuityProof,
    previous_starts: dict[ContinuityKey, datetime],
) -> dict[ContinuityKey, datetime]:
    """Remove pre-boundary feature state and expose verified PG transport.

    A scoped recovery never inherits rows from the interval before its GAP.
    Transport state is derived from the current proof and is intentionally not
    revived from a durable checkpoint without reading a new publication.
    """
    _need(
        isinstance(engine, LiveMayakEngine)
        and engine.symbols == _SEVEN
        and isinstance(previous_starts, dict),
        "ENGINE_OR_PREVIOUS_SCOPE_INVALID",
    )
    starts = r11_stream_continuity_starts(proof)
    for (product, dataset, symbol), start in starts.items():
        market = product.lower()
        cutoff = start.timestamp()
        previous = previous_starts.get((product, dataset, symbol))
        reset = previous is not None and start > previous
        if dataset == "PUBLIC_TRADE":
            window = engine.trades[market, symbol]
            with window.lock:
                window.rows = deque(
                    row for row in window.rows if row[0] >= cutoff
                )
                if reset:
                    window.revision += 1
                    window.large_threshold_cache = None
                    engine.stamps.pop((market, symbol, "trades"), None)
        elif dataset == "ORDERBOOK_50":
            key = (market, symbol)
            engine.book_history[key] = deque(
                row for row in engine.book_history[key]
                if row[0] >= cutoff
            )
            if reset:
                engine.books.pop(key, None)
                engine.previous_books.pop(key, None)
                engine.stamps.pop((market, symbol, "book"), None)
        elif dataset == "TICKER":
            engine.ticker_history[symbol] = deque(
                row for row in engine.ticker_history[symbol]
                if row[0] >= cutoff
            )
            engine.funding_history[symbol] = deque(
                row for row in engine.funding_history[symbol]
                if row[0] >= cutoff
            )
            if reset:
                engine.tickers.pop(symbol, None)
                engine.stamps.pop(("linear", symbol, "ticker"), None)
        elif dataset == "LIQUIDATION":
            with engine.liquidations.lock:
                engine.liquidations.rows = deque(
                    row for row in engine.liquidations.rows
                    if row[1] != symbol or row[0] >= cutoff
                )

    for product, market in (("SPOT", "spot"), ("LINEAR", "linear")):
        product_rows = tuple(
            row for row in proof.scopes
            if row.scope.product == product
            and row.scope.coverage == "STREAM"
        )
        _need(
            bool(product_rows)
            and all(row.continuous_from is not None for row in product_rows),
            "PRODUCT_STREAM_CONTINUITY_INCOMPLETE",
        )
        connected_since = max(
            row.continuous_from for row in product_rows
            if row.continuous_from is not None
        )
        engine.transport[market] = {
            "connected": True,
            "observed_at": proof.as_of_second.timestamp(),
            "connected_since": connected_since.timestamp(),
            "error": None,
        }
    return starts


def _book_ready(
    engine: LiveMayakEngine, *, market: str, symbol: str, now: float,
) -> bool:
    rows = engine.book_history[market, symbol]
    stamp = engine.stamps[market, symbol, "book"].observed_at
    return (
        (market, symbol) in engine.books
        and len(rows) >= 2
        and rows[0][0] <= now - 900
        and rows[-1][0] <= now
        and stamp is not None
        and 0 <= now - stamp <= 8
    )


def _ticker_ready(
    engine: LiveMayakEngine, *, symbol: str, now: float,
) -> bool:
    ticker = engine.tickers.get(symbol) or {}
    required = (
        "open_interest", "open_interest_value", "funding_rate",
        "last_price", "mark_price", "index_price",
        "long_ratio", "short_ratio",
    )
    oi = engine.ticker_history[symbol]
    funding = engine.funding_history[symbol]
    stamp = engine.stamps["linear", symbol, "ticker"].observed_at
    return (
        all(ticker.get(name) is not None for name in required)
        and bool(oi) and oi[0][0] <= now - 3600
        and bool(funding)
        and stamp is not None
        and 0 <= now - stamp <= 45
    )


def _engine_scope_ready(
    engine: LiveMayakEngine, scope: R11ProviderScope, *, now: float,
) -> bool:
    market = scope.product.lower()
    symbol = scope.symbol
    if scope.dataset == "PUBLIC_TRADE":
        rows = engine.trades[market, symbol].frozen_view().rows
        return (
            any(now - 3600 < row[0] <= now for row in rows)
            and any(now - 7200 < row[0] <= now - 3600 for row in rows)
        )
    if scope.dataset == "ORDERBOOK_50":
        return _book_ready(engine, market=market, symbol=symbol, now=now)
    if scope.dataset == "TICKER":
        return _ticker_ready(engine, symbol=symbol, now=now)
    if scope.dataset == "LIQUIDATION":
        return True  # continuous empty provider window is a causal zero
    if scope.dataset == "ACCOUNT_RATIO":
        ticker = engine.tickers.get(symbol) or {}
        return (
            ticker.get("long_ratio") is not None
            and ticker.get("short_ratio") is not None
        )
    if scope.dataset == "INSTRUMENT_INFO":
        return engine.instrument_support[market] is not None
    return False


def _dispatcher_ready(
    engine: LiveMayakEngine, snapshot: dict[str, Any], *,
    fingerprint: str,
) -> bool:
    handoff = snapshot.get("dispatcher_handoff")
    contexts = snapshot.get("coin_market_contexts")
    coins = snapshot.get("coins")
    return (
        isinstance(handoff, dict)
        and isinstance(contexts, dict) and set(contexts) == set(_SEVEN)
        and isinstance(coins, dict) and set(coins) == set(_SEVEN)
        and snapshot.get("config_fingerprint") == fingerprint
        and handoff.get("provenance", {}).get("config_fingerprint")
        == fingerprint
        and handoff.get("provenance", {}).get("trading_command") is False
        and all(
            row.get("provenance", {}).get("trading_command") is False
            and row.get("config_fingerprint") == fingerprint
            for row in contexts.values()
        )
        and set(engine.symbols) == set(_SEVEN)
    )


def evaluate_r11_causal_warmup(
    engine: LiveMayakEngine, *,
    provider_proof: R11ProviderContinuityProof,
    snapshot: dict[str, Any],
    now: datetime,
) -> R11CausalWarmupProof:
    """Evaluate all 63 provider scopes against real engine state/windows."""
    _need(
        isinstance(engine, LiveMayakEngine)
        and engine.symbols == _SEVEN
        and isinstance(snapshot, dict)
        and isinstance(now, datetime)
        and now.tzinfo is not None
        and now.utcoffset() == timedelta(0)
        and now >= provider_proof.as_of_second
        and r11_provider_continuity_proof_matches(
            provider_proof,
            publication_id=provider_proof.publication_id,
            source_session_id=provider_proof.source_session_id,
            release_commit=provider_proof.release_commit,
            as_of_second=provider_proof.as_of_second,
        ),
        "PROVIDER_PROOF_OR_CLOCK_INVALID",
    )
    at = now.astimezone(UTC)
    now_ts = at.timestamp()
    rows: list[R11WarmupScope] = []
    for continuity in provider_proof.scopes:
        scope = continuity.scope
        _need(
            continuity.continuous_from is not None
            and continuity.observed_through is not None,
            "CONTINUITY_INTERVAL_MISSING",
        )
        assert continuity.continuous_from is not None
        horizon = _HORIZON_SECONDS[scope.dataset]
        continuous = max(
            0.0,
            (provider_proof.as_of_second
             - continuity.continuous_from).total_seconds(),
        )
        engine_ok = _engine_scope_ready(
            engine, scope, now=now_ts,
        )
        ready = continuous >= horizon and engine_ok
        rows.append(R11WarmupScope(
            provider_scope=scope,
            required_seconds=horizon,
            continuous_seconds=continuous,
            engine_coverage_verified=engine_ok,
            state="READY" if ready else "WARMUP",
            reason=("CAUSAL_WINDOW_COMPLETE" if ready
                    else "CONTINUITY_OR_ENGINE_BASELINE_INCOMPLETE"),
        ))
    fingerprint = engine.configuration_fingerprint()
    dispatcher = _dispatcher_ready(
        engine, snapshot, fingerprint=fingerprint,
    )
    verified = (
        len(rows) == 63
        and all(row.state == "READY" for row in rows)
        and dispatcher
        and len(engine.history) >= 2
    )
    return R11CausalWarmupProof(
        publication_id=provider_proof.publication_id,
        source_session_id=provider_proof.source_session_id,
        release_commit=provider_proof.release_commit,
        as_of_second=provider_proof.as_of_second,
        config_fingerprint=fingerprint,
        scopes=tuple(rows),
        dispatcher_coverage_verified=dispatcher,
        causal_warmup_verified=verified,
    )
