"""R11: MAYAK seven-coin features cannot be claimed from five-coin compact PG.

Positive candidate and negative source-quality coverage are read-only fixtures.
MAYAK socket, LiveMayakEngine and Dispatcher remain untouched.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.mayak.source_publication_parity import (
    R11_MAYAK_PANEL,
    R11MayakPublicationUnavailable,
    read_r11_mayak_feature_candidate,
)

SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
SYMBOL = "APTUSDT"
SPOT = bybit_instrument_id("spot", SYMBOL)
LINEAR = bybit_instrument_id("linear", SYMBOL)
EPOCH = "a" * 32
COMMIT = "b" * 40
AS_OF = datetime(2026, 11, 14, 7, 0, 12, tzinfo=UTC)
SECOND = AS_OF - timedelta(seconds=1)
NOW = AS_OF + timedelta(seconds=1)
TICKER_FIELDS = (
    "lastPrice", "markPrice", "indexPrice", "openInterest",
    "openInterestValue", "fundingRate",
)
BOOK_FIELDS = (
    "best_bid", "best_ask", "bid_size", "ask_size",
    "bid_5bps_usd", "ask_5bps_usd",
    "bid_10bps_usd", "ask_10bps_usd",
    "bid_25bps_usd", "ask_25bps_usd",
    "bid_50bps_usd", "ask_50bps_usd",
)


class Rows:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def fetchone(self):
        return self.items[0] if self.items else None

    def fetchall(self):
        return self.items


class FakePG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = True
        self.sessions = [(EPOCH, COMMIT)]
        self.pub_at = AS_OF
        self.pub_commit = COMMIT
        self.pub_state = "WATERMARKS_ONLY"
        self.refs: dict[str, dict[str, dict[str, object]]] = {}
        self.quality: dict[str, dict[str, dict[str, object]]] = {}
        self.original: dict[str, dict[str, object]] = {}
        for product, instrument, datasets in (
            ("SPOT", SPOT, ("ORDERBOOK_50",)),
            ("LINEAR", LINEAR, ("TICKER", "ORDERBOOK_50")),
        ):
            self.refs[instrument] = {}
            self.quality[instrument] = {}
            for dataset in datasets:
                self.refs[instrument][dataset] = {
                    "source_session_id": EPOCH,
                    "instrument_id": instrument,
                    "product_category": product,
                    "external_symbol": SYMBOL,
                    "observed_second": SECOND.isoformat(),
                    "source_time_max": SECOND.isoformat(),
                }
                self.quality[instrument][dataset] = {
                    "status": "OBSERVED",
                    "required": dataset == "TICKER",
                    "freshness_seconds": 5,
                }
                if dataset == "TICKER":
                    self.quality[instrument][dataset]["fields"] = {
                        name: "FRESH" for name in TICKER_FIELDS
                    }
            book = {name: "100" for name in BOOK_FIELDS}
            book.update({
                "best_bid": "99", "best_ask": "101",
                "bid_size": "2", "ask_size": "3",
                "source_sequence": 1234,
                "source_time": SECOND.isoformat(),
            })
            self.original[product] = {
                "book": book,
                "ticker": {
                    "lastPrice": "100", "markPrice": "100.1",
                    "indexPrice": "100.2", "openInterest": "100000",
                    "openInterestValue": "10000000",
                    "fundingRate": "-0.0001",
                },
                "ticker_field_times": {
                    name: SECOND.isoformat() for name in TICKER_FIELDS
                },
            }
        self.physical_product = {
            SPOT: "SPOT", LINEAR: "LINEAR",
        }
        self.queries: list[str] = []

    def execute(self, sql: str, params=()) -> Rows:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Rows([(self.isolation,)])
        if sql == "SHOW transaction_read_only":
            return Rows([("on" if self.read_only else "off",)])
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (SOURCE,)
            return Rows(self.sessions)
        if "FROM analytics.market_publications" in sql:
            assert params == (SOURCE, EPOCH)
            return Rows([(
                41, EPOCH, self.pub_commit, self.pub_at, self.pub_state,
                self.refs, self.quality,
            )])
        if "FROM analytics.public_market_source_seconds" in sql:
            epoch, instrument, second = params
            assert epoch == EPOCH and second == SECOND
            product = self.physical_product.get(instrument)
            if product is None:
                return Rows([])
            return Rows([(
                SOURCE, "venue-bybit", COMMIT, product, SYMBOL,
                SECOND, self.original[product],
            )])
        raise AssertionError(f"R11 unexpected SQL {sql}")


def read(db: FakePG, *, symbol: str = SYMBOL, now: datetime = NOW):
    return read_r11_mayak_feature_candidate(
        db, symbol=symbol, observed_at=now,
    )


def test_five_coin_candidate_proves_only_market_feature_projection() -> None:
    db = FakePG()
    candidate = read(db)
    assert candidate.symbol == SYMBOL
    assert candidate.publication_id == 41
    assert candidate.source_session_id == EPOCH
    assert candidate.linear_ticker["fundingRate"] == Decimal("-0.0001")
    assert candidate.spot_book_bands["best_bid"] == Decimal("99")
    assert candidate.linear_book_bands["best_ask"] == Decimal("101")
    assert candidate.seven_symbol_panel_verified is False
    assert candidate.original_spot_and_linear_trade_replay_verified is False
    assert candidate.book_delta_replay_verified is False
    assert candidate.liquidation_event_replay_verified is False
    assert candidate.optional_rest_feature_parity_verified is False
    assert candidate.ready_to_replace_mayak_websockets is False
    assert len([x for x in db.queries if "public_market_source_seconds" in x]) == 3
    assert all("INSERT" not in sql and "UPDATE" not in sql for sql in db.queries)


def test_exact_seven_mayak_panel_is_not_silently_renamed_to_five() -> None:
    assert R11_MAYAK_PANEL == (
        "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
        "BTCUSDT", "ETHUSDT",
    )
    for symbol in ("XRPUSDT",):
        with pytest.raises(
            R11MayakPublicationUnavailable, match="OUTSIDE_MAYAK_SOURCE_SCOPE",
        ):
            read(FakePG(), symbol=symbol)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda db: setattr(db, "isolation", "read committed"), "ATOMIC_SNAPSHOT"),
        (lambda db: setattr(db, "read_only", False), "ATOMIC_SNAPSHOT"),
        (lambda db: setattr(db, "sessions", []), "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "sessions", [(EPOCH, COMMIT)] * 2),
         "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "sessions", [("bad-epoch", COMMIT)]),
         "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "pub_commit", "f" * 40),
         "PUBLICATION_WRONG_EPOCH"),
        (lambda db: setattr(db, "pub_state", "GAP"),
         "PUBLICATION_WRONG_EPOCH"),
        (lambda db: setattr(db, "pub_at", AS_OF - timedelta(seconds=50)),
         "PUBLICATION_WRONG_EPOCH"),
        (
            lambda db: db.refs[SPOT]["ORDERBOOK_50"].update(
                product_category="LINEAR",
            ),
            "FEATURE_REF_OR_QUALITY",
        ),
        (
            lambda db: db.refs[LINEAR]["TICKER"].update(
                source_session_id="f" * 32,
            ),
            "FEATURE_REF_OR_QUALITY",
        ),
        (
            lambda db: db.quality[LINEAR]["TICKER"].update(status="STALE"),
            "FEATURE_REF_OR_QUALITY",
        ),
        (
            lambda db: db.refs[LINEAR]["TICKER"].update(
                observed_second=(AS_OF - timedelta(seconds=20)).isoformat(),
            ),
            "FEATURE_SECOND_STALE",
        ),
        (
            lambda db: db.quality[LINEAR]["TICKER"]["fields"].update(
                fundingRate="STALE",
            ),
            "TICKER_FIELDS_NOT_FRESH",
        ),
        (
            lambda db: db.original["LINEAR"]["ticker_field_times"].update(
                openInterest=(AS_OF - timedelta(seconds=20)).isoformat(),
            ),
            "TICKER_FIELD_EXPIRED",
        ),
        (
            lambda db: db.original["SPOT"]["book"].update(
                best_bid="102",
            ),
            "BOOK_UNVERIFIED_OR_CROSSED",
        ),
        (
            lambda db: db.original["LINEAR"]["book"].update(
                source_sequence=-1,
            ),
            "BOOK_EVENT_IDENTITY",
        ),
        (
            lambda db: db.original["LINEAR"]["ticker"].update(
                markPrice="NaN",
            ),
            "ORIGINAL_VALUE_INVALID",
        ),
        (
            lambda db: db.original["LINEAR"]["ticker"].update(
                openInterest="0",
            ),
            "TICKER_REQUIRED_FIELD_INVALID",
        ),
        (
            lambda db: db.physical_product.update({SPOT: "LINEAR"}),
            "PHYSICAL_IDENTITY",
        ),
        (
            lambda db: db.refs[SPOT].pop("ORDERBOOK_50"),
            "FEATURE_REF_OR_QUALITY",
        ),
    ],
)
def test_publication_feature_gaps_cannot_be_promoted_to_parity(
    change, reason: str,
) -> None:
    db = FakePG()
    change(db)
    with pytest.raises(R11MayakPublicationUnavailable, match=reason):
        read(db)


def test_r11_adapter_cannot_start_network_or_mutate_strategy() -> None:
    code = Path("src/bybit_workbench/mayak/source_publication_parity.py").read_text()
    assert "urllib" not in code
    assert "import websocket" not in code
    assert "websocket.create_connection" not in code
    assert "psycopg.connect" not in code
    assert "ready_to_replace_mayak_websockets" in code
    assert "original_spot_and_linear_trade_replay_verified: bool = False" in code
    assert "seven_symbol_panel_verified: bool = False" in code
    assert "FOR UPDATE" not in code
    assert "DELETE FROM" not in code
