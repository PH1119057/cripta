"""#470 R11: one commit for PG-only MAYAK derived facts and replay cursor.

Executed only by the existing MAYAK observer actor (never by native source).
All facts remain observation-only; snapshot may be WARMUP/DEGRADED, never
used as a source of Strategy/Entry/Exit or an implicit LIVE admission.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.compact_analytics import (
    compact_market_minute_insert_sql,
    compact_market_minute_rows,
)
from bybit_workbench.mayak.context_records import (
    coin_market_context_records,
    shared_market_context_record,
)
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    decode_r11_mayak_state,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)

_SEVEN = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
))
_CONSUMER = "MAYAK_SEVEN_PG_V1"
_MAX_STATE = 25165824


class R11MayakDurableCommitUnverified(RuntimeError):
    """No aligned source/derived-snapshot/consumer commit is authorized."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11MayakDurableCommitUnverified(
            "R11_MAYAK_CONSUMER_" + reason
        )


def _identity(state: bytes) -> str:
    _need(isinstance(state, bytes) and 1 <= len(state) <= _MAX_STATE,
          "ENGINE_STATE_SIZE_INVALID")
    return hashlib.sha256(state).hexdigest()


def read_r11_mayak_durable_checkpoint(
    connection: Any, *,
    source_session_id: str,
    producer_release_commit: str,
) -> tuple[R11SevenMayakReplayCheckpoint | None, bytes | None]:
    """Read-only restart from one previously atomically persisted MAYAK cycle.

    Existing operator-run state is not trusted without current source epoch.
    Loading a private file without a committed PG snapshot would be unsafe.
    """
    _need(
        connection.execute("SHOW transaction_read_only").fetchone() == ("on",)
        and connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",),
        "READ_ONLY_RESTART_REQUIRED",
    )
    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  publication_as_of,snapshot_id,config_fingerprint,state_sha256,
                  replay_engine_state,consumer_quality
             FROM mayak_v2.r11_market_publication_checkpoints
            WHERE consumer_key=%s""", (_CONSUMER,),
    ).fetchone()
    if row is None:
        return None, None
    _need(
        isinstance(row, (tuple, list)) and len(row) == 9
        and row[1] == source_session_id
        and row[2] == producer_release_commit
        and row[8] in ("WARMUP", "DEGRADED"),
        "SAVED_SOURCE_IDENTITY_OR_QUALITY_INVALID",
    )
    pid, _sid, _release, as_of, snapshot_id, fingerprint, checksum, state, _quality = row
    _need(
        type(pid) is int and pid > 0
        and type(snapshot_id) is int and snapshot_id > 0
        and isinstance(state, (bytes, memoryview))
        and isinstance(checksum, str)
        and hashlib.sha256(bytes(state)).hexdigest() == checksum,
        "SAVED_ENGINE_DIGEST_INVALID",
    )
    restored = decode_r11_mayak_state(
        bytes(state), expected_source_session_id=source_session_id,
        expected_release_commit=producer_release_commit,
    )
    _need(
        restored.checkpoint is not None
        and restored.checkpoint.publication_id == pid
        and restored.checkpoint.as_of_second == as_of
        and restored.engine.configuration_fingerprint() == fingerprint,
        "SAVED_ENGINE_PUBLICATION_OR_FINGERPRINT_INVALID",
    )
    physical = connection.execute(
        """SELECT p.source_session_id,p.producer_release_commit,
                  p.as_of_second,s.snapshot_kind
             FROM analytics.market_publications p
             JOIN mayak_v2.snapshots s ON s.id=%s
            WHERE p.publication_id=%s""",
        (snapshot_id, pid),
    ).fetchone()
    _need(
        physical == (
            source_session_id, producer_release_commit, as_of, "REGULAR",
        ),
        "SAVED_PHYSICAL_PUBLICATION_OR_SNAPSHOT_MISSING",
    )
    return restored.checkpoint, bytes(state)


def persist_r11_mayak_original_cycle(
    connection: Any, *,
    snapshot: dict[str, Any],
    engine_state: bytes,
    previous: R11SevenMayakReplayCheckpoint | None,
    current: R11SevenMayakReplayCheckpoint,
) -> int:
    """Transaction-atomic read-model, Dispatcher facts and exact source cursor.

    The source epoch must be current and row-locked while the consumer
    commits. Neither source writer nor another consumer may create false
    continuity by recycling a GAP publication. Every conflict fails closed.
    """
    _need(
        isinstance(snapshot, dict)
        and isinstance(current, R11SevenMayakReplayCheckpoint)
        and current.provider_continuity_verified is False
        and current.mayak_cutover_ready is False
        and snapshot.get("r11_market_publication", {}).get(
            "publication_id"
        ) == current.publication_id
        and snapshot.get("r11_market_publication", {}).get(
            "source_session_id"
        ) == current.source_session_id
        and snapshot.get("r11_market_publication", {}).get(
            "consumer_ready"
        ) is False
        and snapshot.get("r11_market_publication", {}).get(
            "mayak_ws_retirement_allowed"
        ) is False
        and set(snapshot.get("coins", {})) == _SEVEN
        and set(snapshot.get("coin_market_contexts", {})) == _SEVEN
        and snapshot.get("dispatcher_handoff", {}).get(
            "provenance", {}
        ).get("r11_market_publication_id") == current.publication_id,
        "DERIVED_SOURCE_OR_SEVEN_SCOPE_INVALID",
    )
    checked = decode_r11_mayak_state(
        engine_state, expected_source_session_id=current.source_session_id,
        expected_release_commit=current.release_commit,
    )
    _need(
        checked.checkpoint == current
        and checked.engine.configuration_fingerprint()
        == snapshot["config_fingerprint"],
        "REPLAY_BYTES_NOT_CURRENT",
    )
    digest = _identity(engine_state)
    observed = datetime.fromisoformat(str(snapshot["observed_at"]))
    _need(
        observed.utcoffset() == timedelta(0)
        and current.as_of_second <= observed
        and observed - current.as_of_second <= timedelta(seconds=30),
        "OBSERVATION_TIME_OR_PUBLICATION_STALE",
    )
    # The caller opens the connection with READ WRITE and owns this unit of
    # work; the lock and all writes are contained in exactly one transaction.
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='1500ms'")
        connection.execute("SET LOCAL statement_timeout='15s'")
        # Only one MAYAK PG cursor consumer may commit at a time.
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("CRIPTA_R11_MAYAK_PG_CONSUMER_V1",),
        )
        epochs = connection.execute(
            """SELECT source_session_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE market_source_id=%s AND state='ACTIVE'
                FOR SHARE""",
            (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
        ).fetchall()
        _need(
            epochs == [(current.source_session_id, current.release_commit)],
            "SOURCE_GAP_OR_MULTIPLE_WRITERS",
        )
        publication = connection.execute(
            """SELECT source_session_id,producer_release_commit,as_of_second
                 FROM analytics.market_publications
                WHERE publication_id=%s
                  AND market_source_id=%s
                  AND publication_status='WATERMARKS_ONLY'""",
            (current.publication_id, BYBIT_PUBLIC_MARKET_SOURCE_ID),
        ).fetchone()
        _need(
            publication == (
                current.source_session_id, current.release_commit,
                current.as_of_second,
            ),
            "PG_PUBLICATION_CHANGED",
        )
        preceding = connection.execute(
            """SELECT publication_id,source_session_id,
                      producer_release_commit,publication_as_of
                 FROM mayak_v2.r11_market_publication_checkpoints
                WHERE consumer_key=%s FOR UPDATE""",
            (_CONSUMER,),
        ).fetchone()
        if previous is None:
            _need(preceding is None, "UNANCHORED_RESTART_OR_DUPLICATE")
        else:
            _need(
                preceding == (
                    previous.publication_id, previous.source_session_id,
                    previous.release_commit, previous.as_of_second,
                )
                and current.publication_id >= previous.publication_id
                and current.as_of_second >= previous.as_of_second,
                "PERSISTED_SOURCE_CURSOR_DRIFT",
            )
        row = connection.execute(
            """INSERT INTO mayak_v2.snapshots(
                 observed_at,state,confidence,payload,engine_version,
                 snapshot_kind,regular_minute)
               VALUES (%s,%s,%s,%s,%s,'REGULAR',
                       date_trunc('minute',%s::timestamptz))
               ON CONFLICT (regular_minute)
                 WHERE snapshot_kind='REGULAR' DO NOTHING
               RETURNING id""",
            (
                observed, str(snapshot["state"]),
                snapshot["confidence"], Jsonb(snapshot),
                snapshot["engine_version"], observed,
            ),
        ).fetchone()
        _need(
            isinstance(row, (tuple, list)) and len(row) == 1
            and type(row[0]) is int and row[0] > 0,
            "REGULAR_SNAPSHOT_COLLISION",
        )
        snapshot_id = row[0]
        records = coin_market_context_records(snapshot_id, snapshot)
        _need(len(records) == 7, "DERIVED_COIN_CONTEXT_COUNT_INVALID")
        for record in records:
            inserted = connection.execute(
                """INSERT INTO mayak_v2.coin_market_contexts(
                     coin_context_id,market_source_id,
                     spot_instrument_id,linear_instrument_id,
                     mayak_snapshot_id,observed_at,symbol,schema_version,
                     engine_version,feature_version,config_fingerprint,
                     data_quality,payload,provenance,content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (coin_context_id) DO NOTHING
                   RETURNING coin_context_id""",
                (
                    record["coin_context_id"],record["market_source_id"],
                    record["spot_instrument_id"],record["linear_instrument_id"],
                    snapshot_id,record["observed_at"],record["symbol"],
                    record["schema_version"],record["engine_version"],
                    record["feature_version"],record["config_fingerprint"],
                    record["data_quality"],Jsonb(record["payload"]),
                    Jsonb(record["provenance"]),record["content_hash"],
                ),
            ).fetchone()
            _need(inserted is not None, "COIN_CONTEXT_COLLISION")
        handoff = shared_market_context_record(snapshot_id, snapshot)
        global_row = connection.execute(
            """INSERT INTO mayak_v2.shared_market_contexts(
                 market_context_id,market_source_id,mayak_snapshot_id,
                 observed_at,mayak_version,schema_version,
                 config_fingerprint,data_quality,payload,provenance,
                 content_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (market_context_id) DO NOTHING
               RETURNING market_context_id""",
            (
                handoff["market_context_id"],handoff["market_source_id"],
                snapshot_id,handoff["observed_at"],handoff["mayak_version"],
                handoff["schema_version"],handoff["config_fingerprint"],
                handoff["data_quality"],Jsonb(handoff["payload"]),
                Jsonb(handoff["provenance"]),handoff["content_hash"],
            ),
        ).fetchone()
        _need(global_row is not None, "DISPATCHER_HANDOFF_COLLISION")
        data_quality = {
            symbol: item.get("quality", {})
            for symbol, item in snapshot["coins"].items()
        }
        connection.execute(
            """INSERT INTO mayak_v2.observation_journal(
                 snapshot_id,observed_at,market_state,confidence,reasons,
                 price_breadth,money_breadth,direction_synchronization,
                 btc_context,eth_context,data_quality,
                 architecture_version,engine_version,feature_version,
                 config_fingerprint,dispatcher_handoff)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                snapshot_id,observed,str(snapshot["state"]),
                snapshot["confidence"],Jsonb(snapshot["reasons"]),
                Jsonb(snapshot["price_breadth"]),
                Jsonb(snapshot["money_breadth"]),
                Jsonb(snapshot["direction_synchronization"]),
                Jsonb(snapshot.get("btc")),Jsonb(snapshot.get("eth")),
                Jsonb(data_quality),snapshot["architecture_version"],
                snapshot["engine_version"],snapshot["feature_version"],
                snapshot["config_fingerprint"],
                Jsonb(snapshot["dispatcher_handoff"]),
            ),
        )
        compact = compact_market_minute_rows(snapshot_id, snapshot)
        if compact:
            connection.cursor().executemany(
                compact_market_minute_insert_sql(), compact,
            )
        # A complete exact checkpoint and its derived rows commit together.
        connection.execute(
            """INSERT INTO mayak_v2.r11_market_publication_checkpoints(
                 consumer_key,publication_id,source_session_id,
                 producer_release_commit,publication_as_of,snapshot_id,
                 config_fingerprint,state_sha256,replay_engine_state,
                 consumer_quality)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'WARMUP')
               ON CONFLICT (consumer_key) DO UPDATE SET
                 publication_id=EXCLUDED.publication_id,
                 source_session_id=EXCLUDED.source_session_id,
                 producer_release_commit=EXCLUDED.producer_release_commit,
                 publication_as_of=EXCLUDED.publication_as_of,
                 snapshot_id=EXCLUDED.snapshot_id,
                 config_fingerprint=EXCLUDED.config_fingerprint,
                 state_sha256=EXCLUDED.state_sha256,
                 replay_engine_state=EXCLUDED.replay_engine_state,
                 consumer_quality='WARMUP',
                 stored_at=clock_timestamp()""",
            (
                _CONSUMER,current.publication_id,current.source_session_id,
                current.release_commit,current.as_of_second,snapshot_id,
                snapshot["config_fingerprint"],digest,engine_state,
            ),
        )
        return snapshot_id
