"""R04: disposable PG16 spot+linear second source writer, no Exchange action.

Fail closed unless the connection points to EXACTLY cripta_integration. The
test inserts synthetic public facts, never backfills or mutates live runtime.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.public_source_second_store import (
    PublicSecondAccumulator,
    PublicSourceSecondUnavailable,
    persist_compact_source_seconds,
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
