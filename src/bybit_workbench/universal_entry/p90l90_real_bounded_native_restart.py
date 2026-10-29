"""Bounded read-only native recovery certificate for a P90 REAL restart.

Bybit /v5/market/recent-trade returns at most 1000 *latest* public trades and
has no pagination cursor. Recovery from a durable old native cursor is only
possible if the exact old and the new live WS execId/seq are BOTH present in
one independently fetched complete latest-trade slice. Otherwise fail closed:
neither OHLC nor the new live WS can prove an earlier excursion.

This is one native coverage gate, NOT an open-position restart engine, an
account-specific signed REST proof, an execution permission or an Exit action.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    """No complete exact public-trade interval across process restart."""


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
) -> P90RealBoundedNativeRecovery:
    """Require exact persisted+new WS anchors in one bounded Bybit REST slice.

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
            or not isinstance(latest_rest_rows, (tuple, list))
            or not 1 <= len(latest_rest_rows) <= 1000
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
        for row in latest_rest_rows:
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
                    seq > prev_seq or (seq == prev_seq and timestamp > prev_at)
                ))
            ):
                raise P90RealNativeRestartUnverified(
                    "P90_RESTART_REST_CAUSAL_ORDER_OR_PRICE_INVALID"
                )
            parsed.append((name, seq, timestamp))
            prev_seq, prev_at = seq, timestamp
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
            latest_rest_rows,
            anchor=durable_anchor,
            cutoff_at=live_at,
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
