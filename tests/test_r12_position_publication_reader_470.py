"""R12 #470: Position consumes one PG MarketPublication, never public REST."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_data.r12_position_publication import (
    R12PositionPublicationUnavailable,
    read_r12_position_market_snapshot,
)

SOURCE = "ms-bybit-mainnet-public-v1"
SESSION = "a" * 32
RELEASE = "b" * 40
SYMBOL = "LTCUSDT"
INSTRUMENT = "inst-bybit-linear-ltcusdt"
OPENED = datetime(2026, 11, 15, 4, 0, tzinfo=UTC)
PUBLISHED = OPENED + timedelta(seconds=9)
NOW = OPENED + timedelta(seconds=10)
FORMING_AT = OPENED + timedelta(seconds=8)
TICKER_SECOND = OPENED + timedelta(seconds=8)
BOOK_SECOND = OPENED + timedelta(seconds=7)


class Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakePG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = True
        self.publication_age = timedelta(0)
        self.ticker_fields_fresh = True
        self.drop_book = False
        self.queries: list[str] = []

    def execute(self, sql: str, params=()) -> Rows:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Rows([(self.isolation,)])
        if sql == "SHOW transaction_read_only":
            return Rows([("on" if self.read_only else "off",)])
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            return Rows([(SESSION, RELEASE)])
        if "FROM analytics.market_publications" in sql:
            forming_refs = {
                INSTRUMENT: {
                    "FORMING_KLINE": {
                        "forming_observation_id": 11,
                        "source_session_id": SESSION,
                        "instrument_id": INSTRUMENT,
                        "product_category": "LINEAR",
                        "external_symbol": SYMBOL,
                        "timeframe": "1",
                        "opened_at": OPENED.isoformat(),
                        "closed_at": (OPENED + timedelta(minutes=1)).isoformat(),
                        "source_at": FORMING_AT.isoformat(),
                    },
                },
            }
            forming_quality = {
                INSTRUMENT: {
                    "FORMING_KLINE": {
                        "status": "UNCONFIRMED_OPEN_MINUTE",
                        "required": True,
                        "freshness_seconds": 8,
                    },
                },
            }
            closed_refs = {
                INSTRUMENT: {
                    "1": {
                        "market_source_id": SOURCE,
                        "instrument_id": INSTRUMENT,
                        "product_category": "LINEAR",
                        "external_symbol": SYMBOL,
                        "timeframe": "1",
                        "closed_at": OPENED.isoformat(),
                    },
                },
            }
            closed_quality = {
                INSTRUMENT: {
                    "1": {
                        "status": "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED",
                        "required": True,
                        "freshness_seconds": 75,
                        "history_required_bars": 6,
                        "history_window": "NOT_VERIFIED",
                    },
                },
            }
            compact_refs = {
                INSTRUMENT: {
                    "TICKER": {
                        "source_session_id": SESSION,
                        "instrument_id": INSTRUMENT,
                        "product_category": "LINEAR",
                        "external_symbol": SYMBOL,
                        "observed_second": TICKER_SECOND.isoformat(),
                        "source_time_max": TICKER_SECOND.isoformat(),
                    },
                    "ORDERBOOK_50": {
                        "source_session_id": SESSION,
                        "instrument_id": INSTRUMENT,
                        "product_category": "LINEAR",
                        "external_symbol": SYMBOL,
                        "observed_second": BOOK_SECOND.isoformat(),
                        "source_time_max": BOOK_SECOND.isoformat(),
                    },
                },
            }
            if self.drop_book:
                del compact_refs[INSTRUMENT]["ORDERBOOK_50"]
            compact_quality = {
                INSTRUMENT: {
                    "TICKER": {
                        "status": "OBSERVED",
                        "required": True,
                        "freshness_seconds": 2,
                        "fields": {
                            "lastPrice": "FRESH",
                            "bid1Price": "FRESH",
                            "ask1Price": "FRESH",
                            "markPrice": (
                                "FRESH" if self.ticker_fields_fresh else "STALE"
                            ),
                        },
                    },
                    "ORDERBOOK_50": {
                        "status": "OBSERVED",
                        "required": True,
                        "freshness_seconds": 3,
                    },
                },
            }
            return Rows([(
                27, SESSION, RELEASE,
                PUBLISHED - self.publication_age,
                "WATERMARKS_ONLY",
                forming_refs, forming_quality,
                closed_refs, closed_quality,
                compact_refs, compact_quality,
            )])
        if "FROM analytics.public_forming_minute_observations" in sql:
            return Rows([(
                SESSION, INSTRUMENT, "LINEAR", SYMBOL, "1",
                OPENED, OPENED + timedelta(minutes=1), FORMING_AT,
                FORMING_AT, FORMING_AT + timedelta(milliseconds=100),
                {
                    "open": "100", "high": "102", "low": "99",
                    "close": "101", "volume": "5", "turnover": "505",
                },
                RELEASE,
            )])
        if "FROM analytics.closed_candles_effective" in sql:
            rows = []
            for i in range(6):
                closed = OPENED - timedelta(minutes=i)
                opened = closed - timedelta(minutes=1)
                close = Decimal(100 - i)
                rows.append((
                    opened, closed, close, close, close, close, Decimal("1"),
                ))
            return Rows(rows)
        if "FROM analytics.public_market_source_seconds" in sql:
            second = params[2]
            if second == TICKER_SECOND:
                field_time = (
                    TICKER_SECOND
                    if self.ticker_fields_fresh
                    else TICKER_SECOND - timedelta(seconds=10)
                )
                return Rows([(
                    SOURCE, RELEASE, "LINEAR", SYMBOL, TICKER_SECOND,
                    {
                        "ticker": {
                            "lastPrice": "101", "bid1Price": "100.9",
                            "ask1Price": "101.1", "markPrice": "101.02",
                        },
                        "ticker_field_times": {
                            name: field_time.isoformat()
                            for name in (
                                "lastPrice", "bid1Price",
                                "ask1Price", "markPrice",
                            )
                        },
                    },
                )])
            if second == BOOK_SECOND:
                return Rows([(
                    SOURCE, RELEASE, "LINEAR", SYMBOL, BOOK_SECOND,
                    {
                        "book": {
                            "best_bid": "100.9", "best_ask": "101.1",
                            "top25_bid_usd": "125000",
                            "top25_ask_usd": "100000",
                            "source_time": BOOK_SECOND.isoformat(),
                        },
                    },
                )])
        raise AssertionError(f"unexpected SQL: {sql}")


def read(db: FakePG | None = None):
    return read_r12_position_market_snapshot(
        db or FakePG(), symbol=SYMBOL, observed_at=NOW,
    )


def test_one_publication_returns_exact_position_facts() -> None:
    db = FakePG()
    got = read(db)
    assert got.publication_id == 27
    assert got.source_session_id == SESSION
    assert got.forming_close == Decimal("101")
    assert got.closed_closes_newest_first == (
        Decimal("100"), Decimal("99"), Decimal("98"),
        Decimal("97"), Decimal("96"), Decimal("95"),
    )
    assert got.last_price == Decimal("101")
    assert got.public_mark_price == Decimal("101.02")
    assert got.top25_bid_depth_usdt == Decimal("125000")
    assert got.top25_ask_depth_usdt == Decimal("100000")
    assert all(
        sql.lstrip().upper().startswith(("SHOW", "SELECT"))
        for sql in db.queries
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda db: setattr(db, "isolation", "read committed"),
         "READ_ONLY_REPEATABLE"),
        (lambda db: setattr(db, "read_only", False),
         "READ_ONLY_REPEATABLE"),
        (lambda db: setattr(db, "ticker_fields_fresh", False),
         "TICKER_FIELDS_NOT_FRESH"),
        (lambda db: setattr(db, "drop_book", True),
         "ORDERBOOK_50_REF_UNVERIFIED"),
    ],
)
def test_missing_stale_or_nonatomic_publication_fails_closed(
    mutate, reason: str,
) -> None:
    db = FakePG()
    mutate(db)
    with pytest.raises(R12PositionPublicationUnavailable, match=reason):
        read(db)


def test_r12_reader_has_no_network_or_market_writer() -> None:
    source = Path(
        "src/bybit_workbench/market_data/r12_position_publication.py"
    ).read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "requests." not in source
    assert "api.bybit" not in source
    assert "websocket" not in source
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "repeatable read" in source
