"""#470 R11: atomic consumer/source-epoch fence on disposable PostgreSQL 16."""
from __future__ import annotations

import os
import queue
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from operations.monitoring import r11_mayak_pg_observation as pg
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.native_trade_replay_parity import R11ReplayCursor
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.publication_consumer_commit import (
    commit_r11_mayak_publication,
    load_r11_mayak_consumer_commit,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

DSN = os.environ.get("CRIPTA_R11_DISPOSABLE_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN or "/r11_publication_ci" not in DSN,
    reason="R11 disposable PostgreSQL16 (r11_publication_ci) explicitly required",
)
SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
SESSION = "a" * 32
PRODUCER = "b" * 40
NEW_SESSION = "e" * 32
NEW_PRODUCER = "f" * 40
AT = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)
SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)


def _replay(
    publication_id: int = 100, *,
    session: str = SESSION,
    producer: str = PRODUCER,
    at: datetime = AT,
) -> R11MayakPublicationReplay:
    checkpoint = R11SevenMayakReplayCheckpoint(
        publication_id=publication_id,
        source_session_id=session,
        release_commit=producer,
        as_of_second=at,
        optional_cursors=tuple(
            R11OptionalMayakCursor(
                source_session_id=session,
                transport_epoch_id=("c" * 32 if product == "SPOT" else session),
                product_category=product,
                producer_release_commit=producer,
                next_cursor=publication_id,
                next_digest="d" * 64,
                books=(),
            )
            for product in ("SPOT", "LINEAR")
        ),
        native_cursors=tuple(
            R11ReplayCursor(
                symbol=symbol,
                source_session_id=session,
                producer_release_commit=producer,
                publication_id=publication_id,
                publication_as_of=at,
                publication_watermark=(1, 0),
                cursor=(1, 0),
                last_seq=1,
                last_event_at=at - timedelta(seconds=1),
                recent_exec_ids=(),
            )
            for symbol in SEVEN
        ),
    )
    replay = R11MayakPublicationReplay(
        checkpoint=checkpoint,
        first_publication_at=at - timedelta(minutes=1),
        last_original_event_at=at.timestamp() - 1,
        observations=1,
    )
    replay.engine.set_instrument_support("spot", set(SEVEN))
    replay.engine.set_instrument_support("linear", set(SEVEN))
    return replay


def _snapshot(replay: R11MayakPublicationReplay) -> dict[str, Any]:
    assert replay.checkpoint is not None
    value = replay.engine.snapshot(replay.checkpoint.as_of_second)
    value["r11_market_publication"] = {
        "publication_id": replay.checkpoint.publication_id,
        "source_session_id": replay.checkpoint.source_session_id,
        "producer_release_commit": replay.checkpoint.release_commit,
        "as_of_second": replay.checkpoint.as_of_second.isoformat(),
        "source_mode": "PG_ORIGINAL_REPLAY",
        "consumer_ready": False,
        "mayak_ws_retirement_allowed": False,
        "trading_effect": "NONE",
    }
    value["dispatcher_handoff"]["provenance"].update({
        "r11_market_publication_id": replay.checkpoint.publication_id,
        "r11_source_session_id": replay.checkpoint.source_session_id,
    })
    return value


def _apply(admin: psycopg.Connection[Any], relative: str) -> None:
    sql = Path(relative).read_text(encoding="utf-8")
    admin.execute(sql)


@pytest.fixture
def db():
    admin = psycopg.connect(DSN, autocommit=True)
    assert admin.info.dbname == "r11_publication_ci"
    for schema in ("mayak_v2", "analytics", "identity"):
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.execute(f"CREATE SCHEMA {schema}")
    admin.execute(
        "DO $$ BEGIN CREATE ROLE cripta; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    admin.execute("GRANT USAGE ON SCHEMA identity,analytics,mayak_v2 TO cripta")
    admin.execute(
        "CREATE TABLE identity.market_sources (market_source_id text PRIMARY KEY)"
    )
    admin.execute(
        "INSERT INTO identity.market_sources VALUES (%s)", (SOURCE,)
    )
    admin.execute(
        """CREATE TABLE analytics.native_public_trade_source_sessions (
            source_session_id text PRIMARY KEY,
            market_source_id text NOT NULL,
            state text NOT NULL,
            producer_release_commit text NOT NULL,
            subscribed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )"""
    )
    admin.execute(
        """CREATE TABLE analytics.market_publications (
            publication_id bigint PRIMARY KEY,
            source_session_id text NOT NULL REFERENCES
                analytics.native_public_trade_source_sessions(source_session_id),
            market_source_id text NOT NULL,
            producer_release_commit text NOT NULL,
            as_of_second timestamptz NOT NULL,
            publication_status text NOT NULL
        )"""
    )
    admin.execute(
        "GRANT SELECT,INSERT,UPDATE ON analytics.native_public_trade_source_sessions TO cripta"
    )
    admin.execute("GRANT SELECT ON analytics.market_publications TO cripta")
    admin.execute(
        """CREATE TABLE mayak_v2.snapshots (
            id bigserial PRIMARY KEY,
            observed_at timestamptz NOT NULL,
            state text NOT NULL,
            confidence double precision NOT NULL,
            payload jsonb NOT NULL,
            engine_version text NOT NULL,
            snapshot_kind text NOT NULL DEFAULT 'LEGACY',
            regular_minute timestamptz
        )"""
    )
    admin.execute(
        """CREATE UNIQUE INDEX mayak_v2_one_regular_per_minute
           ON mayak_v2.snapshots(regular_minute)
           WHERE snapshot_kind='REGULAR'"""
    )
    admin.execute(
        """CREATE TABLE mayak_v2.state_events (
            id bigserial PRIMARY KEY,
            occurred_at timestamptz NOT NULL,
            state text NOT NULL,
            previous_state text,
            confidence double precision NOT NULL,
            reasons jsonb NOT NULL,
            snapshot_id bigint REFERENCES mayak_v2.snapshots(id)
        )"""
    )
    admin.execute(
        """CREATE TABLE mayak_v2.observation_journal (
            snapshot_id bigint PRIMARY KEY REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL,
            market_state text NOT NULL,
            confidence double precision NOT NULL,
            reasons jsonb NOT NULL,
            price_breadth jsonb NOT NULL,
            money_breadth jsonb NOT NULL,
            direction_synchronization jsonb NOT NULL,
            btc_context jsonb,
            eth_context jsonb,
            data_quality jsonb NOT NULL,
            architecture_version text NOT NULL,
            engine_version text NOT NULL,
            feature_version text NOT NULL,
            config_fingerprint text NOT NULL,
            dispatcher_handoff jsonb
        )"""
    )
    admin.execute(
        """CREATE TABLE mayak_v2.coin_market_contexts (
            coin_context_id text PRIMARY KEY,
            market_source_id text NOT NULL,
            spot_instrument_id text NOT NULL,
            linear_instrument_id text NOT NULL,
            mayak_snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL,
            symbol text NOT NULL,
            schema_version text NOT NULL,
            engine_version text NOT NULL,
            feature_version text NOT NULL,
            config_fingerprint text NOT NULL,
            data_quality text NOT NULL,
            payload jsonb NOT NULL,
            provenance jsonb NOT NULL,
            content_hash text UNIQUE NOT NULL
        )"""
    )
    admin.execute(
        """CREATE TABLE mayak_v2.shared_market_contexts (
            market_context_id text PRIMARY KEY,
            market_source_id text NOT NULL,
            mayak_snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL,
            mayak_version text NOT NULL,
            schema_version text NOT NULL,
            config_fingerprint text NOT NULL,
            data_quality text NOT NULL,
            payload jsonb NOT NULL,
            provenance jsonb NOT NULL,
            content_hash text UNIQUE NOT NULL
        )"""
    )
    admin.execute(
        """GRANT SELECT,INSERT ON mayak_v2.snapshots,
           mayak_v2.state_events,mayak_v2.observation_journal,
           mayak_v2.coin_market_contexts,mayak_v2.shared_market_contexts
           TO cripta"""
    )
    admin.execute(
        "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA mayak_v2 TO cripta"
    )
    admin.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions "
        "(source_session_id,market_source_id,state,producer_release_commit,subscribed_at) "
        "VALUES (%s,%s,'ACTIVE',%s,%s)",
        (SESSION, SOURCE, PRODUCER, AT - timedelta(minutes=5)),
    )
    admin.execute(
        "INSERT INTO analytics.market_publications "
        "(publication_id,source_session_id,market_source_id,producer_release_commit,"
        "as_of_second,publication_status) VALUES (100,%s,%s,%s,%s,'WATERMARKS_ONLY')",
        (SESSION, SOURCE, PRODUCER, AT),
    )
    _apply(admin, "operations/sql/20261114_r07_source_epoch_transaction_guard.sql")
    _apply(admin, "operations/sql/20261115_r11_mayak_consumer_commit.sql")
    try:
        yield admin
    finally:
        admin.close()


def _runtime() -> psycopg.Connection[Any]:
    connection = psycopg.connect(DSN)
    connection.execute("SET ROLE cripta")
    connection.commit()
    return connection


def test_pg16_commit_restore_and_effective_grants(db) -> None:
    admin = db
    assert admin.execute(
        "SELECT has_table_privilege('cripta','mayak_v2.r11_consumer_commit','SELECT,INSERT,UPDATE')"
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT has_table_privilege('cripta','mayak_v2.r11_consumer_commit','DELETE')"
    ).fetchone() == (False,)

    replay = _replay()
    with _runtime() as runtime:
        committed = commit_r11_mayak_publication(
            runtime,
            replay=replay,
            snapshot=_snapshot(replay),
            expected_generation=0,
            expected_previous_publication_id=None,
        )
        restored = load_r11_mayak_consumer_commit(
            runtime,
            expected_source_session_id=SESSION,
            expected_release_commit=PRODUCER,
        )
    assert committed.generation == 1
    assert restored is not None
    assert restored.generation == 1
    assert restored.replay.checkpoint == replay.checkpoint
    assert restored.snapshot == committed.snapshot


def test_pg16_shared_epoch_fence_blocks_source_transition_until_consumer_commit(db) -> None:
    admin = db
    replay = _replay()
    fenced = threading.Event()
    release = threading.Event()
    outcome: queue.Queue[BaseException | None] = queue.Queue()

    def pause(at: str) -> None:
        if at == "AFTER_SOURCE_FENCE":
            fenced.set()
            assert release.wait(timeout=10)

    def consumer() -> None:
        try:
            with _runtime() as runtime:
                commit_r11_mayak_publication(
                    runtime,
                    replay=replay,
                    snapshot=_snapshot(replay),
                    expected_generation=0,
                    expected_previous_publication_id=None,
                    fault_injector=pause,
                )
            outcome.put(None)
        except BaseException as exc:  # pragma: no cover - surfaced below
            outcome.put(exc)

    thread = threading.Thread(target=consumer, daemon=True)
    thread.start()
    assert fenced.wait(timeout=10)
    admin.execute("SET lock_timeout='250ms'")
    try:
        with pytest.raises(psycopg.errors.LockNotAvailable):
            admin.execute(
                "UPDATE analytics.native_public_trade_source_sessions "
                "SET state='GAP' WHERE source_session_id=%s",
                (SESSION,),
            )
        assert admin.execute(
            "SELECT state FROM analytics.native_public_trade_source_sessions "
            "WHERE source_session_id=%s",
            (SESSION,),
        ).fetchone() == ("ACTIVE",)
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    error = outcome.get_nowait()
    if error is not None:
        raise error
    assert admin.execute(
        "SELECT generation,publication_id FROM mayak_v2.r11_consumer_commit"
    ).fetchone() == (1, 100)


def test_pg16_consumer_and_mayak_outputs_share_one_commit(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = db
    replay = _replay()
    snapshot = _snapshot(replay)
    monkeypatch.setattr(pg, "compact_market_minute_rows", lambda *_args: ())

    with _runtime() as runtime:
        with runtime.transaction():
            committed = commit_r11_mayak_publication(
                runtime,
                replay=replay,
                snapshot=snapshot,
                expected_generation=0,
                expected_previous_publication_id=None,
            )
            snapshot_id = pg._persist_mayak_dispatcher_outputs(
                runtime,
                committed.snapshot,
                symbols=SEVEN,
            )

    assert snapshot_id is not None
    assert admin.execute(
        "SELECT generation,publication_id FROM mayak_v2.r11_consumer_commit"
    ).fetchone() == (1, 100)
    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.snapshots"
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.coin_market_contexts"
    ).fetchone() == (7,)
    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.shared_market_contexts"
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.observation_journal"
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.state_events"
    ).fetchone() == (1,)
    assert admin.execute(
        """SELECT payload->'r11_market_publication'->>'source_mode',
                  payload->'r11_market_publication'->>'trading_effect'
             FROM mayak_v2.snapshots"""
    ).fetchone() == ("PG_ORIGINAL_REPLAY", "NONE")


def test_pg16_output_failure_rolls_back_consumer_cursor(
    db, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = db
    replay = _replay()
    snapshot = _snapshot(replay)

    def fail_rows(*_args):
        raise RuntimeError("derived output failed")

    monkeypatch.setattr(pg, "compact_market_minute_rows", fail_rows)
    with pytest.raises(RuntimeError, match="derived output failed"):
        with _runtime() as runtime:
            with runtime.transaction():
                committed = commit_r11_mayak_publication(
                    runtime,
                    replay=replay,
                    snapshot=snapshot,
                    expected_generation=0,
                    expected_previous_publication_id=None,
                )
                pg._persist_mayak_dispatcher_outputs(
                    runtime,
                    committed.snapshot,
                    symbols=SEVEN,
                )

    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.r11_consumer_commit"
    ).fetchone() == (0,)
    assert admin.execute(
        "SELECT count(*) FROM mayak_v2.snapshots"
    ).fetchone() == (0,)


def test_pg16_source_session_reset_replaces_whole_generation(db) -> None:
    admin = db
    first = _replay()
    with _runtime() as runtime:
        committed = commit_r11_mayak_publication(
            runtime,
            replay=first,
            snapshot=_snapshot(first),
            expected_generation=0,
            expected_previous_publication_id=None,
        )
    assert committed.generation == 1

    admin.execute(
        "UPDATE analytics.native_public_trade_source_sessions "
        "SET state='GAP' WHERE source_session_id=%s",
        (SESSION,),
    )
    admin.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions "
        "(source_session_id,market_source_id,state,producer_release_commit,subscribed_at) "
        "VALUES (%s,%s,'ACTIVE',%s,%s)",
        (NEW_SESSION, SOURCE, NEW_PRODUCER, AT + timedelta(seconds=1)),
    )
    admin.execute(
        "INSERT INTO analytics.market_publications "
        "(publication_id,source_session_id,market_source_id,producer_release_commit,"
        "as_of_second,publication_status) "
        "VALUES (200,%s,%s,%s,%s,'WATERMARKS_ONLY')",
        (NEW_SESSION, SOURCE, NEW_PRODUCER, AT + timedelta(seconds=2)),
    )

    second = _replay(
        200,
        session=NEW_SESSION,
        producer=NEW_PRODUCER,
        at=AT + timedelta(seconds=2),
    )
    with _runtime() as runtime:
        reset = commit_r11_mayak_publication(
            runtime,
            replay=second,
            snapshot=_snapshot(second),
            expected_generation=1,
            expected_previous_publication_id=100,
            expected_previous_source_session_id=SESSION,
            expected_previous_release_commit=PRODUCER,
        )
        restored = load_r11_mayak_consumer_commit(
            runtime,
            expected_source_session_id=NEW_SESSION,
            expected_release_commit=NEW_PRODUCER,
        )

    assert reset.generation == 2
    assert restored is not None
    assert restored.generation == 2
    assert restored.replay.observations == 1
    assert restored.replay.checkpoint is not None
    assert restored.replay.checkpoint.source_session_id == NEW_SESSION
    assert restored.replay.checkpoint.publication_id == 200
    assert all(
        cursor.source_session_id == NEW_SESSION
        for cursor in restored.replay.checkpoint.native_cursors
    )
