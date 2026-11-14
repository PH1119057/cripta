"""Research #690: exact negative-three-percent causal new-Entry gate tests."""
from decimal import Decimal as D

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_opposite_loss3_exit_2026_v1 as experiment


def event(side, price, ident):
    return {
        "symbol": "LTCUSDT", "side": side, "price": str(price),
        "ts": "100", "event_id": ident, "trade_id": ident,
        "PREV7D": True,
    }


def source_position(side, price):
    return base.initial_position(
        event(side, price, "opp-" + side), 1, 0, "PREV7D", "TRAIL035"
    )


def open_new(side, price, opposite, cap="OPP3"):
    book = experiment.TimeBook(cap, "ALL")
    book.handle(
        "LTCUSDT", 100, 50, D(str(price)),
        (event(side, price, "new-" + side),),
        (D("90"), D("110")), opponent=opposite,
    )
    assert book.count["accepted"] == 1
    return book, book.pos["LTCUSDT"]


def test_exact_loss_three_short_opponent_long_new():
    opposite = source_position("SHORT", "100")
    opposite.stop = D("101")  # tightened live stop must NOT replace original -10%
    book, pos = open_new("LONG", "103", opposite)
    assert pos._r690_special is True
    assert pos.tp == D("110")
    assert pos.tp_live_after_seq == 50
    assert book.special_entries[0]["opponent_unrealized_gross_pct"] == "-3.00"
    assert book.special_entries[0]["target_price"] == "110.0"


def test_exact_loss_three_long_opponent_short_new():
    opposite = source_position("LONG", "100")
    opposite.stop = D("99")
    book, pos = open_new("SHORT", "97", opposite)
    assert pos._r690_special is True
    assert pos.tp == D("90")
    assert len(book.special_entries) == 1


def test_loss_2point99_does_not_trigger():
    for opposite_side, new_side, price in (
        ("SHORT", "LONG", "102.99"),
        ("LONG", "SHORT", "97.01"),
    ):
        book, pos = open_new(
            new_side, price, source_position(opposite_side, "100")
        )
        assert not getattr(pos, "_r690_special", False)
        assert not book.special_entries


def test_no_opponent_no_trigger():
    book, pos = open_new("LONG", "103", None)
    assert not getattr(pos, "_r690_special", False)
    assert book.special_entries == []


def test_special_trail_activates_only_after_1point2():
    _, pos = open_new("LONG", "103", source_position("SHORT", "100"))
    initial_stop = pos.stop
    returned, outcome = experiment.special_on_trade(
        pos, D("104.225"), 51, 0, D("200"), "TRAIL035"
    )
    assert returned is pos and outcome is None
    assert pos.stop == initial_stop
    returned, outcome = experiment.special_on_trade(
        pos, D("104.236"), 52, 0, D("200"), "TRAIL035"
    )
    assert returned is pos and outcome is None
    assert pos.protected and pos.stop == D("103.206")
    returned, outcome = experiment.special_on_trade(
        pos, pos.stop, 53, 0, D("200"), "TRAIL035"
    )
    assert returned is None and outcome[0] == "PROTECTED_STOP"


def test_fixed_target_resting_cannot_hit_on_entry_tick():
    _, pos = open_new("LONG", "103", source_position("SHORT", "100"))
    returned, outcome = experiment.special_on_trade(
        pos, D("110"), 50, 0, D("1"), "TRAIL035"
    )
    assert returned is pos and outcome is None
    returned, outcome = experiment.special_on_trade(
        pos, D("110"), 51, 0, D("1"), "TRAIL035"
    )
    assert returned is None
    assert outcome == ("RESTING_TP", D("110"), True)


def test_control_remains_unmodified():
    opposite = source_position("SHORT", "100")
    book, pos = open_new("LONG", "103", opposite, cap=None)
    assert not getattr(pos, "_r690_special", False)
    assert not book.special_entries
    assert pos.tp == D("110")
