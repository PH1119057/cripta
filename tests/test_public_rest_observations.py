"""R05: public REST source epoch + per-measurement freshness, no Exchange rights."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_data.public_market_rest_source import (
    decode_public_rest_response,
)
from bybit_workbench.market_data.public_rest_observations import (
    PublicRestEvidenceUnverified,
    collect_public_rest_references,
    persist_public_rest_fact,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

NOW = datetime(2026, 11, 10, 15, 20, tzinfo=UTC)
SESSION = "a" * 32
COMMIT = "a" * 40


def _need(product: str, dataset: str, ttl: int = 600) -> RequiredPublicFeed:
    return RequiredPublicFeed(
        consumer_id="MAYAK",
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id(product.lower(), "LTCUSDT"),
        product=product, symbol="LTCUSDT", dataset=dataset,
        freshness_seconds=ttl,
    )


class FakeDB:
    def __init__(self, *, active: bool = True, rows: tuple = ()) -> None:
        self.active = active
        self.rows = rows
        self.sql: list[str] = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql: str, params: object = ()):
        self.sql.append(sql)
        owner = self

        class Result:
            def fetchone(self):
                if "FROM analytics.native_public_trade_source_sessions" in sql:
                    return (
                        ("ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, COMMIT)
                        if owner.active else
                        ("GAP", BYBIT_PUBLIC_MARKET_SOURCE_ID, COMMIT)
                    )
                if "INSERT INTO analytics.public_market_rest_observations" in sql:
                    return (17,)
                raise AssertionError(sql)

            def fetchall(self):
                if "FROM analytics.public_market_rest_observations" in sql:
                    return list(owner.rows)
                raise AssertionError(sql)

        return Result()


def test_exact_rest_writer_requires_active_source_epoch() -> None:
    fact = decode_public_rest_response(
        {"retCode": 0, "result": {"category": "spot", "list": [
            {"symbol": "LTCUSDT", "status": "Trading"},
        ]}},
        dataset="INSTRUMENT_INFO", product="SPOT",
        symbol="LTCUSDT", received_at=NOW,
    )
    db = FakeDB()
    assert persist_public_rest_fact(
        db, source_session_id=SESSION, producer_release_commit=COMMIT,
        fact=fact,
    ) == 17
    assert "ON CONFLICT" in db.sql[-1]
    with pytest.raises(PublicRestEvidenceUnverified, match="NOT_ACTIVE"):
        persist_public_rest_fact(
            FakeDB(active=False), source_session_id=SESSION,
            producer_release_commit=COMMIT, fact=fact,
        )


def test_rest_publication_tracks_original_ratio_clock_not_new_receipt() -> None:
    at = NOW - timedelta(seconds=180)
    ident = bybit_instrument_id("linear", "LTCUSDT")
    db = FakeDB(rows=((
        79, ident, "LINEAR", "LTCUSDT", at, NOW - timedelta(seconds=1),
        "5min", {"long_account_ratio": "0.6", "short_account_ratio": "0.4"},
    ),))
    refs, quality = collect_public_rest_references(
        db, source_session_id=SESSION, as_of_second=NOW,
        requirements=(
            _need("LINEAR", "ACCOUNT_RATIO", 600),
            _need("LINEAR", "ACCOUNT_RATIO", 300),
        ),
    )
    assert refs[ident]["ACCOUNT_RATIO"]["rest_observation_id"] == 79
    assert quality[ident]["ACCOUNT_RATIO"]["status"] == "OBSERVED"
    assert quality[ident]["ACCOUNT_RATIO"]["freshness_seconds"] == 300
    assert quality[ident]["ACCOUNT_RATIO"]["age_seconds"] == 180


def test_stale_ratio_does_not_relabel_old_exchange_sample_fresh() -> None:
    at = NOW - timedelta(seconds=450)
    ident = bybit_instrument_id("linear", "LTCUSDT")
    db = FakeDB(rows=((
        79, ident, "LINEAR", "LTCUSDT", at, NOW - timedelta(seconds=1),
        "5min", {"long_account_ratio": "0.6", "short_account_ratio": "0.4"},
    ),))
    refs, quality = collect_public_rest_references(
        db, source_session_id=SESSION, as_of_second=NOW,
        requirements=(_need("LINEAR", "ACCOUNT_RATIO", 300),),
    )
    assert refs == {}
    assert quality[ident]["ACCOUNT_RATIO"]["status"] == "STALE"


def test_nontrading_spot_is_not_evidence_of_permission() -> None:
    ident = bybit_instrument_id("spot", "LTCUSDT")
    db = FakeDB(rows=((
        19, ident, "SPOT", "LTCUSDT", NOW - timedelta(seconds=5),
        NOW - timedelta(seconds=5), "",
        {"status": "NOT_LISTED"},
    ),))
    refs, quality = collect_public_rest_references(
        db, source_session_id=SESSION, as_of_second=NOW,
        requirements=(_need("SPOT", "INSTRUMENT_INFO"),),
    )
    assert refs[ident]["INSTRUMENT_INFO"]["rest_observation_id"] == 19
    assert quality[ident]["INSTRUMENT_INFO"]["status"] == "NOT_LISTED"


def test_wrong_market_category_is_a_hard_provenance_conflict() -> None:
    ident = bybit_instrument_id("spot", "LTCUSDT")
    db = FakeDB(rows=((
        19, ident, "LINEAR", "LTCUSDT", NOW - timedelta(seconds=5),
        NOW - timedelta(seconds=5), "", {"status": "Trading"},
    ),))
    with pytest.raises(PublicRestEvidenceUnverified, match="ORIGIN_CONFLICT"):
        collect_public_rest_references(
            db, source_session_id=SESSION, as_of_second=NOW,
            requirements=(_need("SPOT", "INSTRUMENT_INFO"),),
        )
