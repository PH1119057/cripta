"""R03/R05: real PostgreSQL 16 producer publication on a DISPOSABLE DB only.

No test may point to production; all writes must be confined to exactly
cripta_integration and have no Exchange/Strategy/Entry/Exit mutation.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.native_public_trade_journal import (
    NativeTradeEvidence,
)
from operations.monitoring import native_public_trade_source as source

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL only")
RELEASE = "a" * 40
START = 1794221700000
END = START + 300000 - 1
OBSERVED = datetime(2026, 11, 9, 11, 0, 1, tzinfo=UTC)


def _db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("not disposable")
    conn = psycopg.connect(DSN, autocommit=True)
    if conn.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        conn.close()
        pytest.skip("live database is forbidden")
    return conn


def test_one_producer_publishes_exact_source_reference_not_second_tape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source, "RELEASE", RELEASE)
    session = uuid4().hex
    evidence = NativeTradeEvidence(
        market_source_id="ms-bybit-mainnet-public-v1",
        instrument_id="inst-bybit-linear-ltcusdt",
        external_symbol="LTCUSDT",
        exchange_sequence=123456,
        exec_id="publication-ci-" + uuid4().hex,
        event_at=datetime(2026, 11, 9, 10, 59, tzinfo=UTC),
        price=Decimal("55.5"), quantity=Decimal("0.10"),
        taker_side="Buy", source_transport="BYBIT_WS_PUBLIC_TRADE",
        producer_release_commit=RELEASE,
    )
    kline = {
        "topic": "kline.5.LTCUSDT", "type": "snapshot",
        "ts": START + 300000 + 300,
        "data": [{
            "start": START, "end": END, "interval": "5",
            "open": "55.0", "high": "56.0",
            "low": "54.0", "close": "55.5",
            "volume": "150.0", "confirm": True,
        }],
    }

    with _db() as conn:
        # Disposable test bootstrap intentionally does not register every
        # instrument: source/candle validation requires exact registry ID.
        conn.execute(
            """INSERT INTO identity.instruments
                 (instrument_id,venue_id,product_category,external_symbol)
               VALUES ('inst-bybit-linear-ltcusdt','venue-bybit',
                       'LINEAR','LTCUSDT')
               ON CONFLICT (instrument_id) DO NOTHING"""
        )
        source._session_start(conn, session)
        source._persist_frame(
            conn, session=session, frame_number=1, facts=(evidence,),
        )
        # This is the SAME source process and the SAME canonical closed-bar
        # table that Entry/Exit will later read. No per-consumer kline stream.
        source._persist_closed_kline_frame(
            conn, message=kline, symbols=("LTCUSDT",),
            intervals=("5",),
        )

        publication = publish_market_source_second(
            conn, source_session_id=session,
            producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("5",),
            observed_at=OBSERVED,
        )
        assert isinstance(publication, int)
        assert publish_market_source_second(
            conn, source_session_id=session,
            producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("5",),
            observed_at=OBSERVED,
        ) is None

        row = conn.execute(
            """SELECT source_session_id,as_of_second,native_watermarks,
                      closed_candle_watermarks,field_coverage,publication_status
               FROM analytics.market_publications
               WHERE publication_id=%s""", (publication,),
        ).fetchone()
        assert row is not None
        assert row[0] == session and row[1] == OBSERVED.replace(microsecond=0)
        assert row[2]["LTCUSDT"]["exec_id"] == evidence.exec_id
        assert row[2]["LTCUSDT"]["exchange_sequence"] == 123456
        assert row[3]["LTCUSDT"]["5"] == "2026-11-09T11:00:00+00:00"
        assert row[4]["LTCUSDT"]["native"] == "PRESENT_IN_SOURCE_EPOCH"
        assert row[5] == "WATERMARKS_ONLY"
        assert conn.execute(
            """SELECT count(*) FROM analytics.market_publications
               WHERE market_source_id='ms-bybit-mainnet-public-v1'
                 AND source_session_id=%s""", (session,),
        ).fetchone() == (1,)
        assert conn.execute(
            """SELECT count(*) FROM analytics.native_public_trade_source_events
               WHERE source_session_id=%s""", (session,),
        ).fetchone() == (1,)
        assert conn.execute(
            """SELECT close FROM analytics.closed_candles_effective
               WHERE market_source_id='ms-bybit-mainnet-public-v1'
                 AND external_symbol='LTCUSDT' AND timeframe='5'
                 AND closed_at='2026-11-09T11:00:00Z'""",
        ).fetchone()[0] == Decimal("55.5")


def test_publication_schema_allows_read_and_insert_but_not_market_deletion() -> None:
    with _db() as conn:
        assert conn.execute(
            "SELECT has_table_privilege('cripta',"
            "'analytics.market_publications','SELECT')"
        ).fetchone() == (True,)
        assert conn.execute(
            "SELECT has_table_privilege('cripta',"
            "'analytics.market_publications','INSERT')"
        ).fetchone() == (True,)
        assert conn.execute(
            "SELECT has_table_privilege('cripta',"
            "'analytics.market_publications','DELETE')"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT has_sequence_privilege('cripta',"
            "'analytics.market_publications_publication_id_seq','USAGE')"
        ).fetchone() == (True,)
