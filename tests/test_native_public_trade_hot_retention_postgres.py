"""Disposable PostgreSQL16 proof for native publicTrade retention (#642)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_public_trade_hot_retention_ci",
    ROOT / "operations/monitoring/native_public_trade_hot_retention.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture()
def database():
    dsn = os.environ.get("CRIPTA_RETENTION_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("requires isolated disposable PostgreSQL")
    import psycopg

    c = psycopg.connect(dsn, autocommit=True, connect_timeout=4)
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    for schema in ("analytics", "monitoring", "runtime", "strategy_entry"):
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute(f"CREATE SCHEMA {schema}")

    c.execute(
        """CREATE TABLE analytics.native_public_trade_events(
             market_source_id text NOT NULL,
             instrument_id text NOT NULL,
             external_symbol text NOT NULL,
             exchange_sequence bigint NOT NULL,
             exec_id text NOT NULL,
             event_at timestamptz NOT NULL,
             price numeric NOT NULL,
             quantity numeric NOT NULL,
             taker_side text NOT NULL,
             source_transport text NOT NULL,
             producer_release_commit text NOT NULL,
             ingested_at timestamptz NOT NULL,
             PRIMARY KEY(
               market_source_id,instrument_id,exchange_sequence,exec_id
             ))"""
    )
    c.execute(
        """CREATE TABLE analytics.native_public_trade_source_sessions(
             source_session_id text PRIMARY KEY,
             producer_release_commit text NOT NULL,
             state text NOT NULL,
             subscribed_at timestamptz NOT NULL,
             last_accepted_at timestamptz,
             ended_at timestamptz,
             reason text)"""
    )
    c.execute(
        """CREATE TABLE analytics.native_public_trade_source_events(
             source_session_id text NOT NULL REFERENCES
               analytics.native_public_trade_source_sessions(source_session_id),
             frame_number bigint NOT NULL,
             frame_index integer NOT NULL,
             market_source_id text NOT NULL,
             instrument_id text NOT NULL,
             external_symbol text NOT NULL,
             exchange_sequence bigint NOT NULL,
             exec_id text NOT NULL,
             event_at timestamptz NOT NULL,
             accepted_at timestamptz NOT NULL,
             PRIMARY KEY(source_session_id,frame_number,frame_index),
             UNIQUE(source_session_id,market_source_id,instrument_id,
                    exchange_sequence,exec_id),
             FOREIGN KEY(market_source_id,instrument_id,exchange_sequence,exec_id)
               REFERENCES analytics.native_public_trade_events(
                 market_source_id,instrument_id,exchange_sequence,exec_id
               ))"""
    )
    c.execute(
        """CREATE TABLE analytics.native_public_trade_source_cursors(
             source_session_id text NOT NULL,
             frame_number bigint NOT NULL,
             frame_index integer NOT NULL,
             market_source_id text NOT NULL,
             instrument_id text NOT NULL,
             external_symbol text NOT NULL,
             exchange_sequence bigint NOT NULL,
             exec_id text NOT NULL,
             event_at timestamptz NOT NULL,
             accepted_at timestamptz NOT NULL)"""
    )
    c.execute(
        """CREATE TABLE analytics.market_publications(
             publication_id bigint PRIMARY KEY,
             source_session_id text NOT NULL,
             native_watermarks jsonb NOT NULL DEFAULT '{}'::jsonb)"""
    )
    for table in (
        "analytics.native_public_trade_observer_delivery",
        "monitoring.opportunity_source_checkpoints",
        "strategy_entry.p90_r06_checkpoint_release_bridges",
        "strategy_entry.p90_r06_publication_observations",
        "strategy_entry.p90_scoped_native_consumer_checkpoints",
    ):
        c.execute(
            f"""CREATE TABLE {table}(
                  source_session_id text NOT NULL,
                  frame_number bigint NOT NULL,
                  frame_index integer NOT NULL)"""
        )

    c.execute("CREATE TABLE runtime.hot_positions(id bigint)")
    c.execute("CREATE TABLE runtime.hot_orders(id bigint)")
    c.execute(
        """CREATE TABLE runtime.position_ownership(
             position_id text PRIMARY KEY,
             symbol text NOT NULL,
             fill_at timestamptz,
             closed_at timestamptz,
             identity_status text NOT NULL,
             state text NOT NULL)"""
    )
    try:
        yield c
    finally:
        c.close()


def _actor(monkeypatch):
    if sys.platform != "linux":
        pytest.skip("production actor is Linux postgres")
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 42)
    monkeypatch.setattr(MODULE, "_postgres_unix_uid", lambda: 42)


def _parent(c, *, symbol, seq, exec_id, when):
    c.execute(
        """INSERT INTO analytics.native_public_trade_events
           VALUES('ms','inst-'||%s,%s,%s,%s,%s,1,1,'Buy','WS',%s,%s)""",
        (symbol, symbol, seq, exec_id, when, "a" * 40, when),
    )


def _source(c, *, session, frame, symbol, seq, exec_id, when):
    _parent(c, symbol=symbol, seq=seq, exec_id=exec_id, when=when)
    c.execute(
        """INSERT INTO analytics.native_public_trade_source_events
           VALUES(%s,%s,0,'ms','inst-'||%s,%s,%s,%s,%s,%s)""",
        (session, frame, symbol, symbol, seq, exec_id, when, when),
    )


def _session(c, session, release, state, start, end=None):
    c.execute(
        """INSERT INTO analytics.native_public_trade_source_sessions
           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (session, release, state, start, end or start, end, None),
    )


def test_gap_and_active_cleanup_preserve_position_point_and_parent_cursor(
    database, monkeypatch,
):
    c = database
    _actor(monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    cutoff = now - timedelta(minutes=15)
    old = now - timedelta(hours=1)
    active = "a" * 32
    gap = "g" * 32
    pre = "h" * 32
    release = "b" * 40

    _session(c, gap, release, "GAP", old, old + timedelta(minutes=5))
    _session(c, active, release, "ACTIVE", now - timedelta(minutes=30))
    _session(c, pre, release, "GAP", old - timedelta(hours=2),
             old - timedelta(hours=2, minutes=-1))

    # GAP frame 1 is a durable point; frame 2 is a real-position interval.
    # Frames 3/4 are telemetry and must be reclaimed.
    for frame in (1, 2, 3, 4):
        _source(
            c, session=gap, frame=frame, symbol="LTCUSDT",
            seq=100 + frame, exec_id=f"gap-{frame}",
            when=old + timedelta(seconds=frame),
        )
    c.execute(
        """INSERT INTO analytics.native_public_trade_observer_delivery
           VALUES(%s,1,0)""",
        (gap,),
    )
    c.execute(
        """INSERT INTO runtime.position_ownership
           VALUES('POS-1','LTCUSDT',%s,%s,'PROVEN','CLOSED')""",
        (old - timedelta(seconds=30), old + timedelta(seconds=2, milliseconds=500)),
    )

    # Current active: old frame is disposable without a current Entry
    # checkpoint; fresh frame is the live cursor/publication and must remain.
    _source(
        c, session=active, frame=1, symbol="APTUSDT",
        seq=201, exec_id="active-old", when=old,
    )
    fresh = now - timedelta(minutes=1)
    _source(
        c, session=active, frame=2, symbol="APTUSDT",
        seq=202, exec_id="active-fresh", when=fresh,
    )
    c.execute(
        """INSERT INTO analytics.native_public_trade_source_cursors
           VALUES(%s,2,0,'ms','inst-APTUSDT','APTUSDT',202,
                  'active-fresh',%s,%s)""",
        (active, fresh, fresh),
    )
    c.execute(
        """INSERT INTO analytics.market_publications
           VALUES(1,%s,%s::jsonb)""",
        (
            active,
            json.dumps({
                "APTUSDT": {
                    "frame_number": 2,
                    "frame_index": 0,
                }
            }),
        ),
    )

    # Historical cursor from before source_events existed: its parent row is
    # retained, but it does not require a nonexistent source-event row.
    legacy_at = old - timedelta(hours=3)
    _parent(
        c, symbol="DOTUSDT", seq=300, exec_id="legacy-cursor",
        when=legacy_at,
    )
    c.execute(
        """INSERT INTO analytics.native_public_trade_source_cursors
           VALUES(%s,9,0,'ms','inst-DOTUSDT','DOTUSDT',300,
                  'legacy-cursor',%s,%s)""",
        (pre, legacy_at, legacy_at),
    )

    result = MODULE.run_apply(c, cutoff, 100)
    assert result.rows["active_source_events_deleted"] == 1
    assert result.rows["gap_source_events_deleted"] == 2
    assert result.rows["parent_events_deleted"] == 3

    assert set(c.execute(
        """SELECT source_session_id,frame_number
             FROM analytics.native_public_trade_source_events"""
    ).fetchall()) == {(gap, 1), (gap, 2), (active, 2)}
    assert c.execute(
        """SELECT count(*) FROM analytics.native_public_trade_events
            WHERE exec_id='legacy-cursor'"""
    ).fetchone()[0] == 1


def test_active_checkpoint_keeps_unconsumed_suffix(database, monkeypatch):
    c = database
    _actor(monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    cutoff = now - timedelta(minutes=15)
    old = now - timedelta(hours=1)
    active = "c" * 32
    release = "d" * 40
    _session(c, active, release, "ACTIVE", old)

    for frame in (1, 2, 3, 4):
        _source(
            c, session=active, frame=frame, symbol="INJUSDT",
            seq=400 + frame, exec_id=f"active-{frame}",
            when=old + timedelta(seconds=frame),
        )
    c.execute(
        """INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints
           VALUES(%s,2,0)""",
        (active,),
    )
    # Current cursor pins frame 4. Frame 3 is unconsumed and old, but must
    # remain because the consumer frontier is only frame 2.
    c.execute(
        """INSERT INTO analytics.native_public_trade_source_cursors
           VALUES(%s,4,0,'ms','inst-INJUSDT','INJUSDT',404,
                  'active-4',%s,%s)""",
        (active, old + timedelta(seconds=4), old + timedelta(seconds=4)),
    )

    result = MODULE.run_apply(c, cutoff, 100)
    assert result.rows["active_source_events_deleted"] == 1
    assert set(r[0] for r in c.execute(
        """SELECT frame_number
             FROM analytics.native_public_trade_source_events
            WHERE source_session_id=%s ORDER BY frame_number""",
        (active,),
    )) == {2, 3, 4}


def test_current_cursor_without_source_event_blocks(database, monkeypatch):
    c = database
    _actor(monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    active = "e" * 32
    release = "f" * 40
    _session(c, active, release, "ACTIVE", now - timedelta(minutes=1))

    # Establish a source-event era, then create a current cursor whose parent
    # exists but whose source-event row is missing.
    _source(
        c, session=active, frame=1, symbol="APTUSDT",
        seq=501, exec_id="present", when=now - timedelta(seconds=30),
    )
    _parent(
        c, symbol="LTCUSDT", seq=502, exec_id="missing-source",
        when=now - timedelta(seconds=5),
    )
    c.execute(
        """INSERT INTO analytics.native_public_trade_source_cursors
           VALUES(%s,2,0,'ms','inst-LTCUSDT','LTCUSDT',502,
                  'missing-source',%s,%s)""",
        (active, now - timedelta(seconds=5), now - timedelta(seconds=5)),
    )

    with pytest.raises(
        MODULE.NativeTradeRetentionBlocked,
        match="CURRENT_CURSOR_SOURCE_EVENT_DANGLING",
    ):
        MODULE.run_apply(c, now - timedelta(minutes=15), 100)
