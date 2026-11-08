"""Issue #514: independent native source transaction/lock on disposable PostgreSQL.

This test never reaches production or an Exchange API. DB name must be exactly
cripta_integration. Original provider events are synthetic original-shaped
identities; they never authorize Strategy or private Exchange commands.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.native_public_trade_journal import (
    NativeTradeEvidence,
    NativeTradeEvidenceConflict,
)
from operations.monitoring import native_public_trade_source as source
from operations.monitoring.native_public_trade_source import (
    NativeSourceInputUnverified,
    _acquire_exclusive_producer,
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
        # Exact immutable replay is allowed, a different provider value is not.
        _persist_frame(conn, session=session, frame_number=2, facts=(evidence,))
        assert conn.execute(
            "SELECT frame_number FROM analytics.native_public_trade_source_cursors "
            "WHERE source_session_id=%s",
            (session,),
        ).fetchone() == (2,)
        with pytest.raises(NativeTradeEvidenceConflict):
            _persist_frame(
                conn, session=session, frame_number=3,
                facts=(replace(evidence, price=Decimal("53")),),
            )
        assert conn.execute(
            "SELECT frame_number FROM analytics.native_public_trade_source_cursors "
            "WHERE source_session_id=%s",
            (session,),
        ).fetchone() == (2,)


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
