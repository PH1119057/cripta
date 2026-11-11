"""R04 append-only optional source-quality isolation on disposable PostgreSQL16.

Tests skip unless the explicit integration DSN names cripta_integration. This
module does not connect to production or access signed Exchange/private data.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.public_source_quality import (
    PublicSourceQualityEvent,
    PublicSourceQualityInvalid,
    persist_public_source_quality_events,
)
from operations.monitoring import native_public_trade_source as native

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL16 only")
RELEASE = "a" * 40
AT = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def _db() -> psycopg.Connection:
    if DSN is None:
        pytest.skip("disposable PostgreSQL is required")
    connection = psycopg.connect(DSN, autocommit=True)
    if connection.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        connection.close()
        pytest.skip("production database forbidden")
    return connection


def _event(
    session: str, epoch: str, *, quality: str, topic: str = "*",
    symbol: str = "", reason: str = "PUBLIC_SPOT_TRANSPORT_UNAVAILABLE",
) -> PublicSourceQualityEvent:
    return PublicSourceQualityEvent.create(
        source_session_id=session, transport_epoch_id=epoch,
        product="SPOT", topic=topic, symbol=symbol,
        quality=quality, reason=reason, observed_at=AT,
    )


def test_pg16_scoped_optional_spot_gap_does_not_gap_linear_native_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "RELEASE", RELEASE)
    session, first_epoch, second_epoch = uuid4().hex, uuid4().hex, uuid4().hex
    first = _event(session, first_epoch, quality="GAP")
    reconnect = _event(
        session, second_epoch, quality="RECOVERING",
        reason="PUBLIC_SPOT_CONNECTED_NOT_CONTINUOUS",
    )
    book = _event(
        session, second_epoch, quality="UNKNOWN",
        topic="orderbook.50.LTCUSDT", symbol="LTCUSDT",
        reason="PUBLIC_TOPIC_FRAME_UNVERIFIED",
    )
    with _db() as db:
        db.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-spot-ltcusdt','venue-bybit','SPOT','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        db.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-linear-ltcusdt','venue-bybit',
                       'LINEAR','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        native._session_start(db, session)
        native_trade = native.decode_native_trades(
            {
                "topic": "publicTrade.LTCUSDT",
                "data": [{
                    "s": "LTCUSDT", "seq": 89901,
                    "i": "quality-isolation-" + uuid4().hex,
                    "T": int(AT.timestamp() * 1000),
                    "p": "52.0", "v": "0.2", "S": "Buy",
                }],
            },
            symbols=frozenset({"LTCUSDT"}), release=RELEASE,
        )
        native._persist_frame(
            db, session=session, frame_number=1, facts=native_trade,
        )
        events = (first, reconnect, book)
        assert persist_public_source_quality_events(
            db, events=events, producer_release_commit=RELEASE,
        ) == 3
        assert persist_public_source_quality_events(
            db, events=events, producer_release_commit=RELEASE,
        ) == 0
        rows = db.execute(
            """SELECT product_category,dataset_topic,quality_state,
                      transport_epoch_id,reason
                 FROM analytics.public_market_source_quality_events
                WHERE source_session_id=%s ORDER BY observed_at,quality_event_id""",
            (session,),
        ).fetchall()
        assert len(rows) == 3
        assert {r[2] for r in rows} == {"GAP", "RECOVERING", "UNKNOWN"}
        assert {r[0] for r in rows} == {"SPOT"}
        assert {r[3] for r in rows} == {first_epoch, second_epoch}
        assert db.execute(
            """SELECT state,producer_release_commit FROM
                   analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == ("ACTIVE", RELEASE)
        assert db.execute(
            """SELECT frame_number,exchange_sequence
                 FROM analytics.native_public_trade_source_cursors
                WHERE source_session_id=%s AND external_symbol='LTCUSDT'""",
            (session,),
        ).fetchone() == (1, 89901)
        assert db.execute(
            """SELECT count(*) FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (1,)
        with pytest.raises(
            PublicSourceQualityInvalid, match="QUALITY_EPOCH_NOT_ACTIVE",
        ):
            persist_public_source_quality_events(
                db, events=events, producer_release_commit="b" * 40,
            )
        with pytest.raises(
            PublicSourceQualityInvalid, match="QUALITY_EVENT_CONFLICT",
        ):
            persist_public_source_quality_events(
                db, events=(replace(first, reason="PUBLIC_GAP_MISMATCH"),),
                producer_release_commit=RELEASE,
            )
        assert db.execute(
            """SELECT count(*) FROM analytics.public_market_source_quality_events
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (3,)

        native._session_gap(db, session, "OWNER_CONTROLLED_STOP")
        with pytest.raises(
            PublicSourceQualityInvalid, match="QUALITY_EPOCH_NOT_ACTIVE",
        ):
            persist_public_source_quality_events(
                db, events=events, producer_release_commit=RELEASE,
            )


def test_pg16_source_quality_is_append_only() -> None:
    with _db() as db:
        assert db.execute(
            """SELECT
                has_table_privilege(
                  'cripta','analytics.public_market_source_quality_events','SELECT'),
                has_table_privilege(
                  'cripta','analytics.public_market_source_quality_events','INSERT'),
                has_table_privilege(
                  'cripta','analytics.public_market_source_quality_events','UPDATE'),
                has_table_privilege(
                  'cripta','analytics.public_market_source_quality_events','DELETE')
            """
        ).fetchone() == (True, True, False, False)


def test_quality_contract_rejects_false_not_listed_and_ambiguous_clock() -> None:
    session = uuid4().hex
    epoch = uuid4().hex
    with pytest.raises(
        PublicSourceQualityInvalid, match="NOT_LISTED_PROOF_INVALID",
    ):
        _event(session, epoch, quality="NOT_LISTED")
    with pytest.raises(
        PublicSourceQualityInvalid, match="QUALITY_EVENT_INVALID",
    ):
        replace(
            _event(session, epoch, quality="GAP"),
            observed_at=AT.replace(tzinfo=None),
        ).validate()
    with pytest.raises(
        PublicSourceQualityInvalid, match="QUALITY_EVENT_INVALID",
    ):
        replace(
            _event(session, epoch, quality="GAP"),
            observed_at=AT + timedelta(hours=1),
            transport_epoch_id="bad",
        ).validate()
