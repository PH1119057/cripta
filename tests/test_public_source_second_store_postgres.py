"""R04: disposable PG16 spot+linear second source writer, no Exchange action.

Fail closed unless the connection points to EXACTLY cripta_integration. The
test inserts synthetic public facts, never backfills or mutates live runtime.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.public_forming_minute_source import (
    persist_forming_minute_fact,
)
from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.public_source_second_store import (
    PublicSecondAccumulator,
    PublicSourceSecondUnavailable,
    persist_compact_source_seconds,
)
from bybit_workbench.market_data.r12_position_publication import (
    read_r12_position_market_snapshot,
)
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from operations.monitoring import native_public_trade_source as source

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL 16 only")
RELEASE = "a" * 40
AT = datetime(2026, 11, 10, 8, 40, 0, 750000, tzinfo=UTC)
MS = int(AT.timestamp() * 1000)


def _db() -> psycopg.Connection:
    if DSN is None:
        pytest.skip("disposable PostgreSQL required")
    db = psycopg.connect(DSN, autocommit=True)
    if db.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        db.close()
        pytest.skip("production database forbidden")
    return db


def _trade(category: str):
    return decode_bybit_public_frame(
        {
            "topic": "publicTrade.LTCUSDT", "ts": MS,
            "data": [{
                "s": "LTCUSDT", "T": MS,
                "seq": 98822, "i": "compact-ci-" + uuid4().hex,
                "p": "51.75", "v": "0.2", "S": "Buy",
            }],
        },
        product=category,
        allowed_symbols=frozenset({"LTCUSDT"}),
        received_at=AT,
    )[0]


def test_pg16_spot_and_linear_immutable_one_second_and_release_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source, "RELEASE", RELEASE)
    session = uuid4().hex
    accumulator = PublicSecondAccumulator(session=session)
    for category in ("SPOT", "LINEAR"):
        accumulator.accept(_trade(category), received_at=AT)
    rows = accumulator.ready_seconds(before=AT + timedelta(seconds=1))
    assert len(rows) == 2
    with _db() as conn:
        conn.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-spot-ltcusdt','venue-bybit','SPOT','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        conn.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-linear-ltcusdt','venue-bybit',
                       'LINEAR','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        source._session_start(conn, session)
        assert persist_compact_source_seconds(
            conn, rows=rows, producer_release_commit=RELEASE,
        ) == 2
        assert persist_compact_source_seconds(
            conn, rows=rows, producer_release_commit=RELEASE,
        ) == 0

        stored = conn.execute(
            """SELECT instrument_id,product_category,compact_features,
                      producer_release_commit
                 FROM analytics.public_market_source_seconds
                WHERE source_session_id=%s ORDER BY instrument_id""",
            (session,),
        ).fetchall()
        assert len(stored) == 2
        assert {item[1] for item in stored} == {"SPOT", "LINEAR"}
        assert all(item[3] == RELEASE for item in stored)
        assert all(item[2]["trade_count"] == "1" for item in stored)
        assert all(item[2]["trade_buy_usd"] == "10.350" for item in stored)

        with pytest.raises(PublicSourceSecondUnavailable, match="NOT_ACTIVE"):
            persist_compact_source_seconds(
                conn, rows=rows, producer_release_commit="b" * 40,
            )
        assert conn.execute(
            """SELECT count(*) FROM analytics.public_market_source_seconds
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (2,)


def test_pg16_source_compact_writer_is_insert_only() -> None:
    with _db() as conn:
        assert conn.execute(
            """SELECT
                 has_table_privilege(
                   'cripta','analytics.public_market_source_seconds','SELECT'),
                 has_table_privilege(
                   'cripta','analytics.public_market_source_seconds','INSERT'),
                 has_table_privilege(
                   'cripta','analytics.public_market_source_seconds','UPDATE'),
                 has_table_privilege(
                   'cripta','analytics.public_market_source_seconds','DELETE')
            """
        ).fetchone() == (True, True, False, False)


def test_pg16_one_publication_pins_spot_and_linear_compact_source_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline: one producer epoch -> immutable seconds -> one PG slice."""
    monkeypatch.setattr(source, "RELEASE", RELEASE)
    session = uuid4().hex
    event_at = (datetime.now(UTC) - timedelta(seconds=3)).replace(
        microsecond=500000,
    )
    stamp_ms = int(event_at.timestamp() * 1000)
    aggregator = PublicSecondAccumulator(session=session)
    requirements: list[RequiredPublicFeed] = []

    for product in ("SPOT", "LINEAR"):
        native = decode_bybit_public_frame(
            {
                "topic": "publicTrade.LTCUSDT", "ts": stamp_ms,
                "data": [{
                    "s": "LTCUSDT", "T": stamp_ms,
                    "seq": 900, "i": f"publication-{product}-{session}",
                    "p": "51.7", "v": "0.1", "S": "Buy",
                }],
            },
            product=product, allowed_symbols=frozenset({"LTCUSDT"}),
            received_at=event_at,
        )
        for fact in native:
            aggregator.accept(fact, received_at=event_at)
        requirements.append(RequiredPublicFeed(
            consumer_id="MAYAK",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=bybit_instrument_id(product.lower(), "LTCUSDT"),
            product=product,
            symbol="LTCUSDT",
            dataset="PUBLIC_TRADE", freshness_seconds=10,
        ))
    ticker = decode_bybit_public_frame(
        {
            "topic": "tickers.LTCUSDT", "ts": stamp_ms,
            "type": "snapshot",
            "data": {"symbol": "LTCUSDT", "lastPrice": "51.7"},
        },
        product="LINEAR", allowed_symbols=frozenset({"LTCUSDT"}),
        received_at=event_at,
    )
    for fact in ticker:
        aggregator.accept(fact, received_at=event_at)
    requirements.append(RequiredPublicFeed(
        consumer_id="EXIT",
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id("linear", "LTCUSDT"),
        product="LINEAR",
        symbol="LTCUSDT", dataset="TICKER", freshness_seconds=10,
    ))
    rows = aggregator.ready_seconds(before=datetime.now(UTC))
    assert len(rows) == 2

    with _db() as conn:
        conn.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-spot-ltcusdt','venue-bybit','SPOT','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        conn.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-linear-ltcusdt','venue-bybit',
                       'LINEAR','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        source._session_start(conn, session)
        assert persist_compact_source_seconds(
            conn, rows=rows, producer_release_commit=RELEASE,
        ) == 2
        now = datetime.now(UTC)
        publication = publish_market_source_second(
            conn, source_session_id=session,
            producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("5",),
            public_requirements=tuple(requirements),
            observed_at=now,
        )
        assert publication is not None
        compact_refs, compact_quality, publication_status = conn.execute(
            """SELECT compact_second_refs,compact_field_coverage,
                      publication_status
                 FROM analytics.market_publications
                WHERE publication_id=%s""",
            (publication,),
        ).fetchone()
        spot = bybit_instrument_id("spot", "LTCUSDT")
        linear = bybit_instrument_id("linear", "LTCUSDT")
        assert compact_refs[spot]["PUBLIC_TRADE"]["product_category"] == "SPOT"
        assert compact_refs[linear]["PUBLIC_TRADE"]["product_category"] == "LINEAR"
        assert compact_refs[linear]["TICKER"]["source_session_id"] == session
        assert compact_quality[linear]["TICKER"]["fields"]["lastPrice"] == "FRESH"
        assert compact_quality[spot]["PUBLIC_TRADE"]["status"] == (
            "COMPACT_ONLY_NOT_NATIVE"
        )
        assert publication_status == "WATERMARKS_ONLY"
        # This read-model does not invent an exact P90 native trade from a
        # lower-fidelity spot/linear compact second.
        assert conn.execute(
            """SELECT count(*) FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone() == (0,)


def test_r12_pg16_position_reader_uses_one_publication_and_exact_top25(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R12 real SQL: source facts -> one publication -> read-only Position."""
    monkeypatch.setattr(source, "RELEASE", RELEASE)
    session = uuid4().hex
    base = (datetime.now(UTC) - timedelta(minutes=2)).replace(
        second=0, microsecond=0,
    )
    publication_at = base + timedelta(seconds=10)
    instrument_id = bybit_instrument_id("linear", "LTCUSDT")
    requirements = (
        RequiredPublicFeed(
            consumer_id="POSITION",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=instrument_id,
            product="LINEAR", symbol="LTCUSDT",
            dataset="FORMING_KLINE", timeframe="1",
            freshness_seconds=8,
        ),
        RequiredPublicFeed(
            consumer_id="POSITION",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=instrument_id,
            product="LINEAR", symbol="LTCUSDT",
            dataset="CLOSED_KLINE", timeframe="1",
            freshness_seconds=75, lookback_bars=6,
        ),
        RequiredPublicFeed(
            consumer_id="POSITION",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=instrument_id,
            product="LINEAR", symbol="LTCUSDT",
            dataset="TICKER", freshness_seconds=2,
        ),
        RequiredPublicFeed(
            consumer_id="POSITION",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=instrument_id,
            product="LINEAR", symbol="LTCUSDT",
            dataset="ORDERBOOK_50", freshness_seconds=3,
        ),
    )

    forming_at = publication_at - timedelta(seconds=1)
    forming_ms = int(forming_at.timestamp() * 1000)
    forming = decode_bybit_public_frame(
        {
            "topic": "kline.1.LTCUSDT", "type": "snapshot",
            "ts": forming_ms,
            "data": [{
                "start": int(base.timestamp() * 1000),
                "end": int(base.timestamp() * 1000) + 59999,
                "interval": "1", "open": "50", "high": "52",
                "low": "49", "close": "51", "volume": "2",
                "turnover": "102", "confirm": False,
                "timestamp": forming_ms,
            }],
        },
        product="LINEAR", allowed_symbols=frozenset({"LTCUSDT"}),
        received_at=forming_at + timedelta(milliseconds=100),
    )[0]

    accumulator = PublicSecondAccumulator(session=session)
    ticker_at = publication_at - timedelta(seconds=1)
    ticker_ms = int(ticker_at.timestamp() * 1000)
    ticker = decode_bybit_public_frame(
        {
            "topic": "tickers.LTCUSDT", "type": "snapshot",
            "ts": ticker_ms,
            "data": {
                "symbol": "LTCUSDT", "lastPrice": "51",
                "bid1Price": "50.9", "ask1Price": "51.1",
                "markPrice": "51.02",
            },
        },
        product="LINEAR", allowed_symbols=frozenset({"LTCUSDT"}),
        received_at=ticker_at,
    )
    book_at = publication_at - timedelta(seconds=2)
    book_ms = int(book_at.timestamp() * 1000)
    bids = [[str(50 - i / 100), "2"] for i in range(30)]
    asks = [[str(51 + i / 100), "2"] for i in range(30)]
    book = decode_bybit_public_frame(
        {
            "topic": "orderbook.50.LTCUSDT", "type": "snapshot",
            "ts": book_ms,
            "data": {
                "s": "LTCUSDT", "u": 1, "seq": 700,
                "b": bids, "a": asks,
            },
        },
        product="LINEAR", allowed_symbols=frozenset({"LTCUSDT"}),
        received_at=book_at,
    )
    for fact in (*ticker, *book):
        accumulator.accept(fact, received_at=fact.source_time)
    seconds = accumulator.ready_seconds(before=publication_at)
    assert seconds

    with _db() as admin:
        admin.execute(
            """UPDATE analytics.native_public_trade_source_sessions
                  SET state='GAP', gap_reason='R12_DISPOSABLE_TEST'
                WHERE state='ACTIVE'"""
        )
        admin.execute(
            """INSERT INTO identity.instruments
               (instrument_id,venue_id,product_category,external_symbol)
               VALUES (%s,'venue-bybit','LINEAR','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING""",
            (instrument_id,),
        )
        source._session_start(admin, session)
        persist_forming_minute_fact(
            admin,
            source_session_id=session,
            producer_release_commit=RELEASE,
            fact=forming,
            received_at=forming_at + timedelta(milliseconds=100),
        )
        key = MarketCandleKey(
            BYBIT_PUBLIC_MARKET_SOURCE_ID, "venue-bybit",
            instrument_id, "LTCUSDT", "LINEAR", "1",
        )
        bars = []
        for offset in range(6, 0, -1):
            opened = base - timedelta(minutes=offset)
            closed = opened + timedelta(minutes=1)
            price = 50 - Decimal(offset) / Decimal(10)
            bars.append(Candle(
                "LTCUSDT", "1", opened, closed,
                price, price, price, price, Decimal("1"),
            ))
        SharedClosedCandleStore(admin, key).ingest(
            bars,
            producer_release_commit=RELEASE,
            transport="BYBIT_WS_KLINE",
            observed_at=publication_at,
        )
        assert persist_compact_source_seconds(
            admin, rows=seconds, producer_release_commit=RELEASE,
        ) == len(seconds)
        publication = publish_market_source_second(
            admin,
            source_session_id=session,
            producer_release_commit=RELEASE,
            symbols=("LTCUSDT",),
            timeframes=("1",),
            public_requirements=requirements,
            observed_at=publication_at,
        )
        assert publication is not None

    if DSN is None:
        pytest.skip("disposable PostgreSQL required")
    with psycopg.connect(DSN, autocommit=True) as reader:
        reader.execute("SET ROLE cripta")
        with reader.transaction():
            reader.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            snapshot = read_r12_position_market_snapshot(
                reader,
                symbol="LTCUSDT",
                observed_at=publication_at + timedelta(seconds=1),
            )
    assert snapshot.publication_id == publication
    assert snapshot.forming_close == Decimal("51")
    assert len(snapshot.closed_closes_newest_first) == 6
    assert snapshot.last_price == Decimal("51")
    assert snapshot.public_mark_price == Decimal("51.02")
    expected_bid = sum(
        (Decimal(str(50 - i / 100)) * 2 for i in range(25)),
        Decimal(0),
    )
    expected_ask = sum(
        (Decimal(str(51 + i / 100)) * 2 for i in range(25)),
        Decimal(0),
    )
    assert snapshot.top25_bid_depth_usdt == expected_bid
    assert snapshot.top25_ask_depth_usdt == expected_ask
