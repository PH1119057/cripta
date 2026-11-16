"""Disposable PostgreSQL16 proof for bounded MarketSource retention (#642)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "market_source_hot_retention_ci",
    ROOT / "operations/monitoring/market_source_hot_retention.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _state(session: str, release: str, cursor: int = 3) -> bytes:
    raw = json.dumps(
        {
            "checkpoint": {
                "source_session_id": session,
                "release_commit": release,
                "optional_cursors": [
                    {
                        "product_category": "SPOT",
                        "source_session_id": session,
                        "transport_epoch_id": "1" * 32,
                        "next_cursor": cursor,
                    },
                    {
                        "product_category": "LINEAR",
                        "source_session_id": session,
                        "transport_epoch_id": "2" * 32,
                        "next_cursor": cursor,
                    },
                ],
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return (
        MODULE._STATE_MAGIC
        + hashlib.sha256(raw).hexdigest().encode()
        + b"\n"
        + zlib.compress(raw)
    )


@pytest.fixture()
def database():
    dsn = os.environ.get("CRIPTA_RETENTION_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("requires isolated disposable PostgreSQL")
    import psycopg

    c = psycopg.connect(dsn, autocommit=True, connect_timeout=4)
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    for schema in ("analytics", "mayak_v2", "strategy_entry"):
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute(f"CREATE SCHEMA {schema}")
    for ddl in (
        """CREATE TABLE analytics.market_publications(
             publication_id bigint PRIMARY KEY,
             as_of_second timestamptz NOT NULL,
             compact_second_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
             forming_minute_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
             rest_observation_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
             original_optional_frame_refs jsonb NOT NULL DEFAULT '{}'::jsonb)""",
        """CREATE TABLE analytics.public_market_source_seconds(
             source_session_id text NOT NULL,instrument_id text NOT NULL,
             observed_second timestamptz NOT NULL,
             PRIMARY KEY(source_session_id,instrument_id,observed_second))""",
        """CREATE TABLE analytics.public_forming_minute_observations(
             forming_observation_id bigint PRIMARY KEY,received_at timestamptz NOT NULL)""",
        """CREATE TABLE analytics.public_market_rest_observations(
             rest_observation_id bigint PRIMARY KEY,received_at timestamptz NOT NULL)""",
        """CREATE TABLE analytics.public_source_original_event_frames(
             source_session_id text NOT NULL,transport_epoch_id text NOT NULL,
             product_category text NOT NULL,source_frame_number bigint NOT NULL,
             committed_at timestamptz NOT NULL,
             PRIMARY KEY(source_session_id,transport_epoch_id,
                         product_category,source_frame_number))""",
        """CREATE TABLE analytics.native_public_trade_source_sessions(
             source_session_id text PRIMARY KEY,
             producer_release_commit text NOT NULL,state text NOT NULL,
             subscribed_at timestamptz NOT NULL)""",
        """CREATE TABLE mayak_v2.r11_consumer_commit(
             consumer_id text PRIMARY KEY,
             source_session_id text NOT NULL,
             producer_release_commit text NOT NULL,
             state_checkpoint bytea NOT NULL,
             publication_id bigint NOT NULL
               REFERENCES analytics.market_publications(publication_id))""",
    ):
        c.execute(ddl)
    try:
        yield c
    finally:
        c.close()


def _actor(monkeypatch):
    if sys.platform != "linux":
        pytest.skip("production actor is Linux postgres")
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 42)
    monkeypatch.setattr(MODULE, "_postgres_unix_uid", lambda: 42)


def _pub_refs(session: str, second: datetime, raw_frame: int) -> tuple[dict, dict, dict, dict]:
    return (
        {
            "i": {
                "PUBLIC_TRADE": {
                    "source_session_id": session,
                    "instrument_id": "i",
                    "observed_second": second.isoformat(),
                }
            }
        },
        {"i": {"1": {"forming_observation_id": raw_frame}}},
        {"i": {"ACCOUNT_RATIO": {"rest_observation_id": raw_frame}}},
        {
            "LINEAR": {
                "source_session_id": session,
                "transport_epoch_id": "2" * 32,
                "product_category": "LINEAR",
                "source_frame_number": raw_frame,
            }
        },
    )


def _insert_pub(c, pid: int, at: datetime, session: str, frame: int):
    refs = _pub_refs(session, at, frame)
    c.execute(
        """INSERT INTO analytics.market_publications
           VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)""",
        (pid, at, *(json.dumps(x) for x in refs)),
    )
    c.execute(
        "INSERT INTO analytics.public_market_source_seconds VALUES(%s,'i',%s)",
        (session, at),
    )
    c.execute(
        "INSERT INTO analytics.public_forming_minute_observations VALUES(%s,%s)",
        (frame, at),
    )
    c.execute(
        "INSERT INTO analytics.public_market_rest_observations VALUES(%s,%s)",
        (frame, at),
    )
    c.execute(
        """INSERT INTO analytics.public_source_original_event_frames
           VALUES(%s,%s,'LINEAR',%s,%s)""",
        (session, "2" * 32, frame, at),
    )


def test_retention_keeps_fk_pin_and_recent_publication(database, monkeypatch):
    c = database
    _actor(monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    old = now - timedelta(hours=1)
    fresh = now - timedelta(minutes=1)
    session = "a" * 32
    release = "b" * 40
    _insert_pub(c, 1, old, session, 1)
    _insert_pub(c, 2, old + timedelta(seconds=1), session, 2)
    _insert_pub(c, 3, fresh, session, 3)
    c.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions VALUES(%s,%s,'GAP',%s)",
        (session, release, old),
    )
    c.execute(
        "INSERT INTO mayak_v2.r11_consumer_commit VALUES(%s,%s,%s,%s,%s)",
        (MODULE._CONSUMER_ID, session, release, _state(session, release), 1),
    )
    with c.transaction():
        c.execute("SET TRANSACTION READ ONLY")
        check = MODULE.collect_check(c, now - timedelta(minutes=15))
        assert check.rows[MODULE.PUBLICATIONS] == 1
        assert check.rows[MODULE.RAW] == 1
    result = MODULE.run_apply(c, now - timedelta(minutes=15), 100)
    assert result.rows[MODULE.PUBLICATIONS] == 1
    assert set(r[0] for r in c.execute(
        "SELECT publication_id FROM analytics.market_publications"
    )) == {1, 3}
    assert set(r[0] for r in c.execute(
        "SELECT forming_observation_id FROM analytics.public_forming_minute_observations"
    )) == {1, 3}
    assert set(r[0] for r in c.execute(
        "SELECT source_frame_number FROM analytics.public_source_original_event_frames"
    )) == {1, 3}
    second = MODULE.run_apply(c, now - timedelta(minutes=15), 100)
    assert not any(second.rows.values())


def test_active_source_prunes_only_processed_raw_prefix(database, monkeypatch):
    c = database
    _actor(monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    old = now - timedelta(hours=1)
    session = "c" * 32
    release = "d" * 40
    # One pinned publication keeps frame 1 even though the cursor is at 3.
    refs = _pub_refs(session, old, 1)
    c.execute(
        """INSERT INTO analytics.market_publications
           VALUES(10,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)""",
        (old, *(json.dumps(x) for x in refs)),
    )
    c.execute(
        "INSERT INTO analytics.public_market_source_seconds VALUES(%s,'i',%s)",
        (session, old),
    )
    c.execute(
        "INSERT INTO analytics.public_forming_minute_observations VALUES(1,%s)",
        (old,),
    )
    c.execute(
        "INSERT INTO analytics.public_market_rest_observations VALUES(1,%s)",
        (old,),
    )
    c.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions VALUES(%s,%s,'ACTIVE',%s)",
        (session, release, old),
    )
    c.execute(
        "INSERT INTO mayak_v2.r11_consumer_commit VALUES(%s,%s,%s,%s,10)",
        (MODULE._CONSUMER_ID, session, release, _state(session, release, 3)),
    )
    fresh = now - timedelta(minutes=1)
    frame_times = {
        1: old,
        2: fresh,
        3: old + timedelta(seconds=3),
        4: fresh + timedelta(seconds=2),
    }
    for frame, committed_at in frame_times.items():
        c.execute(
            """INSERT INTO analytics.public_source_original_event_frames
               VALUES(%s,%s,'LINEAR',%s,%s)""",
            (session, "2" * 32, frame, committed_at),
        )
    with c.transaction():
        c.execute("SET TRANSACTION READ ONLY")
        check = MODULE.collect_check(c, now - timedelta(minutes=15))
        assert check.rows[MODULE.RAW] == 1
    result = MODULE.run_apply(c, now - timedelta(minutes=15), 100)
    assert result.rows[MODULE.RAW] == 1
    assert set(r[0] for r in c.execute(
        "SELECT source_frame_number FROM analytics.public_source_original_event_frames"
    )) == {1, 3, 4}


def test_active_source_with_stale_mayak_head_keeps_old_raw_tail(
    database, monkeypatch,
):
    c = database
    _actor(monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    old = now - timedelta(hours=1)
    stale_session = "e" * 32
    stale_release = "f" * 40
    active_session = "a" * 32
    active_release = "b" * 40

    _insert_pub(c, 20, old, stale_session, 1)
    c.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions VALUES(%s,%s,'GAP',%s)",
        (stale_session, stale_release, old),
    )
    c.execute(
        "INSERT INTO analytics.native_public_trade_source_sessions VALUES(%s,%s,'ACTIVE',%s)",
        (active_session, active_release, old),
    )
    c.execute(
        "INSERT INTO mayak_v2.r11_consumer_commit VALUES(%s,%s,%s,%s,20)",
        (
            MODULE._CONSUMER_ID,
            stale_session,
            stale_release,
            _state(stale_session, stale_release, 3),
        ),
    )
    for frame in (10, 11):
        c.execute(
            """INSERT INTO analytics.public_source_original_event_frames
               VALUES(%s,%s,'LINEAR',%s,%s)""",
            (active_session, "2" * 32, frame, old + timedelta(seconds=frame)),
        )

    with c.transaction():
        c.execute("SET TRANSACTION READ ONLY")
        check = MODULE.collect_check(c, now - timedelta(minutes=15))
        # The stale session may be reclaimed, but the ACTIVE raw bootstrap tail
        # must not be age-expired before MAYAK owns a matching checkpoint.
        assert check.rows[MODULE.RAW] == 0

    result = MODULE.run_apply(c, now - timedelta(minutes=15), 100)
    assert result.rows[MODULE.RAW] == 0
    assert set(r[0] for r in c.execute(
        """SELECT source_frame_number
             FROM analytics.public_source_original_event_frames
            WHERE source_session_id=%s""",
        (active_session,),
    )) == {10, 11}
