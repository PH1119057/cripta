"""Bounded cold-start preparation. Never grants readiness or trading rights."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.candle_warmup import cache_approved_bybit_history
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID, bybit_instrument_id


def confirm_rest_closed_tail(
    tail: Candle, *, fetch: Callable[[], tuple[Candle, ...]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """A boundary-time REST value is provisional until independent readback.

    Five seconds is an adapter settlement delay, never a Strategy filter or
    a replacement for live WS confirm. Differing uncommitted values WAIT;
    differing committed immutable facts still fail closed in the store.
    """
    delay = max(0.0, 5.0 - (clock() - tail.closed_at).total_seconds())
    sleep(max(1.0, delay))
    confirmed = fetch()
    if confirmed != (tail,):
        raise PreparationWaiting(
            "CANDLE_REST_TAIL_NOT_FINAL", symbol=tail.symbol,
            timeframe=tail.timeframe, expected=tail.closed_at.isoformat(),
        )


def preparation_retry_request(
    connection: Any, *, release_commit: str, blocked_at: datetime,
) -> bool:
    """Only a new authenticated coordinator operation resets a cold budget."""
    return connection.execute(
        """SELECT request_id FROM control.real_readiness_operations
            WHERE scope_key='P90_REAL_V3' AND release_commit=%s
              AND created_at > %s
              AND state IN ('REQUESTED','CHECKING','STARTING_SERVICES',
                            'RECONCILING','VALIDATING')
            ORDER BY created_at DESC LIMIT 1""",
        (release_commit, blocked_at),
    ).fetchone() is not None


class PreparationWaiting(Exception):
    """Expected missing tail before a new source epoch, not a runtime fault."""

    def __init__(self, reason: str, **detail: object) -> None:
        super().__init__(reason)
        self.detail = detail


def cold_preparation_wait(exc: RuntimeError) -> PreparationWaiting:
    """Use ONLY at cold-source proof boundaries, never in live fact dispatch."""
    if str(exc) not in {
        "CANDLE_WARMUP_END_NOT_PROVEN",
        "P90_REAL_FLAT_ACCOUNT_GENERATION_WAIT_TIMEOUT",
        "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_RETRY_BUDGET_EXPIRED",
    }:
        raise exc
    return PreparationWaiting(str(exc), **getattr(exc, "detail", {}))


@dataclass
class PreparationBudget:
    max_attempts: int = 5
    timeout_seconds: float = 300
    started: float | None = None
    attempts: int = 0

    def begin(self, *, now: float) -> None:
        if self.started is None:
            self.started = now

    def remaining(self, *, now: float) -> float:
        started = self.started if self.started is not None else now
        return max(0.0, self.timeout_seconds - (now-started))

    def reset(self) -> None:
        self.started = None
        self.attempts = 0

    def wait(self, waiting: PreparationWaiting, *, now: float) -> dict[str, object]:
        self.begin(now=now)
        self.attempts += 1
        remaining = self.remaining(now=now)
        blocked = self.attempts >= self.max_attempts or remaining <= 0
        return {
            **waiting.detail, "state": "BLOCKED" if blocked else "PREPARING",
            "reason": str(waiting), "attempt": self.attempts,
            "max_attempts": self.max_attempts, "remaining_seconds": remaining,
        }


def prepare_closed_tails(
    *, symbols: tuple[str, ...],
    check: Callable[[str, str, datetime], bool],
    fetch: Callable[[str, str, datetime], None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    progress: Callable[[dict[str, object]], None] = lambda _: None,
    max_passes: int = 3,
) -> None:
    """Synchronize all markets to one causal end BEFORE short WS proof starts.

    Only an absent tail returns WAITING. Integrity/identity/provider exceptions
    propagate unchanged. Crossing a boundary repairs just the new tail; no
    older cutoff, fabricated bar or extended attestation is accepted.
    """
    if not symbols or not 1 <= max_passes <= 5:
        raise ValueError("PREPARATION_SCOPE_OR_BUDGET_INVALID")
    for _ in range(max_passes):
        now = clock()
        if now.utcoffset() is None or now.utcoffset().total_seconds() != 0:
            raise ValueError("PREPARATION_CLOCK_NOT_UTC")
        for symbol in symbols:
            for tf in ("5", "15"):
                end = now.replace(minute=now.minute//int(tf)*int(tf), second=0, microsecond=0)
                detail = {"symbol": symbol, "timeframe": tf, "expected": end.isoformat()}
                progress({"state": "PREPARING", **detail})
                if not check(symbol, tf, end):
                    fetch(symbol, tf, end)
                    if not check(symbol, tf, end):
                        raise PreparationWaiting("CANDLE_TAIL_NOT_YET_AVAILABLE", **detail)
        after = clock()
        if after.replace(minute=after.minute//5*5, second=0, microsecond=0) == (
            now.replace(minute=now.minute//5*5, second=0, microsecond=0)
        ):
            return
    raise PreparationWaiting("CANDLE_PREPARATION_BOUNDARY_ADVANCED")


def prepare_p90_closed_tails(
    connection: Any, *, symbols: tuple[str, ...], source_fetch: Callable[..., Any],
    source_commit: str, progress: Callable[[dict[str, object]], None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Use the existing approved provider/store; append missing tails only."""
    def key(symbol: str, tf: str) -> MarketCandleKey:
        return MarketCandleKey(
            BYBIT_PUBLIC_MARKET_SOURCE_ID, "venue-bybit",
            bybit_instrument_id("linear", symbol), symbol, "LINEAR", tf,
        )

    def check(symbol: str, tf: str, end: datetime) -> bool:
        try:
            SharedClosedCandleStore(connection, key(symbol, tf)).history(
                through=end, required=2600 if tf == "5" else 999,
            )
        except RuntimeError as exc:
            if str(exc) not in {"CANDLE_WARMUP_END_NOT_PROVEN", "CANDLE_WARMUP_INCOMPLETE"}:
                raise
            incomplete[symbol, tf] = str(exc) == "CANDLE_WARMUP_INCOMPLETE"
            return False
        return True

    def fetch(symbol: str, tf: str, end: datetime) -> None:
        latest = connection.execute(
            """SELECT max(closed_at) FROM analytics.closed_candles
                WHERE market_source_id=%s AND instrument_id=%s
                  AND timeframe=%s AND closed_at<=%s""",
            (BYBIT_PUBLIC_MARKET_SOURCE_ID, bybit_instrument_id("linear", symbol), tf, end),
        ).fetchone()[0]
        required = 2600 if tf == "5" else 999
        count = required if latest is None or incomplete.get((symbol, tf)) else max(
            1, int((end-latest).total_seconds()//(int(tf)*60))+1,
        )
        if count > 30000:
            raise RuntimeError("CANDLE_PREPARATION_HISTORY_OUTSIDE_BOUNDED_SCOPE")
        progress({"state": "PREPARING", "symbol": symbol, "timeframe": tf,
                  "expected": end.isoformat(),
                  "actual": None if latest is None else latest.isoformat()})
        history = source_fetch(symbol, end, (tf,), required_counts={tf: count})
        cache_approved_bybit_history(
            connection, symbol=symbol, history=history, observed_at=clock(),
            producer_release_commit=source_commit,
        )

    incomplete: dict[tuple[str, str], bool] = {}
    prepare_closed_tails(
        symbols=symbols, check=check, fetch=fetch, clock=clock, progress=progress,
    )
