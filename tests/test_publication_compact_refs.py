"""R05: one immutable source-second PK, explicit per-field freshness/coverage."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_data.publication_compact_refs import (
    CompactPublicationUnverified,
    collect_compact_public_references,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

AT = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SESSION = "a" * 32
SYMBOL = "LTCUSDT"


def _need(
    consumer: str, dataset: str, *, product: str = "LINEAR",
    ttl: int = 10,
) -> RequiredPublicFeed:
    return RequiredPublicFeed(
        consumer_id=consumer,
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id(product.lower(), SYMBOL),
        product=product,
        symbol=SYMBOL,
        dataset=dataset,
        freshness_seconds=ttl,
    )


class RowsDB:
    def __init__(self, rows: dict[str, tuple]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query: str, args: tuple):
        self.calls.append((query, args))
        owner = self

        class Result:
            def fetchall(self):
                for name, marker in (
                    ("TICKER", "'ticker'"),
                    ("ORDERBOOK_50", "'book'"),
                    ("LIQUIDATION", "'liquidation_count'"),
                    ("PUBLIC_TRADE", "'trade_count'"),
                ):
                    if marker in query:
                        return [owner.rows[name]] if name in owner.rows else []
                raise AssertionError("unexpected source dataset query")

        return Result()


def _row(
    product: str, at: datetime, *,
    ticker: bool = False, book: bool = False,
    trade: bool = False, liquidation: bool = False,
) -> tuple:
    features: dict[str, object] = {}
    if ticker:
        features = {
            "ticker": {"lastPrice": "51.5", "openInterest": "200"},
            "ticker_field_times": {
                "lastPrice": (AT - timedelta(seconds=1)).isoformat(),
                "openInterest": (AT - timedelta(seconds=50)).isoformat(),
            },
        }
    if book:
        features["book"] = {"best_bid": "51.4", "best_ask": "51.6"}
    if trade:
        features["trade_count"] = "2"
    if liquidation:
        features["liquidation_count"] = "1"
    return (
        bybit_instrument_id(product.lower(), SYMBOL),
        product, SYMBOL, at, at, features,
    )


def test_same_instrument_reuses_one_compact_fact_for_entry_and_exit() -> None:
    db = RowsDB({
        "TICKER": _row("LINEAR", AT - timedelta(seconds=1), ticker=True),
        "ORDERBOOK_50": _row("LINEAR", AT - timedelta(seconds=2), book=True),
        "PUBLIC_TRADE": _row("LINEAR", AT - timedelta(seconds=3), trade=True),
    })
    requirements = (
        _need("ENTRY", "TICKER", ttl=10),
        _need("EXIT", "TICKER", ttl=5),
        _need("POSITION", "ORDERBOOK_50", ttl=5),
        _need("MAYAK", "PUBLIC_TRADE", ttl=10),
    )
    refs, coverage = collect_compact_public_references(
        db, source_session_id=SESSION, as_of_second=AT,
        requirements=requirements,
    )
    ident = bybit_instrument_id("linear", SYMBOL)
    assert len(db.calls) == 3  # four consumers but three physical datasets
    assert db.calls[0][1][0] == SESSION
    assert all("observed_second < %s" in q for q, _ in db.calls)
    assert refs[ident]["TICKER"]["source_session_id"] == SESSION
    assert refs[ident]["ORDERBOOK_50"]["product_category"] == "LINEAR"
    assert coverage[ident]["TICKER"]["freshness_seconds"] == 5
    assert coverage[ident]["TICKER"]["fields"] == {
        "lastPrice": "FRESH",
        "openInterest": "STALE",
    }
    assert coverage[ident]["PUBLIC_TRADE"]["status"] == (
        "COMPACT_ONLY_NOT_NATIVE"
    )
    assert "price" not in str(refs)  # no duplicate market payload


def test_zero_liquidation_is_unverified_not_invented_zero() -> None:
    db = RowsDB({})
    refs, coverage = collect_compact_public_references(
        db, source_session_id=SESSION, as_of_second=AT,
        requirements=(_need("MAYAK", "LIQUIDATION"),),
    )
    assert refs == {}
    id_ = bybit_instrument_id("linear", SYMBOL)
    assert coverage[id_]["LIQUIDATION"]["status"] == "ZERO_OR_GAP_UNVERIFIED"


def test_spot_linear_same_symbol_never_merge_or_rename_instrument() -> None:
    spot = _need("MAYAK", "ORDERBOOK_50", product="SPOT")
    db = RowsDB({
        "ORDERBOOK_50": _row(
            "LINEAR", AT - timedelta(seconds=1), book=True,
        ),
    })
    with pytest.raises(CompactPublicationUnverified, match="ROW_CONFLICT"):
        collect_compact_public_references(
            db, source_session_id=SESSION, as_of_second=AT,
            requirements=(spot,),
        )


def test_no_feed_requirements_means_no_spurious_published_fact() -> None:
    db = RowsDB({})
    assert collect_compact_public_references(
        db, source_session_id=SESSION, as_of_second=AT,
        requirements=(),
    ) == ({}, {})
    assert db.calls == []


def test_unsupported_future_required_adapter_fails_closed() -> None:
    db = RowsDB({})
    with pytest.raises(CompactPublicationUnverified, match="UNSUPPORTED"):
        collect_compact_public_references(
            db, source_session_id=SESSION, as_of_second=AT,
            requirements=(_need("POSITION", "FORMING_KLINE"),),
        )


def test_no_partial_ticker_without_field_timestamps() -> None:
    db = RowsDB({
        "TICKER": (
            bybit_instrument_id("linear", SYMBOL),
            "LINEAR", SYMBOL,
            AT - timedelta(seconds=1), AT - timedelta(seconds=1),
            {"ticker": {"lastPrice": "51.5"}},
        ),
    })
    with pytest.raises(CompactPublicationUnverified, match="TIMES_MISSING"):
        collect_compact_public_references(
            db, source_session_id=SESSION, as_of_second=AT,
            requirements=(_need("ENTRY", "TICKER"),),
        )
