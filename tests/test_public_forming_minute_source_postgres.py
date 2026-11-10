"""R04/R05 forming-minute real SQL integration in disposable PostgreSQL16."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.public_forming_minute_source import (
    FormingMinuteUnverified,
    persist_forming_minute_fact,
)
from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from operations.monitoring import native_public_trade_source as native

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="only disposable PostgreSQL16")
RELEASE = "a" * 40
START = datetime(2026, 11, 10, 8, 0, 0, tzinfo=UTC)
AT = START + timedelta(seconds=20, milliseconds=500)
MS = int(AT.timestamp() * 1000)
SESSION_EPOCH = "a"


def _db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("disposable integration DB required")
    conn = psycopg.connect(DSN, autocommit=True)
    if conn.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        conn.close()
        pytest.skip("production DB forbidden")
    return conn


def _source_fact():
    return decode_bybit_public_frame(
        {"topic": "kline.1.LTCUSDT", "type": "snapshot",
         "ts": MS, "data": [{
             "start": int(START.timestamp() * 1000),
             "end": int(START.timestamp() * 1000) + 59999,
             "interval": "1", "open": "50", "high": "52",
             "low": "49", "close": "51", "volume": "2.5",
             "turnover": "125.3", "confirm": False,
             "timestamp": MS - 150,
         }]},
        product="LINEAR", allowed_symbols=frozenset({"LTCUSDT"}),
        received_at=AT + timedelta(milliseconds=100),
    )[0]


def test_forming_only_pg_row_pinned_by_common_publication_and_never_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "RELEASE", RELEASE)
    session = uuid4().hex
    fact = _source_fact()
    ident = bybit_instrument_id("linear", "LTCUSDT")
    need = RequiredPublicFeed(
        consumer_id="MAYAK",
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=ident, product="LINEAR", symbol="LTCUSDT",
        dataset="FORMING_KLINE", timeframe="1", freshness_seconds=10,
    )
    with _db() as conn:
        conn.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-linear-ltcusdt','venue-bybit',
                       'LINEAR','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        native._session_start(conn, session)
        inserted = persist_forming_minute_fact(
            conn, source_session_id=session, producer_release_commit=RELEASE,
            fact=fact, received_at=AT + timedelta(milliseconds=100),
        )
        assert inserted > 0
        assert persist_forming_minute_fact(
            conn, source_session_id=session, producer_release_commit=RELEASE,
            fact=fact, received_at=AT + timedelta(milliseconds=100),
        ) == inserted
        with pytest.raises(FormingMinuteUnverified, match="NOT_ACTIVE"):
            persist_forming_minute_fact(
                conn, source_session_id=session,
                producer_release_commit="f" * 40,
                fact=fact, received_at=AT + timedelta(milliseconds=100),
            )
        publication = publish_market_source_second(
            conn, source_session_id=session, producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("1",),
            public_requirements=(need,),
            observed_at=START + timedelta(seconds=23),
        )
        assert publication is not None
        refs, coverage, status, native_cursor = conn.execute(
            """SELECT forming_minute_refs,forming_field_coverage,
                      publication_status,native_watermarks
                 FROM analytics.market_publications
                WHERE publication_id=%s""",
            (publication,),
        ).fetchone()
        assert refs[ident]["FORMING_KLINE"]["forming_observation_id"] == inserted
        assert coverage[ident]["FORMING_KLINE"]["status"] == (
            "UNCONFIRMED_OPEN_MINUTE"
        )
        assert status == "WATERMARKS_ONLY"
        assert native_cursor == {}  # never synthetic native publicTrade
        future = publish_market_source_second(
            conn, source_session_id=session, producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("1",),
            public_requirements=(need,),
            observed_at=START + timedelta(minutes=1, seconds=5),
        )
        assert future is not None
        gone, stale = conn.execute(
            """SELECT forming_minute_refs,forming_field_coverage
                 FROM analytics.market_publications
                WHERE publication_id=%s""",
            (future,),
        ).fetchone()
        assert gone == {}
        assert stale[ident]["FORMING_KLINE"]["status"] == "MISSING_OR_STALE"


def test_forming_source_pg_permissions_allow_insert_only() -> None:
    with _db() as conn:
        assert conn.execute(
            """SELECT
              has_table_privilege('cripta',
                'analytics.public_forming_minute_observations','SELECT'),
              has_table_privilege('cripta',
                'analytics.public_forming_minute_observations','INSERT'),
              has_table_privilege('cripta',
                'analytics.public_forming_minute_observations','UPDATE'),
              has_table_privilege('cripta',
                'analytics.public_forming_minute_observations','DELETE')
            """
        ).fetchone() == (True, True, False, False)
