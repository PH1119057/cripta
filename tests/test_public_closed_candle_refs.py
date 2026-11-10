"""R05: same symbol on SPOT and LINEAR is never one MarketSource candle."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.market_data.public_closed_candle_refs import (
    ClosedCandleReferenceUnverified,
    collect_instrument_closed_candle_references,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

NOW = datetime(2026, 11, 10, 8, 5, 30, tzinfo=UTC)
CLOSE = NOW.replace(second=0)


def _need(
    product: str, *, ttl: int = 300,
    lookback: int = 30,
) -> RequiredPublicFeed:
    return RequiredPublicFeed(
        consumer_id="MAYAK", market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id(product.lower(), "LTCUSDT"),
        product=product, symbol="LTCUSDT", dataset="CLOSED_KLINE",
        timeframe="5", freshness_seconds=ttl, lookback_bars=lookback,
    )


class DB:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.sql = ""

    def execute(self, sql: str, _args: tuple):
        self.sql = sql
        outer = self

        class Result:
            def fetchall(self):
                return outer.rows

        return Result()


def test_spot_linear_same_symbol_references_distinct_canonical_rows() -> None:
    spot = bybit_instrument_id("spot", "LTCUSDT")
    linear = bybit_instrument_id("linear", "LTCUSDT")
    db = DB([
        (spot, "SPOT", "LTCUSDT", "5", CLOSE),
        (linear, "LINEAR", "LTCUSDT", "5", CLOSE),
    ])
    refs, quality = collect_instrument_closed_candle_references(
        db, as_of_second=NOW, requirements=(
            _need("SPOT"),
            _need("LINEAR", lookback=300),
        ),
    )
    assert set(refs) == {spot, linear}
    assert refs[spot]["5"]["product_category"] == "SPOT"
    assert refs[linear]["5"]["product_category"] == "LINEAR"
    assert refs[spot]["5"]["instrument_id"] != refs[linear]["5"]["instrument_id"]
    assert quality[linear]["5"]["history_required_bars"] == 300
    assert quality[spot]["5"]["history_window"] == "NOT_VERIFIED"
    assert "instrument_id,timeframe" in db.sql
    assert "external_symbol=ANY" not in db.sql


def test_no_evidence_is_missing_not_synthetic_close_or_forward_fill() -> None:
    db = DB([])
    refs, coverage = collect_instrument_closed_candle_references(
        db, as_of_second=NOW, requirements=(_need("SPOT"),),
    )
    assert refs == {}
    inst = bybit_instrument_id("spot", "LTCUSDT")
    assert coverage[inst]["5"]["status"] == "MISSING_OR_STALE"


def test_stale_provider_close_is_not_exposed_as_fresh() -> None:
    spot = bybit_instrument_id("spot", "LTCUSDT")
    db = DB([(spot, "SPOT", "LTCUSDT", "5", CLOSE)])
    refs, coverage = collect_instrument_closed_candle_references(
        db, as_of_second=NOW, requirements=(_need("SPOT", ttl=20),),
    )
    assert refs == {}
    assert coverage[spot]["5"]["status"] == "STALE"


def test_wrong_product_on_same_instrument_fails_closed() -> None:
    spot = bybit_instrument_id("spot", "LTCUSDT")
    db = DB([(spot, "LINEAR", "LTCUSDT", "5", CLOSE)])
    with pytest.raises(ClosedCandleReferenceUnverified, match="ORIGIN_MISMATCH"):
        collect_instrument_closed_candle_references(
            db, as_of_second=NOW, requirements=(_need("SPOT"),),
        )


def test_unrelated_public_dataset_cannot_claim_closed_candle_ready() -> None:
    with pytest.raises(ClosedCandleReferenceUnverified, match="UNSUPPORTED"):
        collect_instrument_closed_candle_references(
            DB([]), as_of_second=NOW,
            requirements=(RequiredPublicFeed(
                consumer_id="MAYAK",
                market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
                instrument_id=bybit_instrument_id("spot", "LTCUSDT"),
                product="SPOT", symbol="LTCUSDT", dataset="INSTRUMENT_INFO",
                freshness_seconds=60,
            ),),
        )
