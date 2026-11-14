"""#470 R11: original MarketPublication REST feature parity for MAYAK."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestPublicationUnverified,
    read_r11_mayak_rest_candidate,
)

SESSION = "a" * 32
RELEASE = "b" * 40
NOW = datetime(2026, 11, 14, 16, 30, tzinfo=UTC)


class Result:
    def __init__(self, row: tuple[object, ...] | None):
        self.row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class DB:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = "on"
        self.symbol = "BTCUSDT"
        self.source = BYBIT_PUBLIC_MARKET_SOURCE_ID
        self.quality: dict[str, dict[str, dict[str, object]]] = {}
        self.refs: dict[str, dict[str, dict[str, object]]] = {}
        self.physical: dict[int, tuple[object, ...]] = {}
        ids = (
            (1, "SPOT", "INSTRUMENT_INFO", {"status": "Trading"}),
            (2, "LINEAR", "INSTRUMENT_INFO", {"status": "Trading"}),
            (3, "LINEAR", "ACCOUNT_RATIO", {
                "long_account_ratio": "0.55",
                "short_account_ratio": "0.45",
            }),
        )
        for pk, product, dataset, payload in ids:
            instrument = bybit_instrument_id(product.lower(), self.symbol)
            sample = NOW - timedelta(seconds=4)
            received = NOW - timedelta(seconds=3)
            self.refs.setdefault(instrument, {})[dataset] = {
                "rest_observation_id": pk,
                "source_session_id": SESSION,
                "instrument_id": instrument,
                "product_category": product,
                "external_symbol": self.symbol,
                "dataset": dataset,
                "source_at": sample.isoformat(),
                "received_at": received.isoformat(),
            }
            self.quality.setdefault(instrument, {})[dataset] = {
                "status": "OBSERVED",
            }
            self.physical[pk] = (
                self.source, SESSION, RELEASE, instrument, product,
                self.symbol, dataset, sample, received,
                "5min" if dataset == "ACCOUNT_RATIO" else "", payload,
            )
        self.queries: list[str] = []

    def execute(self, sql: str, args: object = None) -> Result:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Result((self.isolation,))
        if sql == "SHOW transaction_read_only":
            return Result((self.read_only,))
        if "FROM analytics.market_publications" in sql:
            assert args == (13,)
            return Result((
                self.source, SESSION, RELEASE, NOW,
                "WATERMARKS_ONLY", self.refs, self.quality,
            ))
        if "FROM analytics.public_market_rest_observations" in sql:
            assert isinstance(args, tuple) and len(args) == 1
            return Result(self.physical.get(args[0]))
        raise AssertionError(sql)


def test_seven_panel_btc_rest_ratio_and_support_are_physical_and_readonly() -> None:
    db = DB()
    result = read_r11_mayak_rest_candidate(
        db, publication_id=13, symbol="BTCUSDT",
    )
    assert result.long_ratio == 0.55
    assert result.short_ratio == 0.45
    assert result.spot_supported and result.linear_supported
    assert result.physical_rest_proven
    assert not result.mayak_cutover_ready
    assert len(db.queries) == 6
    assert all(
        sql.lstrip().upper().startswith(("SELECT", "SHOW"))
        for sql in db.queries
    )


@pytest.mark.parametrize("change", [
    "not_readonly", "non_rr", "foreign_source", "wrong_session",
    "wrong_venue_row", "missing_rest", "stale", "bad_ratio",
    "source_wrong_release", "future_received",
])
def test_inconsistent_provider_feature_fails_closed(change: str) -> None:
    db = DB()
    linear = bybit_instrument_id("linear", "BTCUSDT")
    if change == "not_readonly":
        db.read_only = "off"
    elif change == "non_rr":
        db.isolation = "read committed"
    elif change == "foreign_source":
        db.source = "bad-source"
    elif change == "wrong_session":
        db.refs[linear]["ACCOUNT_RATIO"]["source_session_id"] = "other"
    elif change == "wrong_venue_row":
        db.physical[3] = ("other-source", *db.physical[3][1:])
    elif change == "missing_rest":
        del db.refs[linear]["ACCOUNT_RATIO"]
    elif change == "stale":
        db.refs[linear]["ACCOUNT_RATIO"]["source_at"] = (
            NOW - timedelta(hours=1)
        ).isoformat()
    elif change == "bad_ratio":
        db.physical[3] = (*db.physical[3][:-1], {
            "long_account_ratio": "0.9", "short_account_ratio": "0.9",
        })
    elif change == "source_wrong_release":
        db.physical[3] = (*db.physical[3][:2], "f" * 40,
                          *db.physical[3][3:])
    elif change == "future_received":
        db.refs[linear]["ACCOUNT_RATIO"]["received_at"] = (
            NOW + timedelta(seconds=1)
        ).isoformat()
    with pytest.raises(R11MayakRestPublicationUnverified):
        read_r11_mayak_rest_candidate(
            db, publication_id=13, symbol="BTCUSDT",
        )


def test_outside_seven_never_enters_mayak_observation_path() -> None:
    with pytest.raises(R11MayakRestPublicationUnverified):
        read_r11_mayak_rest_candidate(
            DB(), publication_id=13, symbol="XRPUSDT",
        )
