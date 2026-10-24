"""P90L90 independent Exit arithmetic and event-order tests."""
from decimal import Decimal as D

import pytest

from scripts.research.p90l90_exit_v1 import (
    favorable,
    gross_and_net,
    initial_position,
    on_trade,
    take_profit_eligible,
    threshold_price,
    trade_record,
    scenario_keys,
)


def pos(side="LONG", price="100", seq=2, bucket=300):
    event = {"side": side, "price": price, "ts": "300.001"}
    return initial_position(event, seq, bucket, "FIXED90", "LOCK050")


def test_return_signs_long_short_and_tp_threshold():
    assert favorable("LONG", D("100"), D("101")) == D(1)
    assert favorable("SHORT", D("100"), D("99")) == D(1)
    assert threshold_price("SHORT", D("100"), D("0.5")) == D("99.500")
    assert not take_profit_eligible("LONG", D("100"), D("100.49"))
    assert take_profit_eligible("LONG", D("100"), D("100.5"))


def test_lock050_activation_not_retroactive():
    p = pos()
    p, exit_ = on_trade(p, D("100.5"), 3, 300, None, "LOCK050")
    assert exit_ is None and p.protected and p.stop == D("100.500")
    p, exit_ = on_trade(p, D("100.49"), 4, 300, None, "LOCK050")
    assert p is None and exit_ == ("PROTECTED_STOP", D("100.49"), False)


def test_be013_after_half_percent():
    p = pos()
    p, exit_ = on_trade(p, D("100.5"), 3, 300, None, "BE013")
    assert exit_ is None and p.protected
    assert p.stop == D("100.1300")
    p, exit_ = on_trade(p, D("100.12"), 4, 300, None, "BE013")
    assert p is None and exit_[0] == "PROTECTED_STOP"


def test_trail035_percentage_point_distance_and_tighten_only():
    p = pos()
    p, _ = on_trade(p, D("101"), 3, 300, None, "TRAIL035")
    assert p.stop == D("100.6500")  # 1% MFE less 0.35 pp
    p, _ = on_trade(p, D("100.8"), 4, 300, None, "TRAIL035")
    assert p.stop == D("100.6500")
    p, e = on_trade(p, D("100.6"), 5, 300, None, "TRAIL035")
    assert p is None and e[0] == "PROTECTED_STOP"


def test_resting_tp_maker_only_when_preexisting():
    p = pos()
    p.tp = D("101")
    p.tp_live_after_seq = 2
    p, exit_ = on_trade(p, D("101.2"), 3, 300, None, "LOCK050")
    assert p is None and exit_ == ("RESTING_TP", D("101"), True)
    old = pos()
    gross, entry_fee, exit_fee, net = gross_and_net(old, D("101"), True)
    assert gross == D("0.10")
    assert entry_fee == D("0.00550")
    assert exit_fee == D("0.002020")
    assert net == D("0.092480")


def test_market_placement_taker_not_retroactive_maker():
    p = pos()
    p, exit_ = on_trade(p, D("102"), 3, 600, D("101"), "LOCK050")
    assert p is None and exit_ == ("MARKETABLE_TP", D("102"), False)


def test_hardstop_taker_and_short_fee_scaling():
    p = pos("SHORT")
    p, exit_ = on_trade(p, D("110.2"), 3, 300, None, "BE013")
    assert p is None and exit_[0] == "HARD_STOP" and not exit_[2]
    gross, fee_in, fee_out, net = gross_and_net(pos("SHORT"), D("90"), False)
    assert gross == D("1.0") and fee_in == D("0.0055")
    assert fee_out == D("0.00495") and net == D("0.98955")


def test_no_implicit_tp_if_target_below_half_percent():
    p = pos()
    p, result = on_trade(p, D("100.3"), 3, 600, D("100.4"), "LOCK050")
    assert result is None and p.tp is None

def test_model_money_reconciliation_at_declared_quantum():
    # Repeating Decimal divisions previously left a -1E-51 book drift.
    from scripts.research.p90l90_exit_v1 import MONEY_QUANTUM

    p = pos("LONG", "99.91")
    gross_total = D(0)
    entry_fee_total = D(0)
    exit_fee_total = D(0)
    net_total = D(0)
    for px in ("100.12723", "99.81231", "101.10101") * 50:
        gross, entry_fee, exit_fee, net = gross_and_net(p, D(px), True)
        for component in (gross, entry_fee, exit_fee, net):
            assert component == component.quantize(MONEY_QUANTUM)
        assert gross - entry_fee - exit_fee == net
        gross_total += gross
        entry_fee_total += entry_fee
        exit_fee_total += exit_fee
        net_total += net
    assert gross_total - entry_fee_total - exit_fee_total == net_total

def test_trade_record_holding_and_fee_identity():
    p = pos()
    p.entry_event_id = "LTCUSDT:300:LONG"
    p.entry_trade_id = "source-id-1"
    gross, entry_fee, exit_fee, net = gross_and_net(p, D("101"), True)
    row = trade_record(
        "LTCUSDT", "FIXED90", "LOCK050", p,
        100, 321.001, D("101"), "RESTING_TP", True,
        gross, entry_fee, exit_fee, net,
    )
    assert row["candidate_event_id"] == "LTCUSDT:300:LONG"
    assert row["entry_trade_id"] == "source-id-1"
    assert row["holding_seconds"] == "21.000"
    assert row["exit_source_order"] == 100
    assert row["maker_exit"] is True
    assert row["modeled_exchange_fill"] is False
    assert D(row["net_usdt"]) == (
        D(row["gross_usdt"]) - D(row["entry_fee_usdt"]) - D(row["exit_fee_usdt"])
    )


def test_trade_record_fails_bad_chronology_and_maker_category():
    p = pos()
    money = gross_and_net(p, D("101"), True)
    with pytest.raises(ValueError, match="negative modeled holding duration"):
        trade_record(
            "LTCUSDT", "FIXED90", "LOCK050", p, 50, 299.0,
            D("101"), "RESTING_TP", True, *money,
        )
    with pytest.raises(ValueError, match="maker eligibility"):
        trade_record(
            "LTCUSDT", "FIXED90", "LOCK050", p, 50, 320.0,
            D("101"), "HARD_STOP", True, *money,
        )
    with pytest.raises(ValueError, match="monetary reconciliation"):
        trade_record(
            "LTCUSDT", "FIXED90", "LOCK050", p, 50, 320.0,
            D("101"), "RESTING_TP", True, money[0], money[1], money[2],
            money[3] + D("0.01"),
        )

def test_extended_window_research_scope():
    common = scenario_keys("common")
    extended = scenario_keys("extended")
    assert len(common) == 6
    assert len(extended) == 3
    assert all(cohort == "FIXED90" for cohort, _ in extended)
    with pytest.raises(ValueError, match="invalid window"):
        scenario_keys("other")
