"""R11 #470: one atomic authoritative MAYAK publication consumer commit.

The single bounded PostgreSQL row contains the exact engine/source checkpoint
and the snapshot/Dispatcher handoff calculated from it.  PostgreSQL commit is
the only authority: diagnostic JSON files are derived after commit and cannot
advance a cursor.  The table retains no publication history.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    decode_r11_mayak_state,
    encode_r11_mayak_state,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

_CONSUMER_ID = "R11_PG_ORIGINAL_MAYAK_V1"
_SEVEN = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
))
_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_EPOCH_GUARD_LOCK_PREFIX = "CRIPTA:R07:SOURCE_EPOCH_TRANSITION:V1:"
_SOURCE_EPOCH_TRIGGER_SQL = """
SELECT EXISTS (
    SELECT 1
      FROM pg_catalog.pg_trigger t
     WHERE t.tgrelid='analytics.native_public_trade_source_sessions'::regclass
       AND t.tgname='r07_source_epoch_guard_v1'
       AND t.tgenabled='O'
       AND NOT t.tgisinternal
       AND t.tgtype=31
       AND t.tgfoid='analytics.r07_source_epoch_tx_guard_v1()'::regprocedure
       AND pg_catalog.pg_get_triggerdef(t.oid) LIKE '%source_session_id%'
)
"""
FaultInjector = Callable[[str], None]


class R11ConsumerCommitUnverified(RuntimeError):
    """Mixed cursor/engine/snapshot generation or CAS state is invalid."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11ConsumerCommitUnverified("R11_CONSUMER_COMMIT_" + reason)


def _fence_source_epoch(
    connection: Any, *, source_session_id: str, release_commit: str,
    publication_id: int, publication_as_of: datetime,
) -> None:
    """Hold the R07 shared epoch fence through this consumer transaction."""
    connection.execute(
        "LOCK TABLE analytics.native_public_trade_source_sessions "
        "IN ROW EXCLUSIVE MODE NOWAIT"
    )
    guard = connection.execute(_SOURCE_EPOCH_TRIGGER_SQL).fetchone()
    _need(guard == (True,), "SOURCE_EPOCH_GUARD_MISSING_OR_DISABLED")
    lock = connection.execute(
        "SELECT pg_catalog.pg_try_advisory_xact_lock_shared("
        "pg_catalog.hashtextextended(%s,0))",
        (_EPOCH_GUARD_LOCK_PREFIX + BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchone()
    _need(lock == (True,), "SOURCE_EPOCH_LOCK_UNAVAILABLE")
    active = connection.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY source_session_id""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    _need(
        active == [(source_session_id, release_commit)],
        "SOURCE_EPOCH_DRIFT",
    )
    publication = connection.execute(
        """SELECT source_session_id,producer_release_commit,as_of_second,
                  publication_status
             FROM analytics.market_publications
            WHERE publication_id=%s AND market_source_id=%s""",
        (publication_id, BYBIT_PUBLIC_MARKET_SOURCE_ID),
    ).fetchone()
    _need(
        publication == (
            source_session_id, release_commit, publication_as_of,
            "WATERMARKS_ONLY",
        ),
        "PHYSICAL_PUBLICATION_DRIFT",
    )


def _canonical_snapshot(snapshot: dict[str, Any]) -> bytes:
    try:
        binary = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise R11ConsumerCommitUnverified(
            "R11_CONSUMER_COMMIT_SNAPSHOT_JSON_INVALID"
        ) from exc
    _need(len(binary) <= _MAX_SNAPSHOT_BYTES, "SNAPSHOT_TOO_LARGE")
    return binary


def _snapshot_matches_replay(
    replay: R11MayakPublicationReplay,
    snapshot: dict[str, Any],
) -> bool:
    checkpoint = replay.checkpoint
    if checkpoint is None:
        return False
    status = snapshot.get("r11_market_publication")
    handoff = snapshot.get("dispatcher_handoff")
    contexts = snapshot.get("coin_market_contexts")
    return (
        isinstance(status, dict)
        and status.get("publication_id") == checkpoint.publication_id
        and status.get("source_session_id") == checkpoint.source_session_id
        and status.get("producer_release_commit") == checkpoint.release_commit
        and status.get("as_of_second") == checkpoint.as_of_second.isoformat()
        and status.get("consumer_ready") is False
        and status.get("mayak_ws_retirement_allowed") is False
        and isinstance(snapshot.get("coins"), dict)
        and set(snapshot["coins"]) == _SEVEN
        and isinstance(contexts, dict) and set(contexts) == _SEVEN
        and isinstance(handoff, dict)
        and handoff.get("provenance", {}).get(
            "r11_market_publication_id"
        ) == checkpoint.publication_id
        and handoff.get("provenance", {}).get(
            "r11_source_session_id"
        ) == checkpoint.source_session_id
        and handoff.get("provenance", {}).get("trading_command") is False
        and snapshot.get("config_fingerprint")
        == replay.engine.configuration_fingerprint()
        and handoff.get("provenance", {}).get("config_fingerprint")
        == snapshot.get("config_fingerprint")
        and all(
            row.get("provenance", {}).get("trading_command") is False
            for row in contexts.values()
        )
    )


@dataclass(frozen=True, slots=True)
class R11ConsumerCommit:
    generation: int
    replay: R11MayakPublicationReplay
    snapshot: dict[str, Any]
    state_sha256: str
    snapshot_sha256: str
    committed_at: datetime | None = None


def commit_r11_mayak_publication(
    connection: Any, *,
    replay: R11MayakPublicationReplay,
    snapshot: dict[str, Any],
    expected_generation: int,
    expected_previous_publication_id: int | None,
    fault_injector: FaultInjector | None = None,
) -> R11ConsumerCommit:
    """CAS one full generation; every injected pre-commit crash rolls back."""
    _need(
        isinstance(replay, R11MayakPublicationReplay)
        and replay.checkpoint is not None
        and not replay.failed
        and isinstance(snapshot, dict)
        and type(expected_generation) is int and expected_generation >= 0
        and (
            expected_previous_publication_id is None
            or type(expected_previous_publication_id) is int
            and expected_previous_publication_id > 0
        )
        and _snapshot_matches_replay(replay, snapshot),
        "INPUT_GENERATION_INVALID",
    )
    checkpoint = replay.checkpoint
    assert checkpoint is not None
    state = encode_r11_mayak_state(replay)
    snapshot_binary = _canonical_snapshot(snapshot)
    state_sha = hashlib.sha256(state).hexdigest()
    snapshot_sha = hashlib.sha256(snapshot_binary).hexdigest()
    if fault_injector is not None:
        fault_injector("AFTER_ENCODE")

    from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

    generation = expected_generation + 1
    with connection.transaction():
        _fence_source_epoch(
            connection,
            source_session_id=checkpoint.source_session_id,
            release_commit=checkpoint.release_commit,
            publication_id=checkpoint.publication_id,
            publication_as_of=checkpoint.as_of_second,
        )
        if fault_injector is not None:
            fault_injector("AFTER_SOURCE_FENCE")
        current = connection.execute(
            """SELECT generation,publication_id,source_session_id,
                      producer_release_commit
                 FROM mayak_v2.r11_consumer_commit
                WHERE consumer_id=%s FOR UPDATE""",
            (_CONSUMER_ID,),
        ).fetchone()
        if current is None:
            _need(
                expected_generation == 0
                and expected_previous_publication_id is None,
                "MISSING_EXPECTED_PREVIOUS_ROW",
            )
        else:
            _need(
                isinstance(current, (tuple, list)) and len(current) == 4
                and current[0] == expected_generation
                and current[1] == expected_previous_publication_id
                and current[2] == checkpoint.source_session_id
                and current[3] == checkpoint.release_commit
                and checkpoint.publication_id > current[1],
                "CAS_OR_PUBLICATION_REGRESSION",
            )
        if fault_injector is not None:
            fault_injector("AFTER_LOCK")
        saved = connection.execute(
            """INSERT INTO mayak_v2.r11_consumer_commit(
                   consumer_id,generation,market_source_id,source_session_id,
                   producer_release_commit,publication_id,publication_as_of,
                   config_fingerprint,state_checkpoint,state_sha256,snapshot,
                   snapshot_sha256)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(consumer_id) DO UPDATE SET
                   generation=EXCLUDED.generation,
                   market_source_id=EXCLUDED.market_source_id,
                   source_session_id=EXCLUDED.source_session_id,
                   producer_release_commit=EXCLUDED.producer_release_commit,
                   publication_id=EXCLUDED.publication_id,
                   publication_as_of=EXCLUDED.publication_as_of,
                   config_fingerprint=EXCLUDED.config_fingerprint,
                   state_checkpoint=EXCLUDED.state_checkpoint,
                   state_sha256=EXCLUDED.state_sha256,
                   snapshot=EXCLUDED.snapshot,
                   snapshot_sha256=EXCLUDED.snapshot_sha256,
                   committed_at=clock_timestamp()
               RETURNING generation""",
            (
                _CONSUMER_ID, generation, BYBIT_PUBLIC_MARKET_SOURCE_ID,
                checkpoint.source_session_id, checkpoint.release_commit,
                checkpoint.publication_id, checkpoint.as_of_second,
                replay.engine.configuration_fingerprint(), state, state_sha,
                Jsonb(snapshot, dumps=lambda value: json.dumps(
                    value, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str, allow_nan=False,
                )),
                snapshot_sha,
            ),
        ).fetchone()
        _need(saved == (generation,), "WRITE_RETURNING_DRIFT")
        if fault_injector is not None:
            fault_injector("AFTER_WRITE")
    if fault_injector is not None:
        fault_injector("AFTER_COMMIT")
    return R11ConsumerCommit(
        generation=generation, replay=replay, snapshot=snapshot,
        state_sha256=state_sha, snapshot_sha256=snapshot_sha,
    )


def load_r11_mayak_consumer_commit(
    connection: Any, *,
    expected_source_session_id: str,
    expected_release_commit: str,
) -> R11ConsumerCommit | None:
    """Restore only a digest-valid, internally matched whole generation."""
    row = connection.execute(
        """SELECT generation,market_source_id,source_session_id,
                  producer_release_commit,publication_id,publication_as_of,
                  config_fingerprint,state_checkpoint,state_sha256,snapshot,
                  snapshot_sha256,committed_at
             FROM mayak_v2.r11_consumer_commit
            WHERE consumer_id=%s""",
        (_CONSUMER_ID,),
    ).fetchone()
    if row is None:
        return None
    _need(isinstance(row, (tuple, list)) and len(row) == 12,
          "RESTORE_ROW_SHAPE_INVALID")
    (
        generation, source, session, release, publication_id, as_of,
        fingerprint, state, state_sha, snapshot, snapshot_sha, committed_at,
    ) = row
    _need(
        type(generation) is int and generation > 0
        and source == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and session == expected_source_session_id
        and release == expected_release_commit
        and type(publication_id) is int and publication_id > 0
        and isinstance(as_of, datetime) and as_of.tzinfo is not None
        and as_of.utcoffset() == timedelta(0)
        and isinstance(fingerprint, str) and len(fingerprint) == 64
        and isinstance(state, bytes)
        and hashlib.sha256(state).hexdigest() == state_sha
        and isinstance(snapshot, dict),
        "RESTORE_IDENTITY_OR_DIGEST_INVALID",
    )
    snapshot_binary = _canonical_snapshot(snapshot)
    _need(
        hashlib.sha256(snapshot_binary).hexdigest() == snapshot_sha,
        "RESTORE_SNAPSHOT_DIGEST_INVALID",
    )
    replay = decode_r11_mayak_state(
        state,
        expected_source_session_id=session,
        expected_release_commit=release,
    )
    _need(
        replay.checkpoint is not None
        and replay.checkpoint.publication_id == publication_id
        and replay.checkpoint.as_of_second == as_of
        and replay.engine.configuration_fingerprint() == fingerprint
        and _snapshot_matches_replay(replay, snapshot),
        "RESTORE_MIXED_GENERATION",
    )
    return R11ConsumerCommit(
        generation=generation, replay=replay, snapshot=snapshot,
        state_sha256=state_sha, snapshot_sha256=snapshot_sha,
        committed_at=committed_at,
    )
