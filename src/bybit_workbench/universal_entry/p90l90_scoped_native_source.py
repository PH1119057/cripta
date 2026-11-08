"""Per-symbol PostgreSQL native MarketSource evidence, not trading permission.

This reader cannot set Strategy activation, mainnet gate, OWNER ARM or Exit
readiness. It is one required SOURCE proof for a future scoped P90 consumer,
whose other account/Execution/Exit gates must remain independently verified.

The production writer is cripta-native-public-trade-source.service; do not
confuse Observer health, raw rows, or a live WebSocket with this committed
original provider identity proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
)

_EXACT_SYMBOLS = frozenset().union(*ALLOWED_SYMBOLS.values())


class NativeSourceProofUnavailable(RuntimeError):
    """Caller has not supplied an exact P90 source and freshness contract."""


@dataclass(frozen=True, slots=True)
class NativeSourceSymbolEvidence:
    symbol: str
    ready: bool
    reason: str
    exchange_sequence: int | None = None
    exec_id: str | None = None
    event_at: datetime | None = None
    accepted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NativeSourceEvidence:
    market_source_id: str
    session_id: str | None
    producer_release_commit: str | None
    checked_at: datetime
    by_symbol: dict[str, NativeSourceSymbolEvidence]

    @property
    def ready(self) -> bool:
        return bool(self.by_symbol) and all(
            evidence.ready for evidence in self.by_symbol.values()
        )


def _valid_time(value: object, now: datetime, ttl: timedelta) -> bool:
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None or value.utcoffset() is None:
        return False
    delay = now - value.astimezone(UTC)
    # Exchange time and ingestion time are independent; allow at most 2s
    # forward clock skew, never invent source freshness from a later event.
    return timedelta(seconds=-2) <= delay <= ttl


def _blocked(
    symbols: tuple[str, ...], at: datetime, reason: str,
    *, session_id: str | None = None, producer: str | None = None,
) -> NativeSourceEvidence:
    return NativeSourceEvidence(
        BYBIT_PUBLIC_MARKET_SOURCE_ID, session_id, producer, at,
        {s: NativeSourceSymbolEvidence(s, False, reason) for s in symbols},
    )


def read_scoped_native_source(
    connection: Any,
    *,
    symbols: tuple[str, ...],
    accepted_producer_commits: frozenset[str],
    observed_at: datetime,
    max_lag: timedelta,
) -> NativeSourceEvidence:
    """Read-only evidence; never derive OWNER ARM from this result.

    Preconditions: caller independently verified accepted producer commits
    against source compatibility and exact active Strategy/EntryPlan; an
    explicit TTL comes from the consumer's data-requirement manifest.
    """
    if (
        not symbols
        or len(set(symbols)) != len(symbols)
        or not set(symbols) <= _EXACT_SYMBOLS
        or not accepted_producer_commits
        or any(
            not isinstance(commit, str)
            or len(commit) != 40
            or any(c not in "0123456789abcdef" for c in commit)
            for commit in accepted_producer_commits
        )
        or not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or not isinstance(max_lag, timedelta)
        or not timedelta(0) < max_lag <= timedelta(minutes=5)
    ):
        raise NativeSourceProofUnavailable(
            "NATIVE_SOURCE_EXACT_SCOPE_RELEASE_OR_TTL_MISSING"
        )
    at = observed_at.astimezone(UTC)
    requested = tuple(sorted(symbols))
    sessions = connection.execute(
        """SELECT source_session_id,market_source_id,producer_release_commit,
                  state,subscribed_at,last_accepted_at,ended_at
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    if len(sessions) != 1:
        return _blocked(
            requested, at,
            "NATIVE_SOURCE_ACTIVE_SESSION_AMBIGUOUS"
            if sessions else "NATIVE_SOURCE_SESSION_NOT_ACTIVE",
        )
    session_id, market_source_id, producer, state, started, latest, ended = (
        sessions[0]
    )
    session_id = str(session_id)
    producer = str(producer)
    if (
        market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or state != "ACTIVE"
        or ended is not None
        or not _valid_time(started, at, timedelta(days=365))
        or not _valid_time(latest, at, max_lag)
    ):
        return _blocked(
            requested, at, "NATIVE_SOURCE_SESSION_NOT_CURRENT",
            session_id=session_id, producer=producer,
        )
    if producer not in accepted_producer_commits:
        return _blocked(
            requested, at, "NATIVE_SOURCE_RELEASE_NOT_APPROVED",
            session_id=session_id, producer=producer,
        )

    rows = connection.execute(
        """SELECT c.external_symbol,c.market_source_id,c.instrument_id,
                  c.exchange_sequence,c.exec_id,c.event_at,c.accepted_at,
                  n.event_at,n.price,n.quantity,n.taker_side
             FROM analytics.native_public_trade_source_cursors c
             LEFT JOIN analytics.native_public_trade_events n
               ON n.market_source_id=c.market_source_id
              AND n.instrument_id=c.instrument_id
              AND n.exchange_sequence=c.exchange_sequence
              AND n.exec_id=c.exec_id
            WHERE c.source_session_id=%s
              AND c.external_symbol=ANY(%s)
            ORDER BY c.external_symbol""",
        (session_id, list(requested)),
    ).fetchall()
    found: dict[str, NativeSourceSymbolEvidence] = {}
    for row in rows:
        (
            symbol, source, instrument, seq, exec_id, event, accepted,
            journal_at, price, qty, side,
        ) = row
        symbol = str(symbol)
        if symbol not in requested or symbol in found:
            raise NativeSourceProofUnavailable(
                "NATIVE_SOURCE_CURSOR_IDENTITY_AMBIGUOUS"
            )
        reason = "NATIVE_SOURCE_COMMITTED_CURRENT"
        if (
            source != BYBIT_PUBLIC_MARKET_SOURCE_ID
            or instrument != bybit_instrument_id("linear", symbol)
            or type(seq) is not int or seq < 0
            or not isinstance(exec_id, str) or not exec_id
        ):
            reason = "NATIVE_SOURCE_CURSOR_IDENTITY_INVALID"
        elif (
            not _valid_time(event, at, max_lag)
            or not _valid_time(accepted, at, max_lag)
        ):
            reason = "NATIVE_SOURCE_CURSOR_NOT_FRESH"
        elif (
            journal_at != event
            or not isinstance(price, Decimal) or price <= 0
            or not isinstance(qty, Decimal) or qty <= 0
            or side not in ("Buy", "Sell")
        ):
            reason = "NATIVE_SOURCE_JOURNAL_IDENTITY_DRIFT"
        found[symbol] = NativeSourceSymbolEvidence(
            symbol=symbol, ready=reason == "NATIVE_SOURCE_COMMITTED_CURRENT",
            reason=reason,
            exchange_sequence=seq if type(seq) is int else None,
            exec_id=exec_id if isinstance(exec_id, str) else None,
            event_at=event if isinstance(event, datetime) else None,
            accepted_at=accepted if isinstance(accepted, datetime) else None,
        )
    for symbol in requested:
        if symbol not in found:
            found[symbol] = NativeSourceSymbolEvidence(
                symbol, False, "NATIVE_SOURCE_CURSOR_MISSING",
            )
    return NativeSourceEvidence(
        BYBIT_PUBLIC_MARKET_SOURCE_ID, session_id, producer, at, found,
    )
