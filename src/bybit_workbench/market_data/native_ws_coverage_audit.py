"""Independent, read-only MarketSource evidence audit for native restart.

The original producer persists session, original exchange IDs, and an
atomic journal. This audit checks those exact artifacts without manufacturing
a provider message-receipt/WS coverage certificate. In particular, a single
ACTIVE source session and perfect database rows do NOT prove every Bybit
message was received. An actual independently issued receipt-chain witness
is a separate requirement before NATIVE_CONTINUITY_VERIFIED can be true.

No Strategy/Entry/Exit/Exchange/permission writes, cursor rewinds or repair
of historical checkpoints are performed here. The caller supplies an exact
consumer horizon and resource budget and uses a consistent read snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_SESSION = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SYMBOL = re.compile(r"[A-Z0-9]{2,32}\Z")
_BATCH_ROWS = 2048
# This is a technical memory/CPU guard for one audit, not a Strategy horizon
# or a universal number of trades required to reconstruct an Exit.
_MAX_AUDIT_RECORDS = 250_000


class NativeWsCoverageUnverified(RuntimeError):
    """A required physical source, identity, or scoped interval is unverified."""


@dataclass(frozen=True, slots=True)
class NativeWsCoverageAudit:
    """Real issuer/identity/atomic evidence, not market completeness proof."""

    session_id: str
    market_source_id: str
    symbol: str
    producer_release_commit: str
    oldest_frame: tuple[int, int]
    latest_frame: tuple[int, int]
    record_count: int
    digest_sha256: str
    durable_same_seq_ids: frozenset[str]
    native_ids_verified: bool
    atomic_session_verified: bool
    native_continuity_verified: bool
    reason: str


def _utc(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta()
    ):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_UTC_REQUIRED")
    return value.astimezone(UTC)


def _point(frame: object, index: object) -> tuple[int, int]:
    if (
        type(frame) is not int or frame < 1
        or type(index) is not int or index < 0
    ):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_FRAME_INVALID")
    return frame, index


def _identity_rows(
    connection: Any, *, session_id: str, symbol: str, seq: int,
    ids: tuple[str, ...],
) -> dict[str, tuple[tuple[int, int], datetime]]:
    rows = connection.execute(
        """SELECT frame_number,frame_index,exchange_sequence,exec_id,event_at
             FROM analytics.native_public_trade_source_events
            WHERE source_session_id=%s AND market_source_id=%s
              AND instrument_id=%s AND external_symbol=%s
              AND exchange_sequence=%s AND exec_id=ANY(%s)""",
        (session_id, BYBIT_PUBLIC_MARKET_SOURCE_ID,
         bybit_instrument_id("linear", symbol), symbol, seq, list(ids)),
    ).fetchall()
    found: dict[str, tuple[tuple[int, int], datetime]] = {}
    for frame, index, actual_seq, exec_id, when in rows:
        if (
            actual_seq != seq or exec_id not in ids
            or exec_id in found
        ):
            raise NativeWsCoverageUnverified("NATIVE_COVERAGE_ANCHOR_IDENTITY_DRIFT")
        found[exec_id] = (_point(frame, index), _utc(when))
    if set(found) != set(ids):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_SAME_SEQUENCE_IDS_MISSING")
    return found


def audit_native_ws_session_interval(
    connection: Any,
    *,
    session_id: str,
    producer_release_commit: str,
    symbol: str,
    durable_seq: int,
    durable_exec_ids: tuple[str, ...],
    durable_exec_id: str,
    durable_at: datetime,
    live_seq: int,
    live_exec_id: str,
    live_at: datetime,
    checked_at: datetime,
    max_required_records: int,
    max_lag: timedelta,
) -> NativeWsCoverageAudit:
    """Verify an exact bounded original-native source *candidate*.

    Uses one caller-owned consistent PostgreSQL read snapshot. The underlying
    source sessions and immutable event ledger were written by the distinct
    credential-free MarketSource, not the P90 causal consumer. This audit
    still cannot issue the missing provider-frame receipt coverage witness:
    positive atomic/tie/identity checks do NOT grant EXIT_READY.
    """
    old = _utc(durable_at)
    new = _utc(live_at)
    now = _utc(checked_at)
    if (
        not isinstance(session_id, str) or _SESSION.fullmatch(session_id) is None
        or not isinstance(producer_release_commit, str)
        or _SHA.fullmatch(producer_release_commit) is None
        or not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None
        or type(durable_seq) is not int or durable_seq < 0
        or type(live_seq) is not int or live_seq < durable_seq
        or not isinstance(durable_exec_ids, tuple)
        or not durable_exec_ids
        or any(not isinstance(x, str) or not x for x in durable_exec_ids)
        or len(set(durable_exec_ids)) != len(durable_exec_ids)
        or not isinstance(durable_exec_id, str)
        or durable_exec_id not in durable_exec_ids
        or not isinstance(live_exec_id, str) or not live_exec_id
        or type(max_required_records) is not int
        or not 1 <= max_required_records <= _MAX_AUDIT_RECORDS
        or not isinstance(max_lag, timedelta)
        or not timedelta(0) < max_lag <= timedelta(minutes=5)
        or old > new or new > now + timedelta(seconds=2)
    ):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_MANIFEST_INVALID")

    session = connection.execute(
        """SELECT market_source_id,producer_release_commit,state,
                  subscribed_at,last_accepted_at,ended_at,reason
             FROM analytics.native_public_trade_source_sessions
            WHERE source_session_id=%s""",
        (session_id,),
    ).fetchone()
    if session is None:
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_SESSION_MISSING")
    source, producer, state, subscribed, accepted, ended, reason = session
    if (
        source != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or producer != producer_release_commit
        or state != "ACTIVE" or ended is not None or reason is not None
        or _utc(subscribed) > old
        or _utc(accepted) < new
        or not timedelta(seconds=-2) <= now - _utc(accepted) <= max_lag
    ):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_SESSION_NOT_UNBROKEN")

    ids = _identity_rows(
        connection, session_id=session_id, symbol=symbol,
        seq=durable_seq, ids=durable_exec_ids,
    )
    if ids[durable_exec_id][1] != old:
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_DURABLE_TIME_DRIFT")
    live = _identity_rows(
        connection, session_id=session_id, symbol=symbol,
        seq=live_seq, ids=(live_exec_id,),
    )
    if live[live_exec_id][1] != new:
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_LIVE_TIME_DRIFT")
    first = min(point for point, _time in ids.values())
    last = live[live_exec_id][0]
    if any(point > last for point, _time in ids.values()):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_ANCHOR_ORDER_INVALID")

    last_point = (first[0], first[1] - 1)
    digest = hashlib.sha256()
    seen: set[tuple[int, str]] = set()
    seen_old: set[str] = set()
    total = 0
    previous_seq: int | None = None
    previous_at: datetime | None = None
    while True:
        rows = connection.execute(
            """SELECT e.frame_number,e.frame_index,e.market_source_id,
                      e.instrument_id,e.external_symbol,e.exchange_sequence,
                      e.exec_id,e.event_at,e.accepted_at,
                      n.event_at,n.price,n.quantity,n.taker_side,
                      n.source_transport,n.producer_release_commit
                 FROM analytics.native_public_trade_source_events e
                 JOIN analytics.native_public_trade_events n
                   ON n.market_source_id=e.market_source_id
                  AND n.instrument_id=e.instrument_id
                  AND n.exchange_sequence=e.exchange_sequence
                  AND n.exec_id=e.exec_id
                WHERE e.source_session_id=%s AND e.external_symbol=%s
                  AND (e.frame_number,e.frame_index)>(%s,%s)
                  AND (e.frame_number,e.frame_index)<=(%s,%s)
                ORDER BY e.frame_number,e.frame_index LIMIT %s""",
            (session_id, symbol, *last_point, *last, _BATCH_ROWS),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            (
                frame, index, market_source, instrument, trade_symbol,
                seq, exec_id, when, accepted_at, journal_at, price, qty,
                side, transport, journal_producer,
            ) = row
            pos = _point(frame, index)
            when = _utc(when)
            accepted_at = _utc(accepted_at)
            if (
                pos <= last_point or pos > last
                or market_source != BYBIT_PUBLIC_MARKET_SOURCE_ID
                or instrument != bybit_instrument_id("linear", symbol)
                or trade_symbol != symbol
                or type(seq) is not int or seq < 0
                or not isinstance(exec_id, str) or not exec_id
                or journal_at != when
                or not isinstance(price, Decimal) or not price.is_finite()
                or price <= 0 or not isinstance(qty, Decimal)
                or not qty.is_finite() or qty <= 0
                or side not in ("Buy", "Sell")
                or transport != "BYBIT_WS_PUBLIC_TRADE"
                or journal_producer != producer_release_commit
                or accepted_at < when - timedelta(seconds=2)
                or (previous_seq is not None and (
                    seq < previous_seq
                    or previous_at is None or when < previous_at
                ))
                or (seq, exec_id) in seen
            ):
                raise NativeWsCoverageUnverified("NATIVE_COVERAGE_EVENT_IDENTITY_DRIFT")
            seen.add((seq, exec_id))
            if seq == durable_seq and exec_id in ids:
                seen_old.add(exec_id)
            digest.update(json.dumps(
                (frame, index, seq, exec_id, when.isoformat(),
                 str(price), str(qty), side), separators=(",", ":"),
            ).encode("utf-8"))
            digest.update(b"\n")
            last_point = pos
            previous_seq, previous_at = seq, when
            total += 1
            if total > max_required_records:
                raise NativeWsCoverageUnverified(
                    "NATIVE_COVERAGE_RESOURCE_BUDGET_EXCEEDED"
                )
        if len(rows) < _BATCH_ROWS:
            break
    if (
        total < 1 or last_point != last
        or seen_old != set(durable_exec_ids)
        or (live_seq, live_exec_id) not in seen
    ):
        raise NativeWsCoverageUnverified("NATIVE_COVERAGE_SOURCE_INTERVAL_MISSING")

    return NativeWsCoverageAudit(
        session_id=session_id,
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        symbol=symbol,
        producer_release_commit=producer_release_commit,
        oldest_frame=first,
        latest_frame=last,
        record_count=total,
        digest_sha256=digest.hexdigest(),
        durable_same_seq_ids=frozenset(seen_old),
        native_ids_verified=True,
        atomic_session_verified=True,
        native_continuity_verified=False,
        reason="NATIVE_COVERAGE_PRODUCER_FRAME_RECEIPT_WITNESS_NOT_STORED",
    )
