"""R06 staged Entry read adapter: one immutable publication, never a trade gate.

This is a read-only, fail-closed proof boundary for comparing the current
Observer geometry to one MarketPublication. It deliberately does NOT switch
Entry dispatch, take over the durable Entry decision checkpoint or infer
complete ATR/closed history from the R05 WATERMARKS_ONLY publication.

The caller owns a REPEATABLE READ, READ ONLY PostgreSQL transaction and must
supply independently verified producer commits and exact plan lookbacks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_data.candle_warmup import (
    load_verified_r03_native_closed_seed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

ENTRY_SYMBOLS = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
))
ENTRY_INTERVALS = frozenset(("1", "5", "15"))
_HASH = re.compile(r"^[0-9a-f]{40}$")
_EPOCH = re.compile(r"^[0-9a-f]{32}$")


class EntryPublicationUnavailable(RuntimeError):
    """Required native/candle/source/transaction evidence was not proved."""


@dataclass(frozen=True, slots=True)
class EntryPublicationPin:
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    as_of_second: datetime
    native_watermarks: dict[str, dict[str, object]]
    closed_refs: dict[str, dict[str, str]]
    # A pinned watermark alone CANNOT certify the Engine lookback or ATR seed.
    history_verified: bool = False


def _utc(value: object, reason: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise EntryPublicationUnavailable(reason) from exc
    if (
        not isinstance(value, datetime)
        or value.utcoffset() != timedelta(0)
    ):
        raise EntryPublicationUnavailable(reason)
    return value.astimezone(UTC)


def read_entry_publication_pin(
    connection: Any,
    *,
    observed_at: datetime,
    accepted_producer_commits: frozenset[str],
    max_publication_age: timedelta,
    symbols: tuple[str, ...] = tuple(sorted(ENTRY_SYMBOLS)),
    intervals: tuple[str, ...] = ("1", "5", "15"),
) -> EntryPublicationPin:
    """Pin and verify authentic physical refs in caller's immutable PG view.

    This method issues SELECT/SHOW only and never upgrades publication
    WATERMARKS_ONLY to decision readiness. A separate exact history/ATR
    verification and durable decision cursor are mandatory for R06 cutover.
    """
    now = _utc(observed_at, "R06_CLOCK_NOT_UTC")
    if (
        type(max_publication_age) is not timedelta
        or not timedelta(0) < max_publication_age <= timedelta(seconds=30)
        or not accepted_producer_commits
        or any(not isinstance(x, str) or _HASH.fullmatch(x) is None
               for x in accepted_producer_commits)
        or not symbols or len(symbols) != len(set(symbols))
        or not set(symbols) <= ENTRY_SYMBOLS
        or not intervals or len(intervals) != len(set(intervals))
        or not set(intervals) <= ENTRY_INTERVALS
    ):
        raise EntryPublicationUnavailable("R06_CONSUMER_CONTRACT_INVALID")

    isolation = connection.execute("SHOW transaction_isolation").fetchone()
    read_only = connection.execute("SHOW transaction_read_only").fetchone()
    if isolation != ("repeatable read",) or read_only != ("on",):
        raise EntryPublicationUnavailable("R06_ATOMIC_READ_ONLY_SNAPSHOT_REQUIRED")

    sessions = connection.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    if (
        len(sessions) != 1
        or not isinstance(sessions[0][0], str)
        or _EPOCH.fullmatch(sessions[0][0]) is None
        or sessions[0][1] not in accepted_producer_commits
    ):
        raise EntryPublicationUnavailable("R06_ACTIVE_SOURCE_PROOF_UNAVAILABLE")
    source_session_id, producer = sessions[0]

    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,native_watermarks,
                  closed_candle_watermarks,closed_instrument_refs,
                  closed_instrument_coverage
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, source_session_id),
    ).fetchone()
    if row is None:
        raise EntryPublicationUnavailable("R06_PUBLICATION_MISSING")
    (pid, epoch, commit, at, status, native, candles, refs, quality) = row
    pub_at = _utc(at, "R06_PUBLICATION_CLOCK_INVALID")
    if (
        type(pid) is not int or pid < 1
        or epoch != source_session_id
        or commit != producer
        or status != "WATERMARKS_ONLY"
        or not timedelta(0) <= now - pub_at <= max_publication_age
        or not all(isinstance(x, dict)
                   for x in (native, candles, refs, quality))
    ):
        raise EntryPublicationUnavailable("R06_PUBLICATION_IDENTITY_OR_AGE_INVALID")

    approved_native: dict[str, dict[str, object]] = {}
    approved_candles: dict[str, dict[str, str]] = {}
    for symbol in symbols:
        instrument = bybit_instrument_id("linear", symbol)
        watermark = native.get(symbol)
        times = candles.get(symbol)
        linked = refs.get(instrument)
        covers = quality.get(instrument)
        if not all(isinstance(item, dict)
                   for item in (watermark, times, linked, covers)):
            raise EntryPublicationUnavailable("R06_REQUIRED_SOURCE_REF_MISSING")
        try:
            frame = watermark["frame_number"]
            index = watermark["frame_index"]
            seq = watermark["exchange_sequence"]
            exec_id = watermark["exec_id"]
        except KeyError as exc:
            raise EntryPublicationUnavailable("R06_NATIVE_REF_MALFORMED") from exc
        if (
            watermark.get("instrument_id") != instrument
            or type(frame) is not int or frame < 0
            or type(index) is not int or index < 0
            or type(seq) is not int or seq < 0
            or not isinstance(exec_id, str) or not exec_id
            or _utc(watermark.get("accepted_at"), "R06_NATIVE_TIME_INVALID")
            > pub_at + timedelta(seconds=5)
        ):
            raise EntryPublicationUnavailable("R06_NATIVE_REF_MALFORMED")
        found = connection.execute(
            """SELECT count(*)
                 FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s AND market_source_id=%s
                  AND instrument_id=%s AND external_symbol=%s
                  AND frame_number=%s AND frame_index=%s
                  AND exchange_sequence=%s AND exec_id=%s""",
            (source_session_id, BYBIT_PUBLIC_MARKET_SOURCE_ID,
             instrument, symbol, frame, index, seq, exec_id),
        ).fetchone()
        if found != (1,):
            raise EntryPublicationUnavailable("R06_NATIVE_ORIGINAL_REF_UNVERIFIED")
        approved_native[symbol] = dict(watermark)
        approved_candles[symbol] = {}
        for timeframe in intervals:
            ref = linked.get(timeframe)
            scope = covers.get(timeframe)
            stamp = times.get(timeframe)
            if (
                not isinstance(ref, dict)
                or not isinstance(scope, dict)
                or not isinstance(stamp, str)
                or scope.get("required") is not True
                or scope.get("status") not in {
                    "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED", "READY",
                }
                or scope.get("history_window") not in {"NOT_VERIFIED", "VERIFIED"}
                or ref.get("market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID
                or ref.get("instrument_id") != instrument
                or ref.get("external_symbol") != symbol
                or ref.get("product_category") != "LINEAR"
                or ref.get("timeframe") != timeframe
                or ref.get("closed_at") != stamp
            ):
                raise EntryPublicationUnavailable("R06_CLOSED_REF_SCOPE_UNVERIFIED")
            close_at = _utc(stamp, "R06_CLOSED_BOUNDARY_INVALID")
            freshness = scope.get("freshness_seconds")
            if (
                type(freshness) is not int
                or freshness <= 0
                or not timedelta(0) <= pub_at - close_at
                <= timedelta(seconds=freshness)
            ):
                raise EntryPublicationUnavailable("R06_CLOSED_REF_STALE")
            present = connection.execute(
                """SELECT count(*)
                     FROM analytics.closed_candles_effective
                    WHERE market_source_id=%s AND instrument_id=%s
                      AND product_category='LINEAR'
                      AND external_symbol=%s AND timeframe=%s
                      AND closed_at=%s""",
                (BYBIT_PUBLIC_MARKET_SOURCE_ID, instrument,
                 symbol, timeframe, close_at),
            ).fetchone()
            if present != (1,):
                raise EntryPublicationUnavailable("R06_CLOSED_PHYSICAL_REF_MISSING")
            approved_candles[symbol][timeframe] = stamp

    return EntryPublicationPin(
        publication_id=pid, source_session_id=source_session_id,
        producer_release_commit=producer, as_of_second=pub_at,
        native_watermarks=approved_native, closed_refs=approved_candles,
    )


def read_entry_publication_seed(
    connection: Any,
    *,
    pin: EntryPublicationPin,
    symbol: str,
    required_counts: dict[str, int],
) -> dict[str, tuple[Any, ...]]:
    """Verify full CLOSED lookback against the PINNED second, not live REST.

    This is an explicit plan-sized proof; a publication field marked
    NOT_VERIFIED cannot stand in for it. Caller must still prove geometry,
    ATR and event/decision parity before enabling any R06 dispatch.
    """
    if (
        not isinstance(pin, EntryPublicationPin)
        or symbol not in pin.closed_refs
        or not required_counts
        or not set(required_counts) <= set(pin.closed_refs[symbol])
        or any(type(n) is not int or not 1 <= n <= 10000
               for n in required_counts.values())
    ):
        raise EntryPublicationUnavailable("R06_ENTRY_PLAN_LOOKBACK_INVALID")
    try:
        history = load_verified_r03_native_closed_seed(
            connection, symbol=symbol, required_counts=required_counts,
            observed_at=pin.as_of_second,
        )
    except (RuntimeError, ValueError) as exc:
        raise EntryPublicationUnavailable("R06_ENTRY_HISTORY_UNVERIFIED") from exc
    if history.keys() != required_counts.keys():
        raise EntryPublicationUnavailable("R06_ENTRY_HISTORY_SCOPE_MISMATCH")
    for tf, candles in history.items():
        if (
            len(candles) != required_counts[tf]
            or not candles
            or _utc(candles[-1].closed_at, "R06_ENTRY_CLOSE_INVALID")
            != _utc(pin.closed_refs[symbol][tf], "R06_PINNED_CLOSE_INVALID")
        ):
            raise EntryPublicationUnavailable("R06_ENTRY_SEED_PUBLICATION_MISMATCH")
    return history


def read_entry_publication_original_suffix(
    connection: Any,
    *,
    pin: EntryPublicationPin,
    source: Any,
    after_frame: int,
    after_index: int,
    limit: int = 256,
) -> tuple[Any, ...]:
    """Read exact original provider rows, never a per-second trade sample.

    The caller proves SOURCE ABI and supplies its OWN durable causal decision
    checkpoint (not the technical transport ACK). No decision or checkpoint
    write occurs here. The returned suffix is bounded by each symbol's
    immutable watermark in this ONE publication.
    """
    from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
        NativeSourceEvidence,
        NativeSourceProofUnavailable,
        read_ordered_native_events,
    )

    if (
        not isinstance(pin, EntryPublicationPin)
        or not isinstance(source, NativeSourceEvidence)
        or not source.ready
        or source.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or source.session_id != pin.source_session_id
        or source.producer_release_commit != pin.producer_release_commit
        or not set(pin.native_watermarks) <= set(source.by_symbol)
        or type(after_frame) is not int or after_frame < 0
        or type(after_index) is not int or after_index < 0
        or type(limit) is not int or not 1 <= limit <= 1000
    ):
        raise EntryPublicationUnavailable("R06_ENTRY_ORIGINAL_SOURCE_MISMATCH")

    try:
        events = read_ordered_native_events(
            connection,
            source=source,
            after_frame=after_frame,
            after_index=after_index,
            limit=limit,
        )
    except NativeSourceProofUnavailable as exc:
        raise EntryPublicationUnavailable("R06_ENTRY_ORIGINAL_GAP") from exc

    accepted = []
    prior = (after_frame, after_index)
    for event in events:
        pos = (event.frame_number, event.frame_index)
        bound = pin.native_watermarks.get(event.symbol)
        if pos <= prior or not isinstance(bound, dict):
            raise EntryPublicationUnavailable("R06_ENTRY_ORIGINAL_ORDER_INVALID")
        if pos > (bound["frame_number"], bound["frame_index"]):
            # Source advances after the pinned publication. Preserve original
            # frame order, never leap past a not-yet-published event.
            break
        accepted.append(event)
        prior = pos
    return tuple(accepted)
