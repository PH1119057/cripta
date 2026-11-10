"""R04/R05: source-owned REST facts and same publication ID on DISPOSABLE PG16.

Never operate on production. A POST, authenticated Exchange call, Strategy
consumer change or separate public-market writer is not required.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.public_market_rest_source import (
    decode_public_rest_response,
)
from bybit_workbench.market_data.public_rest_observations import (
    PublicRestEvidenceUnverified,
    persist_public_rest_fact,
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
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL16 only")
RELEASE = "a" * 40


def _db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("disposable PostgreSQL16 required")
    conn = psycopg.connect(DSN, autocommit=True)
    if conn.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        conn.close()
        pytest.skip("production database forbidden")
    return conn


def test_real_pg16_single_source_rest_ratio_info_and_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "RELEASE", RELEASE)
    session = uuid4().hex
    response_at = datetime.now(UTC) - timedelta(seconds=3)
    source_ratio_at = response_at - timedelta(minutes=2)
    ratio = decode_public_rest_response(
        {
            "retCode": 0,
            "result": {"list": [{
                "symbol": "LTCUSDT", "buyRatio": "0.6",
                "sellRatio": "0.4",
                "timestamp": str(int(source_ratio_at.timestamp() * 1000)),
            }]},
        },
        dataset="ACCOUNT_RATIO", product="LINEAR", symbol="LTCUSDT",
        received_at=response_at,
    )
    instrument = decode_public_rest_response(
        {"retCode": 0, "result": {
            "category": "spot",
            "list": [{"symbol": "LTCUSDT", "status": "Trading",
                      "priceFilter": {"tickSize": "0.001"}}],
        }},
        dataset="INSTRUMENT_INFO", product="SPOT", symbol="LTCUSDT",
        received_at=response_at,
    )
    needs = (
        RequiredPublicFeed(
            consumer_id="MAYAK",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=bybit_instrument_id("linear", "LTCUSDT"),
            product="LINEAR", symbol="LTCUSDT", dataset="ACCOUNT_RATIO",
            freshness_seconds=600,
        ),
        RequiredPublicFeed(
            consumer_id="MAYAK",
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=bybit_instrument_id("spot", "LTCUSDT"),
            product="SPOT", symbol="LTCUSDT", dataset="INSTRUMENT_INFO",
            freshness_seconds=900,
        ),
    )

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
        native._session_start(conn, session)
        rid = persist_public_rest_fact(
            conn, source_session_id=session,
            producer_release_commit=RELEASE, fact=ratio,
        )
        iid = persist_public_rest_fact(
            conn, source_session_id=session,
            producer_release_commit=RELEASE, fact=instrument,
        )
        assert rid != iid
        # The source's 5-minute measurement must be idempotent even when
        # requested twice by several consumers with the same exact symbol.
        assert persist_public_rest_fact(
            conn, source_session_id=session,
            producer_release_commit=RELEASE, fact=ratio,
        ) == rid
        with pytest.raises(PublicRestEvidenceUnverified, match="NOT_ACTIVE"):
            persist_public_rest_fact(
                conn, source_session_id=session,
                producer_release_commit="b" * 40, fact=ratio,
            )
        publication_id = publish_market_source_second(
            conn, source_session_id=session,
            producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("5",),
            public_requirements=needs,
            observed_at=datetime.now(UTC),
        )
        assert publication_id is not None
        refs, quality, status = conn.execute(
            """SELECT rest_observation_refs,rest_field_coverage,
                      publication_status
                 FROM analytics.market_publications
                WHERE publication_id=%s""", (publication_id,),
        ).fetchone()
        linear = bybit_instrument_id("linear", "LTCUSDT")
        spot = bybit_instrument_id("spot", "LTCUSDT")
        assert refs[linear]["ACCOUNT_RATIO"]["rest_observation_id"] == rid
        assert refs[spot]["INSTRUMENT_INFO"]["rest_observation_id"] == iid
        assert quality[linear]["ACCOUNT_RATIO"]["status"] == "OBSERVED"
        assert quality[spot]["INSTRUMENT_INFO"]["status"] == "OBSERVED"
        assert status == "WATERMARKS_ONLY"
        assert conn.execute(
            """SELECT count(*) FROM analytics.public_market_rest_observations
                WHERE source_session_id=%s""", (session,),
        ).fetchone() == (2,)


def test_pg16_public_rest_writer_permissions_are_insert_only() -> None:
    with _db() as conn:
        assert conn.execute(
            """SELECT
                 has_table_privilege(
                   'cripta','analytics.public_market_rest_observations','SELECT'),
                 has_table_privilege(
                   'cripta','analytics.public_market_rest_observations','INSERT'),
                 has_table_privilege(
                   'cripta','analytics.public_market_rest_observations','UPDATE'),
                 has_table_privilege(
                   'cripta','analytics.public_market_rest_observations','DELETE')
            """
        ).fetchone() == (True, True, False, False)
