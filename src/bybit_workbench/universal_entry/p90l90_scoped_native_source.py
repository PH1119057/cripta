"""Per-symbol PostgreSQL native MarketSource evidence, not trading permission.

This reader cannot set Strategy activation, mainnet gate, OWNER ARM or Exit
readiness. It is one required SOURCE proof for a future scoped P90 consumer,
whose other account/Execution/Exit gates must remain independently verified.

The production writer is cripta-native-public-trade-source.service; do not
confuse Observer health, raw rows, or a live WebSocket with this committed
original provider identity proof.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
)

_EXACT_SYMBOLS = frozenset().union(*ALLOWED_SYMBOLS.values())
# Precisely the four physical MarketSource producer-code surfaces protected
# by install_verified_release.sh. P90 consumer Exit/IPC attestation is a
# SEPARATE contract; those historic consumer SHA sets cannot approve/deny a
# continuously running source process from an older release.
_PRODUCER_ABI_PATHS = (
    "operations/monitoring/native_public_trade_source.py",
    "src/bybit_workbench/market_data/native_public_trade_journal.py",
    "src/bybit_workbench/market_identity.py",
    "operations/systemd/cripta-native-public-trade-source.service",
)



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


@dataclass(frozen=True, slots=True)
class OrderedNativeTrade:
    """Original provider event order (not an inferred seq/execId sort)."""

    frame_number: int
    frame_index: int
    symbol: str
    exchange_sequence: int
    exec_id: str
    event_at: datetime
    price: Decimal
    quantity: Decimal
    taker_side: str
    db_accepted_at: datetime


def read_ordered_native_events(
    connection: Any,
    *,
    source: NativeSourceEvidence,
    after_frame: int,
    after_index: int,
    limit: int,
) -> tuple[OrderedNativeTrade, ...]:
    """Read a bounded native event suffix from one proven source session.

    The future consumer must supply an independently persisted, source-scoped
    position (frame,index) in its OWN transaction, and commit the next
    checkpoint together with Engine decisions. This method performs NO writes,
    invents no WS ordering, and NEVER grants trading permissions.
    """
    if (
        not source.ready or not source.session_id
        or not source.producer_release_commit
        or type(after_frame) is not int or after_frame < 0
        or type(after_index) is not int or after_index < 0
        or type(limit) is not int or not 1 <= limit <= 1000
    ):
        raise NativeSourceProofUnavailable(
            "NATIVE_SOURCE_CONSUMER_CHECKPOINT_OR_PROOF_MISSING"
        )
    wanted = tuple(sorted(source.by_symbol))
    rows = connection.execute(
        """SELECT e.frame_number,e.frame_index,e.market_source_id,
                  e.instrument_id,e.external_symbol,e.exchange_sequence,
                  e.exec_id,e.event_at,e.accepted_at,
                  n.event_at,n.price,n.quantity,n.taker_side
             FROM analytics.native_public_trade_source_events e
             JOIN analytics.native_public_trade_events n
               ON n.market_source_id=e.market_source_id
              AND n.instrument_id=e.instrument_id
              AND n.exchange_sequence=e.exchange_sequence
              AND n.exec_id=e.exec_id
            WHERE e.source_session_id=%s
              AND e.external_symbol=ANY(%s)
              AND (e.frame_number,e.frame_index)>(%s,%s)
            ORDER BY e.frame_number,e.frame_index
            LIMIT %s""",
        (source.session_id, list(wanted), after_frame, after_index, limit),
    ).fetchall()
    events: list[OrderedNativeTrade] = []
    previous = (after_frame, after_index)
    seen: set[tuple[str, int, str]] = set()
    for raw in rows:
        (
            frame, index, market_source, instrument, symbol, seq, exec_id,
            event_time, accepted_at, journal_time, price, qty, side,
        ) = raw
        point = (frame, index)
        if (
            type(frame) is not int or type(index) is not int
            or point <= previous
            or market_source != BYBIT_PUBLIC_MARKET_SOURCE_ID
            or symbol not in wanted
            or instrument != bybit_instrument_id("linear", symbol)
            or type(seq) is not int or seq < 0
            or not isinstance(exec_id, str) or not exec_id
            or not isinstance(event_time, datetime)
            or event_time.utcoffset() is None
            or not isinstance(accepted_at, datetime)
            or accepted_at.utcoffset() is None
            or accepted_at.astimezone(UTC)
               < event_time.astimezone(UTC) - timedelta(seconds=2)
            or event_time != journal_time
            or not isinstance(price, Decimal) or price <= 0
            or not isinstance(qty, Decimal) or qty <= 0
            or side not in ("Buy", "Sell")
            or (symbol, seq, exec_id) in seen
        ):
            raise NativeSourceProofUnavailable(
                "NATIVE_SOURCE_ORDERED_EVENT_IDENTITY_CORRUPT"
            )
        previous = point
        seen.add((symbol, seq, exec_id))
        events.append(OrderedNativeTrade(
            frame, index, symbol, seq, exec_id,
            event_time, price, qty, side, accepted_at,
        ))
    return tuple(events)


def verify_native_producer_code_compatibility(
    *,
    source_checkout: Path,
    claimed_producer_commit: str,
    loaded_consumer_commit: str,
) -> frozenset[str]:
    """Exact immutable code-equivalence proof, NOT P90 consumer attestation.

    An older but still-running MarketSource process may be byte-identical to
    the newly installed consumer release. It must not borrow P90 consumer
    attestation, nor infer compatibility from Git ancestry or a changed
    process status file. Caller STILL verifies the actual running service,
    its session/producer SHA and original events independently.
    """
    for commit in (claimed_producer_commit, loaded_consumer_commit):
        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(char not in "0123456789abcdef" for char in commit)
        ):
            raise NativeSourceProofUnavailable(
                "NATIVE_SOURCE_PRODUCER_COMMIT_INVALID"
            )
    for path in _PRODUCER_ABI_PATHS:
        try:
            origin = subprocess.check_output(
                ["git", "-C", str(source_checkout), "show",
                 f"{claimed_producer_commit}:{path}"],
                stderr=subprocess.DEVNULL,
            )
            consumer = subprocess.check_output(
                ["git", "-C", str(source_checkout), "show",
                 f"{loaded_consumer_commit}:{path}"],
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise NativeSourceProofUnavailable(
                "NATIVE_SOURCE_PRODUCER_GIT_BYTES_UNAVAILABLE"
            ) from exc
        if hashlib.sha256(origin).digest() != hashlib.sha256(consumer).digest():
            raise NativeSourceProofUnavailable(
                "NATIVE_SOURCE_PRODUCER_CODE_DRIFT"
            )
    return frozenset((claimed_producer_commit,))
