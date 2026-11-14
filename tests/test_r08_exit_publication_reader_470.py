"""Issue #470 R08: exact source-pinned Exit observations, no independent REST."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_data.exit_publication_reader import (
    ExitPublicationUnavailable,
    read_exit_l53_candles,
    read_exit_ticker_last_price,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_entry.market_watch import compute_l53_zone

SYMBOL = "APTUSDT"
INSTRUMENT = bybit_instrument_id("linear", SYMBOL)
EPOCH = "a" * 32
COMMIT = "b" * 40
PUBLISHED = datetime(2026, 11, 14, 12, 5, 20, tzinfo=UTC)
CLOSED = datetime(2026, 11, 14, 12, 5, tzinfo=UTC)
TICK_SECOND = PUBLISHED - timedelta(seconds=2)


class Result:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class FakePG:
    def __init__(self) -> None:
        self.read_only = True
        self.isolation = "repeatable read"
        self.sessions = [(EPOCH, COMMIT)]
        self.status = "WATERMARKS_ONLY"
        self.publication_at = PUBLISHED
        self.closed_at = CLOSED
        self.closed_symbol = SYMBOL
        self.closed_product = "LINEAR"
        self.required = 240
        self.quality_status = "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED"
        self.closed_rows = []
        for index in range(240):
            closed = CLOSED - timedelta(minutes=(239 - index) * 5)
            price = Decimal("90") + Decimal(index) / Decimal(10)
            self.closed_rows.append((
                closed - timedelta(minutes=5),
                closed,
                price,
                price + Decimal("0.3"),
                price - Decimal("0.3"),
                price + Decimal("0.1"),
                Decimal("100"),
            ))
        self.ticker_price: object = "111.2"
        self.ticker_epoch = EPOCH
        self.ticker_commit = COMMIT
        self.ticker_second = TICK_SECOND
        self.ticker_field_at = TICK_SECOND
        self.ticker_quality = "OBSERVED"
        self.ticker_field_quality = "FRESH"
        self.ticker_ref_symbol = SYMBOL
        self.queries: list[str] = []

    def execute(self, sql: str, params: object = ()) -> Result:
        self.queries.append(sql)
        if sql == "SHOW transaction_isolation":
            return Result([(self.isolation,)])
        if sql == "SHOW transaction_read_only":
            return Result([("on" if self.read_only else "off",)])
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (BYBIT_PUBLIC_MARKET_SOURCE_ID,)
            return Result(self.sessions)
        if "FROM analytics.market_publications" in sql:
            assert params == (BYBIT_PUBLIC_MARKET_SOURCE_ID, EPOCH)
            closed_refs = {
                INSTRUMENT: {
                    "5": {
                        "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
                        "instrument_id": INSTRUMENT,
                        "external_symbol": self.closed_symbol,
                        "product_category": self.closed_product,
                        "timeframe": "5",
                        "closed_at": self.closed_at.isoformat(),
                    }
                }
            }
            closed_quality = {
                INSTRUMENT: {
                    "5": {
                        "status": self.quality_status,
                        "required": True,
                        "freshness_seconds": 600,
                        "history_required_bars": self.required,
                        "history_window": "NOT_VERIFIED",
                    }
                }
            }
            compact_refs = {
                INSTRUMENT: {
                    "TICKER": {
                        "source_session_id": self.ticker_epoch,
                        "instrument_id": INSTRUMENT,
                        "external_symbol": self.ticker_ref_symbol,
                        "product_category": "LINEAR",
                        "observed_second": self.ticker_second.isoformat(),
                        "source_time_max": self.ticker_second.isoformat(),
                    }
                }
            }
            compact_quality = {
                INSTRUMENT: {
                    "TICKER": {
                        "status": self.ticker_quality,
                        "required": True,
                        "freshness_seconds": 10,
                        "fields": {"lastPrice": self.ticker_field_quality},
                    }
                }
            }
            return Result([(
                17, EPOCH, COMMIT, self.publication_at, self.status,
                closed_refs, closed_quality, compact_refs, compact_quality,
            )])
        if "FROM analytics.closed_candles_effective" in sql:
            assert params == (
                BYBIT_PUBLIC_MARKET_SOURCE_ID, INSTRUMENT, "5", CLOSED, 240,
            )
            return Result(list(reversed(self.closed_rows[-240:])))
        if "FROM analytics.public_market_source_seconds" in sql:
            assert params == (EPOCH, INSTRUMENT, self.ticker_second)
            return Result([(
                BYBIT_PUBLIC_MARKET_SOURCE_ID, self.ticker_commit,
                "LINEAR", SYMBOL, self.ticker_second,
                {
                    "ticker": {"lastPrice": self.ticker_price},
                    "ticker_field_times": {
                        "lastPrice": self.ticker_field_at.isoformat(),
                    },
                },
            )])
        raise AssertionError(f"Unexpected R08 SQL: {sql}")


def test_240_contiguous_5m_source_candles_preserve_atr200_l53_geometry() -> None:
    db = FakePG()
    candles, pin = read_exit_l53_candles(
        db, symbol=SYMBOL, observed_at=PUBLISHED + timedelta(seconds=1),
    )
    assert len(candles) == 240
    assert candles[-1].closed_at == CLOSED
    assert all(bar.symbol == SYMBOL and bar.timeframe == "5" for bar in candles)
    zone = compute_l53_zone(candles)
    assert zone is not None
    assert zone.observed_at == CLOSED
    assert zone.atr > 0
    assert zone.effective_lookback == 36
    assert pin.publication_id == 17
    assert pin.source_session_id == EPOCH
    assert pin.producer_release_commit == COMMIT
    assert "CLOSED_KLINE:5" in pin.evidence_ref("CLOSED_KLINE:5")
    assert all("INSERT" not in sql and "UPDATE" not in sql for sql in db.queries)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda db: setattr(db, "sessions", []), "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "sessions", [(EPOCH, COMMIT)] * 2), "ONE_ACTIVE_SOURCE"),
        (lambda db: setattr(db, "read_only", False), "READ_ONLY_SNAPSHOT"),
        (lambda db: setattr(db, "isolation", "read committed"), "READ_ONLY_SNAPSHOT"),
        (lambda db: setattr(db, "status", "UNKNOWN"), "PUBLICATION_IDENTITY"),
        (
            lambda db: setattr(db, "publication_at", PUBLISHED - timedelta(minutes=2)),
            "PUBLICATION_IDENTITY",
        ),
        (lambda db: setattr(db, "closed_product", "SPOT"), "CLOSED_5M_REF"),
        (lambda db: setattr(db, "closed_symbol", "BTCUSDT"), "CLOSED_5M_REF"),
        (lambda db: setattr(db, "required", 36), "CLOSED_5M_REF"),
        (lambda db: setattr(db, "quality_status", "STALE"), "CLOSED_5M_REF"),
        (
            lambda db: setattr(db, "closed_at", CLOSED - timedelta(minutes=5)),
            "CLOSED_5M_BOUNDARY",
        ),
        (
            lambda db: db.closed_rows.pop(25),
            "CLOSED_240_BAR_HISTORY",
        ),
        (
            lambda db: db.closed_rows.__setitem__(
                180, db.closed_rows[179],
            ),
            "CLOSED_240_BAR_HISTORY",
        ),
    ],
)
def test_l53_fails_closed_for_epoch_manifest_time_or_history(change, reason: str) -> None:
    db = FakePG()
    change(db)
    with pytest.raises(ExitPublicationUnavailable, match=reason):
        read_exit_l53_candles(db, symbol=SYMBOL, observed_at=PUBLISHED)


def test_last_price_only_from_verified_source_epoch_and_fresh_field() -> None:
    db = FakePG()
    price, pin = read_exit_ticker_last_price(
        db, symbol=SYMBOL, observed_at=PUBLISHED,
    )
    assert price == Decimal("111.2")
    assert pin.evidence_ref("TICKER:lastPrice").endswith("TICKER:lastPrice")
    assert len([q for q in db.queries if "public_market_source_seconds" in q]) == 1
    assert all("INSERT" not in q and "UPDATE" not in q for q in db.queries)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda db: setattr(db, "ticker_epoch", "f" * 32), "TICKER_REF_UNVERIFIED"),
        (lambda db: setattr(db, "ticker_ref_symbol", "BTCUSDT"), "TICKER_REF_UNVERIFIED"),
        (lambda db: setattr(db, "ticker_quality", "STALE"), "TICKER_REF_UNVERIFIED"),
        (lambda db: setattr(db, "ticker_field_quality", "STALE"), "TICKER_REF_UNVERIFIED"),
        (
            lambda db: setattr(db, "ticker_second", PUBLISHED - timedelta(seconds=30)),
            "TICKER_TIME_OR_TTL",
        ),
        (lambda db: setattr(db, "ticker_commit", "0" * 40), "TICKER_PHYSICAL_IDENTITY"),
        (
            lambda db: setattr(db, "ticker_field_at", PUBLISHED - timedelta(seconds=30)),
            "TICKER_FIELD_STALE",
        ),
        (lambda db: setattr(db, "ticker_price", "NaN"), "TICKER_LAST_PRICE_INVALID"),
        (lambda db: setattr(db, "ticker_price", "0"), "TICKER_LAST_PRICE_INVALID"),
    ],
)
def test_ticker_fails_closed_on_missing_or_mismatched_physical_fact(change, reason: str) -> None:
    db = FakePG()
    change(db)
    with pytest.raises(ExitPublicationUnavailable, match=reason):
        read_exit_ticker_last_price(db, symbol=SYMBOL, observed_at=PUBLISHED)


def test_r08_exit_wiring_has_no_second_market_rest_path() -> None:
    script = Path("operations/monitoring/universal_exit_shadow_runtime.py").read_text()
    unit = Path("operations/systemd/cripta-universal-exit-shadow.service").read_text()
    assert "read_exit_l53_candles(" in script
    assert "read_exit_ticker_last_price(" in script
    assert "REPEATABLE READ READ ONLY" in script
    assert "with source_connection.transaction():" in script
    assert "urllib.request" not in script
    assert "map_rest_klines" not in script
    assert "PUBLIC_REST" not in script
    assert "CRIPTA_U5_PUBLIC_REST" not in unit
    assert "strategy_exit.exit_observations" in script
    assert 'execution_rights": "NONE"' in script
