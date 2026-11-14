"""#470 R10: Execution consumes exact original ticker + 5m from one PG snapshot.

All values are synthetic fixture rows; no Exchange, production DB or order.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.market_data import r10_execution_publication as r10
from bybit_workbench.market_data.exit_publication_reader import (
    ExitPublicationPin,
    ExitPublicationUnavailable,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

AT = datetime(2026, 11, 14, 11, 12, 10, tzinfo=UTC)
CLOSED = datetime(2026, 11, 14, 11, 10, tzinfo=UTC)
SECOND = AT - timedelta(seconds=1)
SYMBOL = "ARBUSDT"
INSTRUMENT = bybit_instrument_id("linear", SYMBOL)
SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
SESSION = "a" * 32
COMMIT = "b" * 40
FIELDS = ("lastPrice", "bid1Price", "ask1Price")


def pin() -> ExitPublicationPin:
    return ExitPublicationPin(
        publication_id=27,
        source_session_id=SESSION,
        producer_release_commit=COMMIT,
        as_of_second=AT,
        instrument_id=INSTRUMENT,
        symbol=SYMBOL,
        closed_refs={
            "5": {
                "closed_at": CLOSED.isoformat(),
            },
        },
        closed_quality={"5": {"required": True}},
        compact_refs={
            "TICKER": {
                "source_session_id": SESSION,
                "instrument_id": INSTRUMENT,
                "external_symbol": SYMBOL,
                "product_category": "LINEAR",
                "observed_second": SECOND.isoformat(),
                "source_time_max": SECOND.isoformat(),
            },
        },
        compact_quality={
            "TICKER": {
                "required": True,
                "status": "OBSERVED",
                "fields": {name: "FRESH" for name in FIELDS},
            },
        },
    )


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakePG:
    def __init__(self):
        self.original: dict[str, object] = {
            "lastPrice": "100",
            "bid1Price": "99",
            "ask1Price": "101",
        }
        self.field_times = {
            name: SECOND.isoformat() for name in FIELDS
        }
        self.source = SOURCE
        self.producer = COMMIT
        self.product = "LINEAR"
        self.symbol = SYMBOL
        self.physical_at = SECOND
        self.row_missing = False
        self.queries: list[str] = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        assert "FROM analytics.public_market_source_seconds" in sql
        assert params == (SESSION, INSTRUMENT, SECOND)
        if self.row_missing:
            return Cursor(None)
        return Cursor((
            self.source, self.producer, self.product, self.symbol,
            self.physical_at,
            {
                "ticker": self.original,
                "ticker_field_times": self.field_times,
            },
        ))


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch):
    primary = pin()
    monkeypatch.setattr(
        r10, "read_exit_ticker_last_price",
        lambda _db, *, symbol, observed_at: (
            Decimal("100"), primary,
        ),
    )
    candles = tuple(
        SimpleNamespace(
            is_closed=True, timeframe="5",
            closed_at=CLOSED - timedelta(minutes=5 * (239 - i)),
        )
        for i in range(240)
    )
    monkeypatch.setattr(
        r10, "read_exit_l53_candles",
        lambda _db, *, symbol, observed_at: (
            candles, primary,
        ),
    )
    return primary, candles


def quote(db: FakePG):
    return r10.read_r10_execution_quote(
        db, symbol=SYMBOL, observed_at=AT,
    )


def test_exact_original_bid_ask_last_price_source_and_ttl(ports) -> None:
    db = FakePG()
    result = quote(db)
    assert result.last_price == Decimal("100")
    assert result.bid_price == Decimal("99")
    assert result.ask_price == Decimal("101")
    assert result.publication_id == 27
    assert result.source_session_id == SESSION
    assert result.producer_release_commit == COMMIT
    assert result.field_times == (SECOND,) * 3
    assert len(db.queries) == 1
    assert all("INSERT" not in q and "UPDATE" not in q for q in db.queries)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda p, db: p.compact_quality["TICKER"]["fields"].update(
            bid1Price="STALE",
        ), "FIELDS_NOT_FRESH"),
        (lambda p, db: p.compact_quality["TICKER"].update(status="STALE"),
         "FIELDS_NOT_FRESH"),
        (lambda p, db: p.compact_refs["TICKER"].update(
            source_session_id="c" * 32,
        ), "REF_OR_AGE"),
        (lambda p, db: p.compact_refs["TICKER"].update(
            product_category="SPOT",
        ), "REF_OR_AGE"),
        (lambda p, db: p.compact_refs["TICKER"].update(
            observed_second=(AT - timedelta(seconds=5)).isoformat(),
        ), "REF_OR_AGE"),
        (lambda p, db: db.original.update(lastPrice="0"),
         "ORIGINAL_PRICE_INVALID"),
        (lambda p, db: db.original.update(lastPrice="101"),
         "CROSSED_OR_UNVERIFIED"),
        (lambda p, db: db.original.update(bid1Price="102"),
         "CROSSED_OR_UNVERIFIED"),
        (lambda p, db: db.original.update(ask1Price="NaN"),
         "ORIGINAL_PRICE_INVALID"),
        (lambda p, db: db.field_times.update(
            ask1Price=(AT - timedelta(seconds=6)).isoformat(),
        ), "FIELD_TTL_EXPIRED"),
        (lambda p, db: db.field_times.update(
            lastPrice=(AT + timedelta(seconds=1)).isoformat(),
        ), "FIELD_TTL_EXPIRED"),
        (lambda p, db: setattr(db, "producer", "d" * 40),
         "ORIGINAL_IDENTITY_MISMATCH"),
        (lambda p, db: setattr(db, "source", "wrong-source"),
         "ORIGINAL_IDENTITY_MISMATCH"),
        (lambda p, db: setattr(db, "product", "SPOT"),
         "ORIGINAL_IDENTITY_MISMATCH"),
        (lambda p, db: setattr(db, "symbol", "BTCUSDT"),
         "ORIGINAL_IDENTITY_MISMATCH"),
        (lambda p, db: setattr(db, "row_missing", True),
         "PHYSICAL_TICKER_MISSING"),
    ],
)
def test_quote_fails_closed_on_provenance_ttl_market_or_price(
    monkeypatch: pytest.MonkeyPatch, change, reason: str,
) -> None:
    base = pin()
    db = FakePG()
    change(base, db)
    monkeypatch.setattr(
        r10, "read_exit_ticker_last_price",
        lambda *_args, **_kw: (Decimal("100"), base),
    )
    with pytest.raises(r10.R10ExecutionPublicationUnavailable, match=reason):
        quote(db)


@pytest.mark.parametrize("seconds", [0, 6, -1, True])
def test_execution_ticker_age_contract_is_narrow_and_explicit(
    ports, seconds,
) -> None:
    with pytest.raises(
        r10.R10ExecutionPublicationUnavailable,
        match="R10_EXECUTION_TICKER_TTL_INVALID",
    ):
        r10.read_r10_execution_quote(
            FakePG(), symbol=SYMBOL, observed_at=AT,
            max_field_age_seconds=seconds,
        )


@pytest.mark.parametrize("history_limit", [205, 220, 240])
def test_r1_exact_validity_history_is_from_same_publication(
    ports, history_limit: int,
) -> None:
    primary, bars = ports
    db = FakePG()
    result = r10.read_r10_exact_r1_causal_snapshot(
        db, symbol=SYMBOL, observed_at=AT,
        history_limit=history_limit,
    )
    assert result.quote.publication_id == result.publication_id == 27
    assert result.source_session_id == SESSION
    assert result.history_limit == history_limit
    assert result.closed_5m == bars[-history_limit:]
    assert len(db.queries) == 1
    assert primary.producer_release_commit == result.quote.producer_release_commit


@pytest.mark.parametrize("history_limit", [0, 100, 204, 241, True, "220"])
def test_r1_never_uses_wrong_atr200_causal_seed(ports, history_limit) -> None:
    with pytest.raises(
        r10.R10ExecutionPublicationUnavailable,
        match="R10_R1_EXACT_HISTORY_LIMIT_INVALID",
    ):
        r10.read_r10_exact_r1_causal_snapshot(
            FakePG(), symbol=SYMBOL, observed_at=AT,
            history_limit=history_limit,
        )


def test_r1_split_publication_blocks_original_entry_validity(
    monkeypatch: pytest.MonkeyPatch, ports,
) -> None:
    _, candles = ports
    monkeypatch.setattr(
        r10, "read_exit_l53_candles",
        lambda *_args, **_kw: (
            candles, replace(pin(), publication_id=99),
        ),
    )
    with pytest.raises(
        r10.R10ExecutionPublicationUnavailable,
        match="R10_R1_SNAPSHOT_SPLIT_OR_UNCLOSED",
    ):
        r10.read_r10_exact_r1_causal_snapshot(
            FakePG(), symbol=SYMBOL, observed_at=AT,
            history_limit=220,
        )


def test_r1_no_closed_history_blocks_and_does_not_use_rest(
    monkeypatch: pytest.MonkeyPatch, ports,
) -> None:
    def gap(*_args, **_kwargs):
        raise ExitPublicationUnavailable("R08_CLOSED_240_BAR_HISTORY_UNVERIFIED")

    monkeypatch.setattr(r10, "read_exit_l53_candles", gap)
    with pytest.raises(
        r10.R10ExecutionPublicationUnavailable,
        match="R10_R1_EXACT_CLOSED_5M_HISTORY_UNVERIFIED",
    ):
        r10.read_r10_exact_r1_causal_snapshot(
            FakePG(), symbol=SYMBOL, observed_at=AT,
            history_limit=220,
        )


def test_r10_source_module_has_no_independent_exchange_feed() -> None:
    code = Path(
        "src/bybit_workbench/market_data/r10_execution_publication.py"
    ).read_text()
    assert "urllib" not in code
    assert "requests." not in code
    assert "api_get(" not in code
    assert "INSERT INTO" not in code
    assert "UPDATE " not in code
    assert "MarketPublication" in code
