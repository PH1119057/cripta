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
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    R11MayakStateUnverified,
    atomic_write_r11_mayak_state,
    decode_r11_mayak_state,
    encode_r11_mayak_state,
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
_DEFAULT_CHECKPOINT_NAME = "r11_market_publication_checkpoint.bin"


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
) -> dict[str, Any]:
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

    File writes are bounded and atomic. Source cursor is checkpointed only
    together with the exact replay engine; no fabricated persisted
    coin-context/Dispatcher acceptance is emitted on an unverified source.
    """
    from operations.monitoring.mayak_v2 import DSN, STATE_PATH

    _need(
        STATE_PATH.is_absolute() and STATE_PATH.parent.is_dir(),
        "OPERATOR_STATE_PARENT_NOT_PREPARED",
    )
    saved = STATE_PATH.with_name(_DEFAULT_CHECKPOINT_NAME)
    _need(
        saved.parent == STATE_PATH.parent
        and not saved.is_symlink(),
        "STATE_CHECKPOINT_PATH_INVALID",
    )
    replay = R11MayakPublicationReplay(require_provider_continuity=True)
    if saved.exists():
        binary = saved.read_bytes()
        with psycopg.connect(
            DSN, options="-cdefault_transaction_read_only=on",
        ) as db:
            db.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            source_session, release = _source_identity(db)
            replay = decode_r11_mayak_state(
                binary, expected_source_session_id=source_session,
                expected_release_commit=release,
            )
            replay.require_provider_continuity = True
    last_saved_minute: datetime | None = None
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
            minute = now.replace(second=0, microsecond=0)
            if last_saved_minute is None or minute > last_saved_minute:
                atomic_write_r11_mayak_state(
                    saved, encode_r11_mayak_state(replay),
                )
                last_saved_minute = minute
            # NO write to mayak_v2/dispatcher_v2 until source continuity,
            # WARMUP, actual PG/WS BEFORE-AFTER parity and rollback prove it.
            collector.last_snapshot = snapshot
            collector._write_state(snapshot)
        except (
            R11MayakPGSourceUnavailable,
            R11SevenMayakCollectionUnverified,
            R11MayakReplayNotReady,
            R11MayakStateUnverified,
            psycopg.Error,
        ) as exc:
            collector._write_error("PG_PUBLICATION", exc)
            raise
