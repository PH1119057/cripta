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
AT = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)
SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)


def _replay(publication_id: int = 100) -> R11MayakPublicationReplay:
    checkpoint = R11SevenMayakReplayCheckpoint(
        publication_id=publication_id,
        source_session_id=SESSION,
        release_commit=PRODUCER,
        as_of_second=AT,
        optional_cursors=tuple(
            R11OptionalMayakCursor(
                source_session_id=SESSION,
                transport_epoch_id=("c" * 32 if product == "SPOT" else SESSION),
                product_category=product,
                producer_release_commit=PRODUCER,
                next_cursor=publication_id,
                next_digest="d" * 64,
                books=(),
            )
            for product in ("SPOT", "LINEAR")
        ),
        native_cursors=tuple(
            R11ReplayCursor(
                symbol=symbol,
                source_session_id=SESSION,
                producer_release_commit=PRODUCER,
                publication_id=publication_id,
                publication_as_of=AT,
                publication_watermark=(1, 0),
                cursor=(1, 0),
                last_seq=1,
                last_event_at=AT - timedelta(seconds=1),
                recent_exec_ids=(),
            )
            for symbol in SEVEN
        ),
    )
    replay = R11MayakPublicationReplay(
        checkpoint=checkpoint,
        first_publication_at=AT - timedelta(minutes=1),
        last_original_event_at=AT.timestamp() - 1,
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
        "consumer_ready": False,
        "mayak_ws_retirement_allowed": False,
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
