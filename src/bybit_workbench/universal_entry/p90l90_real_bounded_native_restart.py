"""Bounded read-only native recovery certificate for a P90 REAL restart.

Bybit /v5/market/recent-trade returns at most 1000 *latest* public trades and
has no pagination cursor. The <=1000 recent-trade response is an optional *fast-path* proof of a
short native interval, NOT the only possible historical source. A separately
verified complete original-native interval can be supplied by an approved
MarketSource coverage reader. Source data without native seq (e.g. Bybit daily
trade CSV) may establish prices for a compatible Exit/MFE consumer but cannot
be silently passed as an exact native sequence replay. Unknown coverage blocks
only the affected causal consumer; the caller owns scoped readiness.

This is one native coverage gate, NOT an open-position restart engine, an
account-specific signed REST proof, an execution permission or an Exit action.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityNotProvable,
    ReplayTrade,
    TradeCursor,
    build_exact_trade_replay,
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_EPOCH = re.compile(r"p90-real-epoch-[0-9a-f]{40}\Z")
_SYMBOLS = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))


class P90RealNativeRestartUnverified(RuntimeError):
    """A required original-native interval has not been proved."""


@dataclass(frozen=True, slots=True)
class P90VerifiedNativeSourceWindow:
    """Full exact native coverage attested by an approved source *outside* P90.

    This is not a CSV-to-seq adapter, a self-certified PostgreSQL row slice
    or a financial admission proof. The producer/coverage owner must
    independently verify completeness, session boundaries and original
    provider identity before constructing this value. The consumer checks
    the issuer/interval/raw-byte content checksum against the supplied
    evidence. An absent window always retains the REST-only fail-closed path.
    """

    symbol: str
    instrument_id: str
    market_source_id: str
    coverage_start: datetime
    coverage_end: datetime
    event_count: int
    source_sha256: str
    source_reference: str
    coverage_witness: str
    complete_coverage_verified: bool
    rows: tuple[Mapping[str, object], ...]


def _source_rows_digest(rows: Sequence[Mapping[str, object]]) -> str:
    """Deterministic exact record checksum; never a completeness proof."""
    import json

    return sha256(
        json.dumps(
            [dict(item) for item in rows], sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_complete_original_native_source(
    proof: P90VerifiedNativeSourceWindow,
    *,
    old: TradeCursor,
    new: TradeCursor,
) -> tuple[Mapping[str, object], ...]:
    # A source attestation must come from an independent approved native
    # producer; the existence of saved PostgreSQL rows is not one.
    from bybit_workbench.market_identity import (
        BYBIT_PUBLIC_MARKET_SOURCE_ID,
        bybit_instrument_id,
    )

    if (
        not isinstance(proof, P90VerifiedNativeSourceWindow)
        or proof.symbol != old.symbol or new.symbol != old.symbol
        or proof.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or proof.instrument_id != bybit_instrument_id("linear", old.symbol)
        or proof.coverage_witness not in {
            "APPROVED_FULL_NATIVE_SOURCE_ARCHIVE_V1",
            "VERIFIED_ATOMIC_PUBLIC_WS_SESSION_CHAIN_V1",
        }
        or proof.complete_coverage_verified is not True
        or not isinstance(proof.source_reference, str)
        or not proof.source_reference
        or not isinstance(proof.source_sha256, str)
        or len(proof.source_sha256) != 64
        or not isinstance(proof.rows, tuple)
        or not 1 <= len(proof.rows) <= 50_000
        or type(proof.event_count) is not int
        or proof.event_count != len(proof.rows)
        or proof.coverage_start.tzinfo is None
        or proof.coverage_end.tzinfo is None
        or proof.coverage_start.utcoffset() != timedelta()
        or proof.coverage_end.utcoffset() != timedelta()
        or proof.coverage_start > old.traded_at
        or proof.coverage_end < new.traded_at
        or proof.coverage_end < proof.coverage_start
        or _source_rows_digest(proof.rows) != proof.source_sha256
    ):
        raise P90RealNativeRestartUnverified(
            "P90_RESTART_VERIFIED_NATIVE_COVERAGE_PROOF_MISSING"
        )
    # Every row must be an original native trade with exact seq+execId.
    # Archived price/time/trdMatchID alone is intentionally rejected.
    if any(
        not isinstance(row, Mapping)
        or any(key not in row for key in (
            "symbol", "execId", "seq", "time", "price", "size", "side",
        ))
        for row in proof.rows
    ):
        raise P90RealNativeRestartUnverified(
            "P90_RESTART_NATIVE_ARCHIVE_ORIGINAL_SEQUENCE_MISSING"
        )
    return proof.rows


def resolve_durable_restart_anchor(
    *,
    symbol: str,
    last_sequence: int | None,
    last_event_at: datetime | None,
    last_exec_ids: Sequence[str],
    latest_rest_rows: Sequence[Mapping[str, object]],
    verified_native_source: P90VerifiedNativeSourceWindow | None = None,
) -> TradeCursor:
    """Recover one exact persisted cursor without forgetting same-seq peers.

    The checkpoint stores every execId emitted at its last sequence.  One of
    those IDs whose timestamp equals the persisted last-emitted timestamp is
    sufficient as the physical anchor only when *all* persisted IDs are still
    present in the same bounded recent-trade page.  The caller passes the full
    ID set back to :func:`attest_bounded_native_restart` so none is replayed
    a second time.
    """
    try:
        at = _utc(last_event_at) if isinstance(last_event_at, datetime) else None
        ids = tuple(last_exec_ids)
        native_rows = (
            latest_rest_rows
            if verified_native_source is None
            else _require_complete_original_native_source(
                verified_native_source,
                old=TradeCursor(
                    symbol, ids[0] if ids else "", last_sequence or 0, at or datetime.now(UTC),
                ),
                new=TradeCursor(
                    symbol, ids[0] if ids else "", last_sequence or 0, at or datetime.now(UTC),
                ),
            )
        )
        if (
            symbol not in _SYMBOLS
            or type(last_sequence) is not int or last_sequence < 0
            or at is None
            or not isinstance(native_rows, (tuple, list))
            or not 1 <= len(native_rows) <= (
                1000 if verified_native_source is None else 50_000
            )
            # REST page is bounded at 1000. Independently certified exact
            # native windows may be larger; neither implies one another.
            # Reject missing or truncated proof, not a smaller invented ID cap.
            or not ids or len(ids) > len(native_rows)
            or any(not isinstance(item, str) or not item for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_DURABLE_CHECKPOINT_CURSOR_MISSING"
            )
        present: dict[str, datetime] = {}
        for row in native_rows:
            if not isinstance(row, Mapping) or row.get("symbol") != symbol:
                continue
            name = row.get("execId")
            if not isinstance(name, str) or name not in ids:
                continue
            seq = int(str(row["seq"]))
            when = datetime.fromtimestamp(int(str(row["time"])) / 1000, UTC)
            if seq == last_sequence:
                present[name] = when
        if set(present) != set(ids):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_DURABLE_SAME_SEQUENCE_IDS_EVICTED"
            )
        candidates = sorted(name for name, when in present.items() if when == at)
        if not candidates:
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_DURABLE_LAST_TIMESTAMP_NOT_IN_REST"
            )
        return TradeCursor(
            symbol=symbol,
            seq=last_sequence,
            exec_id=candidates[0],
            traded_at=at,
        )
    except P90RealNativeRestartUnverified:
        raise
    except (TypeError, KeyError, ValueError, OverflowError):
        raise P90RealNativeRestartUnverified(
            "P90_RESTART_DURABLE_CHECKPOINT_CURSOR_UNVERIFIED"
        ) from None


@dataclass(frozen=True, slots=True)
class P90RealBoundedNativeRecovery:
    """A bounded causal set; never self-certifies a safe REAL restart."""

    source_epoch_id: str
    source_commit: str
    symbol: str
    durable_anchor: TradeCursor
    live_anchor: TradeCursor
    trades: tuple[ReplayTrade, ...]
    rest_server_at: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta():
        raise P90RealNativeRestartUnverified("P90_RESTART_NON_UTC_SOURCE_TIME")
    return value.astimezone(UTC)


def attest_bounded_native_restart(
    *,
    source_commit: str,
    source_epoch_id: str,
    durable_anchor: TradeCursor,
    live_anchor: TradeCursor,
    mirror: PublicTradeMirrorBuffer,
    latest_rest_rows: Sequence[Mapping[str, object]],
    rest_server_at: datetime,
    checked_at: datetime,
    durable_exec_ids: Sequence[str] | None = None,
    verified_native_source: P90VerifiedNativeSourceWindow | None = None,
) -> P90RealBoundedNativeRecovery:
    """Prove original native coverage via REST fast path or approved full source.

    The caller MUST load the durable anchor from the original source
    checkpoint, independently authenticate Exchange position/protection,
    hold the original epoch's PG transaction lock, and feed replay facts to
    causal Entry/Exit in one transaction. This function performs none of
    those actions; it cannot authorize a trade on its own.
    """
    try:
        at = _utc(checked_at)
        server = _utc(rest_server_at)
        old_at = _utc(durable_anchor.traded_at)
        live_at = _utc(live_anchor.traded_at)
        native_rows = (
            latest_rest_rows
            if verified_native_source is None
            else _require_complete_original_native_source(
                verified_native_source, old=durable_anchor, new=live_anchor,
            )
        )
        if (
            not _SHA.fullmatch(source_commit)
            or not _EPOCH.fullmatch(source_epoch_id)
            or durable_anchor.symbol not in _SYMBOLS
            or live_anchor.symbol != durable_anchor.symbol
            or not durable_anchor.exec_id
            or not live_anchor.exec_id
            or type(durable_anchor.seq) is not int
            or type(live_anchor.seq) is not int
            or durable_anchor.seq < 0
            or live_anchor.seq < durable_anchor.seq
            or old_at > live_at
            or not timedelta() <= at - server <= timedelta(seconds=10)
            or not timedelta() <= server - live_at <= timedelta(seconds=10)
            or not isinstance(native_rows, (tuple, list))
            or not 1 <= len(native_rows) <= (
                1000 if verified_native_source is None else 50_000
            )
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_EXACT_REST_WINDOW_OR_ANCHOR_MISSING"
            )
        before = mirror.snapshot()
        if (
            before.get("state") != "ACTIVE"
            or before.get("gaps") != 0
            or not isinstance(before.get("epoch"), int)
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_NEW_LIVE_WS_MIRROR_UNVERIFIED"
            )
        mirror_ready = datetime.fromisoformat(str(before["epoch_started_at"]))
        if _utc(mirror_ready) > live_at:
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_LIVE_ANCHOR_BEFORE_NEW_WS"
            )
        # Source owner must actually have received the second exact anchor.
        active = mirror.verified_fill_forward_events(
            symbol=live_anchor.symbol,
            fill_at=mirror_ready,
            at=at,
        )
        if not any(
            (t.exec_id, t.seq, t.traded_at) ==
            (live_anchor.exec_id, live_anchor.seq, live_at)
            for t in active
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_LIVE_ANCHOR_NOT_IN_ORIGINAL_WS"
            )
        # The REST endpoint is recent-N, newest-first. All rows between
        # the two exact anchors must belong to this one page. Sorting an
        # arbitrary untrusted subset would falsely certify a missing tick.
        seen: set[str] = set()
        prev_seq: int | None = None
        prev_at: datetime | None = None
        parsed: list[tuple[str, int, datetime]] = []
        for row in native_rows:
            if not isinstance(row, Mapping) or row.get("symbol") != live_anchor.symbol:
                raise P90RealNativeRestartUnverified(
                    "P90_RESTART_REST_FOREIGN_OR_INVALID_TRADE"
                )
            name = row.get("execId")
            if not isinstance(name, str) or not name or name in seen:
                raise P90RealNativeRestartUnverified(
                    "P90_RESTART_REST_DUPLICATE_EXEC_ID"
                )
            seen.add(name)
            seq = int(str(row["seq"]))
            timestamp = datetime.fromtimestamp(int(str(row["time"])) / 1000, UTC)
            side = row.get("side")
            price = Decimal(str(row["price"]))
            size = Decimal(str(row["size"]))
            if (
                seq < 0 or side not in ("Buy", "Sell")
                or not price.is_finite() or price <= 0
                or not size.is_finite() or size <= 0
                or (prev_seq is not None and (
                    prev_at is None or seq > prev_seq
                    or (seq == prev_seq and timestamp > prev_at)
                ))
            ):
                raise P90RealNativeRestartUnverified(
                    "P90_RESTART_REST_CAUSAL_ORDER_OR_PRICE_INVALID"
                )
            parsed.append((name, seq, timestamp))
            prev_seq, prev_at = seq, timestamp
        known_old_ids = (
            (durable_anchor.exec_id,)
            if durable_exec_ids is None
            else tuple(durable_exec_ids)
        )
        if (
            not known_old_ids
            # Every original ID must be independently present in this exact
            # already-validated bounded (<=1000) native REST proof window.
            or len(known_old_ids) > len(parsed)
            or durable_anchor.exec_id not in known_old_ids
            or any(not isinstance(item, str) or not item for item in known_old_ids)
            or len(set(known_old_ids)) != len(known_old_ids)
            or not set(known_old_ids).issubset(seen)
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_DURABLE_SAME_SEQUENCE_IDS_EVICTED"
            )
        old_match = (
            durable_anchor.exec_id, durable_anchor.seq, old_at,
        )
        new_match = (live_anchor.exec_id, live_anchor.seq, live_at)
        old_positions = [i for i, x in enumerate(parsed) if x == old_match]
        new_positions = [i for i, x in enumerate(parsed) if x == new_match]
        if (
            len(old_positions) != 1 or len(new_positions) != 1
            or old_positions[0] < new_positions[0]
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_ANCHOR_EVICTED_OR_OUT_OF_ORDER"
            )
        replay = build_exact_trade_replay(
            native_rows,
            anchor=durable_anchor,
            cutoff_at=live_at,
            known_exec_ids=set(known_old_ids),
        )
        if old_match == new_match:
            if replay:
                raise P90RealNativeRestartUnverified(
                    "P90_RESTART_SAME_ANCHOR_WITH_UNEXPECTED_TICKS"
                )
        elif (
            not replay
            or (replay[-1].exec_id, replay[-1].seq, replay[-1].traded_at)
               != new_match
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_REPLAY_DOES_NOT_END_ON_NEW_WS_ANCHOR"
            )
        after = mirror.snapshot()
        if (
            after["state"] != "ACTIVE" or after["gaps"] != 0
            or after["epoch"] != before["epoch"]
            or after["epoch_started_at"] != before["epoch_started_at"]
        ):
            raise P90RealNativeRestartUnverified(
                "P90_RESTART_SOURCE_CHANGED_DURING_REST_PROOF"
            )
        return P90RealBoundedNativeRecovery(
            source_epoch_id, source_commit, durable_anchor.symbol,
            durable_anchor, live_anchor, replay, server,
        )
    except P90RealNativeRestartUnverified:
        raise
    except (TypeError, KeyError, ValueError, OverflowError,
            InvalidOperation, ContinuityNotProvable):
        raise P90RealNativeRestartUnverified(
            "P90_RESTART_REST_OR_MIRROR_EVIDENCE_UNVERIFIED"
        ) from None
