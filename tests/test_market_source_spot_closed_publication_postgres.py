"""R03/R05: physical confirmed SPOT+LINEAR source and one PG publication.

Only disposable cripta_integration PostgreSQL16, no production writes or
Exchange connection. The original closed bars are stored ONCE in canonical
analytics.closed_candles; the publication contains only exact references.
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
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from operations.monitoring import native_public_trade_source as source

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PostgreSQL16 only")
RELEASE = "a" * 40


def _db() -> psycopg.Connection:
    if not DSN:
        pytest.skip("disposable PostgreSQL required")
    conn = psycopg.connect(DSN, autocommit=True)
    if conn.execute("SELECT current_database()").fetchone() != (
        "cripta_integration",
    ):
        conn.close()
        pytest.skip("production DB forbidden")
    return conn


def _closed_frame(start: datetime, *,
                  category: str) -> dict[str, object]:
    first = int(start.timestamp() * 1000)
    values = (
        ("70", "72", "69", "70.5")
        if category == "SPOT" else
        ("55", "57", "54", "55.5")
    )
    return {
        "topic": "kline.5.LTCUSDT", "type": "snapshot",
        "ts": first + 300000 + 300,
        "data": [{
            "start": first, "end": first + 300000 - 1,
            "interval": "5",
            "open": values[0], "high": values[1],
            "low": values[2], "close": values[3],
            "volume": "120", "confirm": True,
        }],
    }


def test_one_source_persists_spot_linear_distinct_bars_and_publishes_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source, "RELEASE", RELEASE)
    now = datetime.now(UTC)
    closed = now.replace(
        minute=(now.minute // 5) * 5, second=0, microsecond=0,
    )
    start = closed - timedelta(minutes=5)
    session = uuid4().hex
    requirements = tuple(
        RequiredPublicFeed(
            consumer_id="MAYAK", market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument_id=bybit_instrument_id(product.lower(), "LTCUSDT"),
            product=product, symbol="LTCUSDT", dataset="CLOSED_KLINE",
            timeframe="5", freshness_seconds=900, lookback_bars=2600,
        )
        for product in ("SPOT", "LINEAR")
    )
    with _db() as conn:
        for product in ("SPOT", "LINEAR"):
            conn.execute(
                """INSERT INTO identity.instruments
                   (instrument_id,venue_id,product_category,external_symbol)
                   VALUES (%s,'venue-bybit',%s,'LTCUSDT')
                   ON CONFLICT (instrument_id) DO NOTHING""",
                (bybit_instrument_id(product.lower(), "LTCUSDT"), product),
            )
        source._session_start(conn, session)
        for product in ("SPOT", "LINEAR"):
            assert source._persist_closed_kline_frame(
                conn, message=_closed_frame(start, category=product),
                symbols=("LTCUSDT",), intervals=("5",), product=product,
            ) == 1
        publication = publish_market_source_second(
            conn, source_session_id=session,
            producer_release_commit=RELEASE,
            symbols=("LTCUSDT",), timeframes=("5",),
            public_requirements=requirements,
            observed_at=datetime.now(UTC),
        )
        assert publication is not None
        spot = bybit_instrument_id("spot", "LTCUSDT")
        linear = bybit_instrument_id("linear", "LTCUSDT")
        refs, qc, legacy, status = conn.execute(
            """SELECT closed_instrument_refs,closed_instrument_coverage,
                      closed_candle_watermarks,publication_status
                 FROM analytics.market_publications
                WHERE publication_id=%s""", (publication,),
        ).fetchone()
        assert set(refs) == {spot, linear}
        assert refs[spot]["5"]["product_category"] == "SPOT"
        assert refs[linear]["5"]["product_category"] == "LINEAR"
        assert refs[spot]["5"]["closed_at"] == closed.isoformat()
        assert refs[linear]["5"]["closed_at"] == closed.isoformat()
        assert qc[spot]["5"]["history_window"] == "NOT_VERIFIED"
        assert qc[linear]["5"]["status"] == (
            "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED"
        )
        assert legacy["LTCUSDT"]["5"] == closed.isoformat()
        assert status == "WATERMARKS_ONLY"
        values = conn.execute(
            """SELECT instrument_id,product_category,close
                 FROM analytics.closed_candles_effective
                WHERE market_source_id=%s
                  AND instrument_id=ANY(%s)
                  AND timeframe='5' AND closed_at=%s""",
            (BYBIT_PUBLIC_MARKET_SOURCE_ID, [spot, linear], closed),
        ).fetchall()
        assert len(values) == 2
        by_product = {product: price for _, product, price in values}
        assert str(by_product["SPOT"]) == "70.5"
        assert str(by_product["LINEAR"]) == "55.5"
