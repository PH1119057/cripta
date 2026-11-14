"""#470 R11 stage8 original optional provider frames -> same legacy MAYAK engine.

All fixtures are disposable pure frames, not claimed real transport continuity.
The old collector's math is called independently on original provider rows.
No DB, websocket, service, Exchange or trading actions are used here.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakReplayUnverified,
    project_r11_optional_mayak_page,
)
from bybit_workbench.mayak.optional_original_public_frame_reader import (
    R11OriginalFramePage,
)

SYMBOL = "APTUSDT"
SESSION = "a" * 32
TRANSPORT = "c" * 32
COMMIT = "b" * 40
AT = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
MSEC = int(AT.timestamp() * 1000)


def original(
    number: int, product: str, raw: dict, *,
    epoch: str = TRANSPORT,
) -> R11OriginalPublicFrame:
    facts = decode_bybit_public_frame(
        raw, product=product,
        allowed_symbols=frozenset((SYMBOL,)),
        received_at=AT + timedelta(seconds=2),
    )
    return make_r11_original_optional_frame(
        source_session_id=SESSION,
        transport_epoch_id=epoch,
        producer_release_commit=COMMIT,
        product=product,
        source_frame_number=number,
        message=raw, facts=facts,
        received_at=AT + timedelta(seconds=2),
    )


def frame(
    kind: str, *, ts: int = MSEC, product: str = "SPOT",
) -> dict:
    if kind == "trade":
        return {
            "topic": f"publicTrade.{SYMBOL}",
            "type": "snapshot", "ts": ts,
            "data": [
                {
                    "s": SYMBOL, "T": ts, "S": "Buy",
                    "p": "100.4", "v": "3", "seq": 220,
                    "i": "spot-original-a",
                },
                {
                    "s": SYMBOL, "T": ts + 1, "S": "Sell",
                    "p": "100.5", "v": "2", "seq": 220,
                    "i": "spot-original-b",
                },
            ],
        }
    if kind == "book":
        return {
            "topic": f"orderbook.50.{SYMBOL}",
            "type": "snapshot", "ts": ts,
            "data": {
                "s": SYMBOL, "seq": 90, "u": 12,
                "b": [["100", "2"], ["99.9", "3"]],
                "a": [["100.1", "5"], ["100.2", "1"]],
            },
        }
    if kind == "delta":
        return {
            "topic": f"orderbook.50.{SYMBOL}",
            "type": "delta", "ts": ts,
            "data": {
                "s": SYMBOL, "seq": 91, "u": 13,
                "b": [["100", "0"], ["99.95", "6"]],
                "a": [["100.2", "0"], ["100.15", "4"]],
            },
        }
    if kind == "ticker":
        return {
            "topic": f"tickers.{SYMBOL}", "type": "snapshot",
            "ts": ts, "data": {
                "symbol": SYMBOL, "openInterest": "20000",
                "openInterestValue": "2023450",
                "fundingRate": "-0.0004",
                "lastPrice": "100.2",
                "markPrice": "100.21", "indexPrice": "100.22",
            },
        }
    if kind == "ticker_delta":
        return {
            "topic": f"tickers.{SYMBOL}", "type": "delta",
            "ts": ts, "data": {
                "symbol": SYMBOL, "openInterest": "20200",
                "fundingRate": "-0.0005",
            },
        }
    if kind == "liq":
        return {
            "topic": f"allLiquidation.{SYMBOL}",
            "type": "snapshot", "ts": ts,
            "data": [
                {"s": SYMBOL, "T": ts, "S": "Sell",
                 "p": "100.2", "v": "12"},
                {"s": SYMBOL, "T": ts + 2, "S": "Buy",
                 "p": "100.3", "v": "4"},
            ],
        }
    raise AssertionError((kind, product))


def page(
    product: str,
    frames: tuple[R11OriginalPublicFrame, ...] = (),
    *,
    after: int = 0,
    digest: str | None = None,
    more: bool = False,
    transport: str = TRANSPORT,
) -> R11OriginalFramePage:
    # Same structural output as the stage7 read-only SQL reader. Stage8
    # independently re-runs codec, frame SHA and exact event facts.
    return R11OriginalFramePage(
        source_session_id=SESSION,
        transport_epoch_id=transport,
        product_category=product,
        source_state="ACTIVE",
        producer_release_commit=COMMIT,
        requested_after=after,
        next_cursor=frames[-1].source_frame_number if frames else after,
        next_digest=frames[-1].frame_content_sha256 if frames else digest,
        frames=frames,
        more_pages=more,
    )


def legacy_book(
    engine: LiveMayakEngine, market: str,
    event: dict, state: dict[str, dict[float, float]],
) -> None:
    raw = event["data"]
    if event["type"] == "snapshot":
        state["b"] = {}
        state["a"] = {}
    for side in ("b", "a"):
        for raw_price, raw_size in raw.get(side, []):
            price, size = float(raw_price), float(raw_size)
            if size:
                state[side][price] = size
            else:
                state[side].pop(price, None)
    engine.on_book(
        market, SYMBOL, event["ts"] / 1000,
        sorted(state["b"].items(), reverse=True)[:50],
        sorted(state["a"].items())[:50],
    )


def legacy_play(
    engine: LiveMayakEngine, product: str, original_frames: tuple,
) -> None:
    state = {"b": {}, "a": {}}
    market = product.lower()
    for wrapped in original_frames:
        raw = wrapped.provider_frame
        topic = raw["topic"]
        if topic.startswith("publicTrade."):
            for item in raw["data"]:
                engine.on_trade(
                    market, SYMBOL, item["T"] / 1000,
                    item["S"], float(item["p"]), float(item["v"]),
                )
        elif topic.startswith("orderbook."):
            legacy_book(engine, market, raw, state)
        elif topic.startswith("tickers."):
            mapping = {
                "openInterest": "open_interest",
                "openInterestValue": "open_interest_value",
                "fundingRate": "funding_rate",
                "lastPrice": "last_price", "markPrice": "mark_price",
                "indexPrice": "index_price",
            }
            updates = {
                legacy: float(raw["data"][key])
                for key, legacy in mapping.items()
                if raw["data"].get(key) not in (None, "")
            }
            engine.on_ticker(SYMBOL, raw["ts"] / 1000, **updates)
        elif topic.startswith("allLiquidation."):
            for item in raw["data"]:
                engine.on_liquidation(
                    SYMBOL, item["T"] / 1000,
                    item["S"], float(item["p"]), float(item["v"]),
                )


def test_spot_trade_twins_and_book_snapshot_delta_match_legacy_mayak() -> None:
    originals = (
        original(1, "SPOT", frame("trade")),
        original(2, "SPOT", frame("book")),
        original(4, "SPOT", frame("delta", ts=MSEC + 100)),
    )
    first = project_r11_optional_mayak_page(
        page("SPOT", originals[:2], more=True),
    )
    assert first.has_more_pages
    assert [event.event for event in first.events] == [
        "TRADE", "TRADE", "BOOK",
    ]
    assert first.events[0].source_sequence == 220
    assert first.events[1].source_sequence == 220
    assert [event.exec_id for event in first.events[:2]] == [
        "spot-original-a", "spot-original-b",
    ]
    second = project_r11_optional_mayak_page(
        page("SPOT", originals[2:], after=first.cursor.next_cursor),
        previous=first.cursor,
    )
    assert second.cursor.next_cursor == 4
    assert second.events[0].bids[0] == (99.95, 6)
    assert second.events[0].asks[0] == (100.1, 5)
    assert "100" not in str(second.events[0].bids)

    direct = LiveMayakEngine((SYMBOL,))
    projected = LiveMayakEngine((SYMBOL,))
    legacy_play(direct, "SPOT", originals)
    for event in first.events + second.events:
        event.apply_offline(projected)
    assert tuple(projected.trades[("spot", SYMBOL)].rows) == tuple(
        direct.trades[("spot", SYMBOL)].rows
    )
    assert projected.books[("spot", SYMBOL)] == direct.books[("spot", SYMBOL)]
    assert tuple(projected.book_history[("spot", SYMBOL)]) == tuple(
        direct.book_history[("spot", SYMBOL)]
    )
    assert second.provider_transport_continuity_verified is False
    assert second.seven_symbol_panel_verified is False
    assert second.optional_rest_feature_parity_verified is False
    assert second.safe_to_switch_mayak_public_socket is False


def test_linear_ticker_partial_signed_funding_book_and_liq_match_legacy() -> None:
    originals = (
        original(1, "LINEAR", frame("ticker", product="LINEAR")),
        original(2, "LINEAR", frame("liq", product="LINEAR")),
        original(3, "LINEAR", frame("book", product="LINEAR")),
        original(
            5, "LINEAR",
            frame("ticker_delta", ts=MSEC + 200, product="LINEAR"),
        ),
        original(
            6, "LINEAR",
            frame("delta", ts=MSEC + 300, product="LINEAR"),
        ),
    )
    first = project_r11_optional_mayak_page(
        page("LINEAR", originals[:3], more=True),
    )
    second = project_r11_optional_mayak_page(
        page("LINEAR", originals[3:], after=first.cursor.next_cursor),
        previous=first.cursor,
    )
    assert [e.event for e in first.events + second.events] == [
        "TICKER", "LIQUIDATION", "LIQUIDATION", "BOOK",
        "TICKER", "BOOK",
    ]
    assert first.events[0].ticker_updates[-1] == ("index_price", 100.22)
    assert ("funding_rate", -0.0004) in first.events[0].ticker_updates
    assert dict(second.events[0].ticker_updates) == {
        "open_interest": 20200,
        "funding_rate": -0.0005,
    }
    direct = LiveMayakEngine((SYMBOL,))
    projected = LiveMayakEngine((SYMBOL,))
    legacy_play(direct, "LINEAR", originals)
    for event in first.events + second.events:
        event.apply_offline(projected)
    assert projected.tickers == direct.tickers
    assert projected.ticker_history == direct.ticker_history
    assert projected.funding_history == direct.funding_history
    assert tuple(projected.liquidations.rows) == tuple(
        direct.liquidations.rows
    )
    assert projected.books[("linear", SYMBOL)] == direct.books[("linear", SYMBOL)]
    assert tuple(projected.book_history[("linear", SYMBOL)]) == tuple(
        direct.book_history[("linear", SYMBOL)]
    )
    assert second.safe_to_switch_mayak_public_socket is False


def test_delta_without_verified_same_epoch_snapshot_fails_closed() -> None:
    with pytest.raises(
        R11OptionalMayakReplayUnverified, match="DELTA_WITHOUT_SNAPSHOT",
    ):
        project_r11_optional_mayak_page(
            page("SPOT", (original(1, "SPOT", frame("delta")),)),
        )


@pytest.mark.parametrize(
    "seq,update,reason",
    [
        (90, 13, "BOOK_SEQ_OR_RESET"),
        (91, 12, "BOOK_SEQ_OR_RESET"),
        (91, 1, "BOOK_SEQ_OR_RESET"),
    ],
)
def test_non_monotonic_book_delta_and_provider_reset_require_new_snapshot(
    seq: int, update: int, reason: str,
) -> None:
    raw = frame("delta")
    raw["data"]["seq"] = seq
    raw["data"]["u"] = update
    originals = (
        original(1, "SPOT", frame("book")),
        original(2, "SPOT", raw),
    )
    with pytest.raises(R11OptionalMayakReplayUnverified, match=reason):
        project_r11_optional_mayak_page(page("SPOT", originals))


def test_transport_change_cannot_reuse_book_or_source_resume_checkpoint() -> None:
    snap = original(1, "SPOT", frame("book"))
    first = project_r11_optional_mayak_page(page("SPOT", (snap,)))
    changed = original(
        2, "SPOT", frame("delta"),
        epoch="d" * 32,
    )
    with pytest.raises(
        R11OptionalMayakReplayUnverified, match="CURSOR_OR_TRANSPORT_EPOCH",
    ):
        project_r11_optional_mayak_page(
            page(
                "SPOT", (changed,), after=1,
                transport="d" * 32,
            ),
            previous=first.cursor,
        )


def test_resume_cursor_must_have_same_digest_and_no_skipped_page() -> None:
    one = original(1, "SPOT", frame("book"))
    two = original(2, "SPOT", frame("delta"))
    first = project_r11_optional_mayak_page(page("SPOT", (one,)))
    bad = page("SPOT", (two,), after=2)
    with pytest.raises(
        R11OptionalMayakReplayUnverified, match="CURSOR_OR_TRANSPORT_EPOCH",
    ):
        project_r11_optional_mayak_page(bad, previous=first.cursor)
    with pytest.raises(
        R11OptionalMayakReplayUnverified, match="PAGE_CURSOR_OR_DIGEST",
    ):
        project_r11_optional_mayak_page(
            replace(
                page("SPOT", (two,), after=1),
                next_digest="f" * 64,
            ),
            previous=first.cursor,
        )


def test_hash_mismatch_rejected_even_when_original_fact_shape_looks_valid() -> None:
    one = original(1, "SPOT", frame("trade"))
    tampered = replace(one, frame_content_sha256="f" * 64)
    with pytest.raises(
        R11OptionalMayakReplayUnverified, match="FRAME_HASH_OR_FACT",
    ):
        project_r11_optional_mayak_page(page("SPOT", (tampered,)))


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda p: replace(p, mayak_cutover_ready=True),
         "PAGE_IDENTITY_OR_FALSE_READINESS"),
        (lambda p: replace(p, seven_symbol_parity_verified=True),
         "PAGE_IDENTITY_OR_FALSE_READINESS"),
        (lambda p: replace(p, provider_continuity_verified=True),
         "PAGE_IDENTITY_OR_FALSE_READINESS"),
        (lambda p: replace(p, market_publication_cursor_verified=True),
         "PAGE_IDENTITY_OR_FALSE_READINESS"),
        (lambda p: replace(p, product_category="OPTIONS"),
         "PAGE_IDENTITY_OR_FALSE_READINESS"),
        (lambda p: replace(p, source_state="UNVERIFIED"),
         "PAGE_IDENTITY_OR_FALSE_READINESS"),
        (lambda p: replace(p, requested_after=1),
         "UNTRUSTED_INITIAL_CURSOR"),
    ],
)
def test_false_page_readiness_and_out_of_scope_never_pass(
    change, reason: str,
) -> None:
    base = page("SPOT", (original(1, "SPOT", frame("book")),))
    with pytest.raises(R11OptionalMayakReplayUnverified, match=reason):
        project_r11_optional_mayak_page(change(base))


def test_silent_initial_page_no_proof_and_following_real_events_replay() -> None:
    empty = project_r11_optional_mayak_page(page("SPOT"))
    assert empty.events == ()
    assert empty.cursor.next_cursor == 0
    assert empty.cursor.next_digest is None
    assert not empty.provider_transport_continuity_verified
    next_page = project_r11_optional_mayak_page(
        page("SPOT", (original(2, "SPOT", frame("trade")),)),
        previous=empty.cursor,
    )
    assert len(next_page.events) == 2
    assert next_page.cursor.next_cursor == 2


def test_source_only_no_mayak_cutover_or_trading_sockets() -> None:
    path = Path(
        "src/bybit_workbench/mayak/optional_original_event_parity.py"
    )
    source = path.read_text()
    assert "psycopg" not in source
    assert "urllib" not in source
    assert "websocket.create_connection" not in source
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "source_session_id" in source
    assert "safe_to_switch_mayak_public_socket: bool = False" in source
    assert "seven_symbol_panel_verified: bool = False" in source
    assert "provider_transport_continuity_verified: bool = False" in source
