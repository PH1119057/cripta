"""Independent financial unit audit for unified portfolio H9 depth research #638."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.research import p90l90_unified_depth_three_period_qc_v1 as qc


def row(side="SHORT", entry="100", exit_="98", margin="200",
        maker=False, reason="HARD_STOP"):
    return {
        "side": side, "entry_price": entry, "exit_price": exit_,
        "margin_usdt": margin, "notional_usdt": str(D(margin) * 5),
        "maker_exit": maker, "exit_reason": reason,
    }


def test_fixed_margin_short_win_after_all_taker_fees():
    gross, fee_in, fee_out, net = qc.trade_calculation(row())
    assert gross == D("20.000000000000")
    assert fee_in == D("0.550000000000")
    assert fee_out == D("0.539000000000")
    assert net == D("18.911000000000")


def test_resting_maker_long_fee():
    gross, fee_in, fee_out, net = qc.trade_calculation(
        row(side="LONG", exit_="102", maker=True, reason="RESTING_TP")
    )
    assert gross == D("20.000000000000")
    assert fee_in == D("0.550000000000")
    assert fee_out == D("0.204000000000")
    assert net == D("19.246000000000")


def test_reject_wrong_side_leverage_or_maker_classification():
    with pytest.raises(ValueError, match="native trade"):
        qc.trade_calculation({**row(), "notional_usdt": "100"})
    with pytest.raises(ValueError, match="maker classification"):
        qc.trade_calculation(row(maker=True, reason="HARD_STOP"))
    with pytest.raises(ValueError, match="maker classification"):
        qc.trade_calculation(row(maker=False, reason="RESTING_TP"))
    with pytest.raises(ValueError, match="native trade"):
        qc.trade_calculation(row(side="INVALID"))


def test_unique_result_leaf_and_independent_verification_semantics():
    assert qc.STATUS == "INDEPENDENT_QC_PASS_P90L90_UNIFIED_DEPTH_THREE_PERIODS_V1"
    assert qc.run.RESEARCH.startswith("P90L90_UNIFIED_DEPTH0")
    assert qc.run.WINDOW["183d"]["days"] == 183
    assert len(qc.run.BOOK_NAMES) == 8
