"""#470 R11 source original optional events versus legacy MAYAK same-tape math.

Only disposable LiveMayakEngine in-memory instances. Original five-symbol
frames are built through the existing canonical Bybit codec, never from
one-second aggregates. No production writes, WS, Exchange or LIVE.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.r11_optional_original_frame_journal import (
    R11OriginalPublicFrame,
    make_r11_original_optional_frame,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.optional_original_offline_replay import (
    R11DisposableMayakOriginalReplayer,
    R11MayakOfflineParityUnverified,
)
from bybit_workbench.mayak.optional_original_public_frame_reader import (
    R11OriginalFramePage,
)

SYMBOL = "APTUSDT"
SESSION = "a" * 32
EPOCH = "b" * 32
SHA = "c" * 40
AT = datetime(2026, 11, 14, 8, 0, 0, tzinfo=UTC)
RECEIVED = AT + timedelta(seconds=1)
MS = int(AT.timestamp() * 1000)


def raw_frame(
    kind: str, *,
    seq: int = 100,
    update: int = 40,
    delta: bool = False,
) -> dict[str, object]:
    if kind == "TRADE":
        return {
            "topic": f"publicTrade.{SYMBOL}", "type": "snapshot",
            "ts": MS,
            "data": [
                {
                    "s": SYMBOL, "T": MS, "S": "Buy",
                    "p": "100.25", "v": "2.5", "seq": 42,
                    "i": "trade-same-seq-a",
                },
                {
                    "s": SYMBOL, "T": MS + 1, "S": "Sell",
                    "p": "100.55", "v": "1.5", "seq": 42,
                    "i": "trade-same-seq-b",
                },
            ],
        }
    if kind == "BOOK":
        return {
            "topic": f"orderbook.50.{SYMBOL}",
            "type": "delta" if delta else "snapshot",
            "ts": MS + (100 if delta else 0),
            "data": {
                "s": SYMBOL, "seq": seq, "u": update,
                "b": (
                    [["100", "0"], ["99.5", "5"]]
                    if delta else [["100", "1"], ["99.5", "2"]]
                ),
                "a": (
                    [["101", "0"], ["101.25", "3"]]
                    if delta else [["101", "4"], ["101.25", "1"]]
                ),
            },
        }
    if kind == "TICKER":
        return {
            "topic": f"tickers.{SYMBOL}",
            "type": "delta", "ts": MS,
            "data": {
                "symbol": SYMBOL, "lastPrice": "100.30",
                "openInterest": "120000",
                "fundingRate": "-0.00008",
            },
        }
    if kind == "TICKER_PARTIAL":
        return {
            "topic": f"tickers.{SYMBOL}",
            "type": "delta", "ts": MS + 100,
            "data": {"symbol": SYMBOL, "markPrice": "100.35"},
        }
    if kind == "LIQUIDATION":
        return {
            "topic": f"allLiquidation.{SYMBOL}",
            "type": "snapshot", "ts": MS,
            "data": [
                {
                    "s": SYMBOL, "T": MS, "S": "Sell",
                    "p": "99.7", "v": "10",
                },
                {
                    "s": SYMBOL, "T": MS + 1, "S": "Buy",
                    "p": "100.1", "v": "4",
                },
            ],
        }
    raise AssertionError(kind)


def original(
    kind: str, *, product: str, number: int,
    seq: int = 100, update: int = 40, delta: bool = False,
) -> R11OriginalPublicFrame:
    raw = raw_frame(kind, seq=seq, update=update, delta=delta)
    facts = decode_bybit_public_frame(
        raw, product=product, allowed_symbols=frozenset((SYMBOL,)),
        received_at=RECEIVED,
    )
    return make_r11_original_optional_frame(
        source_session_id=SESSION, transport_epoch_id=EPOCH,
        producer_release_commit=SHA, product=product,
        source_frame_number=number,
        message=raw, facts=facts, received_at=RECEIVED,
    )


def page(
    product: str, frames: tuple[R11OriginalPublicFrame, ...],
    *, after: int = 0, digest: str | None = None,
    more: bool = False,
) -> R11OriginalFramePage:
    cursor = frames[-1].source_frame_number if frames else after
    return R11OriginalFramePage(
        source_session_id=SESSION, transport_epoch_id=EPOCH,
        product_category=product, source_state="ACTIVE",
        producer_release_commit=SHA, requested_after=after,
        next_cursor=cursor,
        next_digest=frames[-1].frame_content_sha256 if frames else digest,
        frames=frames, more_pages=more,
    )


def test_spot_trade_and_original_book_snapshot_delta_legacy_feature_parity() -> None:
    trade = original("TRADE", product="SPOT", number=1)
    snapshot = original("BOOK", product="SPOT", number=3)
    delta = original(
        "BOOK", product="SPOT", number=5, seq=101,
        update=41, delta=True,
    )
    from_original = LiveMayakEngine((SYMBOL,))
    from_replay = LiveMayakEngine((SYMBOL,))
    from_original.on_trade(
        "spot", SYMBOL, AT.timestamp(), "Buy", 100.25, 2.5,
    )
    from_original.on_trade(
        "spot", SYMBOL, (AT + timedelta(milliseconds=1)).timestamp(),
        "Sell", 100.55, 1.5,
    )
    from_original.on_book(
        "spot", SYMBOL, AT.timestamp(),
        [(100.0, 1.0), (99.5, 2.0)],
        [(101.0, 4.0), (101.25, 1.0)],
    )
    from_original.on_book(
        "spot", SYMBOL, (AT + timedelta(milliseconds=100)).timestamp(),
        [(99.5, 5.0)],
        [(101.25, 3.0)],
    )
    runner = R11DisposableMayakOriginalReplayer(
        from_replay, product="SPOT",
    )
    one = runner.accept(page("SPOT", (trade, snapshot), more=True))
    two = runner.accept(page(
        "SPOT", (delta,), after=one.next_cursor,
        digest=one.next_digest,
    ))
    assert one.applied_trade_count == 2
    assert one.applied_book_count == 1
    assert two.applied_book_count == 1
    assert two.next_cursor == 5
    assert two.publication_native_event_watermark_verified is False
    assert two.provider_transport_continuity_verified is False
    assert two.seven_symbol_feature_parity_verified is False
    assert two.mayak_live_consumer_cutover_authorized is False
    assert from_replay.books == from_original.books
    assert from_replay.book_history == from_original.book_history
    assert from_replay.stamps[("spot", SYMBOL, "book")] == (
        from_original.stamps[("spot", SYMBOL, "book")]
    )
    assert tuple(from_replay.trades[("spot", SYMBOL)].rows) == (
        tuple(from_original.trades[("spot", SYMBOL)].rows)
    )
    for seconds in (2, 60, 300, 900):
        assert from_replay.trades[("spot", SYMBOL)].metrics(
            AT.timestamp() + 1, seconds,
        ) == from_original.trades[("spot", SYMBOL)].metrics(
            AT.timestamp() + 1, seconds,
        )


def test_linear_liquidation_and_signed_partial_ticker_preserve_old_core() -> None:
    tick = original("TICKER", product="LINEAR", number=1)
    liq = original("LIQUIDATION", product="LINEAR", number=2)
    partial = original("TICKER_PARTIAL", product="LINEAR", number=4)
    original_engine = LiveMayakEngine((SYMBOL,))
    replay_engine = LiveMayakEngine((SYMBOL,))
    original_engine.on_ticker(
        SYMBOL, AT.timestamp(), last_price=100.30,
        open_interest=120000, funding_rate=-0.00008,
    )
    original_engine.on_liquidation(
        SYMBOL, AT.timestamp(), "Sell", 99.7, 10,
    )
    original_engine.on_liquidation(
        SYMBOL, (AT + timedelta(milliseconds=1)).timestamp(),
        "Buy", 100.1, 4,
    )
    original_engine.on_ticker(
        SYMBOL, (AT + timedelta(milliseconds=100)).timestamp(),
        mark_price=100.35,
    )
    runner = R11DisposableMayakOriginalReplayer(
        replay_engine, product="LINEAR",
    )
    output = runner.accept(page("LINEAR", (tick, liq, partial)))
    assert output.applied_ticker_count == 2
    assert output.applied_liquidation_count == 2
    assert output.applied_trade_count == 0
    assert replay_engine.tickers == original_engine.tickers
    assert replay_engine.ticker_history == original_engine.ticker_history
    assert replay_engine.funding_history == original_engine.funding_history
    assert replay_engine.liquidations.metrics(
        AT.timestamp() + 1, 60,
    ) == original_engine.liquidations.metrics(
        AT.timestamp() + 1, 60,
    )


def test_empty_page_changes_no_event_history_but_never_asserts_ready() -> None:
    engine = LiveMayakEngine((SYMBOL,))
    runner = R11DisposableMayakOriginalReplayer(
        engine, product="SPOT",
    )
    output = runner.accept(page("SPOT", ()))
    assert output.applied_trade_count == 0
    assert output.applied_liquidation_count == 0
    assert not engine.trades[("spot", SYMBOL)].rows
    assert output.mayak_live_consumer_cutover_authorized is False


def test_bad_book_delta_requires_real_snapshot_with_no_engine_mutation() -> None:
    engine = LiveMayakEngine((SYMBOL,))
    runner = R11DisposableMayakOriginalReplayer(
        engine, product="SPOT",
    )
    delta = original(
        "BOOK", product="SPOT", number=1,
        seq=101, update=41, delta=True,
    )
    with pytest.raises(
        R11MayakOfflineParityUnverified,
        match="BOOK_DELTA_UNVERIFIED",
    ):
        runner.accept(page("SPOT", (delta,)))
    assert engine.books == {}
    assert engine.book_history == {}
    # A genuine initial snapshot can still initialize the same runner.
    good = original("BOOK", product="SPOT", number=1)
    output = runner.accept(page("SPOT", (good,)))
    assert output.applied_book_count == 1


def test_same_spot_exec_id_duplicate_blocks_whole_new_page() -> None:
    engine = LiveMayakEngine((SYMBOL,))
    runner = R11DisposableMayakOriginalReplayer(
        engine, product="SPOT",
    )
    first = original("TRADE", product="SPOT", number=1)
    runner.accept(page("SPOT", (first,)))
    before_rows = tuple(engine.trades[("spot", SYMBOL)].rows)
    second = original("TRADE", product="SPOT", number=2)
    with pytest.raises(
        R11MayakOfflineParityUnverified,
        match="ORIGINAL_TRADE_DUPLICATE",
    ):
        runner.accept(page(
            "SPOT", (second,), after=1,
            digest=first.frame_content_sha256,
        ))
    assert tuple(engine.trades[("spot", SYMBOL)].rows) == before_rows


@pytest.mark.parametrize(
    "alter,reason",
    [
        (
            lambda frame: replace(frame, frame_content_sha256="0" * 64),
            "FRAME_OR_HASH_UNVERIFIED",
        ),
        (
            lambda frame: replace(frame, external_symbol="BTCUSDT"),
            "FRAME_SCOPE_OR_SOURCE_EPOCH",
        ),
        (
            lambda frame: replace(frame, transport_epoch_id="d" * 32),
            "FRAME_SCOPE_OR_SOURCE_EPOCH",
        ),
    ],
)
def test_modified_original_frame_never_enters_even_disposable_engine(
    alter, reason: str,
) -> None:
    engine = LiveMayakEngine((SYMBOL,))
    runner = R11DisposableMayakOriginalReplayer(
        engine, product="SPOT",
    )
    original_trade = original("TRADE", product="SPOT", number=1)
    with pytest.raises(R11MayakOfflineParityUnverified, match=reason):
        runner.accept(page("SPOT", (alter(original_trade),)))
    assert tuple(engine.trades[("spot", SYMBOL)].rows) == ()


def test_cross_epoch_or_replayed_page_cannot_continue_previous_mayak_engine() -> None:
    engine = LiveMayakEngine((SYMBOL,))
    runner = R11DisposableMayakOriginalReplayer(engine, product="SPOT")
    first = original("TRADE", product="SPOT", number=1)
    runner.accept(page("SPOT", (first,)))
    with pytest.raises(
        R11MayakOfflineParityUnverified, match="PAGE_CURSOR_OR_EPOCH",
    ):
        runner.accept(page("SPOT", (first,)))
    other_epoch = replace(
        page("SPOT", (), after=1, digest=first.frame_content_sha256),
        transport_epoch_id="d" * 32,
    )
    with pytest.raises(
        R11MayakOfflineParityUnverified, match="PAGE_CURSOR_OR_EPOCH",
    ):
        runner.accept(other_epoch)


def test_source_only_no_network_db_trading_and_no_claim_of_full_mayak() -> None:
    source = Path(
        "src/bybit_workbench/mayak/optional_original_offline_replay.py"
    ).read_text()
    for item in (
        "psycopg.connect", "urllib", "websocket.create_connection",
        "INSERT INTO", "DELETE FROM", "UPDATE ",
        "place_order", "TRADING_PERMISSION",
    ):
        assert item not in source
    assert "publication_native_event_watermark_verified: bool = False" in source
    assert "provider_transport_continuity_verified: bool = False" in source
    assert "seven_symbol_feature_parity_verified: bool = False" in source
    assert "mayak_live_consumer_cutover_authorized: bool = False" in source
