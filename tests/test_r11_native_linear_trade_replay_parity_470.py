"""#470 R11 source-only replay: native original trades vs legacy MAYAK event path.

No production database, exchange, network, service or trading rights. These
tests show parity for individual LINEAR trades only, NOT full seven-coin MAYAK.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.native_trade_publication_reader import (
    R11OriginalNativePage,
    R11OriginalTrade,
)
from bybit_workbench.mayak.native_trade_replay_parity import (
    R11TradeReplayUnverified,
    project_r11_native_linear_trade_page,
)

SYMBOL = "APTUSDT"
SESSION = "a" * 32
COMMIT = "b" * 40
AS_OF = datetime(2026, 11, 14, 7, 15, 0, tzinfo=UTC)


def native(
    frame: int, idx: int, seq: int, exec_id: str,
    *, side: str = "Buy",
    price: Decimal = Decimal("100.25"),
    quantity: Decimal = Decimal("0.75"),
    traded_at: datetime | None = None,
) -> R11OriginalTrade:
    at = traded_at or AS_OF - timedelta(seconds=3 - idx)
    return R11OriginalTrade(
        symbol=SYMBOL, source_session_id=SESSION,
        frame_number=frame, frame_index=idx,
        seq=seq, exec_id=exec_id,
        traded_at=at, accepted_at=at + timedelta(milliseconds=90),
        side=side, price=price, quantity=quantity,
    )


def native_page(
    trades: tuple[R11OriginalTrade, ...],
    *, after: tuple[int, int] = (0, 0),
    latest: tuple[int, int] = (5, 1),
    publication_id: int = 101,
    more: bool = False,
) -> R11OriginalNativePage:
    cursor = (
        (trades[-1].frame_number, trades[-1].frame_index)
        if trades else after
    )
    return R11OriginalNativePage(
        symbol=SYMBOL,
        publication_id=publication_id,
        source_session_id=SESSION,
        producer_release_commit=COMMIT,
        publication_as_of=AS_OF,
        publication_cursor=latest, read_after=after,
        next_cursor=cursor, original_events=trades,
        more_pages=more,
        last_event_at_publication_cursor=bool(trades) and cursor == latest,
    )


def test_two_same_seq_siblings_across_pages_preserve_real_mayak_flow() -> None:
    first = native(5, 0, 1001, "native-sibling-a")
    second = native(
        5, 1, 1001, "native-sibling-b", side="Sell",
        price=Decimal("101.125"), quantity=Decimal("2.125"),
    )
    one = project_r11_native_linear_trade_page(
        native_page((first,), more=True),
    )
    two = project_r11_native_linear_trade_page(
        native_page((second,), after=(5, 0)),
        previous=one.cursor,
    )
    assert [x.exec_id for x in one.events + two.events] == [
        "native-sibling-a", "native-sibling-b",
    ]
    assert [x.seq for x in one.events + two.events] == [1001, 1001]
    assert one.has_more_pages is True
    assert two.has_more_pages is False
    assert two.cursor.cursor == (5, 1)
    assert two.cursor.recent_exec_ids == (
        "native-sibling-a", "native-sibling-b",
    )
    assert one.safe_to_switch_mayak_public_socket is False
    assert two.seven_symbol_parity_verified is False
    assert two.source_continuity_verified is False

    legacy = LiveMayakEngine((SYMBOL,))
    source = LiveMayakEngine((SYMBOL,))
    for item in (first, second):
        # Exactly the preexisting mayak_v2.py publicTrade handler semantics.
        legacy.on_trade(
            "linear", item.symbol, item.traded_at.timestamp(), item.side,
            float(item.price), float(item.quantity),
        )
    for item in one.events + two.events:
        source.on_trade(*item.on_trade_args())
    old_rows = tuple(legacy.trades[("linear", SYMBOL)].frozen_view().rows)
    new_rows = tuple(source.trades[("linear", SYMBOL)].frozen_view().rows)
    assert new_rows == old_rows
    assert new_rows[0][1] == float(first.price) * float(first.quantity)
    assert new_rows[1][1] == -float(second.price) * float(second.quantity)
    at = AS_OF.timestamp()
    for seconds in (5, 60, 300, 900):
        assert source.trades[("linear", SYMBOL)].metrics(
            at, seconds,
        ) == legacy.trades[("linear", SYMBOL)].metrics(at, seconds)


def test_silent_publication_is_noop_but_not_source_continuity() -> None:
    trade = native(5, 1, 1001, "first")
    full = project_r11_native_linear_trade_page(
        native_page((trade,)),
    )
    newer = replace(
        native_page((), after=(5, 1), latest=(5, 1)),
        publication_id=102,
        publication_as_of=AS_OF + timedelta(seconds=1),
    )
    state = project_r11_native_linear_trade_page(
        newer, previous=full.cursor,
    )
    assert state.events == ()
    assert state.cursor.cursor == (5, 1)
    assert state.cursor.publication_id == 102
    assert state.source_continuity_verified is False
    assert state.safe_to_switch_mayak_public_socket is False


@pytest.mark.parametrize(
    "change,why",
    [
        (
            lambda page: replace(page, read_after=(5, 0)),
            "UNVERIFIED_INITIAL_CURSOR",
        ),
        (
            lambda page: replace(page, original_events=()),
            "MISSING_OR_FABRICATED_SILENT_EVENTS",
        ),
        (
            lambda page: replace(page, more_pages=True),
            "FALSE_MORE_PAGES",
        ),
        (
            lambda page: replace(page, last_event_at_publication_cursor=False),
            "PAGE_TAIL_NOT_VERIFIED",
        ),
        (
            lambda page: replace(
                page, original_events=(
                    replace(page.original_events[0], exec_id=""),
                ),
            ),
            "ORIGINAL_TRADE_IDENTITY_OR_ORDER_INVALID",
        ),
        (
            lambda page: replace(
                page, original_events=(
                    replace(page.original_events[0], side="Hold"),
                ),
            ),
            "ORIGINAL_TRADE_IDENTITY_OR_ORDER_INVALID",
        ),
        (
            lambda page: replace(
                page, original_events=(
                    replace(page.original_events[0], price=Decimal("Infinity")),
                ),
            ),
            "FLOAT_INVALID",
        ),
        (
            lambda page: replace(
                page, original_events=(
                    replace(page.original_events[0], quantity=Decimal("1e9999")),
                ),
            ),
            "FLOAT_INVALID",
        ),
        (
            lambda page: replace(
                page, original_events=(
                    replace(
                        page.original_events[0],
                        traded_at=AS_OF + timedelta(seconds=2),
                    ),
                ),
            ),
            "ORIGINAL_TRADE_IDENTITY_OR_ORDER_INVALID",
        ),
    ],
)
def test_unverified_mayak_replay_data_cannot_form_legitimate_batch(
    change, why: str,
) -> None:
    page = native_page((native(5, 1, 1001, "original"),))
    with pytest.raises(R11TradeReplayUnverified, match=why):
        project_r11_native_linear_trade_page(change(page))


@pytest.mark.parametrize(
    "change,why",
    [
        (
            lambda page: replace(page, source_session_id="c" * 32),
            "EPOCH_OR_CURSOR_CHAIN_BREAK",
        ),
        (
            lambda page: replace(page, producer_release_commit="d" * 40),
            "EPOCH_OR_CURSOR_CHAIN_BREAK",
        ),
        (
            lambda page: replace(page, read_after=(0, 0)),
            "EPOCH_OR_CURSOR_CHAIN_BREAK",
        ),
        (
            lambda page: replace(page, publication_id=100),
            "EPOCH_OR_CURSOR_CHAIN_BREAK",
        ),
        (
            lambda page: replace(page, publication_cursor=(4, 99)),
            "PAGE_SHAPE_OR_SCOPE_INVALID",
        ),
        (
            lambda page: replace(page, original_events=(
                replace(page.original_events[0], seq=900),
            )),
            "ORIGINAL_TRADE_IDENTITY_OR_ORDER_INVALID",
        ),
        (
            lambda page: replace(page, original_events=(
                replace(page.original_events[0], exec_id="first"),
            )),
            "ORIGINAL_TRADE_IDENTITY_OR_ORDER_INVALID",
        ),
    ],
)
def test_second_page_cannot_restart_duplicate_or_skip_cursor(
    change, why: str,
) -> None:
    first = native(5, 0, 1001, "first")
    previous = project_r11_native_linear_trade_page(
        native_page((first,), more=True),
    ).cursor
    second = native(5, 1, 1002, "second")
    page = native_page((second,), after=(5, 0))
    with pytest.raises(R11TradeReplayUnverified, match=why):
        project_r11_native_linear_trade_page(change(page), previous=previous)


def test_no_io_or_control_effect_inside_native_parity_projection() -> None:
    source = Path(
        "src/bybit_workbench/mayak/native_trade_replay_parity.py"
    ).read_text(encoding="utf-8")
    assert "psycopg" not in source
    assert "urllib" not in source
    assert "websocket.create_connection" not in source
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "source_continuity_verified: bool = False" in source
    assert "seven_symbol_parity_verified: bool = False" in source
    assert "safe_to_switch_mayak_public_socket: bool = False" in source


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
def test_owner_approved_mayak_reference_original_trade_parity(
    symbol: str,
) -> None:
    """BTC/ETH retain native seq/execId without entering trading decisions."""
    event = replace(native(5, 1, 1001, f"mayak-{symbol}"), symbol=symbol)
    page = replace(native_page((event,)), symbol=symbol)
    batch = project_r11_native_linear_trade_page(page)
    assert len(batch.events) == 1
    assert batch.events[0].symbol == symbol
    assert batch.events[0].seq == 1001
    assert batch.events[0].exec_id == f"mayak-{symbol}"
    direct = LiveMayakEngine((symbol,))
    replay = LiveMayakEngine((symbol,))
    direct.on_trade(
        "linear", symbol, event.traded_at.timestamp(), event.side,
        float(event.price), float(event.quantity),
    )
    for item in batch.events:
        replay.on_trade(*item.on_trade_args())
    assert replay.trades[("linear", symbol)].frozen_view().rows == (
        direct.trades[("linear", symbol)].frozen_view().rows
    )
    assert not batch.seven_symbol_parity_verified
    assert not batch.safe_to_switch_mayak_public_socket


def test_unapproved_symbol_never_projects_as_native_mayak_trade() -> None:
    event = replace(native(5, 1, 1001, "xrp-rejected"), symbol="XRPUSDT")
    with pytest.raises(R11TradeReplayUnverified,
                       match="PAGE_SHAPE_OR_SCOPE_INVALID"):
        project_r11_native_linear_trade_page(
            replace(native_page((event,)), symbol="XRPUSDT"),
        )
