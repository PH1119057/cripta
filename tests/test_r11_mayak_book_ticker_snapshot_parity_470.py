"""#470 R11 stage4: real LiveMayakEngine ticker/book math parity, not WS cutover."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.publication_snapshot_parity import (
    R11SnapshotParityUnverified,
    check_r11_book_snapshot_math,
    project_r11_ticker_sample,
)
from bybit_workbench.mayak.source_publication_parity import (
    R11MayakFeatureCandidate,
)

SYMBOL = "APTUSDT"
AT = datetime(2026, 11, 14, 7, 13, tzinfo=UTC)
SPOT_BIDS = (
    (Decimal("100"), Decimal("2")),
    (Decimal("99.99"), Decimal("3")),
    (Decimal("99.90"), Decimal("4")),
    (Decimal("99.50"), Decimal("10")),
)
SPOT_ASKS = (
    (Decimal("100.10"), Decimal("1")),
    (Decimal("100.11"), Decimal("2")),
    (Decimal("100.20"), Decimal("5")),
    (Decimal("100.60"), Decimal("1")),
)
LINEAR_BIDS = (
    (Decimal("50"), Decimal("5")),
    (Decimal("49.99"), Decimal("6")),
    (Decimal("49.96"), Decimal("7")),
    (Decimal("49.75"), Decimal("8")),
)
LINEAR_ASKS = (
    (Decimal("50.05"), Decimal("2")),
    (Decimal("50.06"), Decimal("3")),
    (Decimal("50.10"), Decimal("9")),
    (Decimal("50.30"), Decimal("12")),
)


def compact_book(
    bids: tuple[tuple[Decimal, Decimal], ...],
    asks: tuple[tuple[Decimal, Decimal], ...],
) -> dict[str, Decimal]:
    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    mid = (best_bid + best_ask) / Decimal(2)
    result = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_size": next(q for p, q in bids if p == best_bid),
        "ask_size": next(q for p, q in asks if p == best_ask),
    }
    for bps in (5, 10, 25, 50):
        ratio = Decimal(bps) / Decimal(10000)
        result[f"bid_{bps}bps_usd"] = sum(
            (p * q for p, q in bids if p >= mid * (1 - ratio)),
            Decimal(0),
        )
        result[f"ask_{bps}bps_usd"] = sum(
            (p * q for p, q in asks if p <= mid * (1 + ratio)),
            Decimal(0),
        )
    return result


def candidate() -> R11MayakFeatureCandidate:
    return R11MayakFeatureCandidate(
        symbol=SYMBOL,
        publication_id=77,
        source_session_id="a" * 32,
        producer_release_commit="b" * 40,
        as_of_second=AT,
        spot_book_bands=compact_book(SPOT_BIDS, SPOT_ASKS),
        linear_book_bands=compact_book(LINEAR_BIDS, LINEAR_ASKS),
        linear_ticker={
            "lastPrice": Decimal("50.01"),
            "markPrice": Decimal("50.02"),
            "indexPrice": Decimal("49.98"),
            "openInterest": Decimal("987654"),
            "openInterestValue": Decimal("49388888"),
            "fundingRate": Decimal("-0.00018"),
        },
    )


def test_publication_ticker_uses_exact_legacy_mayak_names_and_funding_sign() -> None:
    source = candidate()
    sample = project_r11_ticker_sample(source)
    mapped = sample.as_legacy_kwargs()
    assert mapped == {
        "open_interest": 987654.0,
        "open_interest_value": 49388888.0,
        "funding_rate": -0.00018,
        "last_price": 50.01,
        "mark_price": 50.02,
        "index_price": 49.98,
    }
    assert sample.symbol == SYMBOL
    assert sample.publication_id == 77
    assert sample.source_session_id == "a" * 32
    assert sample.sampled_as_of == AT
    assert sample.ticker_original_event_timing_verified is False
    assert sample.multi_horizon_history_verified is False
    assert sample.seven_symbol_mayak_ready is False

    # Same *sample* at same time has exact old on_ticker semantics.
    from_original_ws = LiveMayakEngine((SYMBOL,))
    from_pg_sample = LiveMayakEngine((SYMBOL,))
    from_original_ws.on_ticker(
        SYMBOL, AT.timestamp(), **{
            "open_interest": float(source.linear_ticker["openInterest"]),
            "open_interest_value": float(
                source.linear_ticker["openInterestValue"]
            ),
            "funding_rate": float(source.linear_ticker["fundingRate"]),
            "last_price": float(source.linear_ticker["lastPrice"]),
            "mark_price": float(source.linear_ticker["markPrice"]),
            "index_price": float(source.linear_ticker["indexPrice"]),
        },
    )
    from_pg_sample.on_ticker(SYMBOL, AT.timestamp(), **mapped)
    assert from_pg_sample.tickers == from_original_ws.tickers
    assert from_pg_sample.ticker_history == from_original_ws.ticker_history
    assert from_pg_sample.funding_history == from_original_ws.funding_history
    assert from_pg_sample.tickers[SYMBOL]["open_interest_change_5m_pct"] is None


@pytest.mark.parametrize(
    "market,bids,asks",
    [
        ("spot", SPOT_BIDS, SPOT_ASKS),
        ("linear", LINEAR_BIDS, LINEAR_ASKS),
    ],
)
def test_full_original_level_fixture_matches_actual_mayak_book_math(
    market, bids, asks,
) -> None:
    verified = check_r11_book_snapshot_math(
        candidate(), market=market, bids=bids, asks=asks,
    )
    assert verified.snapshot_formula_match is True
    assert verified.market == market
    assert len(verified.compared_keys) == 12
    assert "bid_5bps_usd" in verified.compared_keys
    assert "ask_50bps_usd" in verified.compared_keys
    assert verified.original_book_frame_provenance_verified is False
    assert verified.book_delta_sequence_verified is False
    assert verified.seven_symbol_mayak_ready is False
    assert verified.mayak_socket_retirement_authorized is False


@pytest.mark.parametrize(
    "field",
    [
        "best_bid", "best_ask", "bid_size", "ask_size",
        "bid_5bps_usd", "ask_5bps_usd",
        "bid_10bps_usd", "ask_10bps_usd",
        "bid_25bps_usd", "ask_25bps_usd",
        "bid_50bps_usd", "ask_50bps_usd",
    ],
)
def test_any_spot_book_compact_math_drift_is_rejected(field: str) -> None:
    source = candidate()
    tampered = dict(source.spot_book_bands)
    tampered[field] += Decimal("1")
    with pytest.raises(R11SnapshotParityUnverified, match="BOOK_MATH_DRIFT"):
        check_r11_book_snapshot_math(
            replace(source, spot_book_bands=tampered),
            market="spot", bids=SPOT_BIDS, asks=SPOT_ASKS,
        )


def test_stale_book_fixture_cannot_be_synthesized_from_same_top_of_book() -> None:
    # The top price/size is unchanged. Missing internal depth is detectable.
    altered = (
        SPOT_BIDS[0], SPOT_BIDS[1],
        (SPOT_BIDS[2][0], Decimal("100")),
        SPOT_BIDS[3],
    )
    with pytest.raises(R11SnapshotParityUnverified, match="BOOK_MATH_DRIFT"):
        check_r11_book_snapshot_math(
            candidate(), market="spot", bids=altered, asks=SPOT_ASKS,
        )


@pytest.mark.parametrize(
    "altered,expected",
    [
        (
            ((Decimal("100"), Decimal("2")),
             (Decimal("100"), Decimal("3"))),
            "LEVEL_VALUE_OR_DUPLICATE",
        ),
        (((Decimal("100"), Decimal("0")),), "LEVEL_VALUE_OR_DUPLICATE"),
        (((Decimal("100"), Decimal("-3")),), "LEVEL_VALUE_OR_DUPLICATE"),
        (((Decimal("NaN"), Decimal("2")),), "LEVEL_VALUE_OR_DUPLICATE"),
        (((Decimal("Infinity"), Decimal("2")),), "LEVEL_VALUE_OR_DUPLICATE"),
        (((Decimal("1e9999"), Decimal("2")),),
         "LEVEL_NONFINITE"),
        (
            tuple(
                (Decimal("10") + Decimal(n) / 100, Decimal("1"))
                for n in range(51)
            ),
            "LADDER_BOUNDS",
        ),
    ],
)
def test_invalid_or_unbounded_original_book_fixtures_block_parity(
    altered, expected: str,
) -> None:
    with pytest.raises(R11SnapshotParityUnverified, match=expected):
        check_r11_book_snapshot_math(
            candidate(), market="spot", bids=altered, asks=SPOT_ASKS,
        )


def test_crossed_ladder_and_wrong_market_block_parity() -> None:
    with pytest.raises(R11SnapshotParityUnverified, match="LADDER_CROSSED"):
        check_r11_book_snapshot_math(
            candidate(), market="spot",
            bids=((Decimal("101"), Decimal("2")),),
            asks=((Decimal("100"), Decimal("2")),),
        )
    with pytest.raises(R11SnapshotParityUnverified, match="PRODUCT_SCOPE"):
        check_r11_book_snapshot_math(
            candidate(), market="options",
            bids=SPOT_BIDS, asks=SPOT_ASKS,
        )


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            lambda source: replace(source, symbol="BTCUSDT"),
            "SOURCE_IDENTITY_OR_UTC",
        ),
        (
            lambda source: replace(source, symbol="ETHUSDT"),
            "SOURCE_IDENTITY_OR_UTC",
        ),
        (
            lambda source: replace(
                source, source_session_id="source-that-is-not-an-epoch",
            ),
            "SOURCE_IDENTITY_OR_UTC",
        ),
        (
            lambda source: replace(source, producer_release_commit="no-commit"),
            "SOURCE_IDENTITY_OR_UTC",
        ),
        (
            lambda source: replace(
                source, as_of_second=AT.replace(tzinfo=None),
            ),
            "SOURCE_IDENTITY_OR_UTC",
        ),
        (
            lambda source: replace(
                source, as_of_second=AT + timedelta(microseconds=1),
            ),
            "SOURCE_IDENTITY_OR_UTC",
        ),
        (
            lambda source: replace(
                source, seven_symbol_panel_verified=True,
            ),
            "UNVERIFIED_SCOPE_MARKED_READY",
        ),
        (
            lambda source: replace(
                source, book_delta_replay_verified=True,
            ),
            "UNVERIFIED_SCOPE_MARKED_READY",
        ),
        (
            lambda source: replace(
                source, liquidation_event_replay_verified=True,
            ),
            "UNVERIFIED_SCOPE_MARKED_READY",
        ),
    ],
)
def test_false_source_identity_or_upgraded_readiness_never_passes(
    change, reason: str,
) -> None:
    with pytest.raises(R11SnapshotParityUnverified, match=reason):
        project_r11_ticker_sample(change(candidate()))


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("lastPrice", Decimal("0"), "TICKER_SIGN"),
        ("indexPrice", Decimal("-2"), "TICKER_SIGN"),
        ("openInterest", Decimal("-0.1"), "TICKER_SIGN"),
        ("fundingRate", Decimal("Infinity"), "TICKER_NONFINITE"),
        ("markPrice", Decimal("NaN"), "TICKER_NONFINITE"),
        ("lastPrice", Decimal("1e99999"), "TICKER_NONFINITE"),
    ],
)
def test_malformed_ticker_compact_source_does_not_become_sample(
    field: str, value: Decimal, reason: str,
) -> None:
    original = candidate()
    ticker = dict(original.linear_ticker)
    ticker[field] = value
    with pytest.raises(R11SnapshotParityUnverified, match=reason):
        project_r11_ticker_sample(
            replace(original, linear_ticker=ticker),
        )


def test_stage4_does_not_add_network_db_or_fake_full_book_coverage() -> None:
    source = Path(
        "src/bybit_workbench/mayak/publication_snapshot_parity.py"
    ).read_text()
    assert "psycopg" not in source
    assert "urllib" not in source
    assert "requests." not in source
    assert "websocket.create_connection" not in source
    assert "UPDATE " not in source
    assert "INSERT INTO" not in source
    assert "original_book_frame_provenance_verified: bool = False" in source
    assert "book_delta_sequence_verified: bool = False" in source
    assert "mayak_socket_retirement_authorized: bool = False" in source
