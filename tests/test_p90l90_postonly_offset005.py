from __future__ import annotations

from decimal import Decimal as D

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_postonly_offset005_v1 as research


def event(side="LONG", price="100", t=1000):
    return {
        "PREV7D": True,
        "side": side,
        "price": price,
        "ts": str(t),
        "event_id": "test-event",
        "trade_id": "test-trade",
    }


def test_long_postonly_offsets_below_entry_and_needs_later_trade():
    book = research.PassiveBook(1800)
    state = (D("98"), D("102"))
    book.tick("LTCUSDT", 1000.0, 1, D("100"), "a", (event(),), state)
    assert book.extra["placed"] == 1
    assert book.extra["proxy_filled"] == 0
    assert book.orders["LTCUSDT"]["limit"] == D("99.9500")
    book.tick("LTCUSDT", 1001.0, 2, D("99.96"), "b", (), state)
    assert book.extra["proxy_filled"] == 0
    book.tick("LTCUSDT", 1002.0, 3, D("99.94"), "c", (), state)
    assert book.extra["proxy_filled"] == 1
    assert book.pos["LTCUSDT"].entry == D("99.9500")
    assert book.pos["LTCUSDT"].entry_trade_id == "c"


def test_short_postonly_offsets_above_entry():
    book = research.PassiveBook(None)
    state = (D("98"), D("102"))
    book.tick("LTCUSDT", 1000.0, 1, D("100"), "a",
              (event("SHORT"),), state)
    assert book.orders["LTCUSDT"]["limit"] == D("100.0500")
    book.tick("LTCUSDT", 1001.0, 2, D("100.06"), "b", (), state)
    assert book.extra["proxy_filled"] == 1
    assert book.pos["LTCUSDT"].side == "SHORT"


def test_cancel_on_next_5m_boundary_no_same_tick_fill():
    book = research.PassiveBook(None)
    state = (D("98"), D("102"))
    book.tick("LTCUSDT", 1000.0, 1, D("100"), "a", (event(),), state)
    book.tick("LTCUSDT", 1199.0, 2, D("99.96"), "b", (), state)
    assert book.extra["proxy_filled"] == 0
    book.tick("LTCUSDT", 1200.0, 3, D("99.93"), "c", (), state)
    assert book.extra["expired"] == 1
    assert book.extra["proxy_filled"] == 0


def test_maker_fee_proxy_leaves_baseline_fee_unchanged():
    original = base.gross_and_net
    maker_position = base.initial_position(event(), 1, 900, "PREV7D", "TRAIL035")
    regular_position = base.initial_position(event(), 1, 900, "PREV7D", "TRAIL035")
    maker_position.maker_entry_proxy = True
    regular_position.maker_entry_proxy = False
    try:
        base.gross_and_net = research._money
        maker = base.gross_and_net(maker_position, D("100.5"), False)
        taker = base.gross_and_net(regular_position, D("100.5"), False)
    finally:
        base.gross_and_net = original
    assert maker[1] == D("0.002000000000")
    assert taker[1] == D("0.005500000000")
    assert maker[3] - taker[3] == D("0.003500000000")
