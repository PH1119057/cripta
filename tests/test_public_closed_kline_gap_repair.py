"""R03: only missing original Bybit closed candles, no live producer or trading."""
from __future__ import annotations

import json
import urllib.parse
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.public_closed_kline_gap_repair import (
    ClosedGapRepairUnavailable,
    ClosedGapPage,
    fetch_verified_bybit_closed_page,
    plan_missing_closed_pages,
    repair_source_closed_history,
)
from bybit_workbench.market_data.shared_closed_candles import MarketCandleKey
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

FIVE = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")
NOW = datetime(2026, 11, 10, 18, 30, tzinfo=UTC)
THROUGH = datetime(2026, 11, 10, 18, 25, tzinfo=UTC)
STEP = timedelta(minutes=5)
SOURCE_SESSION = "a" * 32
RELEASE = "b" * 40


def key(symbol: str = "LTCUSDT", product: str = "LINEAR") -> MarketCandleKey:
    return MarketCandleKey(
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        venue_id="venue-bybit",
        instrument_id=bybit_instrument_id(product.lower(), symbol),
        external_symbol=symbol, product_category=product, timeframe="5",
    )


def candle(close: datetime) -> Candle:
    return Candle(
        symbol="LTCUSDT", timeframe="5",
        opened_at=close - STEP, closed_at=close,
        open=Decimal("90"), high=Decimal("101"), low=Decimal("89"),
        close=Decimal("100"), volume=Decimal("3"), is_closed=True,
    )


class Rows:
    def __init__(self, data: list[tuple[object, ...]]) -> None:
        self.data = data

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.data

    def fetchone(self) -> tuple[object, ...] | None:
        return self.data[0] if self.data else None


class FakeDB:
    def __init__(
        self, present: tuple[datetime, ...], *,
        epochs: tuple[tuple[str, str, str], ...] | None = None,
    ) -> None:
        self.present = present
        self.epochs = epochs or (
            ("ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE),
        )
        self.epoch_reads = 0
        self.queries: list[str] = []

    def execute(self, sql: str, params: object = ()) -> Rows:
        self.queries.append(sql)
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            current = self.epochs[min(self.epoch_reads, len(self.epochs) - 1)]
            self.epoch_reads += 1
            return Rows([current])
        if "FROM analytics.closed_candles_effective" in sql:
            return Rows([(x,) for x in self.present])
        raise AssertionError("Unexpected query")

    def transaction(self):
        return nullcontext()


class RESTResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.body[:size]


def payload(
    closes: tuple[datetime, ...], *, symbol: str = "LTCUSDT",
    ret_code: int = 0,
) -> bytes:
    items = [
        [
            str(int((end - STEP).timestamp() * 1000)),
            "90", "101", "89", "100", "3", "250",
        ]
        for end in reversed(closes)
    ]
    return json.dumps({
        "retCode": ret_code, "retMsg": "OK",
        "time": int(NOW.timestamp() * 1000),
        "result": {
            "category": "linear", "symbol": symbol,
            "list": items,
        },
    }).encode()


def test_plan_reads_only_actual_missing_closed_slots() -> None:
    db = FakeDB((THROUGH - 2 * STEP, THROUGH))
    pages = plan_missing_closed_pages(
        db, key=key(), native_symbols=FIVE,
        through=THROUGH, required=3, observed_at=NOW,
    )
    assert pages == (
        ClosedGapPage(THROUGH - STEP, THROUGH - STEP, 1),
    )
    assert len(db.queries) == 1
    assert "closed_candles_effective" in db.queries[0]


def test_complete_cache_causes_zero_public_rest_calls() -> None:
    closes = (THROUGH - 2 * STEP, THROUGH - STEP, THROUGH)
    db = FakeDB(closes)
    store = Mock()
    store.history.return_value = tuple(candle(x) for x in closes)
    opener = Mock(side_effect=AssertionError("must not touch Bybit"))
    with patch(
        "bybit_workbench.market_data.public_closed_kline_gap_repair."
        "SharedClosedCandleStore",
        return_value=store,
    ):
        result = repair_source_closed_history(
            db, key=key(), native_symbols=FIVE,
            source_session_id=SOURCE_SESSION,
            producer_release_commit=RELEASE,
            through=THROUGH, required=3,
            clock=lambda: NOW, opener=opener,
        )
    assert (result.fetched_bars, result.inserted_bars, result.pages) == (0, 0, 0)
    store.ingest.assert_not_called()
    opener.assert_not_called()


def test_only_missing_bar_fetched_and_committed_to_canonical_store() -> None:
    missing = THROUGH - STEP
    db = FakeDB((THROUGH - 2 * STEP, THROUGH))
    store = Mock()
    store.ingest.return_value = 1
    store.history.return_value = tuple(
        candle(x) for x in (THROUGH - 2 * STEP, missing, THROUGH)
    )
    opener = Mock(return_value=RESTResponse(payload((missing,))))
    with patch(
        "bybit_workbench.market_data.public_closed_kline_gap_repair."
        "SharedClosedCandleStore",
        return_value=store,
    ):
        result = repair_source_closed_history(
            db, key=key(), native_symbols=FIVE,
            source_session_id=SOURCE_SESSION,
            producer_release_commit=RELEASE,
            through=THROUGH, required=3,
            clock=lambda: NOW, opener=opener,
        )
    assert (result.fetched_bars, result.inserted_bars, result.pages) == (1, 1, 1)
    assert db.epoch_reads == 2
    request = opener.call_args.args[0]
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(
        request.full_url,
    ).query)
    assert request.full_url.startswith("https://api.bybit.kz/v5/market/kline?")
    assert params["category"] == ["linear"]
    assert params["symbol"] == ["LTCUSDT"]
    assert params["interval"] == ["5"]
    assert params["limit"] == ["1"]
    assert params["start"] == [
        str(int((missing - STEP).timestamp() * 1000))
    ]
    assert params["end"] == params["start"]
    args, kw = store.ingest.call_args
    assert len(args[0]) == 1 and args[0][0].closed_at == missing
    assert kw["transport"] == "BYBIT_REST_KLINE"


@pytest.mark.parametrize("wrong", [
    payload((THROUGH - 2 * STEP,)),  # Wrong UTC end: no silent fill.
    payload((THROUGH - STEP,), symbol="BTCUSDT"),  # Wrong market identity.
    payload((THROUGH - STEP,), ret_code=10001),
    b"{}",
    b"<html>not JSON</html>",
])
def test_unverified_provider_page_never_reaches_postgres(wrong: bytes) -> None:
    db = FakeDB((THROUGH - 2 * STEP, THROUGH))
    store = Mock()
    with patch(
        "bybit_workbench.market_data.public_closed_kline_gap_repair."
        "SharedClosedCandleStore",
        return_value=store,
    ), pytest.raises(ClosedGapRepairUnavailable):
        repair_source_closed_history(
            db, key=key(), native_symbols=FIVE,
            source_session_id=SOURCE_SESSION,
            producer_release_commit=RELEASE,
            through=THROUGH, required=3,
            clock=lambda: NOW,
            opener=Mock(return_value=RESTResponse(wrong)),
        )
    store.ingest.assert_not_called()


def test_epoch_changed_during_network_fetch_blocks_all_writes() -> None:
    db = FakeDB(
        (THROUGH - 2 * STEP, THROUGH),
        epochs=(
            ("ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE),
            ("GAP", BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE),
        ),
    )
    store = Mock()
    with patch(
        "bybit_workbench.market_data.public_closed_kline_gap_repair."
        "SharedClosedCandleStore",
        return_value=store,
    ), pytest.raises(ClosedGapRepairUnavailable, match="EPOCH_NOT_ACTIVE"):
        repair_source_closed_history(
            db, key=key(), native_symbols=FIVE,
            source_session_id=SOURCE_SESSION,
            producer_release_commit=RELEASE,
            through=THROUGH, required=3, clock=lambda: NOW,
            opener=Mock(return_value=RESTResponse(payload((THROUGH - STEP,)))),
        )
    store.ingest.assert_not_called()


@pytest.mark.parametrize("symbols,product,required", [
    (FIVE + ("BTCUSDT",), "LINEAR", 3),
    (("LTCUSDT",), "LINEAR", 3),
    (FIVE, "SPOT", 3),
    (FIVE, "LINEAR", 3001),
])
def test_unapproved_or_unbounded_scope_fails_before_sql_or_network(
    symbols: tuple[str, ...], product: str, required: int,
) -> None:
    db = FakeDB(())
    with pytest.raises(ClosedGapRepairUnavailable, match="SCOPE_UNAUTHORIZED"):
        plan_missing_closed_pages(
            db, key=key(product=product), native_symbols=symbols,
            through=THROUGH, required=required, observed_at=NOW,
        )
    assert not db.queries


def test_unproven_pg_boundary_and_duplicate_fails_closed() -> None:
    db = FakeDB((THROUGH - STEP, THROUGH - STEP))
    with pytest.raises(
        ClosedGapRepairUnavailable, match="PG_PROVENANCE_INVALID",
    ):
        plan_missing_closed_pages(
            db, key=key(), native_symbols=FIVE,
            through=THROUGH, required=3, observed_at=NOW,
        )


def test_page_must_contain_exact_expected_confirmed_utc_bars() -> None:
    missing = THROUGH - STEP
    page = ClosedGapPage(missing, missing, 1)
    bars = fetch_verified_bybit_closed_page(
        key(), page, received_at=NOW,
        opener=lambda *_args, **_kwargs: RESTResponse(payload((missing,))),
    )
    assert len(bars) == 1
    assert bars[0].is_closed and bars[0].closed_at == missing
    with pytest.raises(
        ClosedGapRepairUnavailable, match="PAGE_BOUNDARY_INVALID",
    ):
        fetch_verified_bybit_closed_page(
            key(), page, received_at=missing - STEP,
            opener=Mock(side_effect=AssertionError("no early fetch")),
        )
