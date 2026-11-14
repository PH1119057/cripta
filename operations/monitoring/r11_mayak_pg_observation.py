"""#470 R11: opt-in PG-only MAYAK observation worker.

Uses only original, immutable data from ONE MarketSource/MarketPublication.
There is no Bybit public WebSocket, REST or signed Exchange access on this
path. Default service mode remains unchanged until separately controlled
no-start release + physical source continuity and Dispatcher parity.

A bounded PG-source observation can populate operator diagnostics but does
NOT certify original provider coverage, turn on trading or retire old sockets.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.publication_consumer_commit import (
    R11ConsumerCommitUnverified,
    commit_r11_mayak_publication,
    load_r11_mayak_consumer_commit,
    read_r11_mayak_consumer_head,
)
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    R11MayakStateUnverified,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakCollectionUnverified,
    collect_r11_seven_mayak_candidate,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
    R11MayakReplayNotReady,
)

if TYPE_CHECKING:
    from operations.monitoring.mayak_v2 import Collector

_MAX_PUBLICATION_LAG_SECONDS = 30
_MAX_BACKLOG_PUBLICATIONS = 64


class R11MayakPGSourceUnavailable(RuntimeError):
    """No safe PG-original source session or exact seven panel."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11MayakPGSourceUnavailable("R11_MAYAK_PG_" + reason)


def _latest_publication(db: Any) -> tuple[int, datetime, str, str]:
    row = db.execute(
        """SELECT p.publication_id,p.as_of_second,p.source_session_id,
                  p.producer_release_commit
             FROM analytics.market_publications p
             JOIN analytics.native_public_trade_source_sessions s
               ON s.source_session_id=p.source_session_id
            WHERE p.market_source_id=%s
              AND s.market_source_id=p.market_source_id
              AND s.state='ACTIVE'
              AND p.producer_release_commit=s.producer_release_commit
              AND p.publication_status='WATERMARKS_ONLY'
            ORDER BY p.as_of_second DESC,p.publication_id DESC
            LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchone()
    _need(
        isinstance(row, (tuple, list)) and len(row) == 4
        and type(row[0]) is int and row[0] > 0
        and isinstance(row[1], datetime) and row[1].utcoffset() is not None
        and row[1].utcoffset().total_seconds() == 0
        and isinstance(row[2], str) and isinstance(row[3], str),
        "ACTIVE_SOURCE_PUBLICATION_MISSING",
    )
    return int(row[0]), row[1], str(row[2]), str(row[3])


def _next_publication(
    db: Any, *,
    replay: R11MayakPublicationReplay,
    now: datetime,
) -> tuple[int, datetime] | None:
    checkpoint = replay.checkpoint
    if checkpoint is None:
        publication_id, as_of, _session, _release = _latest_publication(db)
        _need(
            0 <= (now - as_of).total_seconds() <= _MAX_PUBLICATION_LAG_SECONDS,
            "PUBLICATION_STREAM_STALE",
        )
        return publication_id, as_of

    rows = db.execute(
        """SELECT p.publication_id,p.as_of_second
             FROM analytics.market_publications p
             JOIN analytics.native_public_trade_source_sessions s
               ON s.source_session_id=p.source_session_id
            WHERE p.market_source_id=%s
              AND s.market_source_id=p.market_source_id
              AND s.state='ACTIVE'
              AND p.source_session_id=%s
              AND p.producer_release_commit=%s
              AND p.publication_status='WATERMARKS_ONLY'
              AND (p.as_of_second,p.publication_id)>(%s,%s)
            ORDER BY p.as_of_second,p.publication_id
            LIMIT %s""",
        (
            BYBIT_PUBLIC_MARKET_SOURCE_ID,
            checkpoint.source_session_id,
            checkpoint.release_commit,
            checkpoint.as_of_second,
            checkpoint.publication_id,
            _MAX_BACKLOG_PUBLICATIONS + 1,
        ),
    ).fetchall()
    _need(isinstance(rows, list), "BACKLOG_QUERY_INVALID")
    _need(
        len(rows) <= _MAX_BACKLOG_PUBLICATIONS,
        "PUBLICATION_BACKLOG_EXCEEDS_BOUND",
    )
    if rows:
        first = rows[0]
        _need(
            isinstance(first, (tuple, list)) and len(first) == 2
            and type(first[0]) is int
            and first[0] > checkpoint.publication_id
            and isinstance(first[1], datetime)
            and first[1].utcoffset() is not None
            and first[1].utcoffset().total_seconds() == 0
            and first[1] >= checkpoint.as_of_second
            and first[1] <= now,
            "BACKLOG_ORDER_INVALID",
        )
        return int(first[0]), first[1]

    latest_id, latest_as_of, latest_session, latest_release = _latest_publication(db)
    _need(
        (latest_session, latest_release)
        == (checkpoint.source_session_id, checkpoint.release_commit),
        "SOURCE_EPOCH_CHANGED",
    )
    _need(
        latest_id == checkpoint.publication_id
        and latest_as_of == checkpoint.as_of_second,
        "DURABLE_CURSOR_NOT_AT_HEAD",
    )
    _need(
        0 <= (now - latest_as_of).total_seconds()
        <= _MAX_PUBLICATION_LAG_SECONDS,
        "PUBLICATION_STREAM_STALE",
    )
    return None


def _read_one(
    db: Any,
    *,
    replay: R11MayakPublicationReplay,
    now: datetime,
) -> dict[str, Any] | None:
    _need(
        isinstance(now, datetime)
        and now.utcoffset() is not None
        and now.utcoffset().total_seconds() == 0,
        "NOW_NOT_UTC",
    )
    _need(
        db.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and db.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "PG_READ_ONLY_REPEATABLE_READ_REQUIRED",
    )
    target = _next_publication(db, replay=replay, now=now)
    if target is None:
        return None
    publication_id, causal_now = target
    candidate = collect_r11_seven_mayak_candidate(
        db, publication_id=publication_id, observed_at=causal_now,
        previous=replay.checkpoint,
        require_provider_continuity=True,
    )
    _need(
        candidate.as_of_second == causal_now,
        "PUBLICATION_CAUSAL_CLOCK_DRIFT",
    )
    snapshot = replay.apply(candidate, now=causal_now)
    _need(
        snapshot["r11_market_publication"]["consumer_ready"] is False
        and snapshot["r11_market_publication"][
            "mayak_ws_retirement_allowed"
        ] is False,
        "FALSE_MARKET_SOURCE_READY",
    )
    return snapshot

def _source_identity(db: Any) -> tuple[str, str]:
    rows = db.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    _need(
        isinstance(rows, list) and len(rows) == 1
        and isinstance(rows[0][0], str)
        and isinstance(rows[0][1], str),
        "ONE_CURRENT_NATIVE_SOURCE_REQUIRED",
    )
    return str(rows[0][0]), str(rows[0][1])


def _restore_or_reset_replay(
    db: Any,
) -> tuple[
    R11MayakPublicationReplay, int, int | None, str | None, str | None,
]:
    source_session, release = _source_identity(db)
    head = read_r11_mayak_consumer_head(db)
    if head is None:
        return (
            R11MayakPublicationReplay(require_provider_continuity=True),
            0, None, None, None,
        )
    if (head.source_session_id, head.release_commit) == (source_session, release):
        restored = load_r11_mayak_consumer_commit(
            db, expected_source_session_id=source_session,
            expected_release_commit=release,
        )
        _need(restored is not None, "CONSUMER_HEAD_WITHOUT_STATE")
        assert restored is not None
        replay = restored.replay
        replay.require_provider_continuity = True
        return replay, restored.generation, head.publication_id, None, None

    previous = load_r11_mayak_consumer_commit(
        db, expected_source_session_id=head.source_session_id,
        expected_release_commit=head.release_commit,
    )
    _need(
        previous is not None
        and previous.generation == head.generation
        and previous.replay.checkpoint is not None
        and previous.replay.checkpoint.publication_id == head.publication_id,
        "SOURCE_RESET_PREVIOUS_STATE_UNVERIFIED",
    )
    return (
        R11MayakPublicationReplay(require_provider_continuity=True),
        head.generation,
        head.publication_id,
        head.source_session_id,
        head.release_commit,
    )


def run_r11_pg_observation(collector: Collector) -> None:
    """PG-only seven-symbol dry-run path; no new market collector sockets.

    PostgreSQL stores one bounded latest-state row. Source cursor, exact replay
    engine, snapshot and Dispatcher handoff commit in one CAS transaction.
    """
    from operations.monitoring.mayak_v2 import DSN

    with psycopg.connect(
        DSN, options="-cdefault_transaction_read_only=on",
    ) as db:
        db.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        (
            replay, generation, previous_publication_id,
            previous_source_session_id, previous_release_commit,
        ) = _restore_or_reset_replay(db)
    while not collector.stop.wait(1.0):
        now = datetime.now(UTC)
        try:
            with psycopg.connect(
                DSN, options="-cdefault_transaction_read_only=on",
            ) as db:
                db.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                current_source = _source_identity(db)
                checkpoint = replay.checkpoint
                if (
                    checkpoint is not None
                    and current_source
                    != (checkpoint.source_session_id, checkpoint.release_commit)
                ):
                    (
                        replay, generation, previous_publication_id,
                        previous_source_session_id, previous_release_commit,
                    ) = _restore_or_reset_replay(db)
                snapshot = _read_one(db, replay=replay, now=now)
            if snapshot is None:
                continue
            with psycopg.connect(DSN) as writer:
                committed = commit_r11_mayak_publication(
                    writer, replay=replay, snapshot=snapshot,
                    expected_generation=generation,
                    expected_previous_publication_id=previous_publication_id,
                    expected_previous_source_session_id=(
                        previous_source_session_id
                    ),
                    expected_previous_release_commit=previous_release_commit,
                )
            generation = committed.generation
            previous_source_session_id = None
            previous_release_commit = None
            assert replay.checkpoint is not None
            previous_publication_id = replay.checkpoint.publication_id
            # Diagnostic JSON is derived only after the authoritative DB row.
            collector.last_snapshot = committed.snapshot
            collector._write_state(committed.snapshot)
        except (
            R11MayakPGSourceUnavailable,
            R11SevenMayakCollectionUnverified,
            R11MayakReplayNotReady,
            R11MayakStateUnverified,
            R11ConsumerCommitUnverified,
            psycopg.Error,
        ) as exc:
            collector._write_error("PG_PUBLICATION", exc)
            raise
