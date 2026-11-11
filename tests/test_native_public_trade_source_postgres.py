"""Issue #514: independent native source transaction/lock on disposable PostgreSQL.

This test never reaches production or an Exchange API. DB name must be exactly
cripta_integration. Original provider events are synthetic original-shaped
identities; they never authorize Strategy or private Exchange commands.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.native_public_trade_journal import (
    NativeTradeEvidence,
    NativeTradeEvidenceConflict,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_scoped_native_dispatch import (
    consume_scoped_native_batch,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    read_ordered_native_events,
    read_scoped_native_source,
)
from operations.monitoring import native_public_trade_source as source
from operations.monitoring.native_public_trade_source import (
    NativeSourceInputUnverified,
    _acquire_exclusive_producer,
    _enable_source_union_autocommit,
    _persist_frame,
    _session_start,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL only")


@pytest.fixture(autouse=True)
def exact_disposable_release(monkeypatch: pytest.MonkeyPatch) -> None:
    # The service receives this from release.env in production, never from
    # an arbitrary CI runner environment.
    monkeypatch.setattr(source, "RELEASE", "a" * 40)


def _db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("disposable PostgreSQL required")
    connection = psycopg.connect(DSN, autocommit=True)
    if connection.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        connection.close()
        pytest.skip("never use a non-disposable database")
    return connection


def _event(identity: str) -> NativeTradeEvidence:
    return NativeTradeEvidence(
        market_source_id="ms-bybit-mainnet-public-v1",
        instrument_id="inst-bybit-linear-ltcusdt",
        external_symbol="LTCUSDT",
        exchange_sequence=12121,
        exec_id=identity,
        event_at=datetime(2026, 11, 8, 18, 5, tzinfo=UTC),
        price=Decimal("52.125"),
        quantity=Decimal("0.1"),
        taker_side="Buy",
        source_transport="BYBIT_WS_PUBLIC_TRADE",
        producer_release_commit="a" * 40,
    )


def test_pg_session_native_row_and_cursor_commit_as_one_source_boundary() -> None:
    session = uuid4().hex
    evidence = _event("ci-native-514-" + uuid4().hex)
    with _db() as conn:
        _session_start(conn, session)
        _persist_frame(conn, session=session, frame_number=1, facts=(evidence,))
        values = conn.execute(
            """SELECT c.exchange_sequence,c.exec_id,c.event_at,e.price,e.quantity,
                      s.state
                 FROM analytics.native_public_trade_source_cursors c
                 JOIN analytics.native_public_trade_source_sessions s
                   ON s.source_session_id=c.source_session_id
                 JOIN analytics.native_public_trade_events e
                   ON e.market_source_id=c.market_source_id
                  AND e.instrument_id=c.instrument_id
                  AND e.exchange_sequence=c.exchange_sequence
                  AND e.exec_id=c.exec_id
                WHERE c.source_session_id=%s""",
            (session,),
        ).fetchone()
        assert values == (
            evidence.exchange_sequence, evidence.exec_id, evidence.event_at,
            evidence.price, evidence.quantity, "ACTIVE",
        )
        assert conn.execute(
            """SELECT count(*) FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (1,)
        # A redelivered exact native identity is NOT a new ordered trade.
        _persist_frame(conn, session=session, frame_number=2, facts=(evidence,))
        assert conn.execute(
            "SELECT frame_number FROM analytics.native_public_trade_source_cursors "
            "WHERE source_session_id=%s",
            (session,),
        ).fetchone() == (1,)
        with pytest.raises(NativeTradeEvidenceConflict):
            _persist_frame(
                conn, session=session, frame_number=3,
                facts=(replace(evidence, price=Decimal("53")),),
            )
        assert conn.execute(
            "SELECT frame_number FROM analytics.native_public_trade_source_cursors "
            "WHERE source_session_id=%s",
            (session,),
        ).fetchone() == (1,)


def test_pg_transaction_crash_rolls_back_session_native_and_cursor_together() -> None:
    session = uuid4().hex
    evidence = _event("ci-native-rollback-514-" + uuid4().hex)
    with _db() as conn:
        with pytest.raises(
            RuntimeError, match="SIMULATED_CRASH"
        ), conn.transaction():
            _session_start(conn, session)
            _persist_frame(
                conn, session=session, frame_number=1, facts=(evidence,),
            )
            raise RuntimeError("SIMULATED_CRASH")
        assert conn.execute(
            "SELECT count(*) FROM analytics.native_public_trade_source_sessions "
            "WHERE source_session_id=%s",
            (session,),
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM analytics.native_public_trade_events "
            "WHERE exec_id=%s",
            (evidence.exec_id,),
        ).fetchone() == (0,)


def test_pg_exclusive_native_producer_survives_transactions_and_rejects_second() -> None:
    with _db() as first, _db() as second:
        _acquire_exclusive_producer(first)
        with pytest.raises(
            NativeSourceInputUnverified, match="WRITER_ALREADY_OWNED",
        ):
            _acquire_exclusive_producer(second)
        with first.transaction():
            first.execute("SELECT 1")
        with pytest.raises(
            NativeSourceInputUnverified, match="WRITER_ALREADY_OWNED",
        ):
            _acquire_exclusive_producer(second)


def test_pg_unclean_old_producer_epoch_is_not_falsely_active() -> None:
    first, second = uuid4().hex, uuid4().hex
    with _db() as conn:
        _session_start(conn, first)
        _session_start(conn, second)
        state = conn.execute(
            """SELECT source_session_id,state,reason,ended_at IS NOT NULL
                 FROM analytics.native_public_trade_source_sessions
                 WHERE source_session_id IN (%s,%s)
                 ORDER BY source_session_id""",
            (first, second),
        ).fetchall()
        assert len(state) == 2
        by_id = {row[0]: row[1:] for row in state}
        assert by_id[first] == (
            "GAP", "PREVIOUS_WRITER_ENDED_UNCLEAN", True,
        )
        assert by_id[second] == ("ACTIVE", None, False)


def test_pg_scoped_reader_independently_proves_one_symbol_not_other() -> None:
    """Actual PostgreSQL session/journal/cursor, no Observer service or ARM."""
    session = uuid4().hex
    now = datetime.now(UTC)
    evidence = replace(
        _event("ci-scoped-native-470-" + uuid4().hex), event_at=now,
    )
    with _db() as conn:
        _session_start(conn, session)
        _persist_frame(conn, session=session, frame_number=1, facts=(evidence,))
        proof = read_scoped_native_source(
            conn, symbols=("LTCUSDT", "APTUSDT"),
            accepted_producer_commits=frozenset(("a" * 40,)),
            observed_at=datetime.now(UTC),
            max_lag=timedelta(seconds=20),
        )
        assert not proof.ready
        assert proof.session_id == session
        assert proof.by_symbol["LTCUSDT"].ready is True
        assert proof.by_symbol["APTUSDT"].ready is False
        assert proof.by_symbol["APTUSDT"].reason == (
            "NATIVE_SOURCE_CURSOR_MISSING"
        )
        # Historical/observer status and account LIVE arm are NOT read.


def test_pg_original_same_sequence_event_order_is_immutable() -> None:
    """Exact source frame order must not be fabricated from execId sort."""
    session = uuid4().hex
    second = "ci-z-before-a-470-" + uuid4().hex
    first = "ci-a-after-z-470-" + uuid4().hex
    # Deliberately place lexically later ID before earlier ID in provider frame.
    events = (_event(second), _event(first))
    with _db() as conn:
        _session_start(conn, session)
        _persist_frame(conn, session=session, frame_number=7, facts=events)
        rows = conn.execute(
            """SELECT frame_number,frame_index,exchange_sequence,exec_id
                 FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s
                ORDER BY frame_number,frame_index""",
            (session,),
        ).fetchall()
        assert rows == [
            (7, 0, events[0].exchange_sequence, second),
            (7, 1, events[1].exchange_sequence, first),
        ]
        # A repeated exact native provider ID in the next frame may not
        # produce a duplicate causal event, even if the cursor advances.
        _persist_frame(
            conn, session=session, frame_number=8, facts=(events[0],),
        )
        assert conn.execute(
            """SELECT count(*) FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (2,)


def test_pg_source_transaction_abort_removes_frame_order_and_cursor() -> None:
    session = uuid4().hex
    evidence = _event("ci-crash-order-470-" + uuid4().hex)
    with _db() as conn:
        with pytest.raises(RuntimeError, match="INJECTED"), conn.transaction():
            _session_start(conn, session)
            _persist_frame(conn, session=session, frame_number=1, facts=(evidence,))
            raise RuntimeError("INJECTED")
        for table in (
            "analytics.native_public_trade_source_events",
            "analytics.native_public_trade_source_cursors",
            "analytics.native_public_trade_source_sessions",
        ):
            assert conn.execute(
                f"SELECT count(*) FROM {table} WHERE source_session_id=%s",
                (session,),
            ).fetchone() == (0,)


def test_pg_scoped_native_reader_preserves_same_sequence_provider_frame_order() -> None:
    session = uuid4().hex
    now = datetime.now(UTC)
    first = replace(
        _event("ci-z-first-470-" + uuid4().hex), event_at=now,
    )
    second = replace(
        _event("ci-a-second-470-" + uuid4().hex), event_at=now,
    )
    with _db() as conn:
        _session_start(conn, session)
        _persist_frame(
            conn, session=session, frame_number=11, facts=(first, second),
        )
        proof = read_scoped_native_source(
            conn, symbols=("LTCUSDT",),
            accepted_producer_commits=frozenset(("a" * 40,)),
            observed_at=datetime.now(UTC),
            max_lag=timedelta(seconds=20),
        )
        assert proof.ready
        ordered = read_ordered_native_events(
            conn, source=proof, after_frame=10,
            after_index=0, limit=50,
        )
        assert [x.exec_id for x in ordered] == [
            first.exec_id, second.exec_id,
        ]
        assert [(x.frame_number, x.frame_index) for x in ordered] == [
            (11, 0), (11, 1),
        ]


def test_pg_scoped_engine_and_cursor_rollback_atomically() -> None:
    """An Engine fault cannot ACK original source events in PostgreSQL."""
    session = uuid4().hex
    now = datetime.now(UTC)
    ltc = replace(
        _event("ci-scoped-dispatch-ltc-"+uuid4().hex), event_at=now,
    )
    inj = replace(
        _event("ci-scoped-dispatch-inj-"+uuid4().hex), event_at=now,
        external_symbol="INJUSDT",
        instrument_id="inst-bybit-linear-injusdt",
    )
    with _db() as conn:
        _session_start(conn, session)
        _persist_frame(
            conn, session=session, frame_number=1, facts=(ltc, inj),
        )
        proof = read_scoped_native_source(
            conn, symbols=("LTCUSDT", "INJUSDT"),
            accepted_producer_commits=frozenset(("a"*40,)),
            observed_at=datetime.now(UTC),
            max_lag=timedelta(seconds=20),
        )
        assert proof.ready
        conn.execute(
            """INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints
                (strategy_id,strategy_version,entry_plan_fingerprint,
                 exit_plan_fingerprint,source_session_id,
                 producer_release_commit,consumer_release_commit,
                 frame_number,frame_index)
               VALUES (%s,%s,%s,%s,%s,%s,%s,0,0)""",
            (MAIN_ID,VERSION,"c"*64,"d"*64,session,"a"*40,"b"*40),
        )
        conn.execute(
            "CREATE TEMP TABLE p90_ci_native_effects (exec_id text)"
        )

        def callback(event):
            conn.execute(
                "INSERT INTO p90_ci_native_effects(exec_id) VALUES (%s)",
                (event.exec_id,),
            )
            if event.exec_id == inj.exec_id:
                raise RuntimeError("INJECTED_ENGINE_ABORT")

        kwargs = {
            "source": proof,
            "strategy_id": MAIN_ID,
            "strategy_version": VERSION,
            "entry_plan_fingerprint": "c"*64,
            "exit_plan_fingerprint": "d"*64,
            "consumer_release_commit": "b"*40,
            "observed_at": datetime.now(UTC),
            "max_evidence_age": timedelta(seconds=20),
        }
        with pytest.raises(RuntimeError, match="INJECTED_ENGINE_ABORT"):
            consume_scoped_native_batch(conn, on_native_trade=callback, **kwargs)
        assert conn.execute(
            "SELECT count(*) FROM p90_ci_native_effects"
        ).fetchone() == (0,)
        assert conn.execute(
            """SELECT frame_number,frame_index FROM
               strategy_entry.p90_scoped_native_consumer_checkpoints
               WHERE strategy_id=%s AND source_session_id=%s""",
            (MAIN_ID,session),
        ).fetchone() == (0,0)

        accepted = consume_scoped_native_batch(
            conn,
            on_native_trade=lambda ev: conn.execute(
                "INSERT INTO p90_ci_native_effects(exec_id) VALUES (%s)",
                (ev.exec_id,),
            ),
            **kwargs,
        )
        assert [e.exec_id for e in accepted] == [
            ltc.exec_id, inj.exec_id,
        ]
        assert conn.execute(
            "SELECT count(*) FROM p90_ci_native_effects"
        ).fetchone() == (2,)
        assert conn.execute(
            """SELECT frame_number,frame_index FROM
               strategy_entry.p90_scoped_native_consumer_checkpoints
               WHERE strategy_id=%s AND source_session_id=%s""",
            (MAIN_ID,session),
        ).fetchone() == (1,1)


def test_pg_513_plus_same_sequence_original_ids_preserve_atomic_frame_order() -> None:
    """Prove actual PostgreSQL source writes above 512 IDs in one native seq."""
    session = uuid4().hex
    prefix = "ci-native-sameseq-513-470-" + uuid4().hex
    events = tuple(
        _event(f"{prefix}-{index:04d}") for index in range(513)
    )
    next_event = _event(f"{prefix}-0513")
    with _db() as conn:
        _session_start(conn, session)
        _persist_frame(conn, session=session, frame_number=1, facts=events)
        rows = conn.execute(
            """SELECT frame_number,frame_index,exchange_sequence,exec_id
                 FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s
                ORDER BY frame_number,frame_index""",
            (session,),
        ).fetchall()
        assert rows == [
            (1, index, event.exchange_sequence, event.exec_id)
            for index, event in enumerate(events)
        ]
        assert conn.execute(
            """SELECT count(*)
                 FROM analytics.native_public_trade_events j
                 JOIN analytics.native_public_trade_source_events s
                   ON s.market_source_id=j.market_source_id
                  AND s.instrument_id=j.instrument_id
                  AND s.exchange_sequence=j.exchange_sequence
                  AND s.exec_id=j.exec_id
                WHERE s.source_session_id=%s""",
            (session,),
        ).fetchone() == (513,)
        assert conn.execute(
            """SELECT frame_number,frame_index,exchange_sequence,exec_id
                 FROM analytics.native_public_trade_source_cursors
                WHERE source_session_id=%s AND external_symbol='LTCUSDT'""",
            (session,),
        ).fetchone() == (1, 512, 12121, events[-1].exec_id)

        # The next distinct execId with exactly the same original seq is
        # accepted, ordered after the 513 recorded facts and never evicted.
        _persist_frame(
            conn, session=session, frame_number=2, facts=(next_event,),
        )
        assert conn.execute(
            """SELECT count(*) FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (514,)
        assert conn.execute(
            """SELECT frame_number,frame_index,exchange_sequence,exec_id
                 FROM analytics.native_public_trade_source_cursors
                WHERE source_session_id=%s AND external_symbol='LTCUSDT'""",
            (session,),
        ).fetchone() == (2, 0, 12121, next_event.exec_id)


def test_r03_source_gap_select_cannot_hold_an_uncommitted_original_frame() -> None:
    """Disposable PG: prove a second session sees the frame before shutdown.

    R03 production failure previously held an implicit 6-minute outer
    transaction after a SELECT and hid all native trades/cursors from
    Observer. This exact test uses two independent PostgreSQL connections.
    """
    session = uuid4().hex
    evidence = _event("ci-r03-committed-original-" + uuid4().hex)
    with _db() as writer, _db() as independent_reader:
        # Native source normally connects in default non-autocommit mode.
        writer.autocommit = False
        _acquire_exclusive_producer(writer)
        _enable_source_union_autocommit(writer)
        assert writer.autocommit is True
        _session_start(writer, session)
        assert independent_reader.execute(
            "SELECT state FROM analytics.native_public_trade_source_sessions "
            "WHERE source_session_id=%s", (session,),
        ).fetchone() == ("ACTIVE",)

        # This is the exact kind of source gap planner / coverage read
        # that started a hidden outer transaction in the broken R03.
        writer.execute(
            "SELECT count(*) FROM analytics.closed_candles "
            "WHERE external_symbol=%s", ("LTCUSDT",),
        ).fetchone()
        assert writer.info.transaction_status == psycopg.pq.TransactionStatus.IDLE

        _persist_frame(
            writer, session=session, frame_number=1, facts=(evidence,),
        )
        assert writer.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert independent_reader.execute(
            "SELECT count(*) FROM analytics.native_public_trade_source_events "
            "WHERE source_session_id=%s", (session,),
        ).fetchone() == (1,)
        assert independent_reader.execute(
            "SELECT frame_number,exec_id FROM "
            "analytics.native_public_trade_source_cursors "
            "WHERE source_session_id=%s AND external_symbol=%s",
            (session, "LTCUSDT"),
        ).fetchone() == (1, evidence.exec_id)

        # Session advisory lock is still held across two independent COMMITs.
        with pytest.raises(
            NativeSourceInputUnverified, match="WRITER_ALREADY_OWNED",
        ):
            _acquire_exclusive_producer(independent_reader)
        _persist_frame(
            writer, session=session, frame_number=2,
            facts=(replace(
                evidence, exec_id="ci-r03-second-exec-" + uuid4().hex,
                exchange_sequence=evidence.exchange_sequence + 1,
                event_at=evidence.event_at + timedelta(milliseconds=10),
            ),),
        )
        assert independent_reader.execute(
            "SELECT count(*) FROM analytics.native_public_trade_source_events "
            "WHERE source_session_id=%s", (session,),
        ).fetchone() == (2,)
        assert writer.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_r03_autocommit_refuses_to_erase_a_dirty_outer_transaction() -> None:
    with _db() as writer:
        writer.autocommit = False
        writer.execute("SELECT 1")
        assert writer.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        with pytest.raises(
            NativeSourceInputUnverified,
            match="NATIVE_SOURCE_UNION_TRANSACTION_NOT_IDLE",
        ):
            _enable_source_union_autocommit(writer)
        assert writer.autocommit is False
        writer.rollback()
