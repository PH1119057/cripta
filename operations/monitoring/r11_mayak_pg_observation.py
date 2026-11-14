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


class R11MayakPGSourceUnavailable(RuntimeError):
    """No safe PG-original source session or exact seven panel."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11MayakPGSourceUnavailable("R11_MAYAK_PG_" + reason)


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
    latest = db.execute(
        """SELECT p.publication_id
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
        isinstance(latest, (tuple, list))
        and len(latest) == 1
        and type(latest[0]) is int and latest[0] > 0,
        "ACTIVE_SOURCE_PUBLICATION_MISSING",
    )
    if (
        replay.checkpoint is not None
        and latest[0] <= replay.checkpoint.publication_id
    ):
        return None
    candidate = collect_r11_seven_mayak_candidate(
        db, publication_id=latest[0], observed_at=now,
        previous=replay.checkpoint,
        require_provider_continuity=True,
    )
    _need(
        0 <= (now-candidate.as_of_second).total_seconds()
        <= _MAX_PUBLICATION_LAG_SECONDS,
        "PUBLICATION_STALE",
    )
    snapshot = replay.apply(candidate, now=now)
    # Provider continuity is proven per scope, but WARMUP, durable consumer
    # commit and Dispatcher acceptance remain independent gates.
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
        source_session, release = _source_identity(db)
        restored = load_r11_mayak_consumer_commit(
            db, expected_source_session_id=source_session,
            expected_release_commit=release,
        )
    if restored is None:
        replay = R11MayakPublicationReplay(require_provider_continuity=True)
        generation = 0
        previous_publication_id: int | None = None
    else:
        replay = restored.replay
        replay.require_provider_continuity = True
        generation = restored.generation
        assert replay.checkpoint is not None
        previous_publication_id = replay.checkpoint.publication_id
    while not collector.stop.wait(1.0):
        now = datetime.now(UTC)
        try:
            with psycopg.connect(
                DSN, options="-cdefault_transaction_read_only=on",
            ) as db:
                db.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                snapshot = _read_one(db, replay=replay, now=now)
            if snapshot is None:
                continue
            with psycopg.connect(DSN) as writer:
                committed = commit_r11_mayak_publication(
                    writer, replay=replay, snapshot=snapshot,
                    expected_generation=generation,
                    expected_previous_publication_id=previous_publication_id,
                )
            generation = committed.generation
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
